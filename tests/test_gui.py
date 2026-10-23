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


PRELUDE = r'''
import json
import os
import sys
import time
import gi
try:
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    gi.require_version("Graphene", "1.0")
except ValueError:
    sys.exit(77)
from gi.repository import Adw, Gdk, GLib, Graphene, Gsk, Gtk
if not Gtk.init_check():
    sys.exit(77)
Adw.init()
from datetime import datetime, timezone
from pathlib import Path
from lifeboat import setup, timer
from lifeboat.app import flow, wizard
from lifeboat.app.window import Win, gather

SHOTS = os.environ.get("LIFEBOAT_SHOTS")
if SHOTS:
    Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
TOKEN = '{"access_token":"tok-SECRET-1","refresh_token":"ref-SECRET-2"}'
calls = []


def pump(cond=lambda: False, timeout=8.0):
    end = time.monotonic() + timeout
    ctx = GLib.MainContext.default()
    while time.monotonic() < end:
        while ctx.iteration(False):
            pass
        if cond():
            return True
        time.sleep(0.01)
    return cond()


def walk(w):
    yield w
    c = w.get_first_child()
    while c is not None:
        yield from walk(c)
        c = c.get_next_sibling()


def buttons(root):
    return [w for w in walk(root) if isinstance(w, Gtk.Button)]


def click(root, text):
    for b in buttons(root):
        if b.get_label() == text:
            assert b.get_sensitive(), f"{text} is insensitive"
            b.emit("clicked")
            return
    raise AssertionError(f"no button {text!r}: {[b.get_label() for b in buttons(root)]}")


def vis():
    return wiz.nav.get_visible_page()


def tag():
    return wiz.nav.get_visible_page().get_tag()


def labels(root):
    return [w.get_label() for w in walk(root) if isinstance(w, Gtk.Label)]


def shot(name, w=None):
    if not SHOTS:
        return
    w = w or wiz.get_child()
    p = Gtk.WidgetPaintable.new(w)  # made before the wait so the widget's next frame is recorded
    node = None
    for _ in range(6):
        w.queue_draw()
        pump(timeout=0.8)
        snap = Gtk.Snapshot()
        snap.append_color(Gdk.RGBA(red=1, green=1, blue=1, alpha=1), Graphene.Rect().init(0, 0, w.get_width(), w.get_height()))
        probe = Gtk.Snapshot()
        p.snapshot(probe, w.get_width(), w.get_height())
        if probe.to_node() is not None:
            p.snapshot(snap, w.get_width(), w.get_height())
            node = snap.to_node()
            break
    assert node is not None, f"nothing drawn for {name}"
    r = Gsk.CairoRenderer()
    r.realize(None)  # offscreen: no display involved
    tex = r.render_texture(node, None)
    r.unrealize()
    tex.save_to_png(os.path.join(SHOTS, name + ".png"))


def check_no_leak(allow_passphrase=None):
    for t in labels(wiz.get_child()) + labels(win):
        assert "SECRET" not in t, "token in a label"
        if wiz.passphrase and t != allow_passphrase:
            assert wiz.passphrase not in t, "passphrase in a label"


# ---- stubs
class FakeSignIn:
    mode = "ok"
    def __init__(self, timeout=300.0):
        self.n = 0
    def start(self):
        calls.append(("signin.start",))
    auth_url = "http://127.0.0.1:53682/auth?state=x"
    def poll(self):
        self.n += 1
        if FakeSignIn.mode == "never":
            return None
        if FakeSignIn.mode == "error":
            raise setup.SignInError("Sign-in timed out. Try again.")
        return TOKEN if self.n >= 2 else None
    def cancel(self):
        calls.append(("signin.cancel",))

class FakeUri:
    @staticmethod
    def new(url):
        calls.append(("uri", url))
        class L:
            def launch(self, parent, cancel, cb):
                pass
        return L()

class _F:
    def __init__(self, p): self.p = p
    def get_path(self): return self.p

class FakeFileDialog:
    def __init__(self, **kw): self.kw = kw
    def select_folder(self, parent, c, cb): cb(self, "r")
    def select_folder_finish(self, r): return _F(str(HOME / "Extra"))
    def save(self, parent, c, cb): cb(self, "r")
    def save_finish(self, r): return _F(str(HOME / "kit.pdf"))

HOME = Path(sys.argv[1])
flow.UriLauncher = FakeUri
wizard.FileDialog = FakeFileDialog
setup.SignIn = FakeSignIn
setup.write_drive_remote = lambda conf, token: (open(conf, "a").write("[lifeboat]\n"), calls.append(("drive_remote", token)))
setup.make_remotes = lambda conf, token, pw: calls.append(("make_remotes", token, pw))
setup.write_wizard_config = lambda path, device, choices, at, existing_devices=None: calls.append(("config", device, choices, at, existing_devices))
timer.install = lambda cfg: calls.append(("timer",)) or 0
wizard.load_config = lambda p: object()
found = []
setup.find_backups = lambda conf: list(found)
setup.check_passphrase = lambda conf, pw, device: calls.append(("check", pw, device)) or pw == "right-pass"

win = Win(None)
win.run_cli = lambda *a: calls.append(("run_cli",) + a)
win.run_backup = lambda: calls.append(("run_backup",))
win.refresh = lambda: calls.append(("refresh",))
win.open_restore_wizard = lambda device=None, handover=None: calls.append(("restore", device))
win.present()
win.apply(gather())
assert win.data["kind"] == "noconfig"
win.show_page("overview")
names = [b.get_label() for b in buttons(win)]
for want in ("Set Up Backup", "Restore From a Backup", "Edit a config file instead"):
    assert want in names, names
shot("00-gate", win.get_content())

'''

