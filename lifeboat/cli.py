"""Command line entry point. `lifeboat` with no arguments opens the app."""
from __future__ import annotations

import argparse
import json
import sys

from lifeboat import __version__, engine, timer
from lifeboat.config import Config, ConfigError, load_config


def _load() -> Config:
    return load_config()


def cmd_run(a) -> int:
    cfg = _load()
    if a.settings_only:
        return 0 if engine.run_settings(cfg, emit=print) else 1
    st = engine.run_backup(cfg, dry_run=a.dry_run, emit=print, settings=not a.no_settings)
    snap = engine.read_json(engine.state_dir() / "settings.json")
    settings_ok = a.dry_run or a.no_settings or not cfg.settings_command or bool(snap and snap.get("ok"))
    return 0 if st["ok"] and settings_ok else 1


def cmd_prune(_a) -> int:
    st = engine.prune(_load())
    for r in st["removed"]:
        print("pruned", r)
    print(f"prune ok={st['ok']} (cutoff {st['cutoff']})")
    return 0 if st["ok"] else 1


def cmd_status(a) -> int:
    cfg = _load()
    st = engine.status(cfg)
    if a.json:
        print(json.dumps(st, indent=2, default=str))
        return 0
    print(f"{st['verdict']['title']}: {st['verdict']['weakest']}")
    last = st["last"]
    print("last run:", f"{last['time']} ({'ok' if last['ok'] else 'FAILED'}{', dry run' if last['dry_run'] else ''})"
          if last else "never")
    for j in st["jobs"]:
        res = "not run" if j["rc"] is None else "ok" if j["rc"] == 0 else f"failed (rc {j['rc']})"
        print(f"  {j['name']}: {j['source']} -> {j['dest']}  [{res}]")
    return 0


def cmd_check(a) -> int:
    cfg = _load()
    probs = engine.problems(cfg, engine.status(cfg))
    if a.dry_run:
        for p in probs:
            print(f"[{p['page']}] {p['title']}: {p['body']}{' (high priority)' if p['urgent'] else ''}")
        if not probs:
            print("No problems; no notification would be sent.")
    elif probs:
        from lifeboat.app.notify import send
        send(probs)
    return 0


def cmd_versions(a) -> int:
    cfg = _load()
    cfg.job(a.job)
    for stamp in engine.list_versions(cfg, a.job):
        print(stamp)
    return 0


def cmd_restore(a) -> int:
    rc = engine.restore(_load(), a.job, a.to, version=a.version, dry_run=a.dry_run, emit=print)
    print(f"restore rc={rc}")
    return 0 if rc == 0 else 1


def cmd_install_timer(_a) -> int:
    return timer.install(_load())


def cmd_uninstall_timer(_a) -> int:
    return timer.uninstall()


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="lifeboat", description="Back up this laptop with rclone and see its status. "
                                "With no command, opens the app.")
    p.add_argument("--version", action="version", version=f"lifeboat {__version__}")
    sub = p.add_subparsers(dest="cmd", metavar="command")
    s = sub.add_parser("run", help="run the settings command and every backup job")
    s.add_argument("--dry-run", action="store_true", help="ask rclone what it would do; change nothing")
    s.add_argument("--settings-only", action="store_true", help="only run the [settings] command")
    s.add_argument("--no-settings", action="store_true", help="skip the [settings] command, run only the jobs")
    s.set_defaults(fn=cmd_run)
    sub.add_parser("prune", help="delete old versions past keep_days").set_defaults(fn=cmd_prune)
    s = sub.add_parser("status", help="show the current state")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_status)
    s = sub.add_parser("check", help="notify about problems (used by the timer)")
    s.add_argument("--dry-run", action="store_true", help="print instead of notifying")
    s.set_defaults(fn=cmd_check)
    s = sub.add_parser("versions", help="list old versions of a job")
    s.add_argument("job")
    s.set_defaults(fn=cmd_versions)
    s = sub.add_parser("restore", help="copy a job's files into a folder")
    s.add_argument("job")
    s.add_argument("--to", required=True, metavar="DIR", help="target folder")
    s.add_argument("--version", metavar="STAMP", help="old version, e.g. 2026-01-31_130000 (default: current backup)")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(fn=cmd_restore)
    sub.add_parser("install-timer", help="install the daily backup and weekly prune systemd timers").set_defaults(fn=cmd_install_timer)
    sub.add_parser("uninstall-timer", help="remove the systemd timers").set_defaults(fn=cmd_uninstall_timer)
    return p


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # debug aliases from the first version
    if argv[:1] == ["--dump"]:
        argv = ["status", "--json"]
    elif argv[:1] == ["--check"]:
        argv = ["check"] + argv[1:]
    if not argv or argv[0].startswith("--gapplication"):
        from lifeboat.app import main as gui
        return gui(argv)
    a = parser().parse_args(argv)
    if not a.cmd:
        from lifeboat.app import main as gui
        return gui([])
    try:
        return a.fn(a)
    except ConfigError as e:
        print(f"lifeboat: {e}", file=sys.stderr)
        return 2
