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
