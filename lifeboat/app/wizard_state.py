"""GTK-free state and validation for the setup wizard."""
from __future__ import annotations

import os
from datetime import datetime

from lifeboat.config import NAME_RE
from lifeboat.engine import parse_time
from lifeboat.setup import SECRET_FOLDERS, _slug, with_size
from lifeboat.setup import _collapse_home as tilde  # noqa: F401  (re-exported for the wizard)

DEFAULT_TIME = "13:00"


def daily_times() -> list[str]:
    return [f"{h:02d}:00" for h in range(24)]


def validate_device_name(name: str, existing: list[str]) -> str | None:
    """An error message fit to show under the field, or None when the name is fine."""
    if not name:
        return "Enter a name for this laptop."
    if not NAME_RE.match(name):
        return "Use letters, digits, '.', '_' and '-', starting with a letter or digit."
    if name in existing:
        return "Another laptop on this account already uses this name. If this is that laptop, go back and restore from it."
    return None


def same_laptop_backup(existing: list[dict], hostname: str, default_name: str) -> str | None:
    """The device name of the backup that looks like this laptop, or None. The manifest's recorded host is the evidence;
    the device name only counts for manifests without a host, since a name alone is easily shared ('laptop')."""
    for m in existing:
        if m.get("host") == hostname or (not m.get("host") and m["device"] == default_name):
            return m["device"]
    return None


def job_label(name: str) -> str:
    """A manifest job's name ('ssh-keys') as a title fit for a row ('SSH keys')."""
    for _key, label, _dir in SECRET_FOLDERS:
        if _slug(label).lower() == name:
            return label
    text = name.replace("-", " ").replace("_", " ")
    return text[:1].upper() + text[1:]


def keep_backing_up_default(manifest: dict, now: datetime) -> bool:
    """Continue the old device's backups by default only when its last run is over 24 h old (or unreadable): a laptop
    that backed up today is probably still in use, and two laptops must not share one backup folder."""
    t = parse_time(manifest.get("time"))
    if t is not None and t.tzinfo is None:
        now = now.replace(tzinfo=None)
    return t is None or (now - t).total_seconds() > 24 * 3600


def since_text(iso) -> str:
    """'Not backed up since 2 Oct 2026' for a retired job."""
    t = parse_time(iso)
    return f"Not backed up since {t.day} {t.strftime('%b %Y')}" if t else "Not backed up from the old laptop"


def fmt_size(size: int | None, complete: bool) -> str:
    if size is None:
        return "calculating…"
    for unit, div in (("GB", 10**9), ("MB", 10**6), ("kB", 10**3)):
        if size >= div:
            n = size / div
            text = f"{n:.0f}" if not complete else f"{n:.1f}"
            return f"{text} {unit}" if complete else f"about {text} {unit}+"
    return f"{size} B" if complete else f"about {size} B+"


def folders_text(n: int) -> str:
    return f"{n} folder{'s' if n != 1 else ''}"


class Choices:
    """The folders on offer, which are switched on, and the sizes measured so far. User toggles beat later size updates."""

    def __init__(self, folders: list[dict]):
        self._f: dict[str, dict] = {}
        self._on: dict[str, bool] = {}
        self._touched: set[str] = set()
        for f in folders:
            self._f[f["path"]] = dict(f)
            self._on[f["path"]] = f["default_on"]

    def is_on(self, path: str) -> bool:
        return self._on[path]

    def hint(self, path: str) -> str:
        return self._f[path].get("hint", "")

    def toggle(self, path: str, on: bool) -> None:
        self._on[path] = on
        self._touched.add(path)

    def set_size(self, path: str, size: int, complete: bool) -> None:
        if path not in self._f:
            return
        f = with_size(self._f[path], size, complete)
        self._f[path] = f
        if path not in self._touched:
            self._on[path] = f["default_on"]

    def add(self, path: str) -> bool:
        if path in self._f:
            return False
        base = os.path.basename(path.rstrip("/")) or "folder"
        taken = {f["label"].lower() for f in self._f.values()}
        label, n = base, 1
        while label.lower() in taken:
            n += 1
            label = f"{base}-{n}"
        self._f[path] = {"key": label.lower(), "label": label, "path": path, "default_on": True, "secret": False}
        self._on[path] = True
        self._touched.add(path)
        return True

    def remove(self, path: str) -> None:
        self._f.pop(path, None)
        self._on.pop(path, None)
        self._touched.discard(path)

    def selected(self) -> list[dict]:
        return [f for p, f in self._f.items() if self._on[p]]

    def can_continue(self) -> bool:
        return bool(self.selected())
