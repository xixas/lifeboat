import json
import os
import subprocess
import time
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


def test_restore_keep_newer_never_overwrites_a_newer_local_file(tmp_path, make_cfg, clock):
    cfg = make_cfg()
    (tmp_path / "src" / "f.txt").write_text("backed up")
    (tmp_path / "src" / "g.txt").write_text("backed up")
    engine.run_backup(cfg, now=clock())
    target = tmp_path / "target"
    target.mkdir()
    (target / "f.txt").write_text("edited after the backup")
    os.utime(target / "f.txt", (time.time() + 3600, time.time() + 3600))
    assert engine.restore(cfg, "docs", target, keep_newer=True) == 0
    assert read(target / "f.txt") == "edited after the backup"
    assert read(target / "g.txt") == "backed up"  # missing files still come back
    assert engine.restore(cfg, "docs", target) == 0  # default is unchanged: the backup wins
    assert read(target / "f.txt") == "backed up"


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


def test_empty_source_with_missing_dest_is_skipped_ok(tmp_path, make_cfg, clock):
    cfg = make_cfg()
    st = engine.run_backup(cfg, now=clock())
    assert st["ok"] and st["jobs"][0]["rc"] == 0 and st["jobs"][0]["skipped"] is True
    log = read(engine.state_dir() / "run.log")
    assert "docs: skipped — folder is empty, nothing to back up yet" in log and "docs rc=0" in log


def test_a_skipped_job_restores_cleanly_and_skips_again(tmp_path, make_cfg, clock):
    cfg = make_cfg()
    engine.run_backup(cfg, now=clock())
    assert engine.restore(cfg, cfg.jobs[0], tmp_path / "out") == 0
    st = engine.run_backup(cfg, now=clock(60))
    assert st["ok"] and st["jobs"][0]["skipped"] is True


def test_a_failed_mkdir_is_a_failure_not_a_skip(tmp_path, make_cfg, clock, monkeypatch):
    cfg = make_cfg()
    real = engine.rclone
    monkeypatch.setattr(engine, "rclone", lambda c, args, **kw: (1, "") if args[0] == "mkdir" else real(c, args, **kw))
    st = engine.run_backup(cfg, now=clock())
    assert st["ok"] is False and st["jobs"][0]["rc"] == 1 and st["jobs"][0]["skipped"] is False
    assert "could not create the empty backup folder" in read(engine.state_dir() / "run.log")


def test_a_dry_run_skip_creates_nothing(tmp_path, make_cfg, clock):
    cfg = make_cfg()
    st = engine.run_backup(cfg, dry_run=True, now=clock())
    assert st["jobs"][0]["skipped"] is True
    assert engine.restore(cfg, cfg.jobs[0], tmp_path / "out") != 0


def test_empty_source_is_refused_when_dest_listing_fails(tmp_path, make_cfg, clock, monkeypatch):
    cfg = make_cfg()
    real = engine.rclone
    monkeypatch.setattr(engine, "rclone", lambda c, args, **kw: (1, "") if args[0] == "lsf" else real(c, args, **kw))
    st = engine.run_backup(cfg, now=clock())
    assert st["ok"] is False and st["jobs"][0]["rc"] == 2 and st["jobs"][0]["skipped"] is False
    assert "refusing to sync" in read(engine.state_dir() / "run.log")


def test_missing_source_is_refused_without_listing_dest(tmp_path, make_cfg, clock, monkeypatch):
    cfg = make_cfg()
    (tmp_path / "src").rmdir()
    calls = []
    real = engine.rclone
    monkeypatch.setattr(engine, "rclone", lambda c, args, **kw: calls.append(args) or real(c, args, **kw))
    st = engine.run_backup(cfg, now=clock())
    assert st["ok"] is False and st["jobs"][0]["rc"] == 2
    assert not [a for a in calls if a[0] == "lsf"]


def test_status_exposes_skipped_per_job(tmp_path, make_cfg, clock):
    cfg = make_cfg()
    engine.run_backup(cfg, now=clock())
    assert engine.status(cfg)["jobs"][0]["skipped"] is True
    (tmp_path / "src" / "a.txt").write_text("one")
    engine.run_backup(cfg, now=clock(60))
    assert engine.status(cfg)["jobs"][0]["skipped"] is False


