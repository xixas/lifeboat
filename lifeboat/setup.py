"""Everything the setup and restore wizards do, without GTK: sign-in, remotes, discovery, config, recovery kit."""
from __future__ import annotations

import configparser
import functools
import json
import os
import re
import secrets
import signal
import socket
import subprocess
import tempfile
import textwrap
import threading
import time
import weakref
from pathlib import Path

from lifeboat import engine
from lifeboat.config import (
    NAME_RE,
    TIME_RE,
    Config,
    ConfigError,
    Job,
    config_dir,
    render_job_block,
    validate_text,
)

# Google desktop OAuth client "lifeboat-app". Installed-app secrets are not confidential, see
# https://developers.google.com/identity/protocols/oauth2/native-app
# NEVER change or rotate these: the drive.file scope only sees files created by this client ID,
# so changing it orphans every user's backup.
# Stored rclone-obscured, as rclone ships its own Google client: the values are public by design, but
# plain text would trip secret scanners, and an automated revocation would break sign-in for every user.
_CLIENT_ID_OBSCURED = "ZUqDPoZHfe8Kwh06Zv2ZzCXfBClaoZDO4qD21egQq8bgGEzt3K-iGWB2Vu8IjSo7ilVGIjOt6a_oI70m2RzuJwBy53KKV8_q4S6hgw4lf4AsKWMhiQ8B2w"
_CLIENT_SECRET_OBSCURED = "6MsOhaZ6blO8D8Kx7xWjM_DNq3JFg0bUOUlWQeEiZQhe-he7z1hWQXec_2n2SnhB4mZP"  # gitleaks:allow (obscured, see comment above)


@functools.cache
def client_id() -> str:
    return _reveal(_CLIENT_ID_OBSCURED)


@functools.cache
def client_secret() -> str:
    return _reveal(_CLIENT_SECRET_OBSCURED)


