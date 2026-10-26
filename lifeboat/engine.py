"""Backup engine: no GTK. Everything the CLI and the app do goes through here."""
from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

from lifeboat import __version__
from lifeboat.config import Config, ConfigError, Job
from lifeboat.runlog import Rclone, RunLog, human_size, plural

STAMP_FMT = "%Y-%m-%d_%H%M%S"
STAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{6}$")
STALE_DAYS = 2
ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\r")
COMMON = ["--log-level", "INFO", "--stats", "0", "--fast-list"]
JSON_LOG = ["--use-json-log"]  # one JSON record per line, rewritten by lifeboat.runlog; --stats 0 means no stats record
Emit = Callable[[str], None]


# ---------------------------------------------------------------- paths and state
def state_dir() -> Path:
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "lifeboat"


def read_json(path: Path | str):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def write_json(name: str, data: dict) -> None:
    d = state_dir()
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / (name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.replace(d / name)


def tail(path: Path | str, n: int) -> list[str]:
    try:
        with open(path, errors="replace") as f:
            return [ANSI.sub("", ln.rstrip("\n")) for ln in f.readlines()[-n:]]
    except OSError:
        return []


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def parse_time(s):
    try:
        return datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def age_days(iso) -> float | None:
    dt = parse_time(iso)
    return None if dt is None else (datetime.now(dt.tzinfo) - dt).total_seconds() / 86400


# ---------------------------------------------------------------- subprocess helpers
def rclone_env(cfg: Config) -> dict:
    env = dict(os.environ)
    if cfg.password_command:
        env["RCLONE_PASSWORD_COMMAND"] = cfg.password_command
    return env


def rclone_argv(cfg: Config, args: list[str]) -> list[str]:
    # Never prompt: an encrypted rclone.conf without password_command must fail fast
    # (timers and the GUI have no terminal), not wait on stdin.
    argv = ["rclone", "--ask-password=false"]
    if cfg.rclone_config:
        argv += ["--config", os.path.expanduser(cfg.rclone_config)]
    return argv + list(args)


def rclone(cfg: Config, args: list[str], timeout: int | None = 120, input: str | None = None,
           env: dict | None = None) -> tuple[int, str]:
    """Run rclone and capture stdout; `input` is fed to stdin (for rcat). rc 127 if rclone is missing or times out."""
    try:
        p = subprocess.run(rclone_argv(cfg, args), env=env if env is not None else rclone_env(cfg), capture_output=True, text=True,
                           timeout=timeout, input=input, stdin=None if input is not None else subprocess.DEVNULL)
        return p.returncode, p.stdout
    except (OSError, subprocess.TimeoutExpired):
        return 127, ""


def stream(argv: list[str], env: dict, log, emit: Emit | None = None, handle: Callable[[str], str | None] | None = None) -> int:
    """Run argv; send each merged output line to the open file `log` and the `emit` callback.
    `handle` turns a raw line into the line to show, or None to drop it."""
    def show(line: str) -> None:
        line = handle(line) if handle else line
        if line is None:
            return
        log.write(line + "\n")
        log.flush()
        if emit:
            emit(line)

    try:
        p = subprocess.Popen(argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                             errors="replace", stdin=subprocess.DEVNULL)
    except OSError as e:
        show(f"could not start {argv[0]}: {e.strerror or e}")
        return 127
    for line in p.stdout:
        show(ANSI.sub("", line.rstrip("\n")))
    return p.wait()


# ---------------------------------------------------------------- backup
def job_args(cfg: Config, job: Job, stamp: str) -> list[str]:
    args = ["sync", os.path.expanduser(job.source), job.dest,
            "--backup-dir", f"{job.old.rstrip('/')}/{stamp}/{job.name}"]
    for pat in job.include:
        args += ["--include", pat]
    for pat in job.exclude:
        args += ["--exclude", pat]
    if job.filter_file:
        args += ["--filter-from", cfg.filter_path(job)]
    return args


def has_files(path: str) -> bool:
    """True if path is a directory holding at least one regular file (guards against syncing an empty mount)."""
    if not os.path.isdir(path):
        return False
    return any(files for _root, _dirs, files in os.walk(path))


def dest_state(cfg: Config, job: Job, env: dict) -> str:
    """"empty" if the destination lists nothing (a missing one, rclone rc 3, counts), "has_files", or "unreadable"."""
    rc, out = rclone(cfg, ["lsf", job.dest, "--max-depth", "1"], env=env)
    if rc == 3 or (rc == 0 and not out.strip()):
        return "empty"
    return "has_files" if rc == 0 else "unreadable"


def dest_empty(cfg: Config, job: Job, env: dict) -> bool:
    return dest_state(cfg, job, env) == "empty"


def _run_settings(cfg: Config, rl: RunLog) -> bool:
    rl.line("Settings", f"$ {cfg.settings_command}")
    rc = stream(["sh", "-c", cfg.settings_command], rclone_env(cfg), rl.log, rl.emit, lambda raw: rl.stamp("Settings", raw))
    write_json("settings.json", {"time": now_iso(), "ok": rc == 0, "rc": rc})
    return rc == 0


def run_settings(cfg: Config, emit: Emit | None = None) -> bool:
    """Run the optional settings-snapshot command and record settings.json. True if ok or not configured."""
    if not cfg.settings_command:
        return True
    state_dir().mkdir(parents=True, exist_ok=True)
    with open(state_dir() / "run.log", "w") as log:
        return _run_settings(cfg, RunLog(log, emit))


def _job_doc(j: Job) -> dict:
    return {"name": j.name, "source": j.source, "dest": j.dest, "old": j.old, "encrypted": j.encrypted}


def manifest_doc(cfg: Config, state: dict, previous: dict | None = None) -> dict:
    """What a new laptop needs to find and restore this device's backup. Paths only: no file lists, no secrets.
    Jobs that the previous manifest listed but the config no longer has are kept under "retired" (with `since`, the
    time they were first carried over), because their data is still on Drive and a restore must still find it."""
    doc = {"format": 1, "device": cfg.device_name, "host": socket.gethostname(), "lifeboat": __version__,
           "time": state["time"], "ok": state["ok"], "jobs": [_job_doc(j) for j in cfg.jobs]}
    current = {j.name for j in cfg.jobs}
    retired: dict[str, dict] = {}
    for j in (previous or {}).get("jobs", []):
        retired[j["name"]] = {**j, "since": state["time"]}
    for j in (previous or {}).get("retired", []):
        retired[j["name"]] = j  # its original `since` wins
    kept = [j for n, j in retired.items() if n not in current]
    if kept:
        doc["retired"] = kept
    return doc


def upload_manifest(cfg: Config, state: dict) -> dict:
    rc, text = rclone(cfg, ["cat", cfg.manifest], timeout=60)
    if rc not in (0, 3):  # not "missing": we could not read it, and writing now could drop the retired list
        return {"ok": False, "rc": rc}
    try:
        previous = json.loads(text) if rc == 0 else None
    except ValueError:
        previous = None
    if not isinstance(previous, dict):
        previous = None
    rc, _ = rclone(cfg, ["rcat", cfg.manifest] + COMMON, input=json.dumps(manifest_doc(cfg, state, previous), indent=2) + "\n", timeout=None)
    return {"ok": rc == 0, "rc": rc}


PROBE_TEXT = "lifeboat-probe 1\n"


def ensure_probe(cfg: Config) -> dict:
    """Create the passphrase probe inside the crypt remote unless it is already there."""
    rc, out = rclone(cfg, ["lsf", cfg.probe])
    if rc != 0 or not out.strip():
        rc, _ = rclone(cfg, ["rcat", cfg.probe] + COMMON, input=PROBE_TEXT, timeout=None)
    return {"ok": rc == 0, "rc": rc}


def crypt_target(cfg: Config, name: str) -> str:
    """The `remote =` a crypt remote points at ("" if unknown). Env vars (RCLONE_CONFIG_<NAME>_REMOTE) beat the config file, as in rclone."""
    env = rclone_env(cfg)
    if target := env.get(f"RCLONE_CONFIG_{re.sub('[^A-Z0-9]', '_', name.upper())}_REMOTE"):
        return target
    rc, out = rclone(cfg, ["config", "dump"], env=env)
    try:
        return str(json.loads(out)[name]["remote"]) if rc == 0 else ""
    except (ValueError, KeyError, TypeError):
        return ""


def passphrase_state(cfg: Config) -> str:
    """"ok" (the probe reads back), "new" (nothing is stored yet, or it is only other devices' data under this same key: safe to
    create the probe), "wrong" (data exists under a key this passphrase cannot read) or "unknown" (could not tell: treat as unsafe)."""
    if check_probe(cfg.probe, cfg.rclone_config, rclone_env(cfg)):
        return "ok"
    name = cfg.probe.partition(":")[0]
    target = crypt_target(cfg, name)
    if not target:
        return "unknown"
    rc, out = rclone(cfg, ["lsf", target, "--max-depth", "1"])
    if rc == 3 or (rc == 0 and not out.strip()):
        return "new"
    if rc != 0:
        return "unknown"
    # Listing alone proves nothing: a wrong key yields a garbage name now and then (~0.4% of entries pass unpadding).
    rc, out = rclone(cfg, ["lsf", "--dirs-only", f"{name}:"])
    if rc != 0:
        return "unknown"
    for d in out.split():  # every device's probe sits at <device>/.lifeboat-probe; reading it proves the key
        if check_probe(f"{name}:{d.rstrip('/')}/.lifeboat-probe", cfg.rclone_config, rclone_env(cfg)):
            return "new"
    rc, out = rclone(cfg, ["lsf", "-R", "--files-only", "--max-depth", "3", f"{name}:"])
    first = out.split("\n")[0] if rc == 0 else ""
    if first:  # content is MAC-authenticated: one byte decrypts only under the right key
        try:
            p = subprocess.run(rclone_argv(cfg, ["cat", "--count", "1", f"{name}:{first}"]), env=rclone_env(cfg), capture_output=True,
                               text=True, timeout=120, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired):
            return "unknown"
        # only a failed MAC says "wrong key"; a dropped connection says nothing (setup.py never calls that wrong either)
        return "new" if p.returncode == 0 else "wrong" if "bad password" in p.stderr else "unknown"
    # no readable file: only empty dirs (every raw name decrypts) or a wrong key (names drop out of the crypt listing)
    rc, plain = rclone(cfg, ["lsf", "-R", "--max-depth", "3", f"{name}:"])
    rc2, raw = rclone(cfg, ["lsf", "-R", "--max-depth", "3", target])
    if rc or rc2:
        return "unknown"
    return "new" if len(plain.split("\n")) == len(raw.split("\n")) else "wrong"


def check_probe(probe: str, rclone_config: str = "", env: dict | None = None) -> bool:
    """True only if the probe reads back through its crypt remote. A crypt remote's root listing succeeds with a
    wrong password; reading a known file does not. Works without a Lifeboat config (restore wizard)."""
    rc, out = rclone(Config(jobs=[], rclone_config=rclone_config), ["cat", probe], timeout=60, env=env)
    return rc == 0 and out.startswith("lifeboat-probe")


def run_backup(cfg: Config, dry_run: bool = False, now: datetime | None = None, emit: Emit | None = None,
               settings: bool = True, on_event: Callable[[dict], None] | None = None) -> dict:
    """Optional settings command, then every job. Always writes last-run.json. Returns that state.
    `on_event` gets job_start / job_end (files copied, error text, a `reason` code for a failure) per job, for a progress display.
    `emit` gets the readable run log, the same lines as run.log."""
    from lifeboat.app.wizard_state import job_label  # GTK-free; imported late because it imports this module
    event = on_event or (lambda _e: None)
    stamp = (now or datetime.now()).strftime(STAMP_FMT)
    state_dir().mkdir(parents=True, exist_ok=True)
    t_run = time.monotonic()
    env = rclone_env(cfg)
    results, copied, copied_bytes = [], 0, 0
    with open(state_dir() / "run.log", "w") as log:
        rl = RunLog(log, emit)
        what = "dry run" if dry_run else "backup"
        rl.raw(f"Lifeboat {__version__} · {what}{' of ' + cfg.device_name if cfg.device_name else ''} · "
               f"{datetime.now():%a %d %b %Y %H:%M:%S}")
        if settings and cfg.settings_command and not dry_run:
            _run_settings(cfg, rl)
        pstate = passphrase_state(cfg) if cfg.probe and any(j.encrypted for j in cfg.jobs) else "ok"
        refuse = {"wrong": "wrong_passphrase", "unknown": "dest_unreadable"}.get(pstate, "")
        probe = ensure_probe(cfg) if pstate == "new" and not dry_run else None  # before any upload, so an interrupted first run still leaves one
        for job in cfg.jobs:
            t0, label = time.monotonic(), job_label(job.name)
            event({"event": "job_start", "name": job.name})
            files, error, reason = 0, "", ""
            src = os.path.expanduser(job.source)
            rl.line(label, f"{job.source} → Google Drive: {job.dest.partition(':')[2] or job.dest}"
                           f"{' (encrypted)' if job.encrypted else ''}")
            if refuse and job.encrypted:
                reason, rc = refuse, 2
                error = (f"{job.name}: the passphrase doesn't match this backup, nothing was uploaded" if reason == "wrong_passphrase" else
                         f"{job.name}: could not check the passphrase against the existing backup, nothing was uploaded")
                rl.line(label, "ERROR " + error.removeprefix(f"{job.name}: "))
                results.append({"name": job.name, "rc": rc, "seconds": 0, "skipped": False})
                event({"event": "job_end", "name": job.name, "rc": rc, "skipped": False, "files": 0, "error": error, "reason": reason})
                continue
            empty = not has_files(src)
            dest = dest_state(cfg, job, env) if empty and os.path.isdir(src) else ""
            skipped = dest == "empty"
            if skipped:
                rl.line(label, "skipped, folder is empty, nothing to back up yet")
                # an empty destination folder keeps restore working for this job
                rc = 0 if dry_run else rclone(cfg, ["mkdir", job.dest], env=env)[0]
                if rc:
                    skipped, reason = False, "mkdir_failed"
                    error = f"{job.name}: could not create the empty backup folder {job.dest} (rclone exit {rc})"
            elif empty:
                reason = {"has_files": "empty_with_backup", "unreadable": "dest_unreadable"}.get(dest, "missing")
                error = f"{job.name}: refusing to sync: source {src} is missing or empty (unmounted drive or typo?) — nothing was changed"
                rc = 2
            else:
                args = job_args(cfg, job, stamp) + COMMON + JSON_LOG + (["--dry-run"] if dry_run else [])
                tracker = Rclone()
                rc = stream(rclone_argv(cfg, args), env, log, emit, rl.handler(label, tracker))
                files, error = tracker.files, tracker.error
                copied, copied_bytes = copied + files, copied_bytes + tracker.bytes
                seconds = int(time.monotonic() - t0)
                if rc:
                    reason = "rclone"
                    error = error or f"{job.name}: rclone exited with code {rc}"
                    rl.line(label, f"ERROR rclone exited with code {rc}" if not tracker.error else "failed")
                else:
                    rl.line(label, f"done {plural(files, 'file')}, {human_size(tracker.bytes)}, {seconds} s" if files else
                            f"dry run, would copy {plural(tracker.would, 'file')}, {human_size(tracker.would_bytes)}" if tracker.would else
                            "done, already up to date")
            if error and reason != "rclone":
                rl.line(label, "ERROR " + error.removeprefix(f"{job.name}: "))
            results.append({"name": job.name, "rc": rc, "seconds": int(time.monotonic() - t0), "skipped": skipped})
            event({"event": "job_end", "name": job.name, "rc": rc, "skipped": skipped, "files": files, "error": error,
                   "reason": reason})
        state = {"time": now_iso(), "dry_run": dry_run, "ok": all(r["rc"] == 0 for r in results), "jobs": results}
        finishing = bool((cfg.manifest or cfg.probe) and not dry_run)
        if finishing:
            event({"event": "finishing_start"})
        if cfg.manifest and not dry_run:
            state["manifest"] = upload_manifest(cfg, state)
            rl.line("", "Lifeboat saved the list of backed-up folders" if state["manifest"]["ok"] else
                    f"ERROR could not save the list of backed-up folders (rclone exit {state['manifest']['rc']})")
            state["ok"] = state["ok"] and state["manifest"]["ok"]
        if cfg.probe and not dry_run and not refuse and any(j.encrypted for j in cfg.jobs):  # an unchecked key never writes it
            state["probe"] = probe or ensure_probe(cfg)
            rl.line("", "Lifeboat passphrase check file is in place" if state["probe"]["ok"] else
                    f"ERROR could not create the passphrase check file (rclone exit {state['probe']['rc']})")
            state["ok"] = state["ok"] and state["probe"]["ok"]
        if finishing:
            event({"event": "finishing_end", "ok": all(state[k]["ok"] for k in ("manifest", "probe") if k in state)})
        failed = sum(r["rc"] != 0 for r in results)
        if dry_run:
            rl.line("", "Finished: dry run, nothing was changed")
        elif state["ok"]:
            rl.line("", f"Finished: backed up {plural(copied, 'file')} in {int(time.monotonic() - t_run)} s")
        else:
            rl.line("", "Finished with problems: " + (f"{plural(failed, 'folder')} not backed up" if failed else
                                                      "the backup list or passphrase check could not be saved"))
    write_json("last-run.json", state)
    return state


# ---------------------------------------------------------------- versions, prune, restore
def _list_dirs(cfg: Config, remote: str, depth: int = 1) -> tuple[bool, list[str]]:
    """(ok, directory paths) under remote. A missing directory (rclone rc 3) is ok and empty."""
    rc, out = rclone(cfg, ["lsf", remote, "--dirs-only", "-R", "--max-depth", str(depth)])
    if rc == 3:
        return True, []
    return rc == 0, [ln.rstrip("/") for ln in out.splitlines() if ln.strip()]


def prune(cfg: Config, now: datetime | None = None) -> dict:
    """Purge <old>/<stamp> folders whose NAME is older than keep_days. Not --min-age: --backup-dir keeps
    each file's original modtime, so old files would be purged on the first run."""
    cutoff = ((now or datetime.now()) - timedelta(days=cfg.keep_days)).strftime(STAMP_FMT)
    removed, ok = [], True
    for old in dict.fromkeys(j.old.rstrip("/") for j in cfg.jobs):
        listed, dirs = _list_dirs(cfg, old)
        ok &= listed
        for d in dirs:
            if STAMP_RE.match(d) and d < cutoff:
                rc, _ = rclone(cfg, ["purge", f"{old}/{d}"] + COMMON, timeout=None)
                if rc == 0:
                    removed.append(f"{old}/{d}")
                else:
                    ok = False
    state = {"time": now_iso(), "ok": ok, "cutoff": cutoff, "removed": removed}
    write_json("prune.json", state)
    return state


def list_versions(cfg: Config, job: Job | str) -> list[str]:
    """Stamps (newest first) under `old` that hold a copy of this job."""
    job = cfg.job(job) if isinstance(job, str) else job
    _ok, dirs = _list_dirs(cfg, job.old.rstrip("/"), depth=2)
    return sorted((d.split("/")[0] for d in dirs if d.endswith("/" + job.name) and STAMP_RE.match(d.split("/")[0])),
                  reverse=True)


def restore(cfg: Config, job: Job | str, to_dir: str | os.PathLike, version: str | None = None,
            files: list[str] | None = None, dry_run: bool = False, emit: Emit | None = None,
            keep_newer: bool = False) -> int:
    """Copy a job's current backup (version None) or one old version into to_dir with `rclone copy`: it adds
    and overwrites inside to_dir, deletes nothing, and never touches the job's live source unless the caller
    passes that very directory as to_dir. keep_newer adds rclone's --update: a file that is newer
    in to_dir is left alone."""
    job = cfg.job(job) if isinstance(job, str) else job
    if version is not None and not STAMP_RE.match(version):
        raise ConfigError(f"version must look like 2026-01-31_130000, got {version!r}")
    src = job.dest if version is None else f"{job.old.rstrip('/')}/{version}/{job.name}"
    args = ["copy", src, os.path.expanduser(str(to_dir))] + COMMON + JSON_LOG + (["--dry-run"] if dry_run else []) + (["--update"] if keep_newer else [])
    tmp = None
    if files:
        tmp = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False)
        tmp.write("\n".join(files) + "\n")
        tmp.close()
        args += ["--files-from", tmp.name]
    state_dir().mkdir(parents=True, exist_ok=True)
    try:
        with open(state_dir() / "restore.log", "w") as log:
            from lifeboat.app.wizard_state import job_label
            rl, tracker, label = RunLog(log, emit), Rclone(), job_label(job.name)
            rc = stream(rclone_argv(cfg, args), rclone_env(cfg), log, emit, rl.handler(label, tracker))
            rl.line(label, ("restored " + plural(tracker.files, "file") + ", " + human_size(tracker.bytes)) if rc == 0
                    else "ERROR restore failed" if tracker.error else f"ERROR rclone exited with code {rc}")
            return rc
    finally:
        if tmp:
            os.unlink(tmp.name)


