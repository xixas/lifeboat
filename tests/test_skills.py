import json
from datetime import UTC, datetime, timedelta

from lifeboat import config
from lifeboat.skills import coverage, skills


def make_skill(base, name, marker=None):
    d = base / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\n---\n")
    if marker is not None:
        (d / "agent-native-skill.json").write_text(json.dumps(marker))


def test_agent_native_skills_count_as_external(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    base = tmp_path / ".claude" / "skills"
    make_skill(base, "mine")
    make_skill(base, "visual-plan", {"source": "agent-native", "skillName": "visual-plan"})
    s = skills()
    assert s["own"] == ["mine"]
    assert s["external"] == {"agent-native": ["visual-plan"]}
    assert s["external_count"] == 1


# ---------------------------------------------------------------- coverage verdict
def cfg_with(path, *sources):
    jobs = [{"name": f"j{n}", "source": s, "dest": f"x:j{n}", "old": "x:_old"} for n, s in enumerate(sources)]
    return config.parse({"jobs": jobs}, path / "config.toml")


def ran(ok=True, days=0.5, dry_run=False):
    return {"ok": ok, "dry_run": dry_run, "time": (datetime.now(UTC) - timedelta(days=days)).isoformat()}


def linked_skill(home, name):
    """A skill that is a symlink into a git checkout outside ~/.claude, like the ones a skill manager keeps."""
    repo = home / "src" / "my-skills"
    (repo / ".git").mkdir(parents=True, exist_ok=True)
    make_skill(repo / "skills", name)
    (home / ".claude" / "skills").mkdir(parents=True, exist_ok=True)
    (home / ".claude" / "skills" / name).symlink_to(repo / "skills" / name)
    return repo


def test_own_skills_are_covered_when_their_real_path_is_under_a_job_source(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    repo = linked_skill(tmp_path, "mine")
    make_skill(tmp_path / ".claude" / "skills", "plain")
    own = ["mine", "plain"]
    both = cfg_with(tmp_path, "~/.claude", str(repo))
    assert coverage(both, ran(), own)["backed_up"] is True
    # ~/.claude alone does not cover the symlinked skill: the files live in the checkout
    only = coverage(cfg_with(tmp_path, "~/.claude"), ran(), own)
    assert only["backed_up"] is False and only["missing"] == ["mine"]
    assert coverage(cfg_with(tmp_path, str(repo)), ran(), own)["missing"] == ["plain"]


def test_a_stale_failed_or_dry_run_backup_is_not_a_verdict_of_backed_up(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    make_skill(tmp_path / ".claude" / "skills", "plain")
    cfg = cfg_with(tmp_path, "~/.claude")
    for last in (None, ran(ok=False), ran(days=3), ran(dry_run=True)):
        got = coverage(cfg, last, ["plain"])
        assert got["missing"] == [] and got["recent"] is False and got["backed_up"] is False
    assert coverage(cfg, ran(days=1.9), ["plain"])["backed_up"] is True
    assert coverage(cfg, ran(), [])["backed_up"] is False  # nothing of yours to back up


def test_agents_folder_coverage(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert coverage(cfg_with(tmp_path, "~/.agents"), None, [])["agents"] is True
    assert coverage(cfg_with(tmp_path, "~/.claude"), None, [])["agents"] is False


def test_the_fix_is_the_uncovered_catalogue_item_around_each_missing_skill(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    repo = linked_skill(tmp_path, "mine")
    make_skill(tmp_path / ".claude" / "skills", "plain")
    items = [{"key": "claude", "path": str(tmp_path / ".claude")}, {"key": "linked-repo", "path": str(repo)}]
    got = coverage(cfg_with(tmp_path, "~/Documents"), ran(), ["mine", "plain"], items)
    assert [i["key"] for i in got["fix"]] == ["linked-repo", "claude"]
    assert [i["key"] for i in coverage(cfg_with(tmp_path, "~/.claude"), ran(), ["mine", "plain"], items)["fix"]] == ["linked-repo"]
