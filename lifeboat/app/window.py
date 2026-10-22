import os
import sys
import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from lifeboat import APP_ID, engine  # noqa: E402
from lifeboat.app.pages import PAGE_ICONS, PAGE_NAMES, PagesMixin  # noqa: E402
from lifeboat.app.reader import read_lines  # noqa: E402
from lifeboat.app.widgets import CSS, ICONS  # noqa: E402
from lifeboat.config import ConfigError, config_path, load_config  # noqa: E402

DOT_PAGES = ("overview", "backups", "settings", "skills")


def gather() -> dict:
    """Everything the window shows. Runs off the main thread."""
    path = config_path()
    if not path.exists():
        return {"kind": "noconfig", "path": path}
    try:
        cfg = load_config(path)
    except ConfigError as e:
        return {"kind": "error", "path": path, "error": str(e)}
    return {"kind": "ok", "path": path, "cfg": cfg, "st": engine.status(cfg)}


class Win(PagesMixin, Adw.ApplicationWindow):
    def __init__(self, app, snap=False):
        super().__init__(application=app, title="Lifeboat", default_width=980 if snap else 900,
                         default_height=720 if snap else 680)
        self.set_size_request(360, 294)
        self.data, self.busy, self.page, self.action_btns, self.dots = None, False, "overview", [], {}
        self.versions, self.rsel = {}, {"job": 0, "ver": 0, "target": None, "row": None}
        self.toasts = Adw.ToastOverlay()
        self.split = Adw.NavigationSplitView()
        self.toasts.set_child(self.split)
        self.set_content(self.toasts)
        bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 600sp"))
        bp.add_setter(self.split, "collapsed", True)
        self.add_breakpoint(bp)
        # sidebar
        self.list = Gtk.ListBox(css_classes=["navigation-sidebar"])
        tv = Adw.ToolbarView(content=Gtk.ScrolledWindow(child=self.list))
        menu = Gio.Menu()
        menu.append("How it works", "app.show-page('help')")
        menu.append("Usage guide", "app.guide")
        menu.append("About Lifeboat", "app.about")
        shb = Adw.HeaderBar(title_widget=Adw.WindowTitle(title="Lifeboat", subtitle=""))
        shb.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu, tooltip_text="Main menu"))
        tv.add_top_bar(shb)
        self.split.set_sidebar(Adw.NavigationPage(title="Lifeboat", child=tv))
        # content
        self.refresh_btn = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Refresh")
        self.refresh_btn.connect("clicked", lambda *_: self.refresh())
        hb = Adw.HeaderBar()
        hb.pack_end(self.refresh_btn)
        self.ctv = Adw.ToolbarView()
        self.ctv.add_top_bar(hb)
        self.cpage = Adw.NavigationPage(title="Overview", child=self.ctv)
        self.split.set_content(self.cpage)
        self.ctv.set_content(Adw.StatusPage(title="Loading…", icon_name=APP_ID))
        self.list.connect("row-selected", lambda _l, r: r and self.show_page(r.pid))
        self.list.connect("row-activated", lambda *_: self.split.set_show_content(True))
        self.set_pages(["overview", "help", "prefs"])
        self.refresh()

    # -- navigation / data
    @property
    def cfg(self):
        return self.data["cfg"] if self.data and self.data["kind"] == "ok" else None

    @property
    def st(self):
        return self.data["st"] if self.cfg else None

    def page_ids(self):
        if self.cfg is None:
            return ["overview", "help", "prefs"]
        ids = ["overview", "backups", "restore"]
        if self.cfg.settings_command:
            ids.append("settings")
        if self.st["skills"] is not None:
            ids.append("skills")
        return ids + ["help", "prefs"]

    def set_pages(self, ids):
        self.pids = ids
        self.dots = {}
        while (r := self.list.get_row_at_index(0)) is not None:
            self.list.remove(r)
        for pid in ids:
            box = Gtk.Box(spacing=12, margin_top=10, margin_bottom=10, margin_start=6, margin_end=6)
            box.append(Gtk.Image.new_from_icon_name(PAGE_ICONS[pid]))
            box.append(Gtk.Label(label=PAGE_NAMES[pid], xalign=0, hexpand=True))
            if pid in DOT_PAGES:
                dot = Gtk.Image.new_from_icon_name(ICONS["warn"])
                self.dots[pid] = dot
                box.append(dot)
            r = Gtk.ListBoxRow(child=box)
            r.pid = pid
            self.list.append(r)

    def show_page(self, pid):
        if pid not in self.pids:
            pid = "overview"
        self.page = pid
        self.list.select_row(self.list.get_row_at_index(self.pids.index(pid)))
        self.cpage.set_title(PAGE_NAMES[pid])
        if self.data:
            self.action_btns = []
            self.ctv.set_content(Gtk.ScrolledWindow(child=getattr(self, "page_" + pid)()))

    def refresh(self):
        self.refresh_btn.set_sensitive(False)

        def work():
            try:
                d = gather()
            except Exception as e:  # keep the UI alive on any data error
                print("gather failed:", e, file=sys.stderr)
                d = None
            GLib.idle_add(self.apply, d)
        threading.Thread(target=work, daemon=True).start()

    def apply(self, d):
        self.refresh_btn.set_sensitive(True)
        if d is None:
            self.toast("Could not read backup state")
            return
        self.data = d
        self.versions = {}
        if self.page not in self.page_ids():
            self.page = "overview"
        self.set_pages(self.page_ids())
        for pid, dot in self.dots.items():
            st = self.st["status"][pid] if self.st else "warn"
            dot.set_from_icon_name(ICONS[st])
            for c in CSS.values():
                dot.remove_css_class(c)
            dot.add_css_class(CSS[st])
        self.show_page(self.page)

    def toast(self, text):
        self.toasts.add_toast(Adw.Toast.new(text))

    # -- widgets
    def button(self, text, cb, css=("pill",), action=True):
        b = Gtk.Button(label=text, css_classes=list(css))
        b.connect("clicked", lambda *_: cb())
        if action:
            b.set_sensitive(not self.busy)
            self.action_btns.append(b)
        return b

    def _box(self, btns):
        box = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER, margin_top=6, margin_bottom=6)
        for b in btns:
            box.append(b)
        return box

    def copy_btn(self, text):
        b = Gtk.Button(icon_name="edit-copy-symbolic", tooltip_text=f"Copy: {text}", valign=Gtk.Align.CENTER, css_classes=["flat"])
        b.connect("clicked", lambda _b: self.copy(text))
        return b

    def copy(self, text):
        self.get_display().get_clipboard().set_text(text)
        self.toast("Copied")

    def open_url(self, url):
        Gio.AppInfo.launch_default_for_uri(url, None)

    def open_path(self, path):
        self.open_url(Path(path).resolve().as_uri())

    # -- actions
    @staticmethod
    def cli(*args):
        """argv that runs this same lifeboat as a child process, plus the env it needs."""
        root = str(Path(__file__).resolve().parent.parent.parent)
        pp = os.pathsep.join(p for p in (root, os.environ.get("PYTHONPATH", "")) if p)
        return [sys.executable, "-m", "lifeboat", *args], {"PYTHONPATH": pp}

    def run_cli(self, title, *args):
        argv, env = self.cli(*args)
        self.stream(title, argv, env)

    def set_busy(self, b):
        self.busy = b
        for x in self.action_btns:
            x.set_sensitive(not b)

    def in_thread(self, work, done):
        """Run work() off the main loop and pass its result to done() on it."""
        def target():
            res = work()
            GLib.idle_add(done, res)
        threading.Thread(target=target, daemon=True).start()

    def stream(self, title, argv, env=None):
        if self.busy:
            return
        self.set_busy(True)
        wt = Adw.WindowTitle(title=title, subtitle="Running…")
        spin = Adw.Spinner()
        hb = Adw.HeaderBar(title_widget=wt)
        hb.pack_start(spin)
        buf = Gtk.TextBuffer()
        view = Gtk.TextView(buffer=buf, editable=False, cursor_visible=False, monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR,
                            top_margin=8, bottom_margin=8, left_margin=12, right_margin=12)
        tv = Adw.ToolbarView(content=Gtk.ScrolledWindow(child=view, vexpand=True))
        tv.add_top_bar(hb)
        dlg = Adw.Dialog(title=title, child=tv, content_width=720, content_height=480, can_close=False)
        dlg.present(self)

        def add(text):
            buf.insert(buf.get_end_iter(), text + "\n")
            view.scroll_to_mark(buf.create_mark(None, buf.get_end_iter(), False), 0, False, 0, 0)

        def finish(ok, rc):
            spin.set_visible(False)
            wt.set_subtitle("Finished" if ok else f"Failed (exit {rc})")
            dlg.set_can_close(True)
            self.toast(f"{title}: done" if ok else f"{title}: failed (exit {rc})")
            self.set_busy(False)
            self.refresh()

        def done(proc, res):
            proc.wait_finish(res)
            rc = proc.get_exit_status() if proc.get_if_exited() else -1
            finish(rc == 0, rc)

        try:
            launcher = Gio.SubprocessLauncher.new(Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_MERGE)
            # A piped Python child block-buffers stdout, so the dialog would stay empty until
            # the end; unbuffered output streams each line as it happens.
            launcher.setenv("PYTHONUNBUFFERED", "1", True)
            for k, v in (env or {}).items():
                launcher.setenv(k, v, True)
            proc = launcher.spawnv(argv)
        except GLib.Error as e:
            add(f"Could not start: {e.message}")
            finish(False, 127)
            return
        read_lines(proc.get_stdout_pipe(), add, lambda: proc.wait_async(None, done))