# ---------------------------------------------------------------- status
def timers() -> dict:
    unit_dir = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "systemd" / "user"
    res = {n: {"installed": (unit_dir / f"{n}.timer").exists(), "next": None, "last": None}
           for n in ("lifeboat", "lifeboat-prune")}
    if not shutil.which("systemctl"):
        return res

    def when(us):
        return datetime.fromtimestamp(us / 1e6).astimezone().isoformat() if us else None

    try:
        p = subprocess.run(["systemctl", "--user", "list-timers", "--all", "-o", "json"], capture_output=True,
                           text=True, timeout=20, stdin=subprocess.DEVNULL)
        for t in json.loads(p.stdout):
            name = str(t.get("unit", "")).removesuffix(".timer")
            if name in res:
                res[name].update(next=when(t.get("next")), last=when(t.get("last")))
    except (OSError, ValueError, TypeError, subprocess.TimeoutExpired):
        pass
    return res


def list_remotes(cfg: Config) -> list[dict]:
    """rclone remotes as [{"name": "gdrive:", "type": "drive"}]. Raises ConfigError if rclone cannot list them."""
    if not shutil.which("rclone"):
        raise ConfigError("rclone is not installed")
    rc, out = rclone(cfg, ["listremotes", "--long"], timeout=20)
    if rc != 0:
        raise ConfigError("rclone could not list its remotes (is rclone.conf readable and unlocked?)")
    remotes = []
    for line in out.splitlines():
        name, colon, kind = line.partition(":")
        if colon:
            remotes.append({"name": name + ":", "type": kind.strip()})
    return remotes