# ---------------------------------------------------------------- device manifest and probe
def device(tmp_path, name="thinkpad", probe=False, manifest=None):
    d = {"name": name, "manifest": manifest or f"localdrv:{tmp_path}/drive/{name}/manifest.json"}
    if probe:
        d["probe"] = f"vault:{name}/.lifeboat-probe"
    return d


def test_manifest_is_uploaded_with_documented_keys_and_no_secrets(tmp_path, make_cfg, clock):
    cfg = make_cfg(device=device(tmp_path), rclone={"password_command": "echo hunter2-pw"},
                   settings={"command": "echo hunter2-settings"})
    (tmp_path / "src" / "a.txt").write_text("one")
    st = engine.run_backup(cfg, now=clock())
    raw = (tmp_path / "drive" / "thinkpad" / "manifest.json").read_text()
    doc = json.loads(raw)
    assert set(doc) == {"format", "device", "host", "lifeboat", "time", "ok", "jobs"}
    assert doc["format"] == 1 and doc["device"] == "thinkpad" and doc["time"] == st["time"] and doc["ok"] is True
    assert doc["jobs"] == [{"name": "docs", "source": str(tmp_path / "src"), "dest": f"localdrv:{tmp_path}/dest",
                            "old": f"localdrv:{tmp_path}/old", "encrypted": False}]
    assert "hunter2" not in raw and "password_command" not in raw
    assert st["manifest"] == {"ok": True, "rc": 0}


def test_dry_run_uploads_no_manifest(tmp_path, make_cfg, clock):
    cfg = make_cfg(device=device(tmp_path))
    (tmp_path / "src" / "a.txt").write_text("one")
    st = engine.run_backup(cfg, dry_run=True, now=clock())
    assert "manifest" not in st and not (tmp_path / "drive").exists()


def test_no_device_table_uploads_nothing(tmp_path, make_cfg, clock):
    cfg = make_cfg()
    (tmp_path / "src" / "a.txt").write_text("one")
    st = engine.run_backup(cfg, now=clock())
    assert st["ok"] and "manifest" not in st and "probe" not in st
    assert sorted(p.name for p in tmp_path.iterdir() if p.name in ("drive", "crypt")) == []


def test_manifest_failure_keeps_job_rc_but_fails_the_run_and_is_a_problem(tmp_path, make_cfg, clock):
    cfg = make_cfg(device=device(tmp_path, manifest="nosuchremote:thinkpad/manifest.json"))
    (tmp_path / "src" / "a.txt").write_text("one")
    st = engine.run_backup(cfg, now=clock())
    assert st["jobs"][0]["rc"] == 0 and st["ok"] is False
    assert st["manifest"]["ok"] is False and st["manifest"]["rc"] != 0
    assert "manifest rc=" in (engine.state_dir() / "run.log").read_text()
    s = engine.status(cfg)
    assert s["manifest"] == st["manifest"]
    assert any("new laptop" in p["body"] for p in engine.problems(cfg, s))


def test_probe_is_written_once_into_the_crypt_remote(tmp_path, make_cfg, clock):
    cfg = make_cfg(device=device(tmp_path, probe=True))
    (tmp_path / "src" / "a.txt").write_text("one")
    st = engine.run_backup(cfg, now=clock(0))
    assert st["probe"] == {"ok": True, "rc": 0} and st["ok"]
    files = [p for p in (tmp_path / "crypt").rglob("*") if p.is_file()]
    assert len(files) == 1 and files[0].read_bytes() != b"lifeboat-probe 1\n"  # encrypted at rest
    before = (files[0].stat().st_mtime_ns, files[0].read_bytes())
    st = engine.run_backup(cfg, now=clock(60))
    assert st["probe"]["ok"]
    assert (files[0].stat().st_mtime_ns, files[0].read_bytes()) == before
    assert engine.rclone(cfg, ["cat", cfg.probe]) == (0, "lifeboat-probe 1\n")


