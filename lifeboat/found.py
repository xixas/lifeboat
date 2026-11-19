"""Find-more helpers shared by the app and its tests: what a job covers, adding a catalogue item as a job, the ignore list,
dropped sources and the items not seen before. No GTK."""
from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

from lifeboat import catalogue, engine, setup
from lifeboat.config import (
    Config,
    ConfigError,
    Job,
    add_ignore_text,
    add_job_text,
    load_config,
    remove_ignore_text,
    write_config_text,
)

SEEN_FILE = "seen-items.json"


def _expand(path: str) -> str:
    return os.path.expanduser(path)


def covered(item: dict, cfg: Config) -> bool:
    """True if a job's source (or one of an include job's files) is the item's path or a folder above it."""
    sources = [_expand(s) for s in engine.coverage_sources(cfg)]
    return engine._covered(item["path"], sources)


def job_for(cfg: Config, item: dict) -> Job:
    """The job the setup wizard would build for this item, renamed if a job already has that name (a second single file)."""
    if not cfg.device_name:
        raise ConfigError("device.name: the config has no device name, so Lifeboat cannot choose a place for the backup")
    job = setup.wizard_jobs(cfg.device_name, [item])[0]
    taken = {j.name for j in cfg.jobs}
    name = job.name
    if name in taken and item["kind"] == "file":
        name = f"{name}-{item['key']}"
    base, n = name, 1
    while name in taken:
        n += 1
        name = f"{base}-{n}"
    return replace(job, name=name, dest=job.dest.removesuffix(job.name) + name)


def add_item(cfg: Config, item: dict) -> Job:
    """Append the item's job to the config file. Nothing is copied until the next backup."""
    job = job_for(cfg, item)
    write_config_text(cfg.path, add_job_text(cfg.path.read_text(encoding="utf-8"), job))
    return job


def set_ignored(cfg: Config, key: str, on: bool) -> None:
    text = cfg.path.read_text(encoding="utf-8")
    write_config_text(cfg.path, (add_ignore_text if on else remove_ignore_text)(text, key))


def apply_ignore(path: Path, keys: list[str]) -> None:
    """Write a setup wizard's in-memory ignore list into the config it just created."""
    if keys:
        text = Path(path).read_text(encoding="utf-8")
        for key in keys:
            text = add_ignore_text(text, key)
        write_config_text(Path(path), text)


def match_dropped(dropped: list[str], items: list[dict]) -> list[tuple[str, dict | None]]:
    """Each dropped source with the catalogue item at that path, or None when none is (a hand-added folder)."""
    by_path = {os.path.realpath(i["path"]): i for i in items}
    return [(d, by_path.get(os.path.realpath(_expand(d)))) for d in dropped]


def add_back(path: Path, pairs: list[tuple[str, dict | None]]) -> list[str]:
    """Re-add the job for each dropped source that match_dropped found an item for, then forget just those sources.
    Returns the sources added back."""
    done = []
    for source, item in pairs:
        if item is not None:
            add_item(load_config(path), item)
            done.append(source)
    engine.ack_dropped(done)
    return done


def new_items(items: list[dict], cfg: Config, seen: set[str]) -> list[dict]:
    hidden = set(cfg.scan_ignore)
    return [i for i in items if i["default_on"] and not i["ignored"] and i["key"] not in hidden and i["key"] not in seen
            and not covered(i, cfg)]


def read_seen() -> set[str] | None:
    """The item keys the user has been shown; None before the first time."""
    doc = engine.read_json(engine.state_dir() / SEEN_FILE)
    if not isinstance(doc, dict):
        return None
    return {k for k in doc.get("keys", []) if isinstance(k, str)}


def mark_seen(keys) -> None:
    engine.write_json(SEEN_FILE, {"keys": sorted((read_seen() or set()) | set(keys))})


def unseen_new_items(items: list[dict], cfg: Config) -> list[dict]:
    """new_items against the seen file. The setup wizard writes the file when it creates the config, so a missing file means
    a config from before this feature: nothing has been shown yet, and what is not backed up counts as new."""
    return new_items(items, cfg, read_seen() or set())


def grouped(items: list[dict]) -> list[tuple[str, str, list[dict], bool]]:
    """(group key, title, items, all encrypted) in catalogue order. An item without a group is filed under Files, or Keys & logins if secret."""
    out: dict[str, list[dict]] = {}
    for i in items:
        out.setdefault(i.get("group") or ("keys" if i["secret"] else "files"), []).append(i)
    return [(g, catalogue.GROUPS[g], out[g], all(i["secret"] for i in out[g])) for g in catalogue.GROUPS if g in out]