def _reveal(obscured: str) -> str:
    try:
        p = subprocess.run(["rclone", "reveal", obscured], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise SetupError("rclone is needed to sign in to Google; install rclone 1.60 or newer.") from e
    if p.returncode != 0 or not p.stdout.strip():
        raise SetupError("rclone could not read Lifeboat's Google client; install rclone 1.60 or newer.")
    return p.stdout.strip()

DRIVE_REMOTE = "lifeboat"
CRYPT_REMOTE = "lifeboat-secure"
PASSPHRASE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789"  # no 0/O/1/l/I/o


def drive_root() -> str:
    return os.environ.get("LIFEBOAT_DRIVE_ROOT") or "Lifeboat"


def new_passphrase() -> str:
    return "-".join("".join(secrets.choice(PASSPHRASE_ALPHABET) for _ in range(5)) for _ in range(6))


# ---------------------------------------------------------------- sign-in
AUTH_URL_RE = re.compile(r"http://127\.0\.0\.1:\d+/auth\?state=\S+")
TOKEN_RE = re.compile(r"--->\s*(.*?)\s*<---End paste", re.S)


def parse_auth_url(stderr_text: str) -> str | None:
    """The local link rclone asks the user to open (both the 'Please go to' and the 'If your browser' wordings)."""
    m = AUTH_URL_RE.search(stderr_text)
    return m.group(0) if m else None


def parse_token(stdout_text: str) -> str | None:
    """The raw token JSON between '--->' and '<---End paste', or None until it is complete and has a refresh_token."""
    m = TOKEN_RE.search(stdout_text)
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
    except ValueError:
        return None
    return m.group(1) if isinstance(data, dict) and data.get("refresh_token") else None


class SignInError(Exception):
    """Sign-in could not finish; the message is short and fit to show the user."""


def _terminate(p: subprocess.Popen) -> None:
    """SIGTERM then SIGKILL the process group of p and reap it."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        if p.poll() is not None:
            break
        try:
            os.killpg(p.pid, sig)
        except ProcessLookupError:
            break
        try:
            p.wait(3)
        except subprocess.TimeoutExpired:
            continue
    p.wait()


class SignIn:
    """`rclone authorize drive` as a non-blocking subprocess: start(), poll() until it returns the token, cancel().

    drive.file is forced through the environment, and the client ID and secret travel there too, so neither shows
    up in the process list. Reader threads collect the output; poll() never blocks."""

    def __init__(self, timeout: float = 300.0):
        self.timeout = timeout
        self._proc: subprocess.Popen | None = None
        self._out: list[str] = []
        self._err: list[str] = []
        self._threads: list[threading.Thread] = []
        self._deadline = 0.0
        self._finalizer = weakref.finalize(self, lambda: None)

    def start(self) -> None:
        env = dict(os.environ, RCLONE_DRIVE_SCOPE="drive.file", RCLONE_DRIVE_CLIENT_ID=client_id(),
                   RCLONE_DRIVE_CLIENT_SECRET=client_secret())
        try:
            self._proc = subprocess.Popen(["rclone", "authorize", "drive", "--auth-no-open-browser", "--config", os.devnull],
                                          env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                          text=True, errors="replace", start_new_session=True)
        except OSError as e:
            raise SignInError(f"Could not start rclone: {e.strerror or e}") from None
        self._deadline = time.monotonic() + self.timeout
        self._finalizer = weakref.finalize(self, _terminate, self._proc)  # an abandoned SignIn (or exit) never orphans rclone
        for pipe, sink in ((self._proc.stdout, self._out), (self._proc.stderr, self._err)):
            t = threading.Thread(target=lambda p=pipe, k=sink: k.extend(p), daemon=True)
            t.start()
            self._threads.append(t)

    @property
    def auth_url(self) -> str | None:
        return parse_auth_url("".join(self._err))

    def poll(self) -> str | None:
        """The token JSON once sign-in is done; None while waiting or after cancel(). Raises SignInError on failure or timeout."""
        p = self._proc
        if p is None:
            return None
        token = parse_token("".join(self._out))
        if token:
            self.cancel()
            return token
        rc = p.poll()
        if rc is not None:
            for t in self._threads:
                t.join(2)
            token = parse_token("".join(self._out))
            if token:
                self.cancel()
                return token
            err = "".join(self._err)
            self.cancel()
            if "address already in use" in err:
                raise SignInError("Another sign-in is already running. Close it and try again.")
            raise SignInError("Sign-in failed" + (f": {err.strip().splitlines()[-1]}" if err.strip() else "."))
        if time.monotonic() > self._deadline:
            self.cancel()
            raise SignInError("Sign-in timed out. Try again.")
        return None

    def cancel(self) -> None:
        """Stop rclone (SIGTERM, then SIGKILL) and reap it. Safe to call twice."""
        p, self._proc = self._proc, None
        if p is None:
            return
        self._finalizer.detach()
        _terminate(p)
        for t in self._threads:
            t.join(2)
        for pipe in (p.stdout, p.stderr):
            pipe.close()


# ---------------------------------------------------------------- remotes
# Written straight into rclone.conf instead of `rclone config create`: that command takes the token and the
# passphrase as command-line arguments, which every user on the machine can read in /proc. Only `rclone obscure -`
# is used, and it reads the passphrase from stdin.
class SetupError(Exception):
    """A setup step failed; the message is short and fit to show the user."""


def _obscure(passphrase: str) -> str:
    try:
        p = subprocess.run(["rclone", "obscure", "-"], input=passphrase, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        raise SetupError("Could not run rclone.") from None
    if p.returncode != 0:
        raise SetupError("rclone could not encode the passphrase.")
    return p.stdout.strip()


def _write_section(conf: Path, name: str, values: dict[str, str]) -> None:
    """Add or replace one [section] in an rclone.conf, keeping the rest; written atomically (temp file, fsync, rename), mode 600 from its first byte."""
    cp = configparser.ConfigParser(interpolation=None, delimiters=("=",))
    cp.optionxform = str  # type: ignore[assignment,method-assign]
    cp.read(conf)
    cp[name] = values
    fd, tmp = tempfile.mkstemp(dir=conf.parent, prefix=conf.name + ".", suffix=".tmp")  # mode 600 from creation
    try:
        with os.fdopen(fd, "w") as f:
            cp.write(f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, conf)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def write_crypt_remote(conf: Path, passphrase: str, target: str | None = None) -> None:
    """The crypt remote `lifeboat-secure` over target (default: the secure/ folder of the Drive root)."""
    _write_section(conf, CRYPT_REMOTE, {
        "type": "crypt", "remote": target or f"{DRIVE_REMOTE}:{drive_root()}/secure",
        "filename_encryption": "standard", "directory_name_encryption": "true", "password": _obscure(passphrase)})


def write_drive_remote(conf: Path, token: str) -> None:
    _write_section(conf, DRIVE_REMOTE, {"type": "drive", "client_id": client_id(), "client_secret": client_secret(),
                                        "scope": "drive.file", "token": token, "team_drive": ""})


def make_remotes(conf: Path, token: str, passphrase: str) -> None:
    """Create `lifeboat` (Drive, drive.file) and `lifeboat-secure` (crypt over it) in conf, then check rclone can read it."""
    write_drive_remote(conf, token)
    write_crypt_remote(conf, passphrase)
    p = subprocess.run(["rclone", "listremotes", "--config", str(conf)], capture_output=True, text=True, timeout=20)
    if p.returncode != 0 or f"{CRYPT_REMOTE}:" not in p.stdout.split():
        raise SetupError("rclone could not read the new remotes.")


# ---------------------------------------------------------------- discovery and passphrase check
def _cfg(conf: Path) -> Config:
    return Config(jobs=[], rclone_config=str(conf))


def find_backups(conf: Path) -> list[dict]:
    """The manifest of every device folder under the Drive root. Folders without a readable manifest (the owner's
    legacy layout, secure/) are skipped. Raises SetupError if Drive cannot be listed at all."""
    cfg = _cfg(conf)
    base = f"{DRIVE_REMOTE}:{drive_root()}"
    rc, out = engine.rclone(cfg, ["lsf", base + "/", "--dirs-only"], timeout=60)
    if rc == 3:  # no Lifeboat folder yet
        return []
    if rc != 0:
        raise SetupError("Could not list your Google Drive.")
    found = []
    for d in (ln.rstrip("/") for ln in out.splitlines() if ln.strip()):
        rc, text = engine.rclone(cfg, ["cat", f"{base}/{d}/manifest.json"], timeout=60)
        try:
            doc = json.loads(text) if rc == 0 else None
        except ValueError:
            doc = None
        if isinstance(doc, dict) and doc.get("device"):
            found.append(doc)
    return found


UNREACHABLE = "Couldn't reach your backup. Check your internet connection and try again."


def check_passphrase(conf: Path, passphrase: str, device: str) -> bool:
    """True if passphrase decrypts `device`'s probe file, False if it is wrong (the probe is not found or does not
    read back: rclone rc 3). Any other failure, a timeout or a missing rclone raises SetupError, so a network problem
    is never reported as a wrong passphrase. Uses a throwaway copy of conf (mode 600, deleted afterwards)."""
    fd, tmp = tempfile.mkstemp(prefix="lifeboat-check-", suffix=".conf")
    os.close(fd)
    tmp_conf = Path(tmp)
    try:
        tmp_conf.write_text(Path(conf).read_text())
        try:
            write_crypt_remote(tmp_conf, passphrase)
        except SetupError:  # rclone itself would not run
            raise SetupError(UNREACHABLE) from None
        rc, out = engine.rclone(_cfg(tmp_conf), ["cat", f"{CRYPT_REMOTE}:{device}/.lifeboat-probe"], timeout=60)
        if rc == 0:
            return out.startswith("lifeboat-probe")
        if rc == 3:
            return False
        raise SetupError(UNREACHABLE)
    finally:
        tmp_conf.unlink(missing_ok=True)


# ---------------------------------------------------------------- folder suggestions
XDG_FOLDERS = (("DOCUMENTS", "Documents"), ("PICTURES", "Pictures"), ("VIDEOS", "Videos"), ("MUSIC", "Music"), ("DESKTOP", "Desktop"))
SECRET_FOLDERS = (("ssh", "SSH keys", ".ssh"), ("gnupg", "GPG keys", ".gnupg"))
LARGE_BYTES = 5 * 1024**3  # bigger folders start switched off


def _glib_user_dir(name: str) -> str | None:
    """GLib's answer for an XDG folder ('DOCUMENTS'), or None when PyGObject is not installed."""
    try:
        from gi.repository import GLib
        return GLib.get_user_special_dir(getattr(GLib.UserDirectory, f"DIRECTORY_{name}"))
    except (ImportError, ValueError, AttributeError):
        return None


