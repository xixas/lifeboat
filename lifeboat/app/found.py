"""The "found on this laptop" list: one preferences group per catalogue group and a collapsed "Ignored" expander.
Shared by the setup wizard and the Found page. It owns the layout; the caller owns what a row's switch does."""
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from lifeboat import catalogue, found  # noqa: E402
from lifeboat.app.widgets import spinner  # noqa: E402
from lifeboat.app.wizard_state import tilde  # noqa: E402
from lifeboat.config import ConfigError, load_config  # noqa: E402
from lifeboat.found import grouped  # noqa: E402

# These can hold dozens of rows nobody wants to read by default, so their rows sit in an expander.
COLLAPSED = ("skip", "other")


class FoundList:
    """items: catalogue.scan() items. make_row(item) builds the item's switch row (called once per item); on_ignore(item) and
    on_restore(item) run after an item moves in or out of the Ignored expander; on_shown(item) runs once when the item's row first
    becomes visible, so a slow job (a size walk) waits for a collapsed group to be opened."""

    def __init__(self, items, make_row, on_ignore, on_restore, on_shown=None):
        self.items = items
        self._on_ignore, self._on_restore, self._on_shown = on_ignore, on_restore, on_shown
        self.ignored = {i["path"] for i in items if i.get("ignored")}
        self.groups: list[Adw.PreferencesGroup] = []
        self._sections = []  # (group widget, rows container, title, items, encrypted, collapsed)
        self._attached: dict[int, list] = {}
        self._shown: set[str] = set()
        self.rows: dict[str, Adw.ActionRow] = {}
        self.ignored_rows: dict[str, Adw.ActionRow] = {}
        for key, title, members, encrypted in grouped(items):
            collapsed = key in COLLAPSED
            widget = Adw.PreferencesGroup()
            container = None
            if collapsed:
                container = Adw.ExpanderRow()
                widget.add(container)
                container.connect("notify::expanded", lambda *_: self._show_visible())
            for i in members:
                row = make_row(i)
                if i["secret"] and not encrypted:
                    row.add_prefix(Gtk.Image(icon_name="system-lock-screen-symbolic", tooltip_text="Encrypted"))
                btn = Gtk.Button(label="Ignore", valign=Gtk.Align.CENTER, css_classes=["flat"], tooltip_text="Stop suggesting this")
                btn.connect("clicked", lambda *_, i=i: self._ignore(i))
                row.add_suffix(btn)
                self.rows[i["path"]] = row
            self.groups.append(widget)
            self._sections.append((widget, container or widget, title, members, encrypted, collapsed))
        self.ignored_group = Adw.PreferencesGroup()
        self.ignored_exp = Adw.ExpanderRow()
        self.ignored_group.add(self.ignored_exp)
        self._render()

    def _visible(self, members):
        return [i for i in members if i["path"] not in self.ignored]

    def _render(self):
        for n, (widget, container, title, members, encrypted, collapsed) in enumerate(self._sections):
            for r in self._attached.get(n, []):
                container.remove(r)
            rows = [self.rows[i["path"]] for i in self._visible(members)]
            for r in rows:
                container.add_row(r) if collapsed else container.add(r)
            self._attached[n] = rows
            widget.set_title(GLib.markup_escape_text(f"{title} · {len(rows)}"))  # group titles read markup
            widget.set_description("Always encrypted" if encrypted else "")
            widget.set_visible(bool(rows))
            if collapsed:
                container.set_title(f"Show {len(rows)} item{'s' if len(rows) != 1 else ''}")
        for r in self._attached.get("ignored", []):
            self.ignored_exp.remove(r)
        rows = [self._ignored_row(i) for i in self.items if i["path"] in self.ignored]
        for r in rows:
            self.ignored_exp.add_row(r)
        self._attached["ignored"] = rows
        self.ignored_exp.set_title(f"Ignored · {len(rows)}")
        self.ignored_group.set_visible(bool(rows))
        self._show_visible()

    def _ignored_row(self, item):
        if item["path"] not in self.ignored_rows:
            row = Adw.ActionRow(title=item["label"], subtitle=tilde(item["path"]), use_markup=False)
            btn = Gtk.Button(label="Restore", valign=Gtk.Align.CENTER, css_classes=["flat"])
            btn.connect("clicked", lambda *_: self._restore(item))
            row.add_suffix(btn)
            self.ignored_rows[item["path"]] = row
        return self.ignored_rows[item["path"]]

    def _show_visible(self):
        """Tell the caller about every row the user can see now: those in an open or plain group."""
        if self._on_shown is None:
            return
        for _w, container, _t, members, _e, collapsed in self._sections:
            if collapsed and not container.get_expanded():
                continue
            for i in self._visible(members):
                if i["path"] not in self._shown:
                    self._shown.add(i["path"])
                    self._on_shown(i)

    def _ignore(self, item):
        self.ignored.add(item["path"])
        self._render()
        self._on_ignore(item)

    def _restore(self, item):
        self.ignored.discard(item["path"])
        self._render()
        self._on_restore(item)


