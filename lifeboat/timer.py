"""systemd user timers: daily backup and weekly prune."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from lifeboat.config import Config

UNITS = ("lifeboat.service", "lifeboat.timer", "lifeboat-prune.service", "lifeboat-prune.timer")


def unit_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "systemd" / "user"


def q(path: str) -> str:
    """Double-quote a path for a systemd command line or Environment= value."""
    return '"' + str(path).replace("\\", "\\\\").replace('"', '\\"') + '"'


def launcher() -> tuple[str, str]:
    """(command line, extra [Service] lines) that start lifeboat from systemd."""
    exe = shutil.which("lifeboat")
    if exe:
        return q(exe), ""
    root = Path(__file__).resolve().parent.parent
    return f'{q(sys.executable)} -m lifeboat', f'Environment="PYTHONPATH={root}"\n'


def unit_files(cfg: Config) -> dict[str, str]:
    cmd, env = launcher()
    nm = shutil.which("nm-online")
    pre = f"ExecStartPre=-{q(nm)} -q -t 120\n" if nm else ""
    return {
        "lifeboat.service": (
            "[Unit]\nDescription=Lifeboat backup\n\n"
            f"[Service]\nType=oneshot\n{env}{pre}ExecStart={cmd} run\nExecStopPost=-{cmd} check\n"),
        "lifeboat.timer": (
            "[Unit]\nDescription=Lifeboat daily backup\n\n"
            f"[Timer]\nOnCalendar=*-*-* {cfg.daily_at}:00\nPersistent=true\nRandomizedDelaySec=10min\n\n"
            "[Install]\nWantedBy=timers.target\n"),
        "lifeboat-prune.service": (
            "[Unit]\nDescription=Lifeboat prune old versions\n\n"
            f"[Service]\nType=oneshot\n{env}ExecStart={cmd} prune\n"),
        "lifeboat-prune.timer": (
            "[Unit]\nDescription=Lifeboat weekly prune\n\n"
            "[Timer]\nOnCalendar=weekly\nPersistent=true\nRandomizedDelaySec=30min\n\n"
            "[Install]\nWantedBy=timers.target\n"),
    }


def _systemctl(*args: str) -> int:
    return subprocess.run(["systemctl", "--user", *args], stdin=subprocess.DEVNULL).returncode


def install(cfg: Config) -> int:
    d = unit_dir()
    d.mkdir(parents=True, exist_ok=True)
    for name, text in unit_files(cfg).items():
        (d / name).write_text(text)
    rc = _systemctl("daemon-reload")
    return rc or _systemctl("enable", "--now", "lifeboat.timer", "lifeboat-prune.timer")


def uninstall() -> int:
    _systemctl("disable", "--now", "lifeboat.timer", "lifeboat-prune.timer")
    for name in UNITS:
        (unit_dir() / name).unlink(missing_ok=True)
    return _systemctl("daemon-reload")