def test_check_probe_true_with_right_password_false_with_wrong(tmp_path, make_cfg, clock, monkeypatch):
    cfg = make_cfg(device=device(tmp_path, probe=True))
    (tmp_path / "src" / "a.txt").write_text("one")
    engine.run_backup(cfg, now=clock())
    bad = subprocess.run(["rclone", "obscure", "wrong-password"], capture_output=True, text=True, check=True).stdout.strip()
    monkeypatch.setenv("RCLONE_CONFIG_BADVAULT_TYPE", "crypt")
    monkeypatch.setenv("RCLONE_CONFIG_BADVAULT_REMOTE", f"localdrv:{tmp_path}/crypt")
    monkeypatch.setenv("RCLONE_CONFIG_BADVAULT_PASSWORD", bad)
    assert engine.check_probe("vault:thinkpad/.lifeboat-probe") is True
    assert engine.check_probe("badvault:thinkpad/.lifeboat-probe") is False
    assert engine.check_probe("vault:thinkpad/no-such-probe") is False


def test_two_devices_share_one_drive_root_without_clobbering(tmp_path, make_cfg, clock):
    def cfg_for(name):
        c = make_cfg(device=device(tmp_path, name=name))
        c.jobs[0].dest = f"localdrv:{tmp_path}/drive/{name}/docs"
        c.jobs[0].old = f"localdrv:{tmp_path}/drive/{name}/_old"
        c.jobs[0].source = str(tmp_path / f"src-{name}")
        (tmp_path / f"src-{name}").mkdir()
        (tmp_path / f"src-{name}" / f"{name}.txt").write_text(name)
        return c
    a, b = cfg_for("thinkpad"), cfg_for("macbook")
    assert engine.run_backup(a, now=clock(0))["ok"] and engine.run_backup(b, now=clock(60))["ok"]
    for name in ("thinkpad", "macbook"):
        doc = json.loads((tmp_path / "drive" / name / "manifest.json").read_text())
        assert doc["device"] == name and doc["ok"] is True and doc["jobs"][0]["dest"].endswith(f"{name}/docs")
        assert (tmp_path / "drive" / name / "docs" / f"{name}.txt").read_text() == name
    assert engine.run_backup(a, now=clock(120))["ok"]
    assert not (tmp_path / "drive" / "macbook" / "_old").exists()


# ---------------------------------------------------------------- retired jobs stay restorable
def seed_manifest(tmp_path, jobs, retired=None):
    p = tmp_path / "drive" / "thinkpad" / "manifest.json"
    p.parent.mkdir(parents=True)
    doc = {"format": 1, "device": "thinkpad", "jobs": jobs}
    if retired:
        doc["retired"] = retired
    p.write_text(json.dumps(doc))
    return p


def pics(tmp_path):
    return {"name": "pictures", "source": "~/Pictures", "dest": f"localdrv:{tmp_path}/drive/thinkpad/Pictures",
            "old": f"localdrv:{tmp_path}/old", "encrypted": False}


def test_a_job_dropped_from_the_config_is_carried_over_as_retired_once_and_keeps_its_since(tmp_path, make_cfg, clock):
    cfg = make_cfg(device=device(tmp_path))
    p = seed_manifest(tmp_path, [pics(tmp_path)])
    first = engine.run_backup(cfg, now=clock(0))
    doc = json.loads(p.read_text())
    assert [j["name"] for j in doc["jobs"]] == ["docs"]
    assert doc["retired"] == [{**pics(tmp_path), "since": first["time"]}]
    engine.run_backup(cfg, now=clock(60))
    doc2 = json.loads(p.read_text())
    assert doc2["retired"] == doc["retired"]  # carried over once, `since` preserved


def test_a_job_back_in_the_config_is_never_retired(tmp_path, make_cfg, clock):
    cfg = make_cfg(device=device(tmp_path))
    docs = {"name": "docs", "source": "~/x", "dest": "d", "old": "o", "encrypted": False}
    p = seed_manifest(tmp_path, [], retired=[{**docs, "since": "2026-01-01T00:00:00+00:00"}, {**pics(tmp_path), "since": "2026-01-01T00:00:00+00:00"}])
    engine.run_backup(cfg, now=clock())
    doc = json.loads(p.read_text())
    assert [j["name"] for j in doc["retired"]] == ["pictures"]


def test_no_retired_key_without_retired_jobs_and_an_unreadable_manifest_is_not_overwritten(tmp_path, make_cfg, clock):
    cfg = make_cfg(device=device(tmp_path))
    engine.run_backup(cfg, now=clock())
    assert "retired" not in json.loads((tmp_path / "drive" / "thinkpad" / "manifest.json").read_text())
    bad = make_cfg(device=device(tmp_path, manifest="nosuchremote:thinkpad/manifest.json"))
    assert engine.upload_manifest(bad, {"time": "t", "ok": True})["ok"] is False


