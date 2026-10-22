import json

from lifeboat import cli, engine


def write_cfg(env, make_cfg):
    cfg = make_cfg()
    j = cfg.jobs[0]
    cfg.path.parent.mkdir(parents=True)
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
