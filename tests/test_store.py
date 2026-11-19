import re
import subprocess
from pathlib import Path

import pytest

from lifeboat import store

PKG = Path(store.__file__).parent


def _repo(path: Path, skills=("a", "b")):
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    for s in skills:
        (path / "skills" / s).mkdir(parents=True)
        (path / "skills" / s / "SKILL.md").write_text("---\nname: x\n---\n")
    (path / ".git" / "SKILL.md").write_text("ignored")
    return path


def _item(label, path):
    return {"label": label, "path": str(path), "group": "ai"}


def test_candidates_lists_git_roots_of_linked_items_with_skill_counts(tmp_path):
    repo = _repo(tmp_path / "claude-config", ("a", "b", "c"))
    plain = tmp_path / "plain"
    (plain / "x").mkdir(parents=True)
    items = [_item("Linked: claude-config", repo), _item("Linked: plain", plain), _item("Claude Code", repo), _item("Linked: gone", tmp_path / "gone")]
    assert store.candidates(items) == [{"path": str(repo), "name": "claude-config", "skills": 3}]


def test_candidates_dedupes_roots(tmp_path):
    repo = _repo(tmp_path / "r")
    sub = repo / "skills"
    assert len(store.candidates([_item("Linked: r", repo), _item("Linked: skills", sub)])) == 1


def test_current_reads_source(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))
    assert store.current() is None
    cfg = tmp_path / ".config" / "skillshare" / "config.yaml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("# c\n\nsources:\n  skills: /x/y # note\n  agents: /x/agents\n\nmode: merge\n")
    assert store.current() == "/x/y"
    cfg.write_text("source: '~/s'\ntargets: {}\n")
    assert store.current() == str(tmp_path / "s")


@pytest.fixture
def fake_ss(tmp_path):
    log = tmp_path / "argv.log"
    p = tmp_path / "skillshare"
    p.write_text(f'#!/bin/sh\necho "$HOME|$*" >> {log}\nexit ${{FAKE_RC:-0}}\n')
    p.chmod(0o755)
    return p, log


def test_init_adopt_argv_and_home(tmp_path, fake_ss):
    p, log = fake_ss
    r = store.init_adopt(tmp_path / "repo", binary=p, home=tmp_path / "h")
    assert r["ok"] is True
    assert log.read_text().strip() == f"{tmp_path / 'h'}|init --source {tmp_path / 'repo'} --no-copy --no-git --no-skill --no-tui"


def test_init_new_argv(tmp_path, fake_ss):
    p, log = fake_ss
    assert store.init_new(binary=p)["ok"] is True
    assert log.read_text().split("|", 1)[1].strip() == "init --no-copy --git --no-skill --all-targets --no-tui"


def test_init_failure_reports_error(tmp_path, monkeypatch):
    p = tmp_path / "skillshare"
    p.write_text("#!/bin/sh\necho boom >&2\nexit 3\n")
    p.chmod(0o755)
    r = store.init_new(binary=p)
    assert r["ok"] is False and "boom" in r["error"]


def test_init_without_binary(monkeypatch):
    monkeypatch.setattr(store.store_install, "skillshare_path", lambda: None)
    assert store.init_new()["ok"] is False


def test_skillshare_sync_is_never_called():
    for f in (PKG / "store.py", PKG / "store_install.py"):  # engine.py's "sync" is rclone's
        assert not re.search(r"""["']sync["']""", f.read_text()), f"{f} passes sync to skillshare"


# ---------------------------------------------------------------- git state
def _git(cwd, *a):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(cwd), *a], check=True, capture_output=True, text=True).stdout


def test_git_state_not_a_repo(tmp_path):
    assert store.git_state(tmp_path)["is_repo"] is False
    assert store.git_state(tmp_path / "nope")["is_repo"] is False


