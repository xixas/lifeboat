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


BASE = '# my backups\n[schedule]\nkeep_days = 30\n\n[[jobs]]\nname = "a"\nsource = "~/a"\ndest = "r:a"\nold = "r:_old"\n'


def test_default_old_is_a_sibling_of_dest():
    from lifeboat.config import default_old
    assert default_old("gdrive:Backup/Documents") == "gdrive:Backup/_old"
    assert default_old("gdrive:Docs") == "gdrive:_old"
    assert default_old("gdrive:Backup/Docs/") == "gdrive:Backup/_old"


def test_add_job_text_appends_a_parseable_block_and_keeps_comments():
    from lifeboat.config import Job, add_job_text
    job = Job("b", "~/it's \"b\"", "r:x/b", "r:x/_old", exclude=["*.tmp", "é/**"], encrypted=True)
    new = add_job_text(BASE, job)
    assert new.startswith(BASE) and "# my backups" in new
    cfg = parse(__import__("tomllib").loads(new))
    assert cfg.jobs[1] == job
    assert "include" not in new.split('name = "b"')[1] and "filter_file" not in new


def test_add_job_text_refuses_invalid_jobs():
    from lifeboat.config import Job, add_job_text
    with pytest.raises(ConfigError, match="used twice"):
        add_job_text(BASE, Job("a", "~/z", "r:z", "r:_old"))
    with pytest.raises(ConfigError, match="include and exclude"):
        add_job_text(BASE, Job("c", "~/z", "r:z", "r:_old", include=["x"], exclude=["y"]))
    with pytest.raises(ConfigError, match="jobs\\[2\\].old"):
        add_job_text(BASE, Job("c", "~/z", "r:z", "r:z"))


TWO = BASE + '\n# [[jobs]]\n# name = "ghost"\n\n[[jobs]]\nname = "b"\nsource = "~/b"\ndest = "r:b"\nold = "r:_old"\n\n[settings]\ncommand = ""\n'


def test_remove_job_text_removes_only_the_named_block():
    from lifeboat.config import remove_job_text
    new = remove_job_text(TWO, "b")
    assert [j.name for j in parse(__import__("tomllib").loads(new)).jobs] == ["a"]
    assert '# [[jobs]]' in new and "[settings]" in new and "# my backups" in new


def test_remove_job_text_refusals():
    from lifeboat.config import remove_job_text
    with pytest.raises(ConfigError, match="no job named 'ghost'"):
        remove_job_text(TWO, "ghost")
    with pytest.raises(ConfigError, match="only job"):
        remove_job_text(BASE, "a")


def test_write_config_text_is_atomic_keeps_mode_and_backup(tmp_path):
    from lifeboat.config import write_config_text
    p = tmp_path / "config.toml"
    p.write_text(BASE)
    p.chmod(0o600)
    write_config_text(p, TWO)
    assert p.read_text() == TWO and p.stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "config.toml.bak").read_text() == BASE
    with pytest.raises(ConfigError):
        write_config_text(p, "jobs = [")
    assert p.read_text() == TWO and sorted(x.name for x in tmp_path.iterdir()) == ["config.toml", "config.toml.bak"]


def test_default_old_for_a_local_dest():
    from lifeboat.config import default_old
    assert default_old("/mnt/backup/docs") == "/mnt/backup/_old"
    assert default_old("/mnt/backup/docs/") == "/mnt/backup/_old"


def test_write_config_text_writes_through_a_symlink(tmp_path):
    from lifeboat.config import write_config_text
    real = tmp_path / "dotfiles" / "config.toml"
    real.parent.mkdir()
    real.write_text(BASE)
    link = tmp_path / "config.toml"
    link.symlink_to(real)
    write_config_text(link, TWO)
    assert link.is_symlink() and real.read_text() == TWO
    assert (real.parent / "config.toml.bak").read_text() == BASE and not (tmp_path / "config.toml.bak").exists()


def test_remove_job_text_takes_the_leading_comment_with_its_job():
    from lifeboat.config import remove_job_text
    text = BASE.replace("[[jobs]]", "# --- a ---\n[[jobs]]") + '\n# --- photos ---\n[[jobs]]\nname = "b"\nsource = "~/b"\ndest = "r:b"\nold = "r:_old"\n'
    new = remove_job_text(text, "a")
    assert "# --- photos ---" in new and "# --- a ---" not in new
    assert "# --- a ---" in remove_job_text(text, "b") and "# --- photos ---" not in remove_job_text(text, "b")


def test_write_config_text_fsyncs_before_replace(tmp_path, monkeypatch):
    import os

    from lifeboat.config import write_config_text
    calls = []
    real_fsync, real_replace = os.fsync, os.replace
    monkeypatch.setattr(os, "fsync", lambda fd: (calls.append("fsync"), real_fsync(fd))[1])
    monkeypatch.setattr(os, "replace", lambda a, b: (calls.append("replace"), real_replace(a, b))[1])
    p = tmp_path / "config.toml"
    p.write_text(BASE)
    write_config_text(p, TWO)
    assert calls.index("fsync") < calls.index("replace")