SETUP_BODY = r'''
def start():
    global wiz
    calls.clear()
    wiz = wizard.SetupWizard(win)
    wiz.present(win)
    pump(timeout=0.3)
    return wiz

# ---- scenario 1: brand new account
wiz = start()
assert tag() == "welcome"
shot("01-welcome")
click(vis(), "Continue")
assert tag() == "signin"
shot("02-signin")
click(vis(), "Sign In With Google")
assert pump(lambda: tag() == "folders"), tag()
assert ("uri", "http://127.0.0.1:53682/auth?state=x") in calls
assert ("drive_remote", TOKEN) in calls
assert wiz.mode == "new"
check_no_leak()
shot("03-folders")
# nothing selected blocks Continue; bad name blocks Continue
for p in list(wiz.rows):
    wiz.rows[p].set_active(False)
assert not [b for b in buttons(vis()) if b.get_label() == "Continue" and b.get_sensitive()]
wiz.rows[str(HOME / "Documents")].set_active(True)
wiz.rows[str(HOME / ".ssh")].set_active(True)
wiz.name_row.set_text("bad name")
assert wiz.name_row.has_css_class("error")
assert not wiz.continue_btn.get_sensitive()
shot("03b-folders-invalid")
wiz.name_row.set_text("my-laptop")
assert wiz.continue_btn.get_sensitive()
wiz._pick_folder()
assert str(HOME / "Extra") in wiz.rows
assert pump(lambda: "Extra" in wiz.rows[str(HOME / "Extra")].get_subtitle() and "calculating" not in wiz.rows[str(HOME / "Extra")].get_subtitle())
big = [p for p, r in wiz.rows.items() if "calculating" in r.get_subtitle()]
assert not big or pump(lambda: not any("calculating" in wiz.rows[p].get_subtitle() for p in big))
wiz.time_row.set_selected(9)
shot("03c-folders-added")
wiz.continue_btn.emit("clicked")
assert tag() == "kit"
pw = wiz.passphrase
assert pw and wiz.kit_label.get_label() == pw
check_no_leak(allow_passphrase=pw)
assert not wiz.kit_go.get_sensitive()
shot("04-kit")
click(vis(), "Save PDF…")
assert (HOME / "kit.pdf").exists() and pw in (HOME / "kit.pdf").read_text()
assert oct((HOME / "kit.pdf").stat().st_mode & 0o777) == "0o600"
click(vis(), "Copy")
assert win.get_display().get_clipboard() is not None
stored = [w for w in walk(vis()) if isinstance(w, Adw.SwitchRow)][0]
stored.set_active(True)
assert wiz.kit_go.get_sensitive()
wiz.kit_go.emit("clicked")
assert not wiz.get_can_close()
assert pump(lambda: ("run_backup",) in calls), calls
assert ("make_remotes", TOKEN, pw) in calls
cfgc = [c for c in calls if c[0] == "config"][0]
assert cfgc[1] == "my-laptop" and cfgc[3] == "09:00" and cfgc[4] == []
chosen = [c["path"] for c in cfgc[2]]
assert str(HOME / "Extra") in chosen and str(HOME / ".ssh") in chosen, chosen
assert [c for c in cfgc[2] if c["path"] == str(HOME / ".ssh")][0]["secret"] is True
assert ("timer",) in calls
order = [c[0] for c in calls]
assert order.index("make_remotes") < order.index("config") < order.index("timer") < order.index("run_backup")
pump(timeout=0.3)
assert wiz.token is None and wiz.passphrase is None  # dropped on close

# ---- scenario 2: sign-in error, then Drive unreachable
wiz = start()
click(vis(), "Continue")
FakeSignIn.mode = "error"
click(vis(), "Sign In With Google")
assert pump(lambda: wiz.signin_banner.get_revealed())
assert tag() == "signin" and "timed out" in wiz.signin_banner.get_title()
assert "Try again" not in wiz.signin_banner.get_title()  # the banner already has a Try Again button
assert wiz.stack.get_visible_child_name() == "button"
shot("05-signin-error")
FakeSignIn.mode = "ok"
setup.find_backups = lambda conf: (_ for _ in ()).throw(setup.SetupError("Could not list your Google Drive."))
wiz.signin_banner.emit("button-clicked")
click(vis(), "Sign In With Google")
assert pump(lambda: wiz.signin_banner.get_revealed() and "Drive" in wiz.signin_banner.get_title())
assert tag() == "signin"  # never continues to "new backup": that would mint a second passphrase
wiz.force_close()
pump(timeout=0.2)

# ---- scenario 3: account already has a backup, add this laptop
setup.find_backups = lambda conf: [{"device": setup.default_device_name(), "time": datetime.now(timezone.utc).isoformat(),
                                    "jobs": [{"name": "a"}, {"name": "b"}]}]
setup.new_passphrase = lambda: (_ for _ in ()).throw(AssertionError("must not mint a second passphrase"))
wiz = start()
click(vis(), "Continue")
click(vis(), "Sign In With Google")
assert pump(lambda: tag() == "existing"), tag()
sub = [w.get_subtitle() for w in walk(vis()) if isinstance(w, Adw.ActionRow)][0]
assert sub == "Last backup just now · 2 folders", sub
shot("06-existing")
labels_in_bar = [b.get_label() for b in buttons(vis()) if b.get_label() in ("Add This Laptop", "Restore From This Backup")]
assert labels_in_bar == ["Restore From This Backup", "Add This Laptop"], labels_in_bar  # suggested action last
click(vis(), "Restore From This Backup")
assert ("restore", setup.default_device_name()) in calls
wiz = start()
click(vis(), "Continue")
click(vis(), "Sign In With Google")
assert pump(lambda: tag() == "existing")
click(vis(), "Add This Laptop")
assert tag() == "folders" and wiz.mode == "add"
assert wiz.name_row.get_text() == setup.default_device_name() + "-2"
wiz.name_row.set_text(setup.default_device_name())
assert wiz.name_row.has_css_class("error") and not wiz.continue_btn.get_sensitive()
wiz.name_row.set_text("second")
wiz.continue_btn.emit("clicked")
assert tag() == "passphrase"
shot("07-passphrase")
wiz.pw_row.set_text("wrong-pass")
click(vis(), "Check")
assert pump(lambda: wiz.pw_error.get_visible())
assert wiz.pw_row.has_css_class("error") and tag() == "passphrase"
shot("07b-passphrase-wrong")
wiz.pw_row.set_text("right-pass")
click(vis(), "Check")
assert pump(lambda: ("run_backup",) in calls), calls
assert ("make_remotes", TOKEN, "right-pass") in calls
cfgc = [c for c in calls if c[0] == "config"][0]
assert cfgc[1] == "second" and cfgc[4] == [setup.default_device_name()]
assert not any(c[0] == "check" and c[1] == TOKEN for c in calls)

# ---- scenario 4: setup failure shows an error page and lets the user go back
setup.make_remotes = lambda conf, token, pw: (_ for _ in ()).throw(setup.SetupError("rclone could not read the new remotes."))
setup.find_backups = lambda conf: []
setup.new_passphrase = lambda: "fixed-pass"
wiz = start()
click(vis(), "Continue")
click(vis(), "Sign In With Google")
assert pump(lambda: tag() == "folders")
wiz.continue_btn.emit("clicked")
[w for w in walk(vis()) if isinstance(w, Adw.SwitchRow)][-1].set_active(True)
wiz.kit_go.set_sensitive(True)
wiz.kit_go.emit("clicked")
assert pump(lambda: tag() == "setting-up" and wiz.get_can_close())
assert any("rclone could not read" in t for t in labels(vis()))
shot("08-setup-failed")
click(vis(), "Back")
assert pump(lambda: tag() == "kit")
wiz.force_close()
# ---- review round: every failure path leaves the dialog closable, and nothing stale survives
good_remotes = lambda conf, token, pw: calls.append(("make_remotes", token, pw))
setup.make_remotes = good_remotes
setup.find_backups = lambda conf: []
setup.new_passphrase = lambda: "fixed-pass"
CONF = setup.rclone_conf_path()


def to_folders():
    w = start()
    click(vis(), "Continue")
    click(vis(), "Sign In With Google")
    assert pump(lambda: tag() == "folders"), tag()
    return w


def to_setup():
    wiz.continue_btn.emit("clicked")
    [w for w in walk(vis()) if isinstance(w, Adw.SwitchRow)][-1].set_active(True)
    wiz.kit_go.set_sensitive(True)
    wiz.kit_go.emit("clicked")


# in_thread never hangs its caller
got = []
win.in_thread(lambda: 1 / 0, lambda r: got.append(("done", r)), lambda e: got.append(("fail", type(e).__name__)))
assert pump(lambda: got) and got == [("fail", "ZeroDivisionError")], got
win.in_thread(lambda: 1 / 0, lambda r: got.append("x"))  # no fail callback: a toast, no crash
pump(timeout=0.3)

# timer.install raising (no systemctl): clear page, dialog closable, config + remotes kept
timer.install = lambda cfg: (_ for _ in ()).throw(FileNotFoundError(2, "No such file or directory"))
cfg_file = Path(os.environ["XDG_CONFIG_HOME"]) / "lifeboat" / "config.toml"
setup.write_wizard_config = lambda path, device, choices, at, existing_devices=None: (cfg_file.parent.mkdir(parents=True, exist_ok=True), cfg_file.write_text("x"), calls.append(("config",)))
CONF.unlink(missing_ok=True)
to_folders()
to_setup()
assert pump(lambda: tag() == "setting-up" and wiz.get_can_close()), "dialog stuck"
txt = " ".join(labels(vis()))
assert "Almost Done" in txt and "daily timer is not" in txt, txt
assert "systemd isn't available" in txt and "No such file" not in txt, txt
assert not any(c[0] == "run_backup" for c in calls)
shot("09-timer-failed")
calls.clear()
click(vis(), "Close")
pump(timeout=0.4)
assert ("refresh",) in calls
assert CONF.exists()  # config.toml exists, so the remotes are kept
cfg_file.unlink()
timer.install = lambda cfg: calls.append(("timer",)) or 0

# systemctl exits non-zero: same page
timer.install = lambda cfg: 5
to_folders()
to_setup()
assert pump(lambda: tag() == "setting-up" and wiz.get_can_close())
assert "exit 5" in " ".join(labels(vis()))
wiz.force_close()
pump(timeout=0.3)
cfg_file.unlink(missing_ok=True)
timer.install = lambda cfg: calls.append(("timer",)) or 0

# the live token in rclone.conf is removed when the user bails out, but only if this run created the file
CONF.unlink(missing_ok=True)
to_folders()
assert CONF.exists()
wiz.force_close()
pump(timeout=0.4)
assert not CONF.exists(), "token left behind after cancel"
CONF.write_text("[mine]\n")
to_folders()
wiz.force_close()
pump(timeout=0.4)
assert CONF.read_text().startswith("[mine]"), "a pre-existing rclone.conf must never be deleted"
CONF.unlink()

# a stale passphrase never reaches another account's kit
to_folders()
wiz.continue_btn.emit("clicked")
assert tag() == "kit" and wiz.passphrase == "fixed-pass"
wiz.nav.pop_to_tag("signin")
setup.find_backups = lambda conf: [{"device": "other", "time": datetime.now(timezone.utc).isoformat(), "jobs": []}]
click(vis(), "Sign In With Google")
assert pump(lambda: tag() == "existing"), tag()
assert wiz.passphrase is None and wiz.mode == "add" and [m["device"] for m in wiz.existing] == ["other"]
wiz.force_close()
pump(timeout=0.3)
setup.find_backups = lambda conf: []

# clipboard: Copy puts the passphrase there, and it is cleared after the delay; Win.copy works too
wizard.CLIPBOARD_CLEAR_SECONDS = 1
to_folders()
wiz.continue_btn.emit("clicked")
click(vis(), "Copy")
clip = win.get_display().get_clipboard()
read = []
clip.read_text_async(None, lambda c, r: read.append(c.read_text_finish(r)))
assert pump(lambda: read) and read == ["fixed-pass"], read
read.clear()
pump(timeout=2.2)


def got_text(c, r):
    try:
        read.append(c.read_text_finish(r))
    except GLib.Error:
        read.append(None)


clip.read_text_async(None, got_text)
assert pump(lambda: read) and not read[0], f"clipboard not cleared: {read}"
wiz.force_close()
pump(timeout=0.3)
read.clear()
win.copy("plain-text")
clip.read_text_async(None, got_text)
assert pump(lambda: read) and read == ["plain-text"], read

# no suggested folders: the Folders group is hidden until a folder is added
real_suggest = setup.suggest_folders
setup.suggest_folders = lambda: [{"key": "ssh", "label": "SSH keys", "path": str(HOME / ".ssh"), "default_on": True, "secret": True}]
to_folders()
assert not wiz.group_folders.get_visible()
wiz._pick_folder()
assert wiz.group_folders.get_visible()
wiz._remove_folder(str(HOME / "Extra"), wiz.rows[str(HOME / "Extra")], wiz.group_folders)
assert not wiz.group_folders.get_visible()
wiz.force_close()
pump(timeout=0.3)
setup.suggest_folders = real_suggest

# one existing device still looks like a single choice (radio), with the suggested action last
setup.find_backups = lambda conf: [{"device": "solo", "time": datetime.now(timezone.utc).isoformat(), "jobs": [{"name": "a"}]}]
start()
click(vis(), "Continue")
click(vis(), "Sign In With Google")
assert pump(lambda: tag() == "existing")
shot("06b-existing-single")
wiz.force_close()
pump(timeout=0.3)

# re-entering Sign In removes the previous 250 ms poller
ctx = GLib.MainContext.default()
FakeSignIn.mode = "never"
start()
click(vis(), "Continue")
click(vis(), "Sign In With Google")
id1 = wiz._poll_id
assert id1 and ctx.find_source_by_id(id1) is not None
wiz._start_signin()  # re-entry without cancel
id2 = wiz._poll_id
assert id2 and id2 != id1 and ctx.find_source_by_id(id1) is None and ctx.find_source_by_id(id2) is not None
click(vis(), "Cancel")
assert wiz._poll_id == 0 and ctx.find_source_by_id(id2) is None
FakeSignIn.mode = "ok"
wiz.force_close()
pump(timeout=0.3)

print("WIZARD OK")
'''