def test_git_state_fresh_repo_without_commits(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")
    s = store.git_state(tmp_path)
    assert s["is_repo"] and s["branch"] == "main" and s["remote_url"] is None and s["last_commit_time"] is None
    assert (s["ahead"], s["behind"], s["dirty_count"]) == (0, 0, 0)


def test_git_state_tracks_remote_dirty_ahead_behind(tmp_path):
    bare = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(bare))
    a = tmp_path / "a"
    _git(tmp_path, "clone", "-q", str(bare), str(a))
    (a / "f").write_text("1")
    _git(a, "add", "f")
    _git(a, "commit", "-qm", "one")
    _git(a, "push", "-q", "-u", "origin", "HEAD:main")
    _git(a, "branch", "-u", "origin/main")
    s = store.git_state(a)
    assert (s["branch"], s["ahead"], s["behind"], s["dirty_count"]) == ("main", 0, 0, 0)
    assert s["remote_url"] == str(bare)
    assert re.match(r"\d{4}-\d\d-\d\dT", s["last_commit_time"])
    (a / "f").write_text("2")
    (a / "new").write_text("x")
    _git(a, "commit", "-qam", "two")
    (a / "g").write_text("untracked")
    s = store.git_state(a)
    assert (s["ahead"], s["behind"], s["dirty_count"]) == (1, 0, 2)
    b = tmp_path / "b"
    _git(tmp_path, "clone", "-q", str(bare), str(b))
    (b / "h").write_text("x")
    _git(b, "add", "h")
    _git(b, "commit", "-qm", "three")
    _git(b, "push", "-q")
    _git(a, "fetch", "-q")
    assert (store.git_state(a)["ahead"], store.git_state(a)["behind"]) == (1, 1)


# ---------------------------------------------------------------- daily push
AWS = "AKIA" + "IOSFODNN7EXAMPLE"


class NoToken:
    def token(self):
        return None


REPOS = [{"full_name": "me/store", "clone_url": "https://github.com/me/store.git", "private": True}]


class Token:
    def token(self):
        return "ghu_" + "z" * 36

    def installation_repos(self):
        return REPOS


@pytest.fixture(autouse=True)
def _scanner(fake_gitleaks):
    """The pinned gitleaks is a fake that finds nothing; a test that wants another outcome reconfigures or removes it."""
    fake_gitleaks()


@pytest.fixture
def source(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("PYTHONPATH", str(PKG.parent))
    bare = _hub(tmp_path, monkeypatch)
    src = tmp_path / "src"
    _git(tmp_path, "clone", "-q", str(bare), str(src))
    (src / "README.md").write_text("x\n")
    _git(src, "add", "-A")
    _git(src, "commit", "-qm", "init")
    _git(src, "push", "-q", "-u", "origin", "HEAD:main")
    _git(src, "branch", "-u", "origin/main")
    _git(src, "remote", "set-url", "origin", "https://github.com/me/store.git")  # what GitHub knows it as; the push really goes to `bare`
    return src


def _hub(tmp_path, monkeypatch):
    """A local folder standing in for github.com: https://github.com/me/store.git is pushed to <tmp>/hub/me/store.git."""
    bare = tmp_path / "hub" / "me" / "store.git"
    bare.parent.mkdir(parents=True)
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(bare))
    monkeypatch.setattr(store.github, "GIT_HOST", f"file://{tmp_path / 'hub'}")
    return bare


def _cfg(src, push=True):
    from lifeboat.config import parse
    return parse({"jobs": [{"name": "d", "source": "/x", "dest": "a:b", "old": "a:c"}], "store": {"source": str(src), "push": push}})


def test_daily_push_blocks_on_secret_and_does_not_push(source, tmp_path):
    (source / "skills").mkdir()
    (source / "skills" / "SKILL.md").write_text(f"key {AWS}\n")
    r = store.run_daily(_cfg(source), gh=NoToken())
    assert r["ok"] is False and r["pushed"] is False
    assert r["blocked_findings"][0]["file"] == "skills/SKILL.md" and AWS not in str(r)
    assert store.engine.read_json(store.engine.state_dir() / "store.json")["blocked_findings"]


def test_daily_push_fails_closed_when_the_secret_scanner_is_unavailable(source, tmp_path, monkeypatch):
    (source / "a.md").write_text("# fine\n")
    monkeypatch.setattr(store.store_install, "gitleaks_path", lambda: None)
    pushed = []
    monkeypatch.setattr(store.github, "push", lambda *a, **kw: pushed.append(a) or {"ok": True, "error": ""})
    r = store.run_daily(_cfg(source), gh=Token())
    assert r["ok"] is False and r["pushed"] is False and pushed == []
    assert r["error"].startswith("Secret scanner unavailable")
    assert store.engine.read_json(store.engine.state_dir() / "store.json")["error"].startswith("Secret scanner unavailable")
    assert store.git_state(source)["dirty_count"] == 1  # nothing was committed either


