"""Optional module: which Claude Code skills are installed from source vs written by you."""
from __future__ import annotations

from pathlib import Path

from lifeboat.engine import read_json


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
