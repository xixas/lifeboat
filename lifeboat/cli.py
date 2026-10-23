"""Command line entry point. `lifeboat` with no arguments opens the app."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from dataclasses import asdict

from lifeboat import __version__, engine, timer
from lifeboat.config import (
    Config,
    ConfigError,
    Job,
    add_job_text,
    config_path,
    default_old,
    example_config_path,
    load_config,
    remove_job_text,
    render_job_block,
    write_config_text,
)


def _load() -> Config:
    return load_config()


def _emit(a):
    """Where human and rclone output goes: stdout, or stderr when stdout carries JSON."""
    return (lambda line: print(line, file=sys.stderr)) if a.json else print


def _dump(doc) -> None:
    print(json.dumps(doc, indent=2, default=str))


def cmd_run(a) -> int:
    cfg = _load()
    if a.settings_only:
        ok = engine.run_settings(cfg, emit=_emit(a))
        if a.json:
            _dump({"settings_ok": ok})
        return 0 if ok else 1
    st = engine.run_backup(cfg, dry_run=a.dry_run, emit=_emit(a), settings=not a.no_settings)
    snap = engine.read_json(engine.state_dir() / "settings.json")
    settings_ok = a.dry_run or a.no_settings or not cfg.settings_command or bool(snap and snap.get("ok"))
    if a.json:
        _dump({**st, "settings_ok": bool(settings_ok)})
    return 0 if st["ok"] and settings_ok else 1


def cmd_prune(a) -> int:
    st = engine.prune(_load())
    if a.json:
        _dump(st)
        return 0 if st["ok"] else 1
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
        res = ("not run" if j["rc"] is None else "empty, nothing to back up yet" if j["skipped"]
               else "ok" if j["rc"] == 0 else f"failed (rc {j['rc']})")
        print(f"  {j['name']}: {j['source']} -> {j['dest']}  [{res}]")
    return 0


def cmd_check(a) -> int:
    cfg = _load()
    probs = engine.problems(cfg, engine.status(cfg))
    if a.json:
        _dump({"problems": probs})
        return 1 if probs else 0
    elif a.dry_run:
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
    versions = engine.list_versions(cfg, a.job)
    if a.json:
        _dump({"job": a.job, "versions": versions})
    else:
        for stamp in versions:
            print(stamp)
    return 0


def cmd_restore(a) -> int:
    rc = engine.restore(_load(), a.job, a.to, version=a.version, dry_run=a.dry_run, emit=_emit(a))
    if a.json:
        _dump({"ok": rc == 0, "rc": rc, "job": a.job, "to": a.to, "version": a.version, "dry_run": a.dry_run})
    else:
        print(f"restore rc={rc}")
    return 0 if rc == 0 else 1


def cmd_jobs(a) -> int:
    cfg = _load()
    if a.json:
        _dump({"config_path": str(cfg.path), "jobs": [asdict(j) for j in cfg.jobs]})
        return 0
    for j in cfg.jobs:
        print(f"{j.name}: {j.source} -> {j.dest}{'  [encrypted]' if j.encrypted else ''}")
    return 0


def cmd_remotes(a) -> int:
    path = config_path()
    cfg = _load() if path.exists() else Config(jobs=[])  # no config yet: rclone defaults
    remotes = engine.list_remotes(cfg)
    if a.json:
        _dump({"remotes": remotes})
    else:
        for r in remotes:
            print(f"{r['name']} {r['type']}")
        if not remotes:
            print("No rclone remotes yet. Create one with: rclone config")
    return 0


def cmd_config(a) -> int:
    path = config_path()
    error, jobs = None, 0
    try:
        jobs = len(load_config(path).jobs)
    except ConfigError as e:
        error = str(e)
    except Exception as e:  # unreadable, wrong encoding, a directory...: report, never raise
        error = f"{path} could not be read: {e}"
    doc = {"path": str(path), "exists": path.exists(), "ok": error is None, "error": error, "jobs": jobs}
    if a.json:
        _dump(doc)
    else:
        print(f"{path}: {'valid, ' + str(jobs) + ' job(s)' if error is None else 'PROBLEM: ' + error}")
    return 0 if error is None else 2


def cmd_init(_a) -> int:
    path = config_path()
    if path.exists():
        raise ConfigError(f"{path} already exists; edit it, or use add-job / remove-job")
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(example_config_path(), path)
    print(f"Wrote an example config to {path}.")
    print("The example job and its remote names (gdrive:...) are placeholders. Do not run a backup yet.")
    print("Next: `lifeboat add-job NAME SOURCE DEST` for your own folder, then `lifeboat remove-job documents`")
    print("(remove-job refuses to remove the last job, so add yours first).")
    return 0


def _check_remote(cfg: Config, dest: str) -> None:
    """Refuse a dest whose rclone remote does not exist (skipped when rclone is missing or dest is a local path)."""
    name, colon, _ = dest.partition(":")
    if not colon or not name or not shutil.which("rclone"):
        return
    names = [r["name"] for r in engine.list_remotes(cfg)]
    if name + ":" not in names:
        raise ConfigError(f"dest: rclone has no remote named {name + ':'!r} (have: {', '.join(names) or 'none'}). "
                          "See `lifeboat remotes`, or create one with `rclone config`")


def _check_dest_empty(cfg: Config, dest: str) -> None:
    """`run` is a sync: it makes DEST mirror SOURCE and moves anything else there to the old folder (pruned later)."""
    if not shutil.which("rclone"):
        return
    rc, out = engine.rclone(cfg, ["lsf", "--max-depth", "1", dest], timeout=60)
    if rc == 3 or (rc == 0 and not out.strip()):
        return
    if rc != 0:
        raise ConfigError(f"dest: could not look inside {dest} (rclone rc {rc}); is the remote reachable?")
    raise ConfigError(f"dest: {dest} is not empty. `lifeboat run` syncs: it makes DEST mirror SOURCE and moves other files "
                      "there to the old folder, where `prune` later deletes them. Use a new, empty folder, or pass "
                      "--existing-dest if DEST holds this job's own earlier Lifeboat backup")


def _edit(a, cfg: Config, new_text: str, doc: dict, human: list[str]) -> int:
    if not a.dry_run:
        write_config_text(cfg.path, new_text)
    if a.json:
        _dump({**doc, "config_path": str(cfg.path), "dry_run": a.dry_run})
    else:
        print(("Would change " if a.dry_run else "Changed ") + str(cfg.path))
        print("\n".join(human))
    return 0


def cmd_add_job(a) -> int:
    cfg = _load()
    if not os.path.exists(os.path.expanduser(a.source)):
        raise ConfigError(f"source: {a.source} does not exist")
    _check_remote(cfg, a.dest)
    if not a.existing_dest:
        _check_dest_empty(cfg, a.dest)
    job = Job(a.name, a.source, a.dest, a.old or default_old(a.dest), a.exclude, a.include, a.filter_file or "", a.encrypted)
    new_text = add_job_text(cfg.path.read_text(encoding="utf-8"), job)
    human = [render_job_block(job).rstrip(), "No data has been copied yet. Next: lifeboat run --dry-run"]
    return _edit(a, cfg, new_text, {"added": asdict(job)}, human)


def cmd_remove_job(a) -> int:
    cfg = _load()
    job = cfg.job(a.name)
    new_text = remove_job_text(cfg.path.read_text(encoding="utf-8"), a.name)
    human = [f"Removed job {a.name!r}. Remote data is untouched: the backup copy stays at {job.dest} and old versions at "
             f"{job.old}/<timestamp>/{job.name} until you delete them by hand."]
    return _edit(a, cfg, new_text, {"removed": a.name}, human)


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
    s.add_argument("--json", action="store_true", help="print the result as JSON on stdout (other output goes to stderr)")
    s.set_defaults(fn=cmd_run)
    s = sub.add_parser("prune", help="delete old versions past keep_days")
    s.add_argument("--json", action="store_true", help="print the result as JSON")
    s.set_defaults(fn=cmd_prune)
    s = sub.add_parser("status", help="show the current state")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_status)
    s = sub.add_parser("check", help="notify about problems (used by the timer)")
    s.add_argument("--dry-run", action="store_true", help="print instead of notifying")
    s.add_argument("--json", action="store_true", help="print the problems as JSON; never notifies")
    s.set_defaults(fn=cmd_check)
    s = sub.add_parser("versions", help="list old versions of a job")
    s.add_argument("job")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_versions)
    s = sub.add_parser("restore", help="copy a job's files into a folder")
    s.add_argument("job")
    s.add_argument("--to", required=True, metavar="DIR", help="target folder")
    s.add_argument("--version", metavar="STAMP", help="old version, e.g. 2026-01-31_130000 (default: current backup)")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--json", action="store_true", help="print the result as JSON on stdout (other output goes to stderr)")
    s.set_defaults(fn=cmd_restore)
    s = sub.add_parser("jobs", help="list the configured backup jobs")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_jobs)
    s = sub.add_parser("remotes", help="list rclone remotes and their types")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_remotes)
    s = sub.add_parser("config", help="check the config file (exit 2 if missing or invalid)")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_config)
    sub.add_parser("init", help="write an example config if none exists").set_defaults(fn=cmd_init)
    s = sub.add_parser("add-job", help="add a backup job to the config (no data is copied)")
    s.add_argument("name")
    s.add_argument("source", metavar="SOURCE", help="local folder to back up (stored as typed, so ~ is kept)")
    s.add_argument("dest", metavar="DEST", help="rclone remote:path to copy it to")
    s.add_argument("--old", metavar="OLD", help="where replaced files go (default: _old next to DEST)")
    s.add_argument("--exclude", action="append", default=[], metavar="PAT", help="rclone exclude pattern (repeatable)")
    s.add_argument("--include", action="append", default=[], metavar="PAT", help="rclone include pattern (repeatable)")
    s.add_argument("--filter-file", metavar="FILE", help="rclone --filter-from file")
    s.add_argument("--encrypted", action="store_true", help="mark the job as living on a crypt remote (display only)")
    s.add_argument("--existing-dest", action="store_true", help="DEST already holds this job's own earlier backup")
    s.add_argument("--dry-run", action="store_true", help="show the block; change nothing")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_add_job)
    s = sub.add_parser("remove-job", help="remove a job from the config (remote data is kept)")
    s.add_argument("name")
    s.add_argument("--dry-run", action="store_true", help="validate and report; change nothing")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_remove_job)
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
