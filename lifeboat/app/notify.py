"""Desktop notifications for `lifeboat check`."""
from gi.repository import Gio, GLib

from lifeboat import APP_ID


def send(problems: list[dict]) -> None:
    app = Gio.Application(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
    app.register(None)
    for p in problems:
        n = Gio.Notification.new(p["title"])
        n.set_body(p["body"])
        n.set_priority(Gio.NotificationPriority.HIGH if p["urgent"] else Gio.NotificationPriority.NORMAL)
        n.set_default_action_and_target("app.show-page", GLib.Variant("s", p["page"]))
        app.send_notification(f"lifeboat-{p['page']}", n)
    loop = GLib.MainLoop()
    GLib.timeout_add(1500, loop.quit)
    loop.run()