def remote_names(cfg: Config) -> list[str]:
    seen: list[str] = []
    for j in cfg.jobs:
        for spec in (j.dest, j.old):
            m = re.match(r"^([A-Za-z0-9_][A-Za-z0-9_. -]*):", spec)
            if m and m.group(1) + ":" not in seen:
                seen.append(m.group(1) + ":")
    return seen


def status(cfg: Config) -> dict:
    sd = state_dir()
    last = read_json(sd / "last-run.json")
    results = {r["name"]: r for r in (last or {}).get("jobs", [])}
    jobs = [{"name": j.name, "source": j.source, "dest": j.dest, "old": j.old, "encrypted": j.encrypted,
             "rc": results.get(j.name, {}).get("rc"), "seconds": results.get(j.name, {}).get("seconds"),
             "skipped": results.get(j.name, {}).get("skipped")}
            for j in cfg.jobs]
    found = shutil.which("rclone") is not None
    listed = rclone(cfg, ["listremotes"], timeout=20)[1].split() if found else []
    d = {"config_path": str(cfg.path), "state_dir": str(sd), "daily_at": cfg.daily_at, "keep_days": cfg.keep_days,
         "rclone": {"found": found}, "missing_remotes": [r for r in remote_names(cfg) if found and r not in listed],
         "last": last, "manifest": (last or {}).get("manifest"), "probe": (last or {}).get("probe"),
         "last_age_days": age_days((last or {}).get("time")), "jobs": jobs,
         "settings": {"enabled": bool(cfg.settings_command), "command": cfg.settings_command,
                      "last": read_json(sd / "settings.json"), "log": tail(sd / "run.log", 20)},
         "prune": read_json(sd / "prune.json"), "timers": timers(), "skills": None}
    if cfg.skills_enabled:
        from lifeboat.skills import skills
        d["skills"] = skills()
    _verdict(d)
    return d


