"""Load and validate ~/.config/lifeboat/config.toml."""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
MODULE_MODES = ("auto", "on", "off")


class ConfigError(ValueError):
    """Raised for a missing or invalid config; the message names the bad key."""


@dataclass
class Job:
    name: str
    source: str
    dest: str
    old: str
    exclude: list[str] = field(default_factory=list)
    include: list[str] = field(default_factory=list)
    filter_file: str = ""
    encrypted: bool = False


@dataclass
class Config:
    jobs: list[Job]
    rclone_config: str = ""
    password_command: str = ""
    daily_at: str = "13:00"
    keep_days: int = 90
    settings_command: str = ""
    claude_skills: str = "auto"
    device_name: str = ""
    manifest: str = ""
    probe: str = ""
    path: Path = field(default_factory=lambda: config_path())

    def job(self, name: str) -> Job:
        for j in self.jobs:
            if j.name == name:
                return j
        raise ConfigError(f"jobs: no job named {name!r} (have: {', '.join(j.name for j in self.jobs) or 'none'})")

    @property
    def config_dir(self) -> Path:
        return self.path.parent

    def filter_path(self, job: Job) -> str:
        """Absolute path of a job's filter file ('' if none); relative paths resolve against the config dir."""
        if not job.filter_file:
            return ""
        p = Path(os.path.expanduser(job.filter_file))
        return str(p if p.is_absolute() else self.config_dir / p)

    @property
    def skills_enabled(self) -> bool:
        if self.claude_skills == "auto":
            return (Path.home() / ".claude" / "skills").is_dir()
        return self.claude_skills == "on"


def config_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "lifeboat"


def config_path() -> Path:
    return config_dir() / "config.toml"


def _table(data: dict, key: str) -> dict:
    v = data.get(key, {})
    if not isinstance(v, dict):
        raise ConfigError(f"[{key}] must be a table")
    return v


def _get(tbl: dict, where: str, key: str, typ, default):
    v = tbl.get(key, default)
    if typ is int and isinstance(v, bool):
        raise ConfigError(f"{where}.{key} must be a whole number")
    if not isinstance(v, typ):
        raise ConfigError(f"{where}.{key} must be {'a list of strings' if typ is list else typ.__name__}")
    if typ is list and not all(isinstance(x, str) for x in v):
        raise ConfigError(f"{where}.{key} must be a list of strings")
    return v


def parse(data: dict, path: Path | None = None) -> Config:
    known = {"rclone", "schedule", "jobs", "settings", "modules", "device"}
    for k in data:
        if k not in known:
            raise ConfigError(f"unknown section {k!r}")
    rc, sch, st, mod, dev = (_table(data, k) for k in ("rclone", "schedule", "settings", "modules", "device"))
    cfg = Config(
        jobs=[],
        rclone_config=_get(rc, "rclone", "config", str, ""),
        password_command=_get(rc, "rclone", "password_command", str, ""),
        daily_at=_get(sch, "schedule", "daily_at", str, "13:00"),
        keep_days=_get(sch, "schedule", "keep_days", int, 90),
        settings_command=_get(st, "settings", "command", str, ""),
        claude_skills=_get(mod, "modules", "claude_skills", str, "auto"),
        device_name=_get(dev, "device", "name", str, ""),
        manifest=_get(dev, "device", "manifest", str, ""),
        probe=_get(dev, "device", "probe", str, ""),
    )
    if path is not None:
        cfg.path = path
    if not TIME_RE.match(cfg.daily_at):
        raise ConfigError(f"schedule.daily_at must look like 13:00, got {cfg.daily_at!r}")
    if cfg.keep_days < 1:
        raise ConfigError("schedule.keep_days must be at least 1")
    if cfg.claude_skills not in MODULE_MODES:
        raise ConfigError(f"modules.claude_skills must be one of {', '.join(MODULE_MODES)}, got {cfg.claude_skills!r}")
    if "device" in data:
        for k in dev:
            if k not in ("name", "manifest", "probe"):
                raise ConfigError(f"device.{k} is not a known key")
        if not NAME_RE.match(cfg.device_name):
            raise ConfigError(f"device.name may only use letters, digits, '.', '_' and '-', got {cfg.device_name!r}")
        if not cfg.manifest:
            raise ConfigError("device.manifest is required when [device] is present")
    jobs = data.get("jobs", [])
    if not isinstance(jobs, list) or not jobs:
        raise ConfigError("jobs: define at least one [[jobs]] entry")
    seen = set()
    for i, raw in enumerate(jobs, 1):
        if not isinstance(raw, dict):
            raise ConfigError(f"jobs[{i}] must be a table")
        where = f"jobs[{i}]"
        for k in ("name", "source", "dest", "old"):
            if not raw.get(k):
                raise ConfigError(f"{where}.{k} is required")
        for k in raw:
            if k not in {f for f in Job.__dataclass_fields__}:
                raise ConfigError(f"{where}.{k} is not a known key")
        job = Job(
            name=_get(raw, where, "name", str, ""),
            source=_get(raw, where, "source", str, ""),
            dest=_get(raw, where, "dest", str, ""),
            old=_get(raw, where, "old", str, ""),
            exclude=_get(raw, where, "exclude", list, []),
            include=_get(raw, where, "include", list, []),
            filter_file=_get(raw, where, "filter_file", str, ""),
            encrypted=_get(raw, where, "encrypted", bool, False),
        )
        if job.include and job.exclude:
            raise ConfigError(f"{where} ({job.name!r}): include and exclude cannot be combined (rclone does not support mixing them)")
        if not NAME_RE.match(job.name):
            raise ConfigError(f"{where}.name may only use letters, digits, '.', '_' and '-', got {job.name!r}")
        if job.name in seen:
            raise ConfigError(f"{where}.name {job.name!r} is used twice")
        seen.add(job.name)
        if job.dest.rstrip("/") == job.old.rstrip("/"):
            raise ConfigError(f"{where}.old must differ from {where}.dest")
        cfg.jobs.append(job)
    return cfg


