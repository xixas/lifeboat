import json

from lifeboat.skills import skills


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