RESTORE_BODY = r'''
import threading
from datetime import timedelta
from lifeboat.app import restore, window

CONF = setup.rclone_conf_path()
CFG_FILE = Path(os.environ["XDG_CONFIG_HOME"]) / "lifeboat" / "config.toml"
ROOT = setup.drive_root()
NOW = datetime.now(timezone.utc)
DONE_ALL = "Log out and back in so every app picks up its settings."


def manifest(device, age_h):
    base, secure = f"lifeboat:{ROOT}/{device}", f"lifeboat-secure:{device}"
    return {"format": 1, "device": device, "time": (NOW - timedelta(hours=age_h)).isoformat(), "ok": True, "jobs": [
        {"name": "documents", "source": "~/Documents", "dest": f"{base}/Documents", "old": f"{base}/_old", "encrypted": False},
        {"name": "ssh-keys", "source": "~/.ssh", "dest": f"{secure}/ssh-keys", "old": f"{secure}/_old", "encrypted": True}]}


OLD = manifest("old-laptop", 72)   # last backup 3 days ago: nobody is using it
LIVE = manifest("work-laptop", 2)  # backed up two hours ago: still in use
inst = []
RealRestoreWizard = restore.RestoreWizard


class Capture(RealRestoreWizard):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        inst.append(self)


window.RestoreWizard = Capture
win.open_restore_wizard = lambda device=None, handover=None: Capture(win, device, handover).present(win)
win.open_setup_wizard = lambda: calls.append(("open_setup",))
gate = threading.Event()
gate.set()
rc_for = {}


def fake_restore_jobs(cfg, names, emit):
    calls.append(("restore_jobs", cfg, list(names)))
    emit("copying files")
    gate.wait(10)
    return {n: rc_for.get(n, 0) for n in names}


setup.restore_jobs = fake_restore_jobs
setup.config_from_manifest = lambda path, m, at, names=None: calls.append(("from_manifest", path, m, at, names)) or (CFG_FILE.parent.mkdir(parents=True, exist_ok=True), CFG_FILE.write_text("x"))
setup.write_wizard_config = lambda path, device, choices, at, existing_devices=None: (
    calls.append(("config", device, choices, at, existing_devices)), CFG_FILE.parent.mkdir(parents=True, exist_ok=True), CFG_FILE.write_text("x"))
restore.load_config = lambda p: object()
found[:] = []


def start():
    global wiz
    calls.clear()
    CFG_FILE.unlink(missing_ok=True)
    rc_for.clear()
    wiz = RealRestoreWizard(win)
    wiz.present(win)
    pump(timeout=0.3)
    return wiz


def sign_in(expect):
    click(vis(), "Sign In With Google")
    assert pump(lambda: tag() == expect), (tag(), labels(vis()))


def to_what(manifests=None):
    found[:] = manifests or [OLD]
    start()
    sign_in("passphrase" if len(found) == 1 else "choose")
    if tag() == "choose":
        click(vis(), "Continue")
    wiz.pw_row.set_text("right-pass")
    click(vis(), "Check")
    assert pump(lambda: tag() == "what"), tag()


def finish():
    click(vis(), "Restore")
    assert tag().startswith("restoring") and not wiz.get_can_close()


def done_text():
    return " ".join(labels(vis()))


# ---- no backups on this account
start()
assert tag() == "signin"
shot("r01-signin")
sign_in("none")
assert "No Backups Found" in done_text() and "only sees backups it made" in done_text(), done_text()
assert wiz.signin_banner.get_revealed() is False
shot("r02-no-backups")
click(vis(), "Sign In With Another Account")
assert tag() == "signin"
pump(timeout=0.6)  # the popped page leaves the stack when its animation ends
sign_in("none")
click(vis(), "Set Up Backup Instead")
assert ("open_setup",) in calls
pump(timeout=0.3)
assert not CONF.exists(), "token left behind after closing"

# ---- Drive unreachable is never reported as "no backups"
setup.find_backups = lambda conf: (_ for _ in ()).throw(setup.SetupError("Could not list your Google Drive."))
start()
click(vis(), "Sign In With Google")
assert pump(lambda: wiz.signin_banner.get_revealed() and "Drive" in wiz.signin_banner.get_title())
assert tag() == "signin" and "No Backups" not in done_text()
shot("r03-drive-unreachable")
wiz.force_close()
pump(timeout=0.3)
setup.find_backups = lambda conf: list(found)

# ---- one backup: straight to the passphrase; wrong, then right
found[:] = [OLD]
start()
sign_in("passphrase")
assert wiz.picked == "old-laptop"
shot("r04-passphrase")
click(vis(), "Where's my passphrase?")
pump(timeout=0.3)
wiz.pw_row.set_text("wrong-pass")
click(vis(), "Check")
assert pump(lambda: wiz.pw_error.get_visible())
assert wiz.pw_row.has_css_class("error") and tag() == "passphrase"
assert ("check", "wrong-pass", "old-laptop") in calls
assert not any(c[0] == "check" and c[1] == TOKEN for c in calls)
shot("r05-passphrase-wrong")
check_no_leak()
# the check cannot reach Drive: a banner, not "wrong passphrase"
setup.check_passphrase = lambda conf, pw, device: (_ for _ in ()).throw(setup.SetupError(setup.UNREACHABLE))
wiz.pw_row.set_text("right-pass")
click(vis(), "Check")
assert pump(lambda: wiz.pw_banner.get_revealed()) and not wiz.pw_error.get_visible()
setup.check_passphrase = lambda conf, pw, device: calls.append(("check", pw, device)) or pw == "right-pass"
click(vis(), "Check")
assert pump(lambda: tag() == "what")
check_no_leak()
rows = [w for w in walk(vis()) if isinstance(w, Adw.ActionRow) and not isinstance(w, Adw.SwitchRow)]
got = [(r.get_title(), r.get_subtitle()) for r in rows]
assert got == [("Documents", "~/Documents"), ("SSH keys", "~/.ssh · Encrypted")], got
assert "Nothing is deleted." in done_text()
assert wiz.keep_row.get_active() is True and not wiz.name_row.get_visible()  # 3 days old: continue it
shot("r06-what-keep-on")
wiz.force_close()
pump(timeout=0.3)
CONF.unlink(missing_ok=True)

# ---- two backups: choose one; keep backing up ON (old laptop is gone)
found[:] = [LIVE, OLD]
start()
sign_in("choose")
shot("r07-choose")
radios = [w for w in walk(vis()) if isinstance(w, Gtk.CheckButton) and w.get_visible()]
radios[1].set_active(True)
assert wiz.picked == "old-laptop"
click(vis(), "Continue")
wiz.pw_row.set_text("right-pass")
click(vis(), "Check")
assert pump(lambda: tag() == "what")
assert ("check", "right-pass", "old-laptop") in calls
gate.clear()
finish()
assert pump(lambda: any(c[0] == "restore_jobs" for c in calls))
pump(timeout=0.3)
assert "copying files" in wiz.buf.get_text(wiz.buf.get_start_iter(), wiz.buf.get_end_iter(), False)
assert not wiz.get_can_close()
shot("r08-restoring")
gate.set()
assert pump(lambda: tag() == "restoring" and "Your Files Are Back" in done_text()), done_text()
assert wiz.get_can_close()
assert DONE_ALL in done_text()
shot("r09-all-done")
order = [c[0] for c in calls]
# the config is only written after the restore, and the timer only after the config
assert order.index("make_remotes") < order.index("restore_jobs") < order.index("from_manifest") < order.index("timer"), order
assert ("make_remotes", TOKEN, "right-pass") in calls
fm = [c for c in calls if c[0] == "from_manifest"][0]
assert fm[2] == OLD and fm[3] == "13:00" and fm[4] == ["documents", "ssh-keys"] and not any(c[0] == "config" for c in calls)
rj = [c for c in calls if c[0] == "restore_jobs"][0]
assert rj[2] == ["documents", "ssh-keys"] and rj[1].jobs[0].dest == OLD["jobs"][0]["dest"] and str(rj[1].rclone_config) == str(CONF)
calls.clear()
click(vis(), "Done")
pump(timeout=0.4)
assert ("refresh",) in calls
assert CONF.exists(), "config exists, so the remotes are kept"
assert wiz.token is None and wiz.passphrase is None
CONF.unlink()

# ---- keep backing up OFF (old laptop still in use): a new device; restore still reads the old device's folders
to_what([LIVE])
assert wiz.keep_row.get_active() is False and wiz.name_row.get_visible()  # 2 hours old: probably still in use
shot("r10-what-keep-off")
wiz.name_row.set_text("bad name")
assert wiz.name_row.has_css_class("error") and not wiz.restore_btn.get_sensitive()
shot("r11-what-invalid-name")
wiz.name_row.set_text("work-laptop")
assert not wiz.restore_btn.get_sensitive() and "already uses" in done_text()  # another laptop's folder
wiz.name_row.set_text("new-laptop")
list(wiz.checks.values())[1].set_active(False)  # leave the keys out
finish()
assert pump(lambda: "Your Files Are Back" in done_text()), done_text()
cfgc = [c for c in calls if c[0] == "config"][0]
assert cfgc[1] == "new-laptop" and cfgc[3] == "13:00" and cfgc[4] == ["work-laptop"], cfgc
assert [(c["path"], c["secret"], c["label"]) for c in cfgc[2]] == [("~/Documents", False, "documents")], cfgc[2]
assert not any(c[0] == "from_manifest" for c in calls)
rj = [c for c in calls if c[0] == "restore_jobs"][0]
assert rj[2] == ["documents"] and rj[1].jobs[0].dest == LIVE["jobs"][0]["dest"], rj  # the OLD device's folder
assert ("timer",) in calls
wiz.force_close()
pump(timeout=0.3)
CONF.unlink(missing_ok=True)

# ---- the default name for a new device is this laptop's, made unique
to_what([LIVE])
assert wiz.name_row.get_text() == setup.unique_device_name(setup.default_device_name(), ["work-laptop"])
# ---- nothing ticked: Restore stays off
for c in wiz.checks.values():
    c.set_active(False)
assert not wiz.restore_btn.get_sensitive()
wiz.force_close()
pump(timeout=0.3)
CONF.unlink(missing_ok=True)

# ---- one folder fails (keep mode): it is listed, NOT written to the config, no timer; Try Again re-runs only it
to_what()
rc_for["ssh-keys"] = 23
finish()
assert pump(lambda: "Almost Done" in done_text()), done_text()
assert "Some folders could not be restored" in done_text() and "SSH keys" in done_text() and "(exit 23)" in done_text(), done_text()
assert "Not backed up from this laptop: SSH keys" in done_text() and "not scheduled until every folder" in done_text(), done_text()
err_rows = [w for w in walk(vis()) if isinstance(w, Adw.ActionRow) and w.has_css_class("error")]
assert [r.get_title() for r in err_rows] == ["SSH keys"]
assert vis().get_title() == "Not Finished"
fm = [c for c in calls if c[0] == "from_manifest"]
assert [c[4] for c in fm] == [["documents"]], fm  # a half-restored folder must never be synced over its backup
assert not any(c[0] == "timer" for c in calls), calls
shot("r12-partial-failure")
calls.clear()
rc_for.clear()
click(vis(), "Try Again")
assert not wiz.get_can_close()
assert pump(lambda: "Your Files Are Back" in done_text()), done_text()
assert vis().get_title() == "All Done"
rj = [c for c in calls if c[0] == "restore_jobs"]
assert len(rj) == 1 and rj[0][2] == ["ssh-keys"], rj
assert not any(c[0] in ("make_remotes", "config") for c in calls), calls
fm = [c for c in calls if c[0] == "from_manifest"]
assert [c[4] for c in fm] == [["documents", "ssh-keys"]], fm  # now the job is added...
assert [c[0] for c in calls].count("timer") == 1 and [c[0] for c in calls].index("from_manifest") < [c[0] for c in calls].index("timer")  # ...and the timer installed
wiz.force_close()
pump(timeout=0.3)
CONF.unlink(missing_ok=True)

# ---- keep mode, one folder unticked and one failing: neither is written, no timer, and the page says so
MULTI = manifest("old-laptop", 72)
MULTI["jobs"].insert(1, dict(MULTI["jobs"][0], name="pictures", source="~/Pictures", dest=MULTI["jobs"][0]["dest"].replace("Documents", "Pictures")))
to_what([MULTI])
list(wiz.checks.values())[1].set_active(False)  # Pictures left out
rc_for["ssh-keys"] = 23
finish()
assert pump(lambda: "Almost Done" in done_text()), done_text()
assert [c[4] for c in calls if c[0] == "from_manifest"] == [["documents"]]
assert not any(c[0] == "timer" for c in calls)
assert "Not backed up from this laptop: Pictures, SSH keys." in done_text(), done_text()
shot("r12b-partial-and-unticked")
click(vis(), "Done")  # failures outstanding: the config keeps only what was restored
pump(timeout=0.4)
assert [c[4] for c in calls if c[0] == "from_manifest"] == [["documents"]] and not any(c[0] == "timer" for c in calls)
CONF.unlink(missing_ok=True)

# ---- everything fails (keep mode): no config at all, no timer
to_what()
rc_for.update({"documents": 1, "ssh-keys": 1})
finish()
assert pump(lambda: "Almost Done" in done_text()), done_text()
assert not any(c[0] in ("from_manifest", "config", "timer") for c in calls), calls
wiz.force_close()
pump(timeout=0.3)
CONF.unlink(missing_ok=True)

# ---- restore itself blows up: every folder is listed as failed, the dialog is closable
setup.restore_jobs = lambda cfg, names, emit: 1 / 0
to_what()
finish()
assert pump(lambda: "Almost Done" in done_text()) and wiz.get_can_close(), done_text()
assert len([w for w in walk(vis()) if isinstance(w, Adw.ActionRow) and w.has_css_class("error")]) == 2
wiz.force_close()
pump(timeout=0.3)
CONF.unlink(missing_ok=True)
setup.restore_jobs = fake_restore_jobs

# ---- remotes cannot be created: an error page, Back returns, nothing restored, no config; Restore again works
real_make = setup.make_remotes
setup.make_remotes = lambda conf, token, pw: (_ for _ in ()).throw(setup.SetupError("rclone could not read the new remotes."))
to_what()
finish()
assert pump(lambda: wiz.get_can_close() and "Restore Failed" in done_text()), done_text()
assert ".." not in done_text(), done_text()
assert vis().get_title() == "Restore Failed"
assert not CFG_FILE.exists() and not any(c[0] == "restore_jobs" for c in calls)
shot("r13-restore-failed")
click(vis(), "Back")
assert pump(lambda: tag() == "what")
pump(timeout=0.6)
setup.make_remotes = real_make
click(vis(), "Restore")
assert pump(lambda: "Your Files Are Back" in done_text()), (tag(), done_text())  # a fresh progress page, not a hidden one
assert wiz.get_can_close()
wiz.force_close()
pump(timeout=0.3)
CONF.unlink(missing_ok=True)

# ---- a retired folder is listed (off by default), restorable, and never added to the config
RET = manifest("old-laptop", 72)
RET["retired"] = [dict(RET["jobs"][0], name="pictures", source="~/Pictures", since=(NOW - timedelta(days=5)).isoformat(),
                       dest=RET["jobs"][0]["dest"].replace("Documents", "Pictures"))]
to_what([RET])
rows = [w for w in walk(vis()) if isinstance(w, Adw.ActionRow) and not isinstance(w, Adw.SwitchRow)]
got = [(r.get_title(), r.get_subtitle()) for r in rows]
want_since = (NOW - timedelta(days=5))
assert got[2] == ("Pictures", f"~/Pictures · Not backed up since {want_since.day} {want_since.strftime('%b %Y')}"), got
assert [c.get_active() for c in wiz.checks.values()] == [True, True, False]
shot("r16-what-retired")
list(wiz.checks.values())[2].set_active(True)
finish()
assert pump(lambda: "Your Files Are Back" in done_text() or "Almost Done" in done_text()), done_text()
rj = [c for c in calls if c[0] == "restore_jobs"][0]
assert rj[2] == ["documents", "ssh-keys", "pictures"] and rj[1].jobs[2].dest == RET["retired"][0]["dest"], rj
assert [c[4] for c in calls if c[0] == "from_manifest"] == [["documents", "ssh-keys"]]
assert "Not backed up from this laptop: Pictures." in done_text(), done_text()
wiz.force_close()
pump(timeout=0.3)
CONF.unlink(missing_ok=True)

# ---- switching mode after an earlier attempt: the first mode's config is removed and not reused
timer.install = lambda cfg: 1 / 0
to_what()  # old backup, 3 days: keep ON
finish()
assert pump(lambda: wiz.get_can_close() and "Restore Failed" in done_text()), done_text()
assert CFG_FILE.exists() and [c[4] for c in calls if c[0] == "from_manifest"] == [["documents", "ssh-keys"]]
timer.install = lambda cfg: calls.append(("timer",)) or 0
click(vis(), "Back")
assert pump(lambda: tag() == "what")
pump(timeout=0.6)
wiz.keep_row.set_active(False)
wiz.name_row.set_text("new-laptop")
calls.clear()
finish()
assert pump(lambda: "Your Files Are Back" in done_text()), done_text()
cfgc = [c for c in calls if c[0] == "config"]
assert len(cfgc) == 1 and cfgc[0][1] == "new-laptop", calls  # written for the new device, not skipped as "already ours"
assert not any(c[0] == "from_manifest" for c in calls) and ("timer",) in calls
wiz.force_close()
pump(timeout=0.3)
CONF.unlink(missing_ok=True)

# ---- no systemd: files are back, the page says so in plain words
timer.install = lambda cfg: (_ for _ in ()).throw(FileNotFoundError(2, "No such file or directory"))
to_what()
finish()
assert pump(lambda: "Almost Done" in done_text()), done_text()
assert "systemd isn't available" in done_text() and "No such file" not in done_text(), done_text()
assert DONE_ALL in done_text()
shot("r14-no-systemd")
wiz.force_close()
pump(timeout=0.3)
CONF.unlink(missing_ok=True)
timer.install = lambda cfg: calls.append(("timer",)) or 0

# ---- hand-over from the setup wizard: no second sign-in, and the conf survives the hand-over
found[:] = [OLD, LIVE]
calls.clear()
inst.clear()
CONF.unlink(missing_ok=True)
CFG_FILE.unlink(missing_ok=True)
sw = wizard.SetupWizard(win)
sw.present(win)
pump(timeout=0.3)
wiz = sw
click(vis(), "Continue")
click(vis(), "Sign In With Google")
assert pump(lambda: tag() == "existing"), tag()
radios = [w for w in walk(vis()) if isinstance(w, Gtk.CheckButton) and w.get_visible()]
radios[1].set_active(True)
click(vis(), "Restore From This Backup")
assert pump(lambda: inst, 3)
wiz = inst[0]
pump(timeout=0.4)
assert tag() == "passphrase" and wiz.picked == "work-laptop", tag()
assert [c for c in calls if c[0] == "signin.start"] == [("signin.start",)], calls  # signed in once
assert CONF.exists(), "the setup wizard's cleanup must not delete the conf the restore wizard now uses"
assert wiz.token == TOKEN and "SECRET" not in " ".join(labels(wiz.get_child()))
shot("r15-handover-passphrase")
wiz.pw_row.set_text("right-pass")
click(vis(), "Check")
assert pump(lambda: tag() == "what")
assert ("check", "right-pass", "work-laptop") in calls
# closing before restoring removes the conf this run created
wiz.force_close()
pump(timeout=0.4)
assert not CONF.exists(), "token left behind after bailing out"

# a conf that was already there is never deleted
CONF.write_text("[mine]\n")
found[:] = [OLD]
start()
sign_in("passphrase")
wiz.force_close()
pump(timeout=0.4)
assert CONF.read_text().startswith("[mine]")
CONF.unlink()

# closing mid-way (at the passphrase page) removes a conf created in this run
found[:] = [OLD]
start()
sign_in("passphrase")
assert CONF.exists()
wiz.force_close()
pump(timeout=0.4)
assert not CONF.exists()

print("RESTORE OK")
'''

