"""Load and validate ~/.config/lifeboat/config.toml."""
from __future__ import annotations

import os
import re
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
    known = {"rclone", "schedule", "jobs", "settings", "modules"}
    for k in data:
        if k not in known:
            raise ConfigError(f"unknown section {k!r}")
    rc, sch, st, mod = (_table(data, k) for k in ("rclone", "schedule", "settings", "modules"))
    cfg = Config(
        jobs=[],
        rclone_config=_get(rc, "rclone", "config", str, ""),
        password_command=_get(rc, "rclone", "password_command", str, ""),
        daily_at=_get(sch, "schedule", "daily_at", str, "13:00"),
        keep_days=_get(sch, "schedule", "keep_days", int, 90),
        settings_command=_get(st, "settings", "command", str, ""),
        claude_skills=_get(mod, "modules", "claude_skills", str, "auto"),
    )
    if path is not None:
        cfg.path = path
    if not TIME_RE.match(cfg.daily_at):
        raise ConfigError(f"schedule.daily_at must look like 13:00, got {cfg.daily_at!r}")
    if cfg.keep_days < 1:
        raise ConfigError("schedule.keep_days must be at least 1")
    if cfg.claude_skills not in MODULE_MODES:
        raise ConfigError(f"modules.claude_skills must be one of {', '.join(MODULE_MODES)}, got {cfg.claude_skills!r}")
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
