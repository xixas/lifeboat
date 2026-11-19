"""The "found on this laptop" list: one preferences group per catalogue group and a collapsed "Ignored" expander.
Shared by the setup wizard and the Found page. It owns the layout; the caller owns what a row's switch does."""
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from lifeboat.app.wizard_state import tilde  # noqa: E402
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
                    row.add_prefix(Gtk.Image.new_from_icon_name("system-lock-screen-symbolic", tooltip_text="Encrypted"))
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
