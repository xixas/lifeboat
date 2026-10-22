import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gsk, Gtk  # noqa: E402

from lifeboat import APP_ID, GUIDE_URL, REPO_URL, __version__  # noqa: E402
from lifeboat.app.window import Win  # noqa: E402

SNAP = os.environ.get("LIFEBOAT_SNAP") if os.environ.get("LIFEBOAT_CYCLE") else None


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id=os.environ.get("LIFEBOAT_APP_ID", APP_ID), flags=Gio.ApplicationFlags.NON_UNIQUE if os.environ.get("LIFEBOAT_CYCLE")
                         else Gio.ApplicationFlags.DEFAULT_FLAGS)
        act = Gio.SimpleAction.new("show-page", GLib.VariantType.new("s"))
        act.connect("activate", lambda _a, p: self.win().show_page(p.get_string()))
        self.add_action(act)
        about = Gio.SimpleAction.new("about", None)
        about.connect("activate", lambda *_: self.about_dialog().present(self.win()))
        self.add_action(about)
        guide = Gio.SimpleAction.new("guide", None)
        guide.connect("activate", lambda *_: Gio.AppInfo.launch_default_for_uri(GUIDE_URL, None))
        self.add_action(guide)

    def about_dialog(self):
        d = Adw.AboutDialog(
            application_name="Lifeboat", application_icon=APP_ID, version=__version__,
            developer_name="Vikas Kyatannawar",
            developers=["Vikas Kyatannawar (xixas) https://github.com/xixas"],
            copyright="© 2026 Vikas Kyatannawar",
            license_type=Gtk.License.MIT_X11,
            comments="Backs up your laptop to Google Drive (or any rclone remote) and shows whether it is safe to lose.",
            website=REPO_URL,
            issue_url=REPO_URL + "/issues",
            release_notes_version=__version__,
            release_notes="<p>Generic release.</p><ul><li>Everything comes from a config file</li>"
                          "<li>Restore page with old versions</li><li>Command line and systemd timers</li></ul>")
        d.add_link("Usage guide", GUIDE_URL)
        return d

    def win(self):
        w = self.props.active_window or Win(self, snap=bool(SNAP))
        w.present()
        return w

    def do_activate(self):
        w = self.win()
        if os.environ.get("LIFEBOAT_CYCLE"):
            self.cycle(w)

    def cycle(self, w):
        """Debug mode: visit every page once, optionally saving a PNG of each, then quit."""
        seq = []

        def step():
            if not seq:
                seq.extend(w.page_ids() + [None])
            pid = seq.pop(0)
            if pid is None:
                self.quit()
                return False
            w.show_page(pid)
            if SNAP:
                GLib.timeout_add(600, snap, pid)
            return True

        def snap(pid, tries=0):
            p = Gtk.WidgetPaintable.new(w)
            sn = Gtk.Snapshot()
            p.snapshot(sn, w.get_width(), w.get_height())
            node = sn.to_node()
            if node is None and tries < 8:  # nothing drawn yet; try again shortly
                GLib.timeout_add(400, snap, pid, tries + 1)
                return False
            r = w.get_renderer()
            if r is None:  # Broadway has no GL renderer; fall back to cairo
                r = Gsk.CairoRenderer()
                r.realize(w.get_surface())
            r.render_texture(node, None).save_to_png(f"{SNAP}/lifeboat-{pid}.png")
            return False

        GLib.timeout_add_seconds(3, lambda: GLib.timeout_add_seconds(1, step) and False)
