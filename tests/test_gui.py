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


WIZARD_SCRIPT = r'''
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
from lifeboat.app import wizard
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
wizard.UriLauncher = FakeUri
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
win.refresh = lambda: calls.append(("refresh",))
win.open_restore_wizard = lambda device=None: calls.append(("restore", device))
win.present()
win.apply(gather())
assert win.data["kind"] == "noconfig"
win.show_page("overview")
names = [b.get_label() for b in buttons(win)]
for want in ("Set Up Backup", "Restore From a Backup", "Edit a config file instead"):
    assert want in names, names
shot("00-gate", win.get_content())


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
assert pump(lambda: ("run_cli", "Back up now", "run") in calls), calls
assert ("make_remotes", TOKEN, pw) in calls
cfgc = [c for c in calls if c[0] == "config"][0]
assert cfgc[1] == "my-laptop" and cfgc[3] == "09:00" and cfgc[4] == []
chosen = [c["path"] for c in cfgc[2]]
assert str(HOME / "Extra") in chosen and str(HOME / ".ssh") in chosen, chosen
assert [c for c in cfgc[2] if c["path"] == str(HOME / ".ssh")][0]["secret"] is True
assert ("timer",) in calls
order = [c[0] for c in calls]
assert order.index("make_remotes") < order.index("config") < order.index("timer") < order.index("run_cli")
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
assert pump(lambda: ("run_cli", "Back up now", "run") in calls), calls
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
assert "Almost Done" in txt and "daily timer is not" in txt and "No such file" in txt, txt
assert not any(c[0] == "run_cli" for c in calls)
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


def test_setup_wizard_walkthrough(env, monkeypatch):
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
        p = subprocess.run([sys.executable, "-c", WIZARD_SCRIPT, str(home)], env=e, capture_output=True, text=True,
                           timeout=120)
    finally:
        bw.terminate()
    if p.returncode == 77:
        pytest.skip("GTK 4 / libadwaita not available")
    assert p.returncode == 0, p.stdout + p.stderr
    assert "WIZARD OK" in p.stdout
    assert "Traceback" not in p.stderr and "g_value" not in p.stderr, p.stderr
    print(p.stderr)  # errors inside signal handlers do not fail the script