def parse_user_dirs(text: str, home: str) -> dict[str, str]:
    """~/.config/user-dirs.dirs as {'DOCUMENTS': '/home/u/Docs'}."""
    out = {}
    for line in text.splitlines():
        m = re.match(r'\s*XDG_([A-Z]+)_DIR="(.*)"\s*$', line)
        if m:
            out[m.group(1)] = os.path.normpath(m.group(2).replace("$HOME", home))
    return out


def _xdg_dirs() -> dict[str, str]:
    home = str(Path.home())
    conf = Path(os.environ.get("XDG_CONFIG_HOME") or Path(home) / ".config") / "user-dirs.dirs"
    try:
        parsed = parse_user_dirs(conf.read_text(errors="replace"), home)
    except OSError:
        parsed = {}
    return {name: _glib_user_dir(name) or parsed.get(name) or os.path.join(home, default) for name, default in XDG_FOLDERS}


def suggest_folders() -> list[dict]:
    """Folders worth backing up that exist on this laptop: {key, label, path, default_on, secret}. Sizes come from
    folder_size()/with_size(), so this stays instant. The home folder itself (what GLib returns for an unset
    Desktop) is never suggested."""
    home = str(Path.home())
    dirs = _xdg_dirs()
    out = [{"key": name.lower(), "label": label, "path": dirs[name], "default_on": True, "secret": False}
           for name, label in XDG_FOLDERS if dirs[name] != home and os.path.isdir(dirs[name])]
    out += [{"key": key, "label": label, "path": os.path.join(home, rel), "default_on": True, "secret": True}
            for key, label, rel in SECRET_FOLDERS if os.path.isdir(os.path.join(home, rel))]
    return out


