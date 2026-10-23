import json
import sys
import types

import pytest

from lifeboat import cli, engine
from lifeboat.config import load_config


def write_cfg(env, make_cfg):
    cfg = make_cfg()
    j = cfg.jobs[0]
    cfg.path.parent.mkdir(parents=True, exist_ok=True)
    cfg.path.write_text(
        f'[[jobs]]\nname = "docs"\nsource = "{j.source}"\ndest = "{j.dest}"\nold = "{j.old}"\n')
    return cfg


def test_run_status_versions_restore_via_cli(env, make_cfg, capsys):
    write_cfg(env, make_cfg)
    (env / "src" / "f").write_text("1")
    assert cli.main(["run", "--dry-run"]) == 0
    assert cli.main(["run"]) == 0
    capsys.readouterr()
    assert cli.main(["status", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["status"]["backups"] == "ok"
    assert cli.main(["check", "--dry-run"]) == 0
    assert "No problems" in capsys.readouterr().out
    assert cli.main(["versions", "docs"]) == 0
    assert cli.main(["restore", "docs", "--to", str(env / "out")]) == 0
    assert (env / "out" / "f").read_text() == "1"
    assert cli.main(["prune"]) == 0


def test_status_says_an_empty_folder_was_skipped(env, make_cfg, capsys):
    write_cfg(env, make_cfg)
    assert cli.main(["run"]) == 0
    capsys.readouterr()
    assert cli.main(["status"]) == 0
    assert "[empty, nothing to back up yet]" in capsys.readouterr().out


def test_missing_config_exits_2(env, capsys):
    assert cli.main(["status"]) == 2
    assert "no config file" in capsys.readouterr().err


def test_state_dir_follows_xdg(env):
    assert engine.state_dir() == env / "state" / "lifeboat"


def test_no_settings_skips_the_settings_command(env, make_cfg):
    cfg = write_cfg(env, make_cfg)
    marker = env / "settings-ran"
    cfg.path.write_text(f'[settings]\ncommand = "touch {marker}"\n\n' + cfg.path.read_text())
    (env / "src" / "f").write_text("1")
    assert cli.main(["run", "--no-settings"]) == 0
    assert not marker.exists()
    assert cli.main(["run"]) == 0
    assert marker.exists()


def test_run_json_stdout_is_one_document(env, make_cfg, capsys):
    write_cfg(env, make_cfg)
    (env / "src" / "f").write_text("1")
    assert cli.main(["run", "--json"]) == 0
    cap = capsys.readouterr()
    doc = json.loads(cap.out)
    assert doc["ok"] is True and doc["settings_ok"] is True and doc["jobs"][0]["name"] == "docs"
    assert "Docs  done 1 file" in cap.err and "rc=" not in cap.out.replace('"rc"', "")


def test_versions_json(env, make_cfg, capsys):
    write_cfg(env, make_cfg)
    assert cli.main(["versions", "docs", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"job": "docs", "versions": []}


def test_check_json_never_notifies(env, make_cfg, capsys, monkeypatch):
    # A stand-in module: CI has no PyGObject, and importing the real one would need it.
    fake = types.ModuleType("lifeboat.app.notify")
    fake.send = lambda probs: pytest.fail("notified")
    monkeypatch.setitem(sys.modules, "lifeboat.app.notify", fake)
    write_cfg(env, make_cfg)
    assert cli.main(["check", "--json"]) == 1
    probs = json.loads(capsys.readouterr().out)["problems"]
    assert probs and probs[0]["title"] == "Backup needs attention"


def test_restore_json(env, make_cfg, capsys):
    write_cfg(env, make_cfg)
    (env / "src" / "f").write_text("1")
    assert cli.main(["run"]) == 0
    capsys.readouterr()
    assert cli.main(["restore", "docs", "--to", str(env / "out"), "--dry-run", "--json"]) == 0
    cap = capsys.readouterr()
    assert json.loads(cap.out) == {"ok": True, "rc": 0, "job": "docs", "to": str(env / "out"), "version": None, "dry_run": True}
    assert not (env / "out" / "f").exists()


def test_prune_json(env, make_cfg, capsys):
    write_cfg(env, make_cfg)
    assert cli.main(["prune", "--json"]) == 0
    cap = capsys.readouterr()
    doc = json.loads(cap.out)
    assert doc["ok"] is True and doc["removed"] == [] and "cutoff" in doc


def test_jobs_json_and_text(env, make_cfg, capsys):
    cfg = write_cfg(env, make_cfg)
    assert cli.main(["jobs", "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["config_path"] == str(cfg.path)
    assert doc["jobs"][0] == {"name": "docs", "source": cfg.jobs[0].source, "dest": cfg.jobs[0].dest, "old": cfg.jobs[0].old,
                              "exclude": [], "include": [], "filter_file": "", "encrypted": False}
    assert cli.main(["jobs"]) == 0
    assert f"docs: {cfg.jobs[0].source} -> {cfg.jobs[0].dest}" in capsys.readouterr().out


def test_remotes_works_without_a_config_file(env, capsys):
    assert cli.main(["remotes", "--json"]) == 0
    remotes = json.loads(capsys.readouterr().out)["remotes"]
    assert {"name": "localdrv:", "type": "local"} in remotes and {"name": "vault:", "type": "crypt"} in remotes
    assert cli.main(["remotes"]) == 0
    assert "vault:" in capsys.readouterr().out


def test_config_command_missing_invalid_valid(env, make_cfg, capsys):
    path = env / "config" / "lifeboat" / "config.toml"
    assert cli.main(["config", "--json"]) == 2
    doc = json.loads(capsys.readouterr().out)
    assert doc["exists"] is False and doc["ok"] is False and "no config file" in doc["error"] and doc["jobs"] == 0
    path.parent.mkdir(parents=True)
    path.write_text("jobs = [")
    assert cli.main(["config", "--json"]) == 2
    doc = json.loads(capsys.readouterr().out)
    assert doc["exists"] is True and doc["ok"] is False and "not valid TOML" in doc["error"]
    write_cfg(env, make_cfg)
    assert cli.main(["config", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"path": str(path), "exists": True, "ok": True, "error": None, "jobs": 1}
    assert cli.main(["config"]) == 0
    assert str(path) in capsys.readouterr().out


def test_init_creates_then_refuses(env, capsys):
    path = env / "config" / "lifeboat" / "config.toml"
    assert cli.main(["init"]) == 0
    assert path.exists() and "[[jobs]]" in path.read_text()
    capsys.readouterr()
    assert cli.main(["init"]) == 2
    assert "already exists" in capsys.readouterr().err


def test_add_job_appends_and_keeps_everything_else(env, make_cfg, capsys):
    cfg = write_cfg(env, make_cfg)
    cfg.path.write_text("# keep me\n" + cfg.path.read_text())
    (env / "notes").mkdir()
    before = cfg.path.read_text()
    assert cli.main(["add-job", "notes", str(env / "notes"), "localdrv:Backup/notes", "--exclude", "*.tmp", "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["added"]["old"] == "localdrv:Backup/_old" and doc["added"]["exclude"] == ["*.tmp"]
    assert cfg.path.read_text().startswith(before) and "# keep me" in cfg.path.read_text()
    assert cfg.path.with_name("config.toml.bak").read_text() == before
    assert [j.name for j in load_config(cfg.path).jobs] == ["docs", "notes"]


def test_add_job_keeps_tilde_and_honours_old_flag(env, make_cfg, capsys, monkeypatch):
    cfg = write_cfg(env, make_cfg)
    monkeypatch.setenv("HOME", str(env))
    (env / "stuff").mkdir()
    assert cli.main(["add-job", "stuff", "~/stuff", "vault:stuff", "--old", "vault:versions", "--encrypted"]) == 0
    job = load_config(cfg.path).jobs[1]
    assert (job.source, job.old, job.encrypted) == ("~/stuff", "vault:versions", True)
    assert "lifeboat run --dry-run" in capsys.readouterr().out


@pytest.mark.parametrize("args,msg", [
    (["x", "{env}/missing", "localdrv:x"], "does not exist"),
    (["x", "{env}/src", "nosuchremote:x"], "nosuchremote:.*localdrv:"),
    (["docs", "{env}/src", "localdrv:x"], "used twice"),
    (["bad/name", "{env}/src", "localdrv:x"], "name may only use"),
])
def test_add_job_refusals_change_nothing(env, make_cfg, capsys, args, msg):
    import re
    cfg = write_cfg(env, make_cfg)
    before = cfg.path.read_text()
    assert cli.main(["add-job", *[a.replace("{env}", str(env)) for a in args]]) == 2
    assert re.search(msg, capsys.readouterr().err)
    assert cfg.path.read_text() == before and not cfg.path.with_name("config.toml.bak").exists()


def test_add_job_dry_run_writes_nothing(env, make_cfg, capsys):
    cfg = write_cfg(env, make_cfg)
    before = cfg.path.read_text()
    assert cli.main(["add-job", "notes", str(env / "src"), "localdrv:n", "--dry-run"]) == 0
    assert '[[jobs]]\nname = "notes"' in capsys.readouterr().out
    assert cli.main(["add-job", "notes", str(env / "src"), "localdrv:n", "--dry-run", "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["added"]["name"] == "notes" and doc["config_path"] == str(cfg.path)
    assert cfg.path.read_text() == before and not cfg.path.with_name("config.toml.bak").exists()


def test_remove_job(env, make_cfg, capsys):
    cfg = write_cfg(env, make_cfg)
    text = cfg.path.read_text()
    cfg.path.write_text(text + '\n# [[jobs]]\n# name = "ghost"\n\n[[jobs]]\nname = "b"\nsource = "~/b"\ndest = "r:b"\nold = "r:_old"\n')
    assert cli.main(["remove-job", "b", "--dry-run"]) == 0
    assert len(load_config(cfg.path).jobs) == 2
    capsys.readouterr()
    assert cli.main(["remove-job", "b", "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["removed"] == "b" and doc["config_path"] == str(cfg.path)
    assert [j.name for j in load_config(cfg.path).jobs] == ["docs"] and "# name" in cfg.path.read_text()
    assert cfg.path.with_name("config.toml.bak").exists()


def test_remove_job_refusals(env, make_cfg, capsys):
    cfg = write_cfg(env, make_cfg)
    cfg.path.write_text(cfg.path.read_text() + '\n# [[jobs]]\n# name = "ghost"\n')
    assert cli.main(["remove-job", "docs"]) == 2
    assert "only job" in capsys.readouterr().err
    assert cli.main(["remove-job", "ghost"]) == 2
    assert "no job named" in capsys.readouterr().err
    assert len(load_config(cfg.path).jobs) == 1


def test_add_job_refuses_a_non_empty_dest_unless_existing_dest(env, make_cfg, capsys):
    cfg = write_cfg(env, make_cfg)
    (env / "full").mkdir()
    (env / "full" / "unrelated.txt").write_text("x")
    args = ["add-job", "n", str(env / "src"), f"localdrv:{env}/full"]
    before = cfg.path.read_text()
    assert cli.main(args) == 2
    err = capsys.readouterr().err
    assert "not empty" in err and "--existing-dest" in err and "sync" in err
    assert cfg.path.read_text() == before
    assert cli.main([*args, "--existing-dest"]) == 0
    assert [j.name for j in load_config(cfg.path).jobs] == ["docs", "n"]


def test_add_job_accepts_an_empty_existing_dest_folder(env, make_cfg):
    cfg = write_cfg(env, make_cfg)
    (env / "empty").mkdir()
    assert cli.main(["add-job", "n", str(env / "src"), f"localdrv:{env}/empty"]) == 0
    assert len(load_config(cfg.path).jobs) == 2


def test_config_command_never_raises(env, capsys):
    path = env / "config" / "lifeboat" / "config.toml"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"\xff\xfe\x00bad")
    assert cli.main(["config", "--json"]) == 2
    doc = json.loads(capsys.readouterr().out)
    assert doc["ok"] is False and doc["error"]
    path.unlink()
    path.mkdir()  # a directory where the file should be: OSError
    assert cli.main(["config", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["ok"] is False


def test_check_json_exit_code_follows_problems(env, make_cfg, capsys):
    write_cfg(env, make_cfg)
    assert cli.main(["check", "--json"]) == 1
    capsys.readouterr()
    (env / "src" / "f").write_text("1")
    assert cli.main(["run"]) == 0
    capsys.readouterr()
    assert cli.main(["check", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"problems": []}


def test_init_guidance_says_add_first_and_placeholders(env, capsys):
    assert cli.main(["init"]) == 0
    out = capsys.readouterr().out
    assert out.index("add-job") < out.index("remove-job") and "placeholder" in out


def test_run_events_are_json_lines_on_stdout_and_logs_on_stderr(env, make_cfg, capsys):
    write_cfg(env, make_cfg)
    (env / "src" / "f").write_text("1")
    assert cli.main(["run", "--events"]) == 0
    cap = capsys.readouterr()
    events = [json.loads(ln) for ln in cap.out.splitlines()]
    assert [e["event"] for e in events] == ["job_start", "job_end", "done"]
    assert events[1]["files"] == 1 and events[2] == {"event": "done", "ok": True}
    assert "Docs  done 1 file" in cap.err and "copied f (new" in cap.err


def test_run_events_done_is_false_when_a_job_fails(env, make_cfg, capsys):
    write_cfg(env, make_cfg)
    (env / "src").rmdir()
    assert cli.main(["run", "--events"]) == 1
    events = [json.loads(ln) for ln in capsys.readouterr().out.splitlines()]
    assert events[1]["rc"] == 2 and "refusing to sync" in events[1]["error"] and events[-1] == {"event": "done", "ok": False}
