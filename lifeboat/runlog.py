"""The readable run log: rclone's JSON lines rewritten as `HH:MM:SS  <folder>  <what>`. GTK-free."""
from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime

NOISE = ("Set directory modification time", "There was nothing to transfer")
NOT_THE_CAUSE = ("Attempt ", "not deleting ")


def human_size(n: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB"):
        if n < 1024 or unit == "GiB":
            return f"{int(n)} B" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return ""


def plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


class Rclone:
    """One rclone run's lines: `feed` returns the text to show (None to drop it) and keeps the file count, bytes and cause."""

    def __init__(self):
        self.files, self.bytes, self.error = 0, 0, ""
        self.would, self.would_bytes = 0, 0  # files a --dry-run would copy (rclone 1.74: one "Skipped copy as --dry-run is set" notice each)

    def feed(self, raw: str) -> str | None:
        try:
            rec = json.loads(raw)
        except ValueError:
            return raw  # a password prompt, a Go panic, a shell command's output: shown as it came
        if not isinstance(rec, dict) or not isinstance(rec.get("msg"), str):
            return raw
        msg, level = rec["msg"], str(rec.get("level", "info")).lower()
        if "stats" in rec or any(n in msg for n in NOISE):
            return None
        obj = rec.get("object")
        named = isinstance(obj, str) and obj and not str(rec.get("objectType", "")).endswith(".Fs")
        text = f"{obj}: {msg}" if named else msg
        if level in ("error", "critical"):
            if not msg.startswith(NOT_THE_CAUSE):
                self.error = text
            return "ERROR " + text
        if msg.startswith("Copied") and named:
            self.files += 1
            size = rec.get("size") if isinstance(rec.get("size"), int) else 0
            self.bytes += size
            kind = "replaced" if "replaced" in msg else "new"
            return f"copied {obj} ({kind}, {human_size(size)})"
        if msg.startswith("Skipped copy as --dry-run") and named:
            self.would += 1
            self.would_bytes += rec["size"] if isinstance(rec.get("size"), int) else 0  # the line itself is shown as rclone wrote it
        if msg.startswith("Moved") and named:
            return f"kept the old copy of {obj}"
        return ("WARNING " if level == "warning" else "") + text


class RunLog:
    """Writes each line to the open run log file and to `emit`."""

    def __init__(self, log, emit: Callable[[str], None] | None = None, clock: Callable[[], datetime] = datetime.now):
        self.log, self.emit, self.clock = log, emit, clock

    def raw(self, text: str) -> None:
        self.log.write(text + "\n")
        self.log.flush()
        if self.emit:
            self.emit(text)

    def line(self, label: str, text: str) -> None:
        self.raw(self.stamp(label, text))

    def stamp(self, label: str, text: str) -> str:
        return f"{self.clock():%H:%M:%S}  " + (f"{label}  " if label else "") + text

    def handler(self, label: str, rclone: Rclone) -> Callable[[str], str | None]:
        """What `engine.stream` calls per raw line: the finished line, or None to drop it."""
        def handle(raw: str) -> str | None:
            text = rclone.feed(raw)
            return None if text is None else self.stamp(label, text)
        return handle