PROGRESS_BODY = r'''
from gi.repository import Gio
from lifeboat.app import progress

progress.FileDialog = FakeFileDialog
win.toast = lambda t: calls.append(("toast", t))
win.refresh = lambda: calls.append(("refresh",))
bp = progress.BackupProgress(win, ["true"], {}, ["documents", "photos", "ssh-keys", "empty"])
wiz = bp.dlg  # shot() default target is wiz.get_child(); nothing is spawned, the test feeds the lines
win.set_busy(True)
bp.dlg.present(win)
bp.dlg.set_can_close(False)
rows = lambda: {s.title: s for s in [*bp.steps.values(), bp.finishing]}
ev = lambda **kw: bp.on_event(json.dumps(kw))
assert [s.state for s in rows().values()] == ["waiting"] * 5
shot("progress-0-waiting")

ev(event="job_start", name="documents")
assert bp.wt.get_subtitle() == "0 of 4"
bp.on_log("INFO : a.txt: Copied (new)")
assert rows()["Documents"].state == "running" and not bp.dlg.get_can_close()
shot("progress-1-running")
ev(event="job_end", name="documents", rc=0, skipped=False, files=2, error="")
ev(event="job_start", name="photos")
bp.on_log("photos: refusing to sync: source /x is missing or empty - nothing was changed")
ev(event="job_end", name="photos", rc=2, skipped=False, files=0, reason="empty_with_backup",
   error="photos: refusing to sync: source /x is missing or empty - nothing was changed")
ev(event="job_start", name="ssh-keys")
ev(event="job_end", name="ssh-keys", rc=0, skipped=False, files=0, error="")
ev(event="job_start", name="empty")
ev(event="job_end", name="empty", rc=0, skipped=True, files=0, error="")
ev(event="finishing_start")
ev(event="finishing_end", ok=True)
r = rows()
assert [r[n].state for n in ("Documents", "Photos", "SSH keys", "Empty", "Finishing up")] == ["ok", "error", "ok", "skip", "ok"]
assert r["Documents"].row.get_subtitle() == "Copied 2 files"
assert r["SSH keys"].row.get_subtitle() == "Already up to date"
assert r["Empty"].row.get_subtitle() == "Nothing to back up yet"
assert isinstance(r["Photos"].row, Adw.ExpanderRow) and r["Photos"].row.get_expanded()
assert "Is the drive plugged in" in r["Photos"].row.get_subtitle()
assert bp.list.get_row_at_index(1) is r["Photos"].row  # swapped in place, order kept
ev(event="done", ok=False)
assert not bp.finished and not bp.retry.get_visible()  # the child may still be running
bp._ended()
assert bp.summary.get_label() == "1 folder not backed up", bp.summary.get_label()
assert bp.summary_sub.get_label() == "The others are safe on Google Drive"
assert bp.retry.get_visible() and bp.dlg.get_can_close()
assert bp.wt.get_subtitle() == "Finished with problems"
assert "Try Again" in [b.get_label() for b in buttons(bp.dlg.get_child())] and bp.retry.is_ancestor(bp.dlg) and bp.retry.get_visible()
shot("progress-2-failed")

for reason, want in (("missing", "doesn't exist any more"), ("dest_unreadable", "Check your connection"),
                     ("mkdir_failed", "create its folder"), ("rclone", "ERROR : boom")):
    st = progress.Step("x")
    bp.list.append(st.row)
    bp._job_end(st, {"rc": 1, "error": "ERROR : boom", "reason": reason})
    assert want in st.row.get_subtitle(), (reason, st.row.get_subtitle())
    bp.list.remove(st.row)

# the Log toggle swaps pages, "Show in Log" lands on the failed job's lines
assert bp.stack.get_visible_child_name() == "progress"
click(bp.dlg.get_child(), "Show in Log")
assert bp.stack.get_visible_child_name() == "log" and bp.log_btn.get_active() and not bp.progress_btn.get_active()
assert "Copied (new)" in bp.buf.get_text(bp.buf.get_start_iter(), bp.buf.get_end_iter(), False)
pump(timeout=1.5)  # let the toggle buttons finish their CSS transition before the snapshot
shot("progress-3-log")
bp.progress_btn.set_active(True)
assert bp.stack.get_visible_child_name() == "progress" and not bp.log_btn.get_active()
bp.log_btn.set_active(True)

click(bp.dlg.get_child(), "Copy Error")
assert ("toast", "Copied") in calls
saved = HOME / "kit.pdf"
click(bp.dlg.get_child(), "Save Log…")
assert "Copied (new)" in saved.read_text() and ("toast", "Log saved") in calls

# Try Again resets the same dialog
bp._reset()  # Try Again, with the Log showing, goes back to Progress
assert bp.stack.get_visible_child_name() == "progress" and bp.progress_btn.get_active() and not bp.log_btn.get_active()
assert [s.state for s in rows().values()] == ["waiting"] * 5 and not bp.retry.get_visible() and not bp.head.get_visible()

# a clean run
ev(event="job_start", name="documents")
for n in ("documents", "photos", "ssh-keys", "empty"):
    ev(event="job_end", name=n, rc=0, skipped=False, files=1, error="")
ev(event="done", ok=True)
bp._ended()
assert bp.wt.get_subtitle() == "Finished" and bp.summary.get_label() == "Backed up" and bp.summary_sub.get_label() == "4 files copied"
assert not bp.retry.get_visible() and bp.list.get_row_at_index(4) is None  # no finishing row when nothing finished up
shot("progress-4-done")

# Try Again via its button: spawns again, and a callback of the old run cannot touch the new one
spawned = []
def fake_spawn():
    spawned.append(1)
    return Gio.Subprocess.new(["sh", "-c", 'echo hello >&2; echo "$1"', "sh", json.dumps({"event": "done", "ok": True})],
                              Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE)
bp._spawn = fake_spawn
bp._reset()
win.set_busy(False)
ev(event="job_start", name="documents")
bp.verdict = False
bp._ended()
assert bp.retry.get_visible()
old_gen = bp.gen
click(bp.dlg.get_child(), "Try Again")
assert spawned == [1] and bp.gen == old_gen + 1 and not bp.finished
bp._ended(old_gen)  # the previous run's late eof
assert not bp.finished and win.busy
assert pump(lambda: bp.finished), "the new run never finished"
assert bp.summary.get_label() == "Backed up" and "hello" in bp.buf.get_text(bp.buf.get_start_iter(), bp.buf.get_end_iter(), False)

# a stream that ends without a verdict marks what was running as failed
bp._reset()
win.set_busy(True)
ev(event="job_start", name="documents")
bp._ended()
assert rows()["Documents"].state == "error" and bp.summary.get_label() == "Backup didn't finish"
print("PROGRESS OK")
'''