def test_a_gitleaks_finding_blocks_the_daily_push_without_the_secret(source, tmp_path, monkeypatch, fake_gitleaks):
    fake_gitleaks(dir=[{"File": "index/mcp.json", "StartLine": 3, "RuleID": "generic-api-key", "Secret": "REDACTED"}])
    (source / "mcp.json").write_text("{}\n")
    _git(source, "add", "mcp.json")
    r = store.run_daily(_cfg(source), gh=Token())
    assert r["ok"] is False and r["pushed"] is False
    assert r["blocked_findings"] == [{"file": "mcp.json", "line": 3, "rule": "generic-api-key"}]


def test_a_configured_store_never_pushes_without_a_lifeboat_token(source, tmp_path, monkeypatch):
    """No fallback to skillshare push or to system credentials: no token means signed out, nothing committed or pushed."""
    (source / "skills").mkdir()
    (source / "skills" / "SKILL.md").write_text("# fine\n")
    calls = []
    monkeypatch.setattr(store.github, "push", lambda *a, **kw: calls.append(a) or {"ok": True, "error": ""})
    monkeypatch.setattr(store.store_install, "run_skillshare", lambda *a, **kw: calls.append(a))
    r = store.run_daily(_cfg(source), gh=NoToken())
    assert r["ok"] is False and r["pushed"] is False and r["signed_out"] == "Signed out: sign in to push"
    assert calls == [] and store.git_state(source)["dirty_count"] == 1
    assert (source / ".git" / "hooks" / "pre-push").exists()


def test_daily_push_with_lifeboat_token_uses_git_push(source, tmp_path, monkeypatch):
    (source / "a.md").write_text("# fine\n")
    pushed = []
    monkeypatch.setattr(store.github, "push", lambda path, token, full_name, **kw: pushed.append((str(path), token, full_name)) or {"ok": True, "error": ""})
    r = store.run_daily(_cfg(source), gh=Token())
    assert r["ok"] and r["pushed"]
    assert pushed == [(str(source), "ghu_" + "z" * 36, "me/store")]
    assert store.git_state(source)["dirty_count"] == 0  # committed first


def test_daily_push_nothing_to_push(source, tmp_path):
    r = store.run_daily(_cfg(source), gh=NoToken())
    assert r["ok"] is True and r["pushed"] is False


def test_daily_push_failure_recorded(source, tmp_path, monkeypatch):
    (source / "a.md").write_text("ok\n")
    monkeypatch.setattr(store.github, "push", lambda *a, **kw: {"ok": False, "error": "no remote"})
    r = store.run_daily(_cfg(source), gh=Token())
    assert r["ok"] is False and "no remote" in r["error"]


def test_daily_push_off_or_unconfigured(source, tmp_path):
    assert store.run_daily(_cfg(source, push=False), gh=NoToken()) is None
    assert store.run_daily(_cfg(""), gh=NoToken()) is None


def test_last_ok_time_carries_forward(source, tmp_path):
    cfg = _cfg(source)
    ok = store.run_daily(cfg, gh=NoToken())
    (source / "s.md").write_text(f"{AWS}\n")
    bad = store.run_daily(cfg, gh=NoToken())
    assert bad["ok"] is False and bad["last_ok_time"] == ok["time"]


def test_status_and_problems_report_store_push(source, tmp_path, make_cfg, monkeypatch):
    from datetime import datetime, timedelta
    cfg = make_cfg(store={"source": str(source), "push": True})
    assert cfg.store_push and engine_status(cfg)["store_push"] is None
    engine = store.engine
    ago = lambda d: (datetime.now().astimezone() - timedelta(days=d)).isoformat(timespec="seconds")  # noqa: E731
    engine.write_json("store.json", {"time": ago(0), "ok": False, "pushed": False, "blocked_findings": [{"file": "skills/x/SKILL.md", "line": 2, "rule": "aws-access-key"}], "error": "", "last_ok_time": ago(1)})
    st = engine_status(cfg)
    assert any(p["title"] == "Config store push blocked: secret found in skills/x/SKILL.md" and p["urgent"] for p in engine.problems(cfg, st))
    engine.write_json("store.json", {"time": ago(0), "ok": False, "pushed": False, "blocked_findings": [], "error": "boom", "last_ok_time": ago(3)})
    assert any(p["title"] == "Config store not pushed for 3 days" for p in engine.problems(cfg, engine_status(cfg)))
    engine.write_json("store.json", {"time": ago(0), "ok": True, "pushed": True, "blocked_findings": [], "error": "", "last_ok_time": ago(0)})
    assert not any("Config store" in p["title"] for p in engine.problems(cfg, engine_status(cfg)))