def _verdict(d: dict) -> None:
    last, age = d["last"], d["last_age_days"]
    if not d["rclone"]["found"]:
        b = "error"
    elif last is None:
        b = "warn"
    elif not last.get("ok"):
        b = "error"
    else:
        b = "ok" if not last.get("dry_run") and age is not None and age <= STALE_DAYS else "warn"
    s = {"backups": b}
    if d["settings"]["enabled"]:
        snap = d["settings"]["last"]
        s["settings"] = "warn" if snap is None else "ok" if snap.get("ok") else "error"
    if d["skills"] is not None:
        s["skills"] = "ok"
    core = [s[k] == "ok" for k in ("backups", "settings") if k in s]
    s["overview"] = "ok" if all(core) else "warn" if any(core) else "error"
    n = sum(core)
    title = "Fully covered" if n == len(core) else "Partly covered" if n else "Not covered"
    if b == "ok":
        weakest = "Settings are not snapshotted." if s.get("settings", "ok") != "ok" else "Everything is backed up."
    elif not d["rclone"]["found"]:
        weakest = "rclone is not installed."
    elif last is None:
        weakest = "No backup has run yet."
    elif not last.get("ok"):
        weakest = "The last backup failed."
    elif last.get("dry_run"):
        weakest = "Only a dry run has happened; no real backup yet."
    else:
        weakest = f"The last backup is more than {STALE_DAYS} days old."
    d["status"] = s
    d["verdict"] = {"title": title, "weakest": weakest}


