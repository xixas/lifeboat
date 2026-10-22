import pytest

from lifeboat import __version__
from lifeboat.config import ConfigError, load_config, parse

JOB = {"name": "a", "source": "~/a", "dest": "r:a", "old": "r:old"}


def test_defaults():
    cfg = parse({"jobs": [JOB]})
    assert (cfg.daily_at, cfg.keep_days, cfg.claude_skills, cfg.settings_command) == ("13:00", 90, "auto", "")
    assert cfg.jobs[0].exclude == [] and cfg.jobs[0].encrypted is False


@pytest.mark.parametrize("data,key", [
    ({}, "jobs"),
    ({"jobs": [{**JOB, "name": ""}]}, "jobs[1].name"),
    ({"jobs": [{**JOB, "name": "bad/name"}]}, "jobs[1].name"),
    ({"jobs": [{k: v for k, v in JOB.items() if k != "dest"}]}, "jobs[1].dest"),
    ({"jobs": [JOB, JOB]}, "used twice"),
    ({"jobs": [{**JOB, "old": "r:a"}]}, "jobs[1].old"),
    ({"jobs": [{**JOB, "exclude": "x"}]}, "jobs[1].exclude"),
    ({"jobs": [{**JOB, "include": ["a"], "exclude": ["b"]}]}, "include and exclude cannot be combined"),
    ({"jobs": [{**JOB, "encrypted": "yes"}]}, "jobs[1].encrypted"),
    ({"jobs": [{**JOB, "colour": 1}]}, "jobs[1].colour"),
    ({"jobs": [JOB], "schedule": {"daily_at": "25:00"}}, "schedule.daily_at"),
    ({"jobs": [JOB], "schedule": {"keep_days": 0}}, "schedule.keep_days"),
    ({"jobs": [JOB], "schedule": {"keep_days": True}}, "schedule.keep_days"),
    ({"jobs": [JOB], "modules": {"claude_skills": "maybe"}}, "modules.claude_skills"),
    ({"jobs": [JOB], "rclone": {"config": 3}}, "rclone.config"),
    ({"jobs": [JOB], "bogus": {}}, "bogus"),
])
def test_validation_names_the_bad_key(data, key):
    with pytest.raises(ConfigError, match=key.replace("[", r"\[").replace("]", r"\]")):
        parse(data)


def test_load_errors(tmp_path):
    with pytest.raises(ConfigError, match="no config file"):
        load_config(tmp_path / "missing.toml")
    bad = tmp_path / "bad.toml"
    bad.write_text("jobs = [")
    with pytest.raises(ConfigError, match="not valid TOML"):
        load_config(bad)


def test_example_config_is_valid_and_filter_path_is_relative_to_config(tmp_path):
    from pathlib import Path
    cfg = load_config(Path(__file__).parent.parent / "data" / "config.example.toml")
    assert cfg.jobs[0].dest.startswith("gdrive:")
    cfg.jobs[0].filter_file = "f.filter"
    assert cfg.filter_path(cfg.jobs[0]) == str(cfg.config_dir / "f.filter")


def test_version_matches_pyproject():
    from pathlib import Path
    assert f'version = "{__version__}"' in (Path(__file__).parent.parent / "pyproject.toml").read_text()