def engine_status(cfg):
    return store.engine.status(cfg)


def test_run_backup_pushes_the_store_after_the_jobs(source, tmp_path, make_cfg, monkeypatch):
    from lifeboat import engine
    (tmp_path / "src").mkdir(exist_ok=True)
    (tmp_path / "src" / "f.txt").write_text("data")
    (source / "a.md").write_text("fine\n")
    calls = []
    monkeypatch.setattr(store, "run_daily", lambda cfg: calls.append("daily") or {"ok": True, "pushed": True, "blocked_findings": [], "error": ""})
    cfg = make_cfg(store={"source": str(source), "push": True})
    assert engine.run_backup(cfg, settings=False)["ok"] and calls == ["daily"]
    engine.run_backup(cfg, dry_run=True, settings=False)
    assert calls == ["daily"]  # a dry run never pushes


def test_a_crashing_store_push_never_fails_the_backup(source, tmp_path, make_cfg, monkeypatch):
    from lifeboat import engine
    (tmp_path / "src").mkdir(exist_ok=True)
    (tmp_path / "src" / "f.txt").write_text("data")

    def boom(cfg):
        raise OSError("disk")
    monkeypatch.setattr(store, "run_daily", boom)
    assert engine.run_backup(make_cfg(store={"source": str(source), "push": True}), settings=False)["ok"] is True


# ---------------------------------------------------------------- setup steps the UI drives
@pytest.mark.parametrize("url,name", [("https://github.com/xixas/claude-config.git", "xixas/claude-config"),
                                      ("https://github.com/xixas/claude-config", "xixas/claude-config"),
                                      ("git@github.com:xixas/skillshare-source.git", "xixas/skillshare-source"),
                                      ("https://x-access-token:abc@github.com/o/r.git/", "o/r"),
                                      ("https://gitlab.com/o/r.git", None), ("/tmp/bare.git", None), ("", None), (None, None)])
def test_github_repo_names(url, name):
    assert store.github_repo(url) == name


def test_prepare_adopts_disables_update_check_and_installs_the_hook(tmp_path, fake_ss):
    p, log = fake_ss
    repo = _repo(tmp_path / "claude-config")
    r = store.prepare(repo, binary=p, home=tmp_path / "h")
    assert r == {"ok": True, "error": "", "source": str(repo)}
    cmds = [ln.split("|", 1)[1].strip() for ln in log.read_text().splitlines()]
    assert cmds[0].startswith("init --source") and cmds[1] == "updates off"
    assert (repo / ".git" / "hooks" / "pre-push").is_file()


def test_prepare_stops_when_init_fails(tmp_path, monkeypatch):
    p = tmp_path / "skillshare"
    p.write_text("#!/bin/sh\necho boom >&2\nexit 3\n")
    p.chmod(0o755)
    r = store.prepare(_repo(tmp_path / "r"), binary=p)
    assert r["ok"] is False and "boom" in r["error"] and not (tmp_path / "r" / ".git" / "hooks" / "pre-push").exists()


def test_prepare_a_new_store_uses_the_folder_skillshare_reports(tmp_path, monkeypatch):
    log = tmp_path / "argv.log"
    new = _repo(tmp_path / "newstore")
    cfg = tmp_path / "c" / "skillshare" / "config.yaml"
    p = tmp_path / "skillshare"  # like the real one, `init` is what writes its config
    p.write_text(f'#!/bin/sh\necho "$*" >> {log}\ncase "$1" in init) mkdir -p {cfg.parent}; printf "sources:\\n  skills: {new}\\n" > {cfg};; esac\n')
    p.chmod(0o755)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    r = store.prepare(None, binary=p)
    assert r["ok"] and r["source"] == str(new) and "--git" in log.read_text()
    assert (new / ".git" / "hooks" / "pre-push").is_file()