def folder_size(path: str | os.PathLike, limit_s: float = 10) -> tuple[int, bool]:
    """(bytes, complete): total size of regular files under path, symlinks skipped. Stops at limit_s seconds and says so."""
    deadline = time.monotonic() + limit_s
    total, stack = 0, [os.fspath(path)]
    while stack:
        try:
            with os.scandir(stack.pop()) as it:
                for e in it:
                    if time.monotonic() > deadline:
                        return total, False
                    try:
                        if e.is_symlink():
                            continue
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        elif e.is_file(follow_symlinks=False):
                            total += e.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return total, True


def with_size(folder: dict, size: int, complete: bool) -> dict:
    """A copy of folder with its size; a folder over 5 GB, or too big to measure in time, starts switched off."""
    return {**folder, "size": size, "size_complete": complete, "default_on": folder["default_on"] and complete and size <= LARGE_BYTES}


def default_device_name() -> str:
    """This laptop's host name made safe for Config's NAME_RE ('laptop' if nothing usable is left)."""
    name = re.sub(r"[^A-Za-z0-9._-]", "-", socket.gethostname().partition(".")[0]).lstrip("._-")
    return name or "laptop"


# ---------------------------------------------------------------- config writer
KIT_PATTERN = "Lifeboat-recovery-kit*.pdf"
JUNK_EXCLUDES = [KIT_PATTERN, ".cache/**", "node_modules/**", ".venv/**", "__pycache__/**"]


def rclone_conf_path() -> Path:
    """Lifeboat's own rclone.conf, next to its config.toml (never the user's ~/.config/rclone)."""
    return config_dir() / "rclone.conf"


