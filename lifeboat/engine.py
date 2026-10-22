"""Backup engine: no GTK. Everything the CLI and the app do goes through here."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

from lifeboat.config import Config, ConfigError, Job

STAMP_FMT = "%Y-%m-%d_%H%M%S"
STAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{6}$")
STALE_DAYS = 2
ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\r")
COMMON = ["--log-level", "INFO", "--stats", "0", "--fast-list"]
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


def rclone(cfg: Config, args: list[str], timeout: int | None = 120) -> tuple[int, str]:
    """Run rclone and capture stdout. rc 127 if rclone is missing or times out."""
    try:
        p = subprocess.run(rclone_argv(cfg, args), env=rclone_env(cfg), capture_output=True, text=True,
                           timeout=timeout, stdin=subprocess.DEVNULL)
        return p.returncode, p.stdout
    except (OSError, subprocess.TimeoutExpired):
        return 127, ""


def stream(argv: list[str], env: dict, log, emit: Emit | None = None) -> int:
    """Run argv; send each merged output line to the open file `log` and the `emit` callback."""
    try:
        p = subprocess.Popen(argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                             errors="replace", stdin=subprocess.DEVNULL)
    except OSError as e:
        msg = f"could not start {argv[0]}: {e.strerror or e}"
        log.write(msg + "\n")
        if emit:
            emit(msg)
        return 127
    for line in p.stdout:
        line = ANSI.sub("", line.rstrip("\n"))
        log.write(line + "\n")
        log.flush()
        if emit:
            emit(line)
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


def run_settings(cfg: Config, emit: Emit | None = None) -> bool:
    """Run the optional settings-snapshot command and record settings.json. True if ok or not configured."""
    if not cfg.settings_command:
        return True
    state_dir().mkdir(parents=True, exist_ok=True)
    with open(state_dir() / "run.log", "w") as log:
        log.write(f"$ {cfg.settings_command}\n")
        rc = stream(["sh", "-c", cfg.settings_command], rclone_env(cfg), log, emit)
    write_json("settings.json", {"time": now_iso(), "ok": rc == 0, "rc": rc})
    return rc == 0


def run_backup(cfg: Config, dry_run: bool = False, now: datetime | None = None, emit: Emit | None = None,
               settings: bool = True) -> dict:
    """Optional settings command, then every job. Always writes last-run.json. Returns that state."""
    stamp = (now or datetime.now()).strftime(STAMP_FMT)
    state_dir().mkdir(parents=True, exist_ok=True)
    ran_settings = settings and bool(cfg.settings_command) and not dry_run
    if ran_settings:
        run_settings(cfg, emit)
    results = []
    env = rclone_env(cfg)
    with open(state_dir() / "run.log", "a" if ran_settings else "w") as log:
        for job in cfg.jobs:
            t0 = time.monotonic()
            args = job_args(cfg, job, stamp) + COMMON + (["--dry-run"] if dry_run else [])
            rc = stream(rclone_argv(cfg, args), env, log, emit)
            results.append({"name": job.name, "rc": rc, "seconds": int(time.monotonic() - t0)})
            line = f"{job.name} rc={rc}"
            log.write(line + "\n")
            if emit:
                emit(line)
    state = {"time": now_iso(), "dry_run": dry_run, "ok": all(r["rc"] == 0 for r in results), "jobs": results}
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
            files: list[str] | None = None, dry_run: bool = False, emit: Emit | None = None) -> int:
    """Copy a job's current backup (version None) or one old version into to_dir with `rclone copy`: it adds
    and overwrites inside to_dir, deletes nothing, and never touches the job's live source unless the caller
    passes that very directory as to_dir."""
    job = cfg.job(job) if isinstance(job, str) else job
    if version is not None and not STAMP_RE.match(version):
        raise ConfigError(f"version must look like 2026-01-31_130000, got {version!r}")
    src = job.dest if version is None else f"{job.old.rstrip('/')}/{version}/{job.name}"
    args = ["copy", src, os.path.expanduser(str(to_dir))] + COMMON + (["--dry-run"] if dry_run else [])
    tmp = None
    if files:
        tmp = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False)
        tmp.write("\n".join(files) + "\n")
        tmp.close()
        args += ["--files-from", tmp.name]
    state_dir().mkdir(parents=True, exist_ok=True)
    try:
        with open(state_dir() / "restore.log", "w") as log:
            return stream(rclone_argv(cfg, args), rclone_env(cfg), log, emit)
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
             "rc": results.get(j.name, {}).get("rc"), "seconds": results.get(j.name, {}).get("seconds")}
            for j in cfg.jobs]
    found = shutil.which("rclone") is not None
    listed = rclone(cfg, ["listremotes"], timeout=20)[1].split() if found else []
    d = {"config_path": str(cfg.path), "state_dir": str(sd), "daily_at": cfg.daily_at, "keep_days": cfg.keep_days,
         "rclone": {"found": found}, "missing_remotes": [r for r in remote_names(cfg) if found and r not in listed],
         "last": last, "last_age_days": age_days((last or {}).get("time")), "jobs": jobs,
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
    snap = st["settings"]["last"]
    if st["settings"]["enabled"] and snap and not snap.get("ok"):
        out.append({"page": "settings", "title": "Settings snapshot failed",
                    "body": "The last settings snapshot did not complete.", "urgent": True})
    return out