def test_find_repo_matches_the_installed_repos_by_name():
    class GH:
        def installation_repos(self):
            return [{"full_name": "xixas/skillshare-source", "clone_url": "https://github.com/xixas/skillshare-source.git", "private": True}]
    assert store.find_repo(GH(), "https://github.com/xixas/skillshare-source")["full_name"] == "xixas/skillshare-source"
    assert store.find_repo(GH(), "https://github.com/xixas/other") is None
    assert store.find_repo(GH(), "not a url") is None


def test_connect_remote_adds_origin_and_an_upstream_for_the_current_branch(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    store.connect_remote(repo, "https://github.com/xixas/skillshare-source.git")
    assert _git(repo, "remote", "get-url", "origin").strip() == "https://github.com/xixas/skillshare-source.git"
    assert _git(repo, "config", "branch.main.remote").strip() == "origin"
    assert _git(repo, "config", "branch.main.merge").strip() == "refs/heads/main"
    store.connect_remote(repo, "https://github.com/xixas/other.git")  # an existing origin is left alone
    assert "skillshare-source" in _git(repo, "remote", "get-url", "origin")


def test_a_secret_in_an_unpushed_commit_blocks_even_if_the_next_commit_removes_it(source, tmp_path):
    (source / "SKILL.md").write_text(f"key {AWS}\n")
    _git(source, "add", "-A")
    _git(source, "commit", "-qm", "oops")
    sha = _git(source, "rev-parse", "--short=7", "HEAD").strip()
    (source / "SKILL.md").write_text("clean\n")
    _git(source, "commit", "-qam", "fix")
    cfg = _cfg(source)
    r = store.run_daily(cfg, gh=NoToken())
    assert r["ok"] is False and r["pushed"] is False
    assert [(f["file"], f["commit"]) for f in r["blocked_findings"]] == [("SKILL.md", sha)] and AWS not in str(r)
    probs = store.engine.problems(cfg, engine_status(cfg))
    body = next(p["body"] for p in probs if p["page"] == "store")
    assert f"unpushed commit {sha}" in body
    assert f"git -C {source} reset --soft origin/main" in body and "commit again" in body


def _skillshare_config(tmp_path, monkeypatch, source):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    (tmp_path / "c" / "skillshare").mkdir(parents=True)
    (tmp_path / "c" / "skillshare" / "config.yaml").write_text(f"sources:\n  skills: {source}\n")


def test_setup_can_be_resumed_a_second_init_is_not_run(tmp_path, fake_ss, monkeypatch):
    """Close after step 2 and reopen: skillshare is already initialised and its second `init` would exit 1."""
    p, log = fake_ss
    repo = _repo(tmp_path / "claude-config")
    _skillshare_config(tmp_path, monkeypatch, repo)
    r = store.prepare(repo, binary=p)
    assert r == {"ok": True, "error": "", "source": str(repo)}
    cmds = [ln.split("|", 1)[1].strip() for ln in log.read_text().splitlines()]
    assert cmds == ["updates off"] and (repo / ".git" / "hooks" / "pre-push").is_file()


def test_resuming_a_new_store_uses_the_folder_skillshare_already_has(tmp_path, fake_ss, monkeypatch):
    p, log = fake_ss
    repo = _repo(tmp_path / "newstore")
    _skillshare_config(tmp_path, monkeypatch, repo)
    r = store.prepare(None, binary=p)
    assert r["ok"] and r["source"] == str(repo) and "init" not in log.read_text()


def test_a_different_pick_than_the_existing_skillshare_source_is_refused(tmp_path, fake_ss, monkeypatch):
    p, log = fake_ss
    _skillshare_config(tmp_path, monkeypatch, tmp_path / "other")
    r = store.prepare(_repo(tmp_path / "picked"), binary=p)
    assert r["ok"] is False and f"skillshare already uses {tmp_path / 'other'}" in r["error"] and not log.exists()


def test_a_new_store_with_no_upstream_is_pushed_with_dash_u_on_the_first_run(tmp_path, monkeypatch):
    """A fresh repo and a remote with no refs: ahead is 0 because there is no upstream, but nothing is on GitHub yet."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    bare = _hub(tmp_path, monkeypatch)
    src = tmp_path / "src"
    src.mkdir()
    _git(src, "init", "-q", "-b", "main")
    (src / "skills.md").write_text("# s\n")
    _git(src, "add", "-A")
    _git(src, "commit", "-qm", "first")
    _git(src, "remote", "add", "origin", "https://github.com/me/store.git")
    assert store.git_state(src)["ahead"] == 0
    r = store.run_daily(_cfg(src), gh=Token())
    assert r["ok"] and r["pushed"], r
    assert _git(bare, "rev-list", "--count", "main").strip() == "1"
    assert _git(src, "rev-parse", "--abbrev-ref", "@{u}").strip() == "origin/main"
    again = store.run_daily(_cfg(src), gh=Token())
    assert again["ok"] and not again["pushed"]


# ---------------------------------------------------------------- signed out means no pushes
class RevokedGH:
    def __init__(self):
        self.signed_out = False

    def installation_repos(self):
        return REPOS

    def token(self):
        return None if self.signed_out else "ghu_" + "z" * 36

    def sign_out(self):
        self.signed_out = True


def test_the_signed_out_store_raises_a_problem_on_the_store_page(source, tmp_path):
    (source / "a.md").write_text("# new\n")
    cfg = _cfg(source)
    store.run_daily(cfg, gh=NoToken())
    p = next(p for p in store.engine.problems(cfg, engine_status(cfg)) if p["page"] == "store")
    assert "Signed out" in p["title"] and "Sign in" in p["body"]


def test_a_rejected_token_signs_out_with_the_reason_access_was_revoked(source, tmp_path, monkeypatch):
    (source / "a.md").write_text("# new\n")
    monkeypatch.setattr(store.github, "push", lambda *a, **kw: {"ok": False, "error": "fatal: unable to access: The requested URL returned error: 403"})
    gh = RevokedGH()
    r = store.run_daily(_cfg(source), gh=gh)
    assert r["ok"] is False and r["signed_out"] == "GitHub access was revoked" and gh.signed_out is True


def test_a_failed_push_that_is_not_an_auth_error_stays_signed_in(source, tmp_path, monkeypatch):
    (source / "a.md").write_text("# new\n")
    monkeypatch.setattr(store.github, "push", lambda *a, **kw: {"ok": False, "error": "could not resolve host"})
    gh = RevokedGH()
    r = store.run_daily(_cfg(source), gh=gh)
    assert r["ok"] is False and not r.get("signed_out") and gh.signed_out is False


def test_prepare_refuses_a_source_that_is_not_a_git_repository_and_writes_no_hook(tmp_path, fake_ss, monkeypatch):
    p, _ = fake_ss
    plain, cwd = tmp_path / "plain", tmp_path / "cwd"
    plain.mkdir()
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    _skillshare_config(tmp_path, monkeypatch, plain)
    r = store.prepare(None, binary=p)  # an existing skillshare source that git has never seen
    assert r["ok"] is False and r["error"] == f"{plain} is not a git repository"
    assert list(cwd.iterdir()) == [] and list(plain.iterdir()) == []
    assert store.prepare(plain, binary=p)["error"] == f"{plain} is not a git repository"
    assert list(cwd.iterdir()) == []


def test_the_revoked_sign_in_problem_title_is_in_sentence_case(source):
    cfg = _cfg(source)
    store.engine.write_json("store.json", {"time": store.engine.now_iso(), "ok": False, "pushed": False, "blocked_findings": [],
                                           "error": "", "signed_out": "GitHub access was revoked", "last_ok_time": None})
    p = next(p for p in store.engine.problems(cfg, engine_status(cfg)) if p["page"] == "store")
    assert p["title"] == "Config store: GitHub access was revoked"


# ---------------------------------------------------------------- the push is hermetic
@pytest.mark.parametrize("origin", ["https://github.com/me/other.git", "git@github.com:me/other.git", "/some/local/path.git", "https://gitlab.com/me/store.git", ""])
def test_a_store_whose_remote_is_not_the_confirmed_github_repo_is_refused(source, monkeypatch, origin):
    if origin:
        _git(source, "remote", "set-url", "origin", origin)
    else:
        _git(source, "remote", "remove", "origin")
    (source / "a.md").write_text("# new\n")
    calls = []
    monkeypatch.setattr(store.github, "push", lambda *a, **kw: calls.append(a) or {"ok": True, "error": ""})
    r = store.run_daily(_cfg(source), gh=Token())
    assert r["ok"] is False and r["pushed"] is False and r["error"] and calls == []
    assert store.git_state(source)["dirty_count"] == 1  # nothing was committed either


def test_an_ssh_origin_of_the_confirmed_repo_still_pushes_to_the_https_url(source, tmp_path):
    _git(source, "remote", "set-url", "origin", "git@github.com:me/store.git")
    (source / "a.md").write_text("# new\n")
    r = store.run_daily(_cfg(source), gh=Token())
    assert r["ok"] and r["pushed"], r
    assert _git(tmp_path / "hub" / "me" / "store.git", "log", "-1", "--format=%s", "main").strip() == "Update skills"
    assert store.git_state(source)["ahead"] == 0  # the tracking ref follows the push, so the next run has nothing to do
    assert store.run_daily(_cfg(source), gh=Token())["pushed"] is False


# ---------------------------------------------------------------- a store is a whole repo
def _inside_a_bigger_repo(tmp_path):
    top = _repo(tmp_path / "dotfiles", skills=())
    sub = top / "ai" / "skills"
    sub.mkdir(parents=True)
    (sub / "x.md").write_text("# x\n")
    return top, sub


def test_prepare_refuses_a_folder_inside_a_bigger_repo_and_writes_no_hook(tmp_path, fake_ss):
    p, log = fake_ss
    top, sub = _inside_a_bigger_repo(tmp_path)
    r = store.prepare(sub, binary=p)
    assert r["ok"] is False and r["error"] == f"{sub} is inside the git repo {top}; pick the repo root"
    assert not log.exists() and not (top / ".git" / "hooks" / "pre-push").exists()


def test_prepare_refuses_an_existing_skillshare_source_that_is_inside_a_bigger_repo(tmp_path, fake_ss, monkeypatch):
    p, _ = fake_ss
    top, sub = _inside_a_bigger_repo(tmp_path)
    _skillshare_config(tmp_path, monkeypatch, sub)
    for pick in (None, sub):
        r = store.prepare(pick, binary=p)
        assert r["ok"] is False and r["error"] == f"{sub} is inside the git repo {top}; pick the repo root"
    assert not (top / ".git" / "hooks" / "pre-push").exists()


def test_the_daily_push_refuses_a_source_inside_a_bigger_repo_and_commits_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    top, sub = _inside_a_bigger_repo(tmp_path)
    _git(top, "add", "-A")
    _git(top, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
    (top / "unrelated.txt").write_text("not part of the store\n")
    r = store.run_daily(_cfg(sub), gh=Token())
    assert r["ok"] is False and r["error"] == f"{sub} is inside the git repo {top}; pick the repo root"
    assert store.git_state(top)["dirty_count"] == 1 and not (top / ".git" / "hooks" / "pre-push").exists()
    assert _git(top, "log", "--format=%s").strip() == "init"


# ---------------------------------------------------------------- the app pushes through the CLI, so signal cleanup applies
def test_the_apps_push_is_a_lifeboat_store_push_subprocess(monkeypatch):
    import json
    import subprocess
    import sys
    seen = []

    def fake_run(argv, **kw):
        seen.append((argv, kw))
        return subprocess.CompletedProcess(argv, 1, json.dumps({"ok": False, "pushed": False, "error": "boom", "blocked_findings": []}), "")
    monkeypatch.setattr(store.subprocess, "run", fake_run)
    res = store.push_in_subprocess()
    assert res["error"] == "boom" and res["ok"] is False
    assert seen[0][0] == [sys.executable, "-m", "lifeboat", "store-push", "--json"] and seen[0][1]["capture_output"] is True


def test_the_apps_push_survives_a_crashed_or_silent_subprocess(monkeypatch):
    import subprocess
    monkeypatch.setattr(store.subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(argv, 1, "Traceback...", "oops"))
    res = store.push_in_subprocess()
    assert res["ok"] is False and res["pushed"] is False and res["error"]

    def hang(argv, **kw):
        raise subprocess.TimeoutExpired(argv, 1)
    monkeypatch.setattr(store.subprocess, "run", hang)
    assert store.push_in_subprocess()["ok"] is False
