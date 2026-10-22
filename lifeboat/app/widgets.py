"""Small libadwaita helpers shared by the pages."""
from datetime import datetime

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from lifeboat.engine import parse_time  # noqa: E402

ICONS = {"ok": "object-select-symbolic", "warn": "dialog-warning-symbolic", "error": "dialog-error-symbolic"}
CSS = {"ok": "success", "warn": "warning", "error": "error"}
WORDS = {"ok": "OK", "warn": "Needs attention", "error": "Failed"}


def ago(iso) -> str:
    dt = parse_time(iso)
    if dt is None:
        return "never"
    s = max(0, (datetime.now(dt.tzinfo) - dt).total_seconds())
    for size, unit in ((86400, "day"), (3600, "hour"), (60, "minute")):
        if s >= size:
            n = int(s // size)
            return f"{n} {unit}{'s' if n != 1 else ''} ago"
    return "just now"


def fmt_time(iso, default="Not scheduled") -> str:
    dt = parse_time(iso)
    return dt.strftime("%a %d %b, %H:%M") if dt else default


def row(title, subtitle="", suffix=None, icon=None):
    r = Adw.ActionRow(use_markup=False)
    r.set_title(title)
    r.set_subtitle(subtitle)
    if icon:
        r.add_prefix(Gtk.Image.new_from_icon_name(icon))
    for w in suffix or []:
        r.add_suffix(w)
    return r


def label(text, css):
    return Gtk.Label(label=text, css_classes=css if isinstance(css, list) else [css], valign=Gtk.Align.CENTER)


def group(title=None, desc=None, rows=()):
    g = Adw.PreferencesGroup(title=title or "", description=desc or "")
    for r in rows:
        g.add(r)
    return g