def test_run_backup_reports_job_events_with_file_counts(tmp_path, make_cfg, clock):
    cfg = make_cfg()
    (tmp_path / "src" / "a.txt").write_text("one")
    (tmp_path / "src" / "b.txt").write_text("two")
    events = []
    engine.run_backup(cfg, now=clock(), on_event=events.append)
    assert events[0] == {"event": "job_start", "name": "docs"}
    assert events[1] == {"event": "job_end", "name": "docs", "rc": 0, "skipped": False, "files": 2, "error": "", "reason": ""}
    events.clear()
    engine.run_backup(cfg, now=clock(5), on_event=events.append)
    assert events[1]["files"] == 0


def test_job_end_carries_skip_and_error_text(tmp_path, make_cfg, clock, monkeypatch):
    cfg = make_cfg()
    events = []
    engine.run_backup(cfg, now=clock(), on_event=events.append)
    assert events[1]["skipped"] is True and events[1]["files"] == 0 and events[1]["error"] == ""
    events.clear()
    (tmp_path / "src").rmdir()
    engine.run_backup(cfg, now=clock(5), on_event=events.append)
    assert events[1]["rc"] == 2 and "refusing to sync" in events[1]["error"]
    events.clear()
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.txt").write_text("one")

    def broken(argv, env, log, emit=None):
        emit("ERROR : a.txt: Failed to copy: boom")
        return 1
    monkeypatch.setattr(engine, "stream", broken)
    engine.run_backup(cfg, now=clock(9), on_event=events.append)
    assert events[1]["rc"] == 1 and events[1]["error"] == "ERROR : a.txt: Failed to copy: boom"


def test_manifest_and_probe_are_one_finishing_step(tmp_path, make_cfg, clock):
    cfg = make_cfg(crypt=True, device=device(tmp_path, probe=True))
    (tmp_path / "src" / "a.txt").write_text("one")
    events = []
    engine.run_backup(cfg, now=clock(), on_event=events.append)
    assert [e["event"] for e in events] == ["job_start", "job_end", "finishing_start", "finishing_end"]
    assert events[3]["ok"] is True
    events.clear()
    engine.run_backup(cfg, dry_run=True, now=clock(5), on_event=events.append)
    assert [e["event"] for e in events] == ["job_start", "job_end"]


def _reason(cfg, clock, **kw):
    events = []
    engine.run_backup(cfg, now=clock(), on_event=events.append, **kw)
    return events[1]["reason"]


def test_job_end_reason_missing_folder(tmp_path, make_cfg, clock):
    cfg = make_cfg()
    (tmp_path / "src").rmdir()
    assert _reason(cfg, clock) == "missing"


def test_job_end_reason_empty_folder_with_a_backup(tmp_path, make_cfg, clock):
    cfg = make_cfg()
    (tmp_path / "dest").mkdir()
    (tmp_path / "dest" / "a.txt").write_text("one")
    assert _reason(cfg, clock) == "empty_with_backup"


def test_job_end_reason_unreadable_destination(tmp_path, make_cfg, clock, monkeypatch):
    cfg = make_cfg()
    real = engine.rclone
    monkeypatch.setattr(engine, "rclone", lambda c, args, **kw: (1, "") if args[0] == "lsf" else real(c, args, **kw))
    assert _reason(cfg, clock) == "dest_unreadable"


def test_job_end_reason_mkdir_failed(tmp_path, make_cfg, clock, monkeypatch):
    cfg = make_cfg()
    real = engine.rclone
    monkeypatch.setattr(engine, "rclone", lambda c, args, **kw: (1, "") if args[0] == "mkdir" else real(c, args, **kw))
    assert _reason(cfg, clock) == "mkdir_failed"


def test_job_end_reason_rclone_failure_and_success(tmp_path, make_cfg, clock, monkeypatch):
    cfg = make_cfg()
    (tmp_path / "src" / "a.txt").write_text("one")
    assert _reason(cfg, clock) == ""
    monkeypatch.setattr(engine, "stream", lambda argv, env, log, emit=None: 1)
    assert _reason(cfg, clock) == "rclone"
