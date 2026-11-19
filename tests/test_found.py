import pytest

from lifeboat import config, found, setup


def item(key, path, **kw):
    return {"key": key, "label": kw.pop("label", key.title()), "group": "ai", "path": str(path), "kind": kw.pop("kind", "dir"),
            "secret": kw.pop("secret", False), "default_on": kw.pop("default_on", True), "exclude": [], "links": False,
            "ignored": kw.pop("ignored", False), **kw}


@pytest.fixture
def cfg_file(fake_home):
    path = fake_home / ".config" / "lifeboat" / "config.toml"
    choice = {"key": "documents", "label": "Documents", "path": str(fake_home / "Documents"), "secret": False}
    setup.write_wizard_config(path, "laptop", [choice], "13:00")
    return path


def test_covered_by_a_job_source_or_an_include_file(cfg_file, fake_home):
    cfg = config.load_config(cfg_file)
    assert found.covered(item("documents", fake_home / "Documents"), cfg)
    assert found.covered(item("sub", fake_home / "Documents" / "sub"), cfg)
    assert not found.covered(item("claude", fake_home / ".claude"), cfg)
    found.add_item(cfg, item("zshrc", fake_home / ".zshrc", kind="file"))
    cfg = config.load_config(cfg_file)
    assert found.covered(item("zshrc", fake_home / ".zshrc", kind="file"), cfg)
    assert not found.covered(item("npmrc", fake_home / ".npmrc", kind="file"), cfg)


def test_add_item_appends_a_job_built_like_the_wizard_builds_it(cfg_file, fake_home):
    cfg = config.load_config(cfg_file)
    found.add_item(cfg, item("claude", fake_home / ".claude", label="Claude Code", secret=True, links=True, exclude=["sessions/**"]))
    job = config.load_config(cfg_file).job("claude-code")
    assert (job.source, job.encrypted, job.links, job.dest) == ("~/.claude", True, True, "lifeboat-secure:laptop/claude-code")
    assert "sessions/**" in job.exclude


def test_adding_a_second_single_file_gets_its_own_job(cfg_file, fake_home):
    for name in (".zshrc", ".npmrc"):
        found.add_item(config.load_config(cfg_file), item(name[1:], fake_home / name, kind="file", secret=True))
    jobs = config.load_config(cfg_file).jobs
    assert [j.name for j in jobs][1:] == ["home-files", "home-files-npmrc"]
    assert len({j.dest for j in jobs}) == 3


def test_add_item_needs_a_device_name(fake_home):
    path = fake_home / "c.toml"
    path.write_text('[[jobs]]\nname = "d"\nsource = "~/D"\ndest = "x:d"\nold = "x:_old"\n')
    with pytest.raises(config.ConfigError):
        found.add_item(config.load_config(path), item("claude", fake_home / ".claude"))


def test_ignore_and_unignore_edit_the_config(cfg_file):
    found.set_ignored(config.load_config(cfg_file), "cache", True)
    assert config.load_config(cfg_file).scan_ignore == ["cache"]
    found.set_ignored(config.load_config(cfg_file), "cache", False)
    assert config.load_config(cfg_file).scan_ignore == []


def test_apply_ignore_writes_a_list_into_a_fresh_config(cfg_file):
    found.apply_ignore(cfg_file, ["a", "b"])
    assert config.load_config(cfg_file).scan_ignore == ["a", "b"]
    found.apply_ignore(cfg_file, [])  # nothing to write


def test_the_wizard_writes_its_ignore_list_into_the_new_config(fake_home):
    path = fake_home / ".config" / "lifeboat" / "config.toml"
    choice = {"key": "documents", "label": "Documents", "path": str(fake_home / "Documents"), "secret": False}
    setup.write_wizard_config(path, "laptop", [choice], "13:00")
    found.apply_ignore(path, ["cache"])
    cfg = config.load_config(path)
    assert cfg.scan_ignore == ["cache"] and [j.name for j in cfg.jobs] == ["documents"]


def test_dropped_sources_match_catalogue_items_by_path(fake_home):
    items = [item("claude", fake_home / ".claude"), item("zshrc", fake_home / ".zshrc", kind="file")]
    got = found.match_dropped(["~/.claude", str(fake_home / ".zshrc"), "~/Old"], items)
    assert [(p, i and i["key"]) for p, i in got] == [("~/.claude", "claude"), (str(fake_home / ".zshrc"), "zshrc"), ("~/Old", None)]


def test_new_items_are_default_on_unignored_uncovered_and_unseen(cfg_file, fake_home):
    cfg = config.load_config(cfg_file)
    items = [item("documents", fake_home / "Documents"), item("claude", fake_home / ".claude"),
             item("codex", fake_home / ".codex"), item("cache", fake_home / ".cache", default_on=False),
             item("gemini", fake_home / ".gemini", ignored=True), item("cursor", fake_home / ".cursor")]
    assert [i["key"] for i in found.new_items(items, cfg, {"cursor"})] == ["claude", "codex"]


def test_seen_keys_are_kept_in_a_state_file(fake_home, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(fake_home / "state"))
    assert found.read_seen() is None
    found.mark_seen(["b", "a"])
    found.mark_seen(["c", "a"])
    assert found.read_seen() == {"a", "b", "c"}


def test_first_check_marks_everything_seen_and_reports_nothing(cfg_file, fake_home, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(fake_home / "state"))
    cfg = config.load_config(cfg_file)
    items = [item("claude", fake_home / ".claude")]
    assert found.unseen_new_items(items, cfg) == []
    assert found.read_seen() == {"claude"}
    items.append(item("codex", fake_home / ".codex"))
    assert [i["key"] for i in found.unseen_new_items(items, cfg)] == ["codex"]


def test_items_group_in_catalogue_order_and_group_less_items_fall_back():
    items = [item("ssh", "/h/.ssh", group="keys", secret=True), item("documents", "/h/Documents", group="files"),
             item("gpg", "/h/.gnupg", group="keys", secret=True), {"key": "x", "label": "X", "path": "/x", "secret": False}]
    got = found.grouped(items)
    assert [(g, t, [i["key"] for i in rows], enc) for g, t, rows, enc in got] == [
        ("files", "Files", ["documents", "x"], False), ("keys", "Keys & logins", ["ssh", "gpg"], True)]


def test_a_group_with_a_plain_item_is_not_called_encrypted():
    got = found.grouped([item("zsh", "/h/z", group="shell", secret=True), item("fish", "/h/f", group="shell")])
    assert got[0][3] is False


def test_add_back_re_adds_the_matched_jobs_and_acknowledges_only_those(cfg_file, fake_home, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(fake_home / "state"))
    from lifeboat import engine
    engine.write_json("coverage.json", {"sources": [], "dropped": ["~/.claude", "~/Old"]})
    items = [item("claude", fake_home / ".claude", label="Claude Code", secret=True)]
    pairs = found.match_dropped(engine.dropped_sources(), items)
    assert found.add_back(cfg_file, pairs) == ["~/.claude"]
    assert config.load_config(cfg_file).job("claude-code").source == "~/.claude"
    assert engine.dropped_sources() == ["~/Old"]  # what has no catalogue item stays until it is dismissed
