import json
from datetime import datetime

import pytest
from conftest import stamps

from lifeboat import engine, timer
from lifeboat.config import ConfigError


def read(p):
    return p.read_text()


def test_backup_creates_dest_and_state(tmp_path, make_cfg, clock):
    cfg = make_cfg()
    (tmp_path / "src" / "a.txt").write_text("one")
    st = engine.run_backup(cfg, now=clock())
    assert st["ok"] and not st["dry_run"] and st["jobs"][0]["rc"] == 0
    assert read(tmp_path / "dest" / "a.txt") == "one"
    saved = json.loads((engine.state_dir() / "last-run.json").read_text())
    assert saved["ok"] and saved["jobs"][0]["name"] == "docs" and "seconds" in saved["jobs"][0]
    assert (engine.state_dir() / "run.log").exists()


def test_changed_and_deleted_files_move_to_old_stamp(tmp_path, make_cfg, clock):
    cfg = make_cfg()
    src = tmp_path / "src"
    (src / "keep.txt").write_text("v1")
    (src / "gone.txt").write_text("bye")
    engine.run_backup(cfg, now=clock(0))
    (src / "keep.txt").write_text("version two")
    (src / "gone.txt").unlink()
    engine.run_backup(cfg, now=clock(60))
    assert stamps(tmp_path / "old") == ["2026-01-01_120100"]
    held = tmp_path / "old" / "2026-01-01_120100" / "docs"
    assert read(held / "keep.txt") == "v1" and read(held / "gone.txt") == "bye"
    assert read(tmp_path / "dest" / "keep.txt") == "version two" and not (tmp_path / "dest" / "gone.txt").exists()
    assert engine.list_versions(cfg, "docs") == ["2026-01-01_120100"]


def test_exclude_include_and_filter_file(tmp_path, make_cfg, clock):
    cfg = make_cfg()
    job = cfg.jobs[0]
    job.exclude = ["skip/**"]
    (tmp_path / "src" / "skip").mkdir()
    (tmp_path / "src" / "skip" / "x").write_text("x")
    (tmp_path / "src" / "ok.txt").write_text("ok")
    (cfg.config_dir).mkdir(parents=True)
    (cfg.config_dir / "f.filter").write_text("- *.log\n")
    job.filter_file = "f.filter"
    (tmp_path / "src" / "a.log").write_text("log")
    engine.run_backup(cfg, now=clock())
    assert (tmp_path / "dest" / "ok.txt").exists()
    assert not (tmp_path / "dest" / "skip").exists() and not (tmp_path / "dest" / "a.log").exists()
    job.exclude, job.filter_file, job.include = [], "", ["/*.txt"]
    engine.run_backup(cfg, now=clock(5))
    assert (tmp_path / "dest" / "ok.txt").exists()


def test_dry_run_records_state_and_changes_nothing(tmp_path, make_cfg, clock):
    cfg = make_cfg()
    (tmp_path / "src" / "a.txt").write_text("one")
    st = engine.run_backup(cfg, dry_run=True, now=clock())
    assert st["dry_run"] is True
    assert json.loads((engine.state_dir() / "last-run.json").read_text())["dry_run"] is True
    assert not (tmp_path / "dest" / "a.txt").exists()


def test_failed_job_makes_overall_not_ok(tmp_path, make_cfg, clock):
    cfg = make_cfg()
    cfg.jobs[0].source = str(tmp_path / "does-not-exist")
    st = engine.run_backup(cfg, now=clock())
    assert st["ok"] is False and st["jobs"][0]["rc"] != 0


def test_settings_command_runs_before_jobs_and_is_recorded(tmp_path, make_cfg, clock):
    cfg = make_cfg(settings={"command": f"echo hi > {tmp_path}/src/settings.txt"})
    cfg.settings_command = f"echo hi > {tmp_path}/src/settings.txt"
    engine.run_backup(cfg, now=clock())
    assert (tmp_path / "dest" / "settings.txt").exists()
    assert json.loads((engine.state_dir() / "settings.json").read_text())["ok"] is True
    cfg.settings_command = "exit 3"
    engine.run_backup(cfg, now=clock(5))
    snap = json.loads((engine.state_dir() / "settings.json").read_text())
    assert snap == {**snap, "ok": False, "rc": 3}


