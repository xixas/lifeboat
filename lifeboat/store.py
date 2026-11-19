"""The config store: a git-tracked skillshare source folder. Detect it, adopt or create it. No GTK.
Lifeboat never runs `skillshare sync`: only init, status, push and ui."""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from lifeboat import store_install


def _git_root(path: Path) -> Path | None:
    try:
        r = subprocess.run(["git", "-C", str(path), "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return Path(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else None


def skill_count(repo: Path) -> int:
    return sum(1 for p in repo.rglob("SKILL.md") if ".git" not in p.relative_to(repo).parts)


def candidates(scan_items: list[dict]) -> list[dict]:
    """Git checkouts behind the 'Linked:' catalogue items, each with the number of skills it holds."""
    roots: dict[Path, None] = {}
    for item in scan_items:
        path = Path(item["path"])
        if item["label"].startswith("Linked:") and path.is_dir() and (root := _git_root(path)):
            roots[root] = None
    return [{"path": str(r), "name": r.name, "skills": skill_count(r)} for r in roots]


def config_file() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "skillshare" / "config.yaml"


def current() -> str | None:
    """Source path from skillshare's config.yaml (`sources: skills:`, or the older top-level `source:`), if present."""
    try:
        text = config_file().read_text()
    except OSError:
        return None
    m = re.search(r"^sources:\s*\n(?:[ \t]+.*\n|\s*\n)*?[ \t]+skills:[ \t]*(\S.*)$", text, re.M) or re.search(r"^source:[ \t]*(\S.*)$", text, re.M)
    if not m:
        return None
    value = re.sub(r"\s+#.*$", "", m.group(1)).strip().strip("'\"")
    return os.path.expanduser(value) or None


def _init(args: list[str], binary: Path | str | None, home: Path | str | None) -> dict:
    binary = binary or store_install.skillshare_path()
    if not binary:
        return {"ok": False, "error": "skillshare is not installed"}
    r = store_install.run_skillshare(binary, ["init", *args], home=home)
    return {"ok": r.returncode == 0, "error": "" if r.returncode == 0 else (r.stderr or r.stdout).strip()[-500:]}


def init_adopt(repo: Path | str, binary: Path | str | None = None, home: Path | str | None = None) -> dict:
    """Point skillshare at an existing checkout without copying, git-initialising or adding its built-in skill to it."""
    return _init(["--source", str(repo), "--no-copy", "--no-git", "--no-skill", "--no-tui"], binary, home)


def init_new(binary: Path | str | None = None, home: Path | str | None = None) -> dict:
    return _init(["--no-copy", "--git", "--no-skill", "--all-targets", "--no-tui"], binary, home)


def _git(path: Path | str, *args: str) -> str | None:
    try:
        r = subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True, timeout=20, check=False,
                           env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout if r.returncode == 0 else None


def git_state(path: Path | str) -> dict:
    """{is_repo, branch, remote_url, dirty_count, ahead, behind, last_commit_time}, read straight from git."""
    state = {"is_repo": False, "branch": None, "remote_url": None, "dirty_count": 0, "ahead": 0, "behind": 0, "last_commit_time": None}
    if not Path(path).is_dir() or (out := _git(path, "status", "--porcelain=v2", "--branch")) is None:
        return state
    state["is_repo"] = True
    for line in out.splitlines():
        if line.startswith("# branch.head "):
            head = line.split(" ", 2)[2]
            state["branch"] = None if head == "(detached)" else head
        elif line.startswith("# branch.ab "):
            plus, minus = line.split()[2:4]
            state["ahead"], state["behind"] = int(plus[1:]), int(minus[1:])
        elif not line.startswith("#"):
            state["dirty_count"] += 1
    if (url := _git(path, "remote", "get-url", "origin")) and url.strip():
        state["remote_url"] = url.strip()
    if (when := _git(path, "log", "-1", "--format=%cI")) and when.strip():
        state["last_commit_time"] = when.strip()
    return state