def _slug(label: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", label).strip("-._") or "folder"


def _collapse_home(path: str) -> str:
    home = str(Path.home())
    return "~" + path[len(home):] if path == home or path.startswith(home + "/") else path


def wizard_job(device: str, choice: dict) -> Job:
    """The backup job for one chosen folder: normal ones go to the device's Drive folder, secret ones through the crypt remote."""
    slug = _slug(choice["label"])
    if choice["secret"]:
        base, dest_name = f"{CRYPT_REMOTE}:{device}", slug.lower()
    else:
        base, dest_name = f"{DRIVE_REMOTE}:{drive_root()}/{device}", slug
    return Job(name=slug.lower(), source=_collapse_home(choice["path"]), dest=f"{base}/{dest_name}", old=f"{base}/_old",
               exclude=list(JUNK_EXCLUDES), encrypted=bool(choice["secret"]))


def unique_device_name(base: str, existing: list[str]) -> str:
    """base, or base-2, base-3 ... the first not in existing (the device names find_backups returned)."""
    name, n = base, 1
    while name in existing:
        n += 1
        name = f"{base}-{n}"
    return name


def _write_new_config(path: Path, device: str, daily_at: str, jobs: list[Job]) -> None:
    """Write a NEW config.toml for `device` with these jobs (never replaces one). Validated as a whole before anything is written."""
    path = Path(path)
    if path.exists():
        raise ConfigError(f"{path} already exists; Lifeboat will not replace it")
    if not NAME_RE.match(device):
        raise ConfigError(f"device.name may only use letters, digits, '.', '_' and '-', got {device!r}")
    if not TIME_RE.match(daily_at):
        raise ConfigError(f"schedule.daily_at must look like 13:00, got {daily_at!r}")
    q = json.dumps
    head = (f"[device]\nname = {q(device)}\nmanifest = {q(f'{DRIVE_REMOTE}:{drive_root()}/{device}/manifest.json')}\n"
            f"probe = {q(f'{CRYPT_REMOTE}:{device}/.lifeboat-probe')}\n\n"
            f"[rclone]\nconfig = {q(str(rclone_conf_path()), ensure_ascii=False)}\n\n[schedule]\ndaily_at = {q(daily_at)}\n")
    text = head + "".join("\n" + render_job_block(j) for j in jobs)
    validate_text(text, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        raise ConfigError(f"{path} already exists; Lifeboat will not replace it") from None
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)


def write_wizard_config(path: Path, device: str, choices: list[dict], daily_at: str,
                        existing_devices: list[str] | None = None) -> None:
    """Write a NEW config.toml for this device (never replaces one).
    A device name already used in this account would share (and prune) another laptop's folder, so it is refused."""
    if device in (existing_devices or []):
        raise SetupError(f"The device name {device!r} is already used by another laptop on this account.")
    _write_new_config(path, device, daily_at, [wizard_job(device, c) for c in choices])


def _manifest_jobs(manifest: dict, retired: bool = False) -> list[Job]:
    """The manifest's jobs; with retired=True also those no longer backed up from the device (still on Drive, restorable)."""
    try:
        return [Job(name=j["name"], source=j["source"], dest=j["dest"], old=j["old"], exclude=list(JUNK_EXCLUDES),
                    encrypted=bool(j.get("encrypted"))) for j in manifest["jobs"] + (manifest.get("retired", []) if retired else [])]
    except (KeyError, TypeError):
        raise ConfigError("The backup's manifest is incomplete.") from None


def config_from_manifest(path: Path, manifest: dict, daily_at: str, names: list[str] | None = None) -> None:
    """Write a NEW config.toml that continues a restored backup: the same device name (so the same Drive folder, manifest
    and probe) and the manifest's jobs exactly as stored (dest and old unchanged, sources keep their `~`). `names`
    limits it to those jobs: a folder that was not restored must never be synced over its backup."""
    jobs = [j for j in _manifest_jobs(manifest) if names is None or j.name in names]
    try:
        device = manifest["device"]
    except (KeyError, TypeError):
        raise ConfigError("The backup's manifest is incomplete.") from None
    _write_new_config(path, device, daily_at, jobs)


def restore_config(manifest: dict, conf: Path) -> Config:
    """An in-memory Config that reads from the manifest's own Drive locations, for restore_jobs. It never touches disk,
    so a laptop that continues as a NEW device still restores from the old device's folders."""
    return Config(jobs=_manifest_jobs(manifest, retired=True), rclone_config=str(conf), device_name=str(manifest.get("device", "")))


# ---------------------------------------------------------------- restore
def fix_permissions(path: Path) -> None:
    """Directories 700 and files 600 under path (Drive does not store modes; ssh and gpg refuse loose ones)."""
    path.chmod(0o700)
    for root, dirs, files in os.walk(path):
        for d in dirs:
            if not os.path.islink(os.path.join(root, d)):
                os.chmod(os.path.join(root, d), 0o700)
        for f in files:
            if not os.path.islink(os.path.join(root, f)):
                os.chmod(os.path.join(root, f), 0o600)


def restore_jobs(cfg: Config, names: list[str], emit: engine.Emit) -> dict[str, int]:
    """Restore the named jobs into their original folders without ever overwriting a newer local file, then fix the
    permissions of anything restored under ~/.ssh or ~/.gnupg. Returns {job name: rclone exit code}."""
    jobs = [cfg.job(n) for n in names]  # unknown names fail before anything is copied
    secret_roots = [Path.home() / ".ssh", Path.home() / ".gnupg"]
    results = {}
    for job in jobs:
        target = Path(os.path.expanduser(job.source))
        secret = any(target == r or r in target.parents for r in secret_roots)
        old_umask = os.umask(0o077) if secret else None  # restored keys are never world-readable, not even briefly
        try:
            results[job.name] = engine.restore(cfg, job, job.source, emit=emit, keep_newer=True)
        finally:
            if old_umask is not None:
                os.umask(old_umask)
        if secret and target.is_dir():  # also after a failed or partial copy
            fix_permissions(target)
    return results


# ---------------------------------------------------------------- recovery kit PDF
def _pdf_text(s: str) -> str:
    """ASCII-only and escaped for a PDF literal string."""
    s = s.encode("ascii", "replace").decode("ascii")
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


KIT_STEPS = ("To get your files back on a new laptop:",
             "1. Install Lifeboat and open it.",
             "2. Choose Restore from a backup.",
             "3. Sign in to the same Google account.",
             "4. Enter the passphrase above when asked.")
KIT_MANUAL = ("If Lifeboat is unavailable: download the Lifeboat folder from drive.google.com, then decrypt the "
              "secure/ folder with rclone using a crypt remote with this passphrase (filename encryption: standard).")


def kit_pdf(path: Path, account_email: str | None, device: str, passphrase: str, created: str) -> None:
    """Write the one-page recovery kit as a minimal PDF 1.4 (base-14 fonts, ASCII only, no dependencies), mode 600."""
    H, HB, C = "/F1", "/F2", "/F3"
    rows: list[tuple[str, int, int, str]] = [(HB, 22, 34, "Lifeboat recovery kit"),
                                              (H, 11, 18, f"Account: {account_email or '(your Google account)'}"),
                                              (H, 11, 18, f"Device: {device}"),
                                              (H, 11, 30, f"Created: {created}"),
                                              (HB, 12, 20, "Passphrase (keep this safe, it cannot be recovered)"),
                                              (C, 18, 36, passphrase),
                                              (HB, 12, 20, "Client ID"),
                                              (C, 9, 30, client_id())]
    rows += [(HB if i == 0 else H, 12 if i == 0 else 11, 17, line) for i, line in enumerate(KIT_STEPS)]
    rows += [(H, 11, 14, line) for line in [""] + textwrap.wrap(KIT_MANUAL, 88)]
    y, ops = 790, []
    for font, size, lead, text in rows:
        if text:
            ops.append(f"BT {font} {size} Tf 50 {y} Td ({_pdf_text(text)}) Tj ET")
        y -= lead
    stream = "\n".join(ops)
    objs = ["<< /Type /Catalog /Pages 2 0 R >>",
            "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 7 0 R "
            "/Resources << /Font << /F1 4 0 R /F2 5 0 R /F3 6 0 R >> >> >>",
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>",
            "<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>",
            f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream"]
    out, offsets = "%PDF-1.4\n", []
    for n, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n{body}\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n" + "".join(f"{o:010d} 00000 n \n" for o in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as f:
        f.write(out)
