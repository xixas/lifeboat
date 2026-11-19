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


class Token:
    def token(self):
        return "ghu_" + "z" * 36


@pytest.fixture
def source(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("PYTHONPATH", str(PKG.parent))
    bare = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(bare))
    src = tmp_path / "src"
    _git(tmp_path, "clone", "-q", str(bare), str(src))
    (src / "README.md").write_text("x\n")
    _git(src, "add", "-A")
    _git(src, "commit", "-qm", "init")
    _git(src, "push", "-q", "-u", "origin", "HEAD:main")
    _git(src, "branch", "-u", "origin/main")
    return src


def _cfg(src, push=True):
    from lifeboat.config import parse
    return parse({"jobs": [{"name": "d", "source": "/x", "dest": "a:b", "old": "a:c"}], "store": {"source": str(src), "push": push}})


def _skillshare_push(tmp_path, log):
    """A fake skillshare whose `push` commits everything and pushes, like the real one."""
    p = tmp_path / "skillshare"
    p.write_text(f'#!/bin/sh\necho "$*" >> {log}\ngit add -A && git -c user.name=t -c user.email=t@t commit -qm "Update skills" && git push -q\n')
    p.chmod(0o755)
    return p


def test_daily_push_blocks_on_secret_and_does_not_push(source, tmp_path):
    (source / "skills").mkdir()
    (source / "skills" / "SKILL.md").write_text(f"key {AWS}\n")
    log = tmp_path / "ss.log"
    r = store.run_daily(_cfg(source), gh=NoToken(), binary=_skillshare_push(tmp_path, log))
    assert r["ok"] is False and r["pushed"] is False
    assert r["blocked_findings"][0]["file"] == "skills/SKILL.md" and AWS not in str(r)
    assert not log.exists()
    assert store.engine.read_json(store.engine.state_dir() / "store.json")["blocked_findings"]


def test_daily_push_with_skillshare_when_no_lifeboat_token(source, tmp_path):
    (source / "skills").mkdir()
    (source / "skills" / "SKILL.md").write_text("# fine\n")
    log = tmp_path / "ss.log"
    r = store.run_daily(_cfg(source), gh=NoToken(), binary=_skillshare_push(tmp_path, log))
    assert r["ok"] and r["pushed"] and r["blocked_findings"] == [] and r["error"] == ""
    assert log.read_text().strip() == "push"
    assert store.git_state(source)["ahead"] == 0
    assert (source / ".git" / "hooks" / "pre-push").exists()


def test_daily_push_with_lifeboat_token_uses_git_push(source, tmp_path, monkeypatch):
    (source / "a.md").write_text("# fine\n")
    pushed = []
    monkeypatch.setattr(store.github, "push", lambda path, token: pushed.append((str(path), token)) or {"ok": True, "error": ""})
    log = tmp_path / "ss.log"
    r = store.run_daily(_cfg(source), gh=Token(), binary=_skillshare_push(tmp_path, log))
    assert r["ok"] and r["pushed"] and not log.exists()
    assert pushed == [(str(source), "ghu_" + "z" * 36)]
    assert store.git_state(source)["dirty_count"] == 0  # committed first


def test_daily_push_nothing_to_push(source, tmp_path):
    r = store.run_daily(_cfg(source), gh=NoToken(), binary=_skillshare_push(tmp_path, tmp_path / "l"))
    assert r["ok"] is True and r["pushed"] is False


def test_daily_push_failure_recorded(source, tmp_path):
    (source / "a.md").write_text("ok\n")
    p = tmp_path / "skillshare"
    p.write_text("#!/bin/sh\necho no remote >&2\nexit 1\n")
    p.chmod(0o755)
    r = store.run_daily(_cfg(source), gh=NoToken(), binary=p)
    assert r["ok"] is False and "no remote" in r["error"]


def test_daily_push_off_or_unconfigured(source, tmp_path):
    assert store.run_daily(_cfg(source, push=False), gh=NoToken()) is None
    assert store.run_daily(_cfg(""), gh=NoToken()) is None


def test_last_ok_time_carries_forward(source, tmp_path):
    cfg = _cfg(source)
    ok = store.run_daily(cfg, gh=NoToken(), binary=_skillshare_push(tmp_path, tmp_path / "l"))
    (source / "s.md").write_text(f"{AWS}\n")
    bad = store.run_daily(cfg, gh=NoToken(), binary=_skillshare_push(tmp_path, tmp_path / "l"))
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


def test_prepare_a_new_store_uses_the_folder_skillshare_reports(tmp_path, fake_ss, monkeypatch):
    p, log = fake_ss
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    new = _repo(tmp_path / "newstore")
    (tmp_path / "c" / "skillshare").mkdir(parents=True)
    (tmp_path / "c" / "skillshare" / "config.yaml").write_text(f"sources:\n  skills: {new}\n")
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
    log = tmp_path / "ss.log"
    cfg = _cfg(source)
    r = store.run_daily(cfg, gh=NoToken(), binary=_skillshare_push(tmp_path, log))
    assert r["ok"] is False and r["pushed"] is False and not log.exists()
    assert [(f["file"], f["commit"]) for f in r["blocked_findings"]] == [("SKILL.md", sha)] and AWS not in str(r)
    probs = store.engine.problems(cfg, engine_status(cfg))
    body = next(p["body"] for p in probs if p["page"] == "store")
    assert f"unpushed commit {sha}" in body
    assert f"git -C {source} reset --soft origin/main" in body and "commit again" in body