def test_prune_removes_only_old_stamp_folders(tmp_path, make_cfg):
    cfg = make_cfg(keep_days=30)
    old = tmp_path / "old"
    for name in ("2025-11-01_000000", "2025-12-25_000000", "2026-01-05_000000", "notes", "2025-13-99"):
        (old / name / "docs").mkdir(parents=True)
        (old / name / "docs" / "f").write_text("x")
    res = engine.prune(cfg, now=datetime(2026, 1, 10))
    assert res["ok"] and len(res["removed"]) == 1
    assert sorted(p.name for p in old.iterdir()) == ["2025-12-25_000000", "2025-13-99", "2026-01-05_000000", "notes"]
    assert not (old / "2025-11-01_000000").exists()
    assert (old / "notes").exists() and (old / "2026-01-05_000000").exists()
    assert (engine.state_dir() / "prune.json").exists()


def test_prune_cutoff_is_keep_days(tmp_path, make_cfg):
    cfg = make_cfg(keep_days=30)
    old = tmp_path / "old"
    for name in ("2025-12-10_235959", "2025-12-11_000001"):
        (old / name).mkdir(parents=True)
    engine.prune(cfg, now=datetime(2026, 1, 10))
    assert stamps(old) == ["2025-12-11_000001"]


def test_prune_with_missing_old_dir_is_ok(make_cfg):
    res = engine.prune(make_cfg(), now=datetime(2026, 1, 10))
    assert res["ok"] and res["removed"] == []


def test_restore_latest_and_from_version(tmp_path, make_cfg, clock):
    cfg = make_cfg()
    src = tmp_path / "src"
    (src / "f.txt").write_text("v1")
    engine.run_backup(cfg, now=clock(0))
    (src / "f.txt").write_text("v2 longer")
    engine.run_backup(cfg, now=clock(60))
    latest = tmp_path / "r1"
    assert engine.restore(cfg, "docs", latest) == 0
    assert read(latest / "f.txt") == "v2 longer"
    older = tmp_path / "r2"
    assert engine.restore(cfg, "docs", older, version="2026-01-01_120100") == 0
    assert read(older / "f.txt") == "v1"
    assert read(src / "f.txt") == "v2 longer"  # live source untouched
    dry = tmp_path / "r3"
    assert engine.restore(cfg, "docs", dry, dry_run=True) == 0 and not dry.exists()
    one = tmp_path / "r4"
    (src / "g.txt").write_text("g")
    engine.run_backup(cfg, now=clock(120))
    assert engine.restore(cfg, "docs", one, files=["g.txt"]) == 0
    assert sorted(p.name for p in one.iterdir()) == ["g.txt"]


def test_restore_rejects_bad_version_and_unknown_job(tmp_path, make_cfg):
    cfg = make_cfg()
    with pytest.raises(ConfigError, match="version"):
        engine.restore(cfg, "docs", tmp_path / "x", version="../../etc")
    with pytest.raises(ConfigError, match="no job named"):
        engine.restore(cfg, "nope", tmp_path / "x")


def test_crypt_job_round_trip(tmp_path, make_cfg, clock):
    cfg = make_cfg(crypt=True)
    (tmp_path / "src" / "secret.txt").write_text("top secret")
    assert engine.run_backup(cfg, now=clock())["ok"]
    stored = [p.name for p in (tmp_path / "crypt").rglob("*") if p.is_file()]
    assert stored and all("secret" not in n for n in stored)
    assert engine.restore(cfg, "docs", tmp_path / "back") == 0
    assert read(tmp_path / "back" / "secret.txt") == "top secret"