WIZARD_SCRIPT = PRELUDE + SETUP_BODY
RESTORE_SCRIPT = PRELUDE + RESTORE_BODY
PROGRESS_SCRIPT = PRELUDE + PROGRESS_BODY


def run_broadway(env, script, expect):
    if not shutil.which("gtk4-broadwayd"):
        pytest.skip("gtk4-broadwayd not available")
    home = env / "home"
    for d in ("Documents", "Pictures", ".ssh", "Extra"):
        (home / d).mkdir(parents=True)
        (home / d / "f.txt").write_text("x")
    bw = subprocess.Popen(["gtk4-broadwayd", ":9"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(1)
        e = dict(os.environ, GDK_BACKEND="broadway", BROADWAY_DISPLAY=":9", HOME=str(home),
                 XDG_CONFIG_HOME=str(env / "config"), XDG_STATE_HOME=str(env / "state"),
                 PYTHONPATH=str(Path(__file__).parent.parent))
        e.pop("WAYLAND_DISPLAY", None)
        e.pop("DISPLAY", None)
        p = subprocess.run([sys.executable, "-c", script, str(home)], env=e, capture_output=True, text=True,
                           timeout=180)
    finally:
        bw.terminate()
    if p.returncode == 77:
        pytest.skip("GTK 4 / libadwaita not available")
    assert p.returncode == 0, p.stdout + p.stderr
    assert expect in p.stdout
    assert "Traceback" not in p.stderr and "g_value" not in p.stderr, p.stderr
    print(p.stderr)  # errors inside signal handlers do not fail the script


def test_setup_wizard_walkthrough(env):
    run_broadway(env, WIZARD_SCRIPT, "WIZARD OK")


def test_restore_wizard_walkthrough(env):
    run_broadway(env, RESTORE_SCRIPT, "RESTORE OK")


def test_backup_checklist_dialog_follows_events(env):
    run_broadway(env, PROGRESS_SCRIPT, "PROGRESS OK")
