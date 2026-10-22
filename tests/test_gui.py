"""GTK smoke test. Runs in a child process against a hidden Broadway display, never the real one."""
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("gi")

SCRIPT = '''
import sys
import gi
try:
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
except ValueError:
    sys.exit(77)
from gi.repository import Adw, Gtk
if not Gtk.init_check():
    sys.exit(77)
Adw.init()
from pathlib import Path
from lifeboat.app.window import Win, gather

win = Win(None)
assert win.page_ids() == ["overview", "help", "prefs"]
win.apply(gather())
assert win.data["kind"] == "noconfig"
for pid in win.page_ids():
    win.show_page(pid)
cfg = Path(sys.argv[1])
cfg.parent.mkdir(parents=True)
cfg.write_text(sys.argv[2])
win.apply(gather())
assert win.data["kind"] == "ok"
assert win.page_ids() == ["overview", "backups", "restore", "help", "prefs"], win.page_ids()
for pid in win.page_ids():
    win.show_page(pid)
'''


def test_main_window_builds_with_and_without_config(env, make_cfg):
    if not shutil.which("gtk4-broadwayd"):
        pytest.skip("gtk4-broadwayd not available")
    cfg = make_cfg()
    j = cfg.jobs[0]
    text = (f'[[jobs]]\nname = "docs"\nsource = "{j.source}"\ndest = "{j.dest}"\nold = "{j.old}"\n'
            '[modules]\nclaude_skills = "off"\n')
    bw = subprocess.Popen(["gtk4-broadwayd", ":9"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(1)
        e = dict(os.environ, GDK_BACKEND="broadway", BROADWAY_DISPLAY=":9",
                 PYTHONPATH=str(Path(__file__).parent.parent))
        e.pop("WAYLAND_DISPLAY", None)
        p = subprocess.run([sys.executable, "-c", SCRIPT, str(cfg.path), text], env=e, capture_output=True, text=True,
                           timeout=60)
    finally:
        bw.terminate()
    if p.returncode == 77:
        pytest.skip("GTK 4 / libadwaita not available")
    assert p.returncode == 0, p.stderr