def test_status_and_problems_flag_failure_and_staleness(tmp_path, make_cfg, clock):
    cfg = make_cfg()
    st = engine.status(cfg)
    assert st["status"]["backups"] == "warn" and st["last"] is None
    assert engine.problems(cfg, st)[0]["title"] == "Backup needs attention"

    (tmp_path / "src" / "a").write_text("a")
    engine.run_backup(cfg, now=clock())
    st = engine.status(cfg)
    assert st["status"]["backups"] == "ok" and st["verdict"]["title"] == "Fully covered"
    assert engine.problems(cfg, st) == []

    cfg.jobs[0].source = str(tmp_path / "nope")
    engine.run_backup(cfg, now=clock(9))
    st = engine.status(cfg)
    assert st["status"]["backups"] == "error"
    (p,) = engine.problems(cfg, st)
    assert "docs" in p["body"] and p["urgent"]

    stale = json.loads((engine.state_dir() / "last-run.json").read_text())
    stale.update(ok=True, time="2020-01-01T00:00:00+00:00", jobs=[{"name": "docs", "rc": 0, "seconds": 1}])
    (engine.state_dir() / "last-run.json").write_text(json.dumps(stale))
    st = engine.status(cfg)
    assert st["status"]["backups"] == "warn" and engine.problems(cfg, st)


def test_dry_run_only_does_not_count_as_a_backup(make_cfg, tmp_path, clock):
    cfg = make_cfg()
    (tmp_path / "src" / "a.txt").write_text("one")
    engine.run_backup(cfg, dry_run=True, now=clock())
    assert engine.status(cfg)["status"]["backups"] == "warn"


def test_missing_remote_is_reported(make_cfg):
    cfg = make_cfg()
    cfg.jobs[0].dest = "nosuchremote:x"
    assert "nosuchremote:" in engine.status(cfg)["missing_remotes"]


def test_settings_failure_is_a_problem(make_cfg, clock):
    cfg = make_cfg()
    cfg.settings_command = "exit 1"
    engine.run_backup(cfg, now=clock())
    probs = engine.problems(cfg, engine.status(cfg))
    assert any(p["page"] == "settings" for p in probs)


def test_rclone_config_and_password_command_are_injected(make_cfg, tmp_path):
    cfg = make_cfg()
    cfg.rclone_config = "~/x.conf"
    cfg.password_command = "echo pw"
    argv = engine.rclone_argv(cfg, ["lsf"])
    assert argv[:4] == ["rclone", "--ask-password=false", "--config", str(__import__("os").path.expanduser("~/x.conf"))]
    assert engine.rclone_env(cfg)["RCLONE_PASSWORD_COMMAND"] == "echo pw"


def test_unit_files(make_cfg):
    cfg = make_cfg()
    cfg.daily_at = "07:45"
    u = timer.unit_files(cfg)
    assert "OnCalendar=*-*-* 07:45:00" in u["lifeboat.timer"] and "Persistent=true" in u["lifeboat.timer"]
    assert "RandomizedDelaySec=10min" in u["lifeboat.timer"]
    assert " run\n" in u["lifeboat.service"] and "ExecStopPost=-" in u["lifeboat.service"]
    assert "prune" in u["lifeboat-prune.service"] and "OnCalendar=weekly" in u["lifeboat-prune.timer"]


def test_unit_files_quote_paths_with_spaces(make_cfg, monkeypatch, tmp_path):
    monkeypatch.setattr(timer.shutil, "which", lambda n: None)
    fake = tmp_path / "my checkout" / "lifeboat" / "timer.py"
    monkeypatch.setattr(timer, "__file__", str(fake))
    monkeypatch.setattr(timer.sys, "executable", "/opt/py thon/bin/python3")
    svc = timer.unit_files(make_cfg())["lifeboat.service"]
    assert f'Environment="PYTHONPATH={tmp_path / "my checkout"}"' in svc
    assert 'ExecStart="/opt/py thon/bin/python3" -m lifeboat run' in svc
    assert 'ExecStopPost=-"/opt/py thon/bin/python3" -m lifeboat check' in svc


def test_empty_or_missing_source_is_refused(tmp_path, make_cfg, clock):
    cfg = make_cfg()
    src = tmp_path / "src"
    (src / "a.txt").write_text("one")
    assert engine.run_backup(cfg, now=clock(0))["ok"]
    (src / "a.txt").unlink()
    (src / "sub").mkdir()
    st = engine.run_backup(cfg, now=clock(60))
    assert st["ok"] is False and st["jobs"][0]["rc"] == 2
    assert (tmp_path / "dest" / "a.txt").read_text() == "one"
    assert stamps(tmp_path / "old") == []
    assert "refusing to sync" in (engine.state_dir() / "run.log").read_text()