class FoundDialog(Adw.Dialog):
    """Everything the catalogue finds on this laptop, after setup: folders a job already covers are on, and turning another on
    adds a job to the config. Ignore and Restore edit the config's ignore list. Sizes are not measured here."""

    def __init__(self, win):
        super().__init__(title="Find more to back up", content_width=480, content_height=640, width_request=320, height_request=300)
        self.win = win
        self.dead = False
        self.covered: dict[str, bool] = {}
        self.view = Adw.ToolbarView()
        self.view.add_top_bar(Adw.HeaderBar())
        self.toasts = Adw.ToastOverlay(child=self.view)
        self.set_child(self.toasts)
        self.view.set_content(Adw.StatusPage(title="Looking around…", child=spinner(), css_classes=["compact"]))
        self.connect("closed", self._closed)
        self.path = win.cfg.path
        win.in_thread(self._scan, self._loaded, lambda _e: self._failed("Could not look through this laptop."))

    def _scan(self):
        items = catalogue.scan(Path.home())
        found.mark_seen(i["key"] for i in items)  # shown now, so Overview stops calling them new
        return items, load_config(self.path)

    def _closed(self, *_):
        self.dead = True
        self.win.refresh()

    def _failed(self, text):
        if not self.dead:
            self.view.set_content(Adw.StatusPage(icon_name="dialog-error-symbolic", title="Nothing to show", description=text))

    def _loaded(self, res):
        if self.dead:
            return
        items, cfg = res
        self.items = items
        self.covered = {i["path"]: found.covered(i, cfg) for i in items}
        self.list = FoundList(items, self._make_row, lambda i: self._ignore(i, True), lambda i: self._ignore(i, False))
        pg = Adw.PreferencesPage()
        pg.add(Adw.PreferencesGroup(description="Turn on what you want in your backup. Folders a job already covers are on."))
        for g in self.list.groups:
            pg.add(g)
        pg.add(self.list.ignored_group)
        self.view.set_content(pg)

    def _make_row(self, item):
        on = self.covered[item["path"]]
        row = Adw.SwitchRow(title=item["label"], subtitle=tilde(item["path"]) + (" · already backed up" if on else ""), active=on,
                            sensitive=not on, use_markup=False)
        row.connect("notify::active", lambda r, _p: self._toggled(item, r))
        return row

    def _toggled(self, item, row):
        if not row.get_active() or self.covered[item["path"]]:
            return
        self.covered[item["path"]] = True
        row.set_sensitive(False)
        path = self.win.cfg.path

        def done(job):
            row.set_subtitle(f"{tilde(item['path'])} · added, backed up from the next run")
            self.toasts.add_toast(Adw.Toast.new(f"Added {item['label']} to your backup"))
            self.win.refresh()

        def fail(e):
            self.covered[item["path"]] = False
            row.set_sensitive(True)
            row.set_active(False)
            self.toasts.add_toast(Adw.Toast.new(str(e) if isinstance(e, ConfigError) else "Could not change the config"))

        self.win.in_thread(lambda: found.add_item(load_config(path), item), done, fail)

    def _ignore(self, item, on):
        path = self.win.cfg.path

        def fail(e):
            self.toasts.add_toast(Adw.Toast.new(str(e) if isinstance(e, ConfigError) else "Could not change the config"))

        self.win.in_thread(lambda: found.set_ignored(load_config(path), item["key"], on), lambda _r: self.win.refresh(), fail)