def load_config(path: Path | str | None = None) -> Config:
    p = Path(path) if path else config_path()
    try:
        with open(p, "rb") as f:
            data = tomllib.load(f)
    except FileNotFoundError:
        raise ConfigError(f"no config file at {p}") from None
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{p} is not valid TOML: {e}") from None
    return parse(data, p)


# ---------------------------------------------------------------- editing config.toml as text
SYSTEM_EXAMPLE_CONFIG = Path("/usr/share/lifeboat/config.example.toml")  # where the RPM puts it


def example_config_path() -> Path:
    """The example config: next to the package (repo and install.sh layout), else the system-wide copy (RPM)."""
    local = Path(__file__).resolve().parent.parent / "data" / "config.example.toml"
    return local if local.is_file() else SYSTEM_EXAMPLE_CONFIG


def default_old(dest: str) -> str:
    """The versions folder next to dest: gdrive:Backup/Documents -> gdrive:Backup/_old, gdrive:Docs -> gdrive:_old."""
    head, sep, tail = dest.rstrip("/").partition(":")
    if not sep:  # a local path
        return f"{head.rpartition('/')[0]}/_old" if "/" in head else "_old"
    parent = tail.rpartition("/")[0]
    return f"{head}{sep}{parent}{'/' if parent else ''}_old"


def render_job_block(job: Job) -> str:
    """A [[jobs]] block; strings use JSON escaping, which is valid for TOML basic strings. Only set keys are written."""
    lines = ["[[jobs]]"] + [f"{k} = {json.dumps(getattr(job, k), ensure_ascii=False)}" for k in ("name", "source", "dest", "old")]
    for k in ("exclude", "include"):
        if getattr(job, k):
            lines.append(f"{k} = {json.dumps(getattr(job, k), ensure_ascii=False)}")
    if job.filter_file:
        lines.append(f"filter_file = {json.dumps(job.filter_file, ensure_ascii=False)}")
    if job.encrypted:
        lines.append("encrypted = true")
    return "\n".join(lines) + "\n"


def validate_text(text: str, path: Path | None = None) -> Config:
    try:
        return parse(tomllib.loads(text), path)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"the edited config would not be valid TOML: {e}") from None


def add_job_text(text: str, job: Job) -> str:
    """text with job appended as a new block. Raises ConfigError if the result would not be a valid config."""
    new = text.rstrip("\n") + "\n\n" + render_job_block(job)
    validate_text(new)
    return new


def _segments(text: str) -> list[str]:
    """Split at table headers (lines starting with '['); comments such as '# [[jobs]]' never split."""
    segs: list[list[str]] = [[]]
    for line in text.splitlines(keepends=True):
        if re.match(r"\s*\[", line):
            lead = []  # comment lines directly above a header belong to it
            while segs[-1] and segs[-1][-1].lstrip().startswith("#"):
                lead.insert(0, segs[-1].pop())
            segs.append(lead)
        segs[-1].append(line)
    return ["".join(s) for s in segs]


def _job_name(segment: str) -> str | None:
    segment = "".join(ln for ln in segment.splitlines(keepends=True) if not ln.lstrip().startswith("#") or ln.strip() == "")
    if not segment.lstrip().startswith("[[jobs]]"):
        return None
    try:
        return tomllib.loads(segment)["jobs"][0].get("name")
    except (tomllib.TOMLDecodeError, KeyError, IndexError):
        return None


def remove_job_text(text: str, name: str) -> str:
    """text without the [[jobs]] block (and the comments directly above it) named `name`. Raises ConfigError for an unknown name or the last job."""
    segs = _segments(text)
    hits = [i for i, s in enumerate(segs) if _job_name(s) == name]
    if not hits:
        raise ConfigError(f"jobs: no job named {name!r} in the config file")
    if sum(_job_name(s) is not None for s in segs) < 2:
        raise ConfigError(f"jobs: {name!r} is the only job; the config needs at least one")
    new = "".join(s for i, s in enumerate(segs) if i != hits[0])
    validate_text(new)
    return new


def write_config_text(path: Path, text: str) -> None:
    """Replace the config atomically (same mode), keeping the previous file as <name>.bak."""
    validate_text(text, path)
    path = path.resolve()  # write through a symlink (stow, chezmoi) instead of replacing it
    mode = path.stat().st_mode & 0o7777
    shutil.copy2(path, path.with_name(path.name + ".bak"))
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
        dfd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except BaseException:
        if os.path.exists(tmp):  # already gone if the replace succeeded and only the dir fsync failed
            os.unlink(tmp)
        raise