def problems(cfg: Config, st: dict) -> list[dict]:
    out = []
    if not st["rclone"]["found"]:
        out.append({"page": "backups", "title": "rclone not found", "body": "Install rclone to run backups.", "urgent": True})
    elif st["status"]["backups"] != "ok":
        last = st["last"]
        failed = [j["name"] for j in st["jobs"] if j["rc"] not in (None, 0)]
        if last is None:
            body = "No backup has run yet."
        elif failed:
            body = "These backup jobs failed: " + ", ".join(failed) + "."
        elif not last.get("ok"):
            body = "The last backup failed."
        else:
            body = f"The last backup is a dry run or older than {STALE_DAYS} days."
        out.append({"page": "backups", "title": "Backup needs attention", "body": body, "urgent": True})
    if st["missing_remotes"]:
        out.append({"page": "backups", "title": "rclone remote not configured", "urgent": False,
                    "body": "Missing: " + ", ".join(st["missing_remotes"]) + ". Run rclone config."})
    if any(st[k] and not st[k]["ok"] for k in ("manifest", "probe")):
        out.append({"page": "backups", "title": "Restore discovery is broken", "urgent": True,
                    "body": "Restore on a new laptop would not find this backup: the device manifest or passphrase probe could not be written."})
    snap = st["settings"]["last"]
    if st["settings"]["enabled"] and snap and not snap.get("ok"):
        out.append({"page": "settings", "title": "Settings snapshot failed",
                    "body": "The last settings snapshot did not complete.", "urgent": True})
    return out
