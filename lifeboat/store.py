"""The config store: a git-tracked skillshare source folder. Detect it, adopt or create it. No GTK.
Lifeboat never runs `skillshare sync`: only init, status, push and ui."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from lifeboat import engine, github, secretscan, store_install
from lifeboat.config import Config


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


# ---------------------------------------------------------------- daily push, after the backup jobs
def _upstream(repo: Path | str) -> str | None:
    out = _git(repo, "rev-parse", "--verify", "-q", "@{u}")
    return out.strip() if out and out.strip() else None


def _record(result: dict) -> dict:
    prev = engine.read_json(engine.state_dir() / "store.json") or {}
    result["last_ok_time"] = result["time"] if result["ok"] else prev.get("last_ok_time")
    engine.write_json("store.json", result)
    return result


def _push(repo: Path, gh, binary) -> dict:
    """Push with Lifeboat's own GitHub token when it has one, else with `skillshare push` (never `sync`)."""
    if token := gh.token():
        if (git_state(repo)["dirty_count"]):
            for args in (["add", "-A"], ["-c", "user.name=Lifeboat", "-c", "user.email=lifeboat@localhost", "commit", "-qm", "Update skills"]):
                if _git(repo, *args) is None:
                    return {"ok": False, "error": "could not commit the changes"}
        return github.push(repo, token)
    binary = binary or store_install.skillshare_path() or shutil.which("skillshare")
    if not binary:
        return {"ok": False, "error": "skillshare is not installed and Lifeboat is not signed in to GitHub"}
    r = store_install.run_skillshare(binary, ["push"], cwd=repo, timeout=180)
    return {"ok": r.returncode == 0, "error": "" if r.returncode == 0 else (r.stderr or r.stdout).strip()[-500:]}


def run_daily(cfg: Config, gh=None, binary: Path | str | None = None) -> dict | None:
    """Secret scan, then push the store. Records {time, ok, pushed, blocked_findings, error} in store.json. None if not set up."""
    if not (cfg.store_source and cfg.store_push):
        return None
    repo = Path(os.path.expanduser(cfg.store_source))
    result = {"time": engine.now_iso(), "ok": False, "pushed": False, "blocked_findings": [], "error": ""}
    before = git_state(repo)
    if not before["is_repo"]:
        result["error"] = f"{repo} is not a git repository"
        return _record(result)
    try:
        secretscan.install_pre_push_hook(repo)
    except (OSError, RuntimeError) as e:
        result["error"] = f"could not install the pre-push check: {e}"
        return _record(result)
    found = secretscan.scan_working_tree(repo)
    if before["last_commit_time"]:
        found += secretscan.scan_range(repo, _upstream(repo), "HEAD")
    if found:
        result["blocked_findings"] = [{k: f[k] for k in ("file", "line", "rule")} for f in found]
        return _record(result)
    if not before["dirty_count"] and not before["ahead"]:
        result["ok"] = True
        return _record(result)
    out = _push(repo, gh or github.GitHub(), binary)
    result.update(ok=out["ok"], pushed=out["ok"], error=out["error"])
    return _record(result)
