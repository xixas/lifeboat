"""Optional module: which Claude Code skills are installed from source vs written by you."""
from __future__ import annotations

import os
from pathlib import Path

from lifeboat.config import Config
from lifeboat.engine import _covered, age_days, coverage_sources, read_json

RECENT_DAYS = 2  # a verdict of "backed up" needs a good run at least this recent


def skills() -> dict:
    home = Path.home()
    lock = (read_json(home / ".agents" / ".skill-lock.json") or {}).get("skills", {})
    base = home / ".claude" / "skills"
    ext: dict[str, list[str]] = {}
    own: list[str] = []
    for n, v in lock.items():
        ext.setdefault(v.get("source", "unknown"), []).append(n)
    for p in sorted(base.iterdir()) if base.is_dir() else []:
        if p.name.startswith(".") or not p.is_dir() or p.name in lock:
            continue
        if (p / ".git").exists():
            ext.setdefault("local git checkout", []).append(p.name)
        elif isinstance(marker := read_json(p / "agent-native-skill.json"), dict):
            ext.setdefault(str(marker.get("source") or "agent-native"), []).append(p.name)
        else:
            own.append(p.name)
    for v in ext.values():
        v.sort()
    return {"external": dict(sorted(ext.items())), "external_count": sum(map(len, ext.values())), "own": own}


def coverage(cfg: Config, last: dict | None, own: list[str], items: list[dict] = ()) -> dict:
    """Whether your own skills are backed up. Each skill's real path (symlinks resolved, since a skill manager links skills in
    from a checkout elsewhere) must lie under a job's source, and the last run must have succeeded within RECENT_DAYS.
    missing: skills no job covers; fix: the uncovered catalogue items around them (the most specific one per skill);
    agents: whether ~/.agents, where the external skills live, is covered."""
    home = Path.home()
    sources = [os.path.realpath(os.path.expanduser(s)) for s in coverage_sources(cfg)]
    real = {n: os.path.realpath(home / ".claude" / "skills" / n) for n in own}
    missing = [n for n, p in real.items() if not _covered(p, sources)]
    fix: dict[str, dict] = {}
    for n in missing:
        around = [i for i in items if _covered(real[n], [os.path.realpath(i["path"])]) and not _covered(os.path.realpath(i["path"]), sources)]
        if around:
            best = max(around, key=lambda i: len(os.path.realpath(i["path"])))
            fix[best["key"]] = best
    age = age_days((last or {}).get("time"))
    recent = bool(last and last.get("ok") and not last.get("dry_run") and age is not None and age <= RECENT_DAYS)
    return {"missing": missing, "recent": recent, "backed_up": bool(own) and not missing and recent, "fix": list(fix.values()),
            "agents": _covered(os.path.realpath(home / ".agents"), sources)}
