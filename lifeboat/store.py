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


def _push(repo: Path, gh, binary, first_branch: str | None = None) -> dict:
    """Push with Lifeboat's own GitHub token when it has one, else with `skillshare push` (never `sync`)."""
    if token := gh.token():
        if (git_state(repo)["dirty_count"]):
            for args in (["add", "-A"], ["-c", "user.name=Lifeboat", "-c", "user.email=lifeboat@localhost", "commit", "-qm", "Update skills"]):
                if _git(repo, *args) is None:
                    return {"ok": False, "error": "could not commit the changes"}
        return github.push(repo, token, set_upstream=first_branch)
    if first_branch:  # skillshare push has no upstream to push to yet
        try:
            r = subprocess.run(["git", "-C", str(repo), "push", "-u", "origin", first_branch], capture_output=True, text=True, timeout=180,
                               check=False, env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))
        except (OSError, subprocess.TimeoutExpired) as e:
            return {"ok": False, "error": str(e)}
        return {"ok": r.returncode == 0, "error": "" if r.returncode == 0 else (r.stderr or r.stdout).strip()[-500:]}
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
        result["blocked_findings"] = [{k: f[k] for k in ("file", "line", "rule", "commit") if k in f} for f in found]
        out = _git(repo, "rev-parse", "--abbrev-ref", "@{u}")
        result["repo"], result["upstream"] = str(repo), (out or "").strip() or None
        return _record(result)
    first = before["branch"] if before["remote_url"] and before["last_commit_time"] and not _upstream(repo) else None  # never pushed yet
    if not before["dirty_count"] and not before["ahead"] and not first:
        result["ok"] = True
        return _record(result)
    out = _push(repo, gh or github.GitHub(), binary, first)
    result.update(ok=out["ok"], pushed=out["ok"], error=out["error"])
    return _record(result)


# ---------------------------------------------------------------- setup steps the app drives
def github_repo(url: str | None) -> str | None:
    """'owner/name' for a github.com remote (https or ssh, with or without .git or a login), else None."""
    m = re.match(r"^(?:https?://(?:[^@/]+@)?|ssh://git@|git@)github\.com[:/]([\w.-]+)/([\w.-]+?)(?:\.git)?/?$", (url or "").strip())
    return f"{m.group(1)}/{m.group(2)}" if m else None


def prepare(repo: Path | str | None, binary: Path | str | None = None, home: Path | str | None = None) -> dict:
    """Adopt `repo` (or create a new store when None), turn skillshare's online update check off and install the pre-push
    check. {ok, error, source}; stops at the first step that fails."""
    existing = current()  # a second `skillshare init` exits 1, so an interrupted setup carries on from here
    if existing and repo and os.path.realpath(existing) != os.path.realpath(str(repo)):
        return {"ok": False, "error": f"skillshare already uses {existing}", "source": existing}
    out = {"ok": True, "error": "", "source": existing} if existing else init_adopt(repo, binary, home) if repo else init_new(binary, home)
    out["source"] = out["source"] if existing else str(repo) if repo else current()
    if not out["ok"]:
        return out
    if not store_install.disable_update_check(binary, home):
        return {**out, "ok": False, "error": "could not turn off skillshare's update check"}
    try:
        secretscan.install_pre_push_hook(out["source"])
    except (OSError, RuntimeError, TypeError) as e:
        return {**out, "ok": False, "error": f"could not install the pre-push check: {e}"}
    return out


def connect_remote(repo: Path | str, clone_url: str) -> None:
    """Give a store with no remote its GitHub origin and make the current branch track it, so a plain `git push` works.
    A remote that is already there is never changed."""
    if _git(repo, "remote", "get-url", "origin"):
        return
    branch = (git_state(repo)["branch"]) or "main"
    for args in (["remote", "add", "origin", clone_url], ["config", f"branch.{branch}.remote", "origin"],
                 ["config", f"branch.{branch}.merge", f"refs/heads/{branch}"]):
        if _git(repo, *args) is None:
            raise RuntimeError(f"git {args[0]} failed")


def find_repo(gh, url: str) -> dict | None:
    """The repo the app can reach that `url` names, or None (the app is not installed on it, or the URL is not GitHub's)."""
    want = github_repo(url)
    return next((r for r in gh.installation_repos() if want and r["full_name"].lower() == want.lower()), None) if want else None


def start_ui(binary: Path | str | None = None) -> bool:
    """`skillshare ui start`, left running in the background (it serves http://127.0.0.1:19420)."""
    binary = binary or store_install.skillshare_path() or shutil.which("skillshare")
    if not binary:
        return False
    try:
        subprocess.Popen([str(binary), "ui", "start"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    except OSError:
        return False
    return True
