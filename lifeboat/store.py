"""The config store: a git-tracked skillshare source folder. Detect it, adopt or create it. No GTK.
Lifeboat never runs `skillshare sync`: only init, status, push and ui."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from lifeboat import engine, github, secretscan, store_install
from lifeboat.config import Config


def _git_root(path: Path) -> Path | None:
    try:
        r = subprocess.run(["git", "-C", str(path), "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return Path(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else None


def not_a_repo_root(path: Path | str) -> str | None:
    """Why `path` cannot be a store: a store is a whole git repo, because `git add -A` and a push cover the whole repo."""
    top = _git_root(Path(path))
    if top and os.path.realpath(top) != os.path.realpath(path):
        return f"{path} is inside the git repo {top}; pick the repo root"
    return None


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


AUTH_FAILED = re.compile(r"\b40[13]\b|Authentication failed|Invalid username or password", re.I)


def _confirmed_repo(repo: Path, gh) -> tuple[str | None, str]:
    """("owner/name", "") for the GitHub repo the store's `origin` names, once the app confirms it can reach it; else (None, why).
    The remote is read raw from the repo's config: it only says which repo, never where Lifeboat pushes to."""
    url = (_git(repo, "config", "--get", "remote.origin.url") or "").strip()
    if not github_repo(url):
        return None, "the store's origin is not a github.com repository" if url else "the store has no origin on GitHub"
    try:
        found = find_repo(gh, url)
    except github.GitHubError as e:
        return None, str(e)
    return (found["full_name"], "") if found else (None, f"the Lifeboat GitHub App is not installed on {github_repo(url)}")


def _push(repo: Path, gh) -> dict:
    """Push with Lifeboat's own GitHub token, and only with it: never `skillshare push`, never another login on this machine, never
    `origin` itself: github.push goes to the https URL of the repo the app confirmed."""
    token = gh.token()
    if not token:
        return {"ok": False, "error": "", "signed_out": "Signed out of GitHub"}
    name, why = _confirmed_repo(repo, gh)
    if not name:
        return {"ok": False, "error": why}
    if git_state(repo)["dirty_count"]:
        for args in (["add", "-A"], ["-c", "user.name=Lifeboat", "-c", "user.email=lifeboat@localhost", "commit", "-qm", "Update skills"]):
            if _git(repo, *args) is None:
                return {"ok": False, "error": "could not commit the changes"}
    branch, sha = git_state(repo)["branch"], (_git(repo, "rev-parse", "HEAD") or "").strip()
    out = github.push(repo, token, name)
    if out["ok"] and branch and sha:  # a push to a URL moves no tracking ref: do it, so ahead/behind and the next scan are right
        for args in (["update-ref", f"{secretscan.PUSHED}/{branch}", sha],  # the one ref the secret scan trusts as already checked
                     ["update-ref", f"refs/remotes/origin/{branch}", sha], ["branch", "--set-upstream-to", f"origin/{branch}", branch]):
            _git(repo, *args)
    if not out["ok"] and AUTH_FAILED.search(out["error"]):
        gh.sign_out()  # the token no longer works: say so instead of failing the same way every day
        return {**out, "signed_out": "GitHub access was revoked"}
    return out


def push_in_subprocess(timeout: int = 300) -> dict:
    """What the app's Push now runs: `lifeboat store-push` in its own process, whose main thread cleans the token file up on SIGTERM or
    SIGHUP (an app thread cannot install signal handlers). Returns the run's store.json record, or a failed one."""
    failed = {"ok": False, "pushed": False, "blocked_findings": [], "error": "the push did not complete", "signed_out": ""}
    try:
        r = subprocess.run([sys.executable, "-m", "lifeboat", "store-push", "--json"], capture_output=True, text=True, timeout=timeout, check=False)
        doc = json.loads(r.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return failed
    return doc if isinstance(doc, dict) else failed


def clear_signed_out() -> None:
    """After a new sign-in, forget the last run's "signed out" mark so the page and alerts stop showing it."""
    last = engine.read_json(engine.state_dir() / "store.json")
    if isinstance(last, dict) and last.get("signed_out"):
        engine.write_json("store.json", {**last, "signed_out": ""})


def run_daily(cfg: Config, gh=None) -> dict | None:
    """Secret scan, then push the store. Records {time, ok, pushed, blocked_findings, error} in store.json. None if not set up."""
    if not (cfg.store_source and cfg.store_push):
        return None
    repo = Path(os.path.expanduser(cfg.store_source))
    result = {"time": engine.now_iso(), "ok": False, "pushed": False, "blocked_findings": [], "error": "", "signed_out": ""}
    before = git_state(repo)
    if not before["is_repo"]:
        result["error"] = f"{repo} is not a git repository"
        return _record(result)
    if (msg := not_a_repo_root(repo)):
        result["error"] = msg
        return _record(result)
    try:
        secretscan.install_pre_push_hook(repo)
    except (OSError, RuntimeError) as e:
        result["error"] = f"could not install the pre-push check: {e}"
        return _record(result)
    try:  # strict: without a working gitleaks nothing is pushed (the pre-push hook alone may skip it; this push must not)
        found = secretscan.scan_working_tree(repo, strict=True)
        if before["last_commit_time"]:
            found += secretscan.scan_range(repo, None, "HEAD", strict=True)
    except secretscan.ScannerUnavailable as e:
        result["error"] = f"Secret scanner unavailable ({e}); nothing was pushed"
        return _record(result)
    if found:
        result["blocked_findings"] = [{k: f[k] for k in ("file", "line", "rule", "commit") if k in f} for f in found]
        out = _git(repo, "rev-parse", "--abbrev-ref", "@{u}")
        result["repo"], result["upstream"] = str(repo), (out or "").strip() or None
        return _record(result)
    never_pushed = before["remote_url"] and before["last_commit_time"] and not _upstream(repo)
    if not before["dirty_count"] and not before["ahead"] and not never_pushed:
        result["ok"] = True
        return _record(result)
    out = _push(repo, gh or github.GitHub())
    result.update(ok=out["ok"], pushed=out["ok"], error=out["error"], signed_out=out.get("signed_out", ""))
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
    if (target := existing or repo) and (msg := not_a_repo_root(target)):
        return {"ok": False, "error": msg, "source": str(target)}
    out = {"ok": True, "error": "", "source": existing} if existing else init_adopt(repo, binary, home) if repo else init_new(binary, home)
    out["source"] = out["source"] if existing else str(repo) if repo else current()
    if not out["ok"]:
        return out
    if not store_install.disable_update_check(binary, home):
        return {**out, "ok": False, "error": "could not turn off skillshare's update check"}
    if out["source"] and not git_state(out["source"])["is_repo"]:
        return {**out, "ok": False, "error": f"{out['source']} is not a git repository"}
    if out["source"] and (msg := not_a_repo_root(out["source"])):  # a new store: only skillshare knows the folder it made
        return {**out, "ok": False, "error": msg}
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
