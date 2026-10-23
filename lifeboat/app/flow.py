"""What the setup and restore wizards share: a dialog with a navigation stack, the Google sign-in page and its poller,
discovery of existing backups, banners, the recovery-passphrase page and its check, and the error and timer pages.
Subclasses decide what happens once backups are found (`_discovered`) and once the passphrase is right (`_passphrase_ok`)."""
import sys
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from lifeboat import setup, timer  # noqa: E402
from lifeboat.app.widgets import spinner  # noqa: E402
from lifeboat.config import config_path  # noqa: E402

# Indirection so the Broadway test can swap the browser launcher for a stub.
UriLauncher = Gtk.UriLauncher

NO_TIMER = "You can still back up from the Lifeboat window."


def pill(text, cb, suggested=True):
    b = Gtk.Button(label=text, css_classes=["pill", "suggested-action"] if suggested else ["pill"], halign=Gtk.Align.CENTER)
    b.connect("clicked", lambda *_: cb())
    return b


def vbox(*children, spacing=12):
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing, halign=Gtk.Align.CENTER)
    for c in children:
        box.append(c)
    return box


def install_timer(cfg) -> str | None:
    """Schedule the daily backup. None when it worked, otherwise a plain sentence for the user; the technical detail
    goes to stderr only."""
    try:
        rc = timer.install(cfg)
    except OSError as e:
        print("timer install failed:", e, file=sys.stderr)
        return f"systemd isn't available, so Lifeboat can't schedule daily backups. {NO_TIMER}"
    if rc:
        print("timer install failed: exit", rc, file=sys.stderr)
        return f"systemd refused to schedule daily backups (exit {rc}). {NO_TIMER}"
    return None


class AccountDialog(Adw.Dialog):
    def __init__(self, win, title):
        super().__init__(title=title, content_width=480, content_height=640, width_request=320, height_request=300)
        self.win = win
        self.mode = "new"  # "add": this account already has backups, so the existing passphrase is asked for
        self.token = None  # kept in memory only, never logged or shown
        self.passphrase = None
        self.existing: list[dict] = []
        self.signin = None
        self.dead = False
        self._poll_id = 0
        self._polling = False
        self._conf_made_here = False  # we created rclone.conf in this run, so we may delete it if the user bails out
        self._refresh_on_close = False
        self.nav = Adw.NavigationView()
        self.set_child(self.nav)
        bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 400sp"))
        self.add_breakpoint(bp)
        self._bp = bp
        self.connect("closed", lambda *_: self._cleanup())

    # -- plumbing
    def _cleanup(self):
        self.dead = True
        self._stop_poller()
        self._drop_signin()
        self.token = self.passphrase = None
        if self._conf_made_here and not config_path().exists():
            # the Drive token was written before the user committed to anything: take it back
            setup.rclone_conf_path().unlink(missing_ok=True)
        if self._refresh_on_close:
            self.win.refresh()

    def _stop_poller(self):
        if self._poll_id:
            GLib.source_remove(self._poll_id)
            self._poll_id = 0

    def _drop_signin(self):
        """Cancel the rclone sign-in in the background: cancel() waits for the process and must not block the main loop."""
        s, self.signin = self.signin, None
        if s is not None:
            threading.Thread(target=s.cancel, daemon=True).start()

    def _page(self, tag, title, content, bottom=None, banner=None, back=True):
        tv = Adw.ToolbarView(content=content)
        tv.add_top_bar(Adw.HeaderBar(show_back_button=back))
        if banner is not None:
            tv.add_top_bar(banner)
        if bottom is not None:
            tv.add_bottom_bar(bottom)
        page = Adw.NavigationPage(title=title, tag=tag, child=tv)
        page.toolbar = tv
        return page

    def _bar(self, *buttons):
        box = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER, margin_top=12, margin_bottom=12, margin_start=24, margin_end=24)
        for b in buttons:
            box.append(b)
        self._bp.add_setter(box, "margin-start", 12)
        self._bp.add_setter(box, "margin-end", 12)
        return box

    def _banner(self):
        b = Adw.Banner(title="", button_label="Try Again")
        b.connect("button-clicked", lambda *_: self._retry())
        self._retry = lambda: None
        return b

    def _show_error(self, banner, msg, retry):
        banner.set_title(msg.removesuffix(" Try again."))  # the banner has a Try Again button
        banner.set_revealed(True)
        self._retry = retry

    @staticmethod
    def _mark_invalid(widget, error_label, message):
        """Error style, accessible INVALID state and description on an entry, with the message shown in a label below it."""
        error_label.set_label(message or "")
        error_label.set_visible(bool(message))
        if message:
            widget.add_css_class("error")
        else:
            widget.remove_css_class("error")
        widget.update_state([Gtk.AccessibleState.INVALID], [int(Gtk.AccessibleInvalidState.TRUE if message else Gtk.AccessibleInvalidState.FALSE)])
        # DESCRIBED_BY takes a GList of widgets, which PyGObject cannot pass; the description property carries the same text
        widget.update_property([Gtk.AccessibleProperty.DESCRIPTION], [message or ""])

    def push(self, page):
        stale = self.nav.find_page(page.get_tag())  # a page popped a moment ago still holds its tag until its animation ends
        if stale is not None and stale is not page:
            self.nav.remove(stale)
        self.nav.push(page)

    def _error_page(self, page, title, err, back):
        """Replace a working page's content with an error and a Back button; the dialog can be closed again."""
        self.set_can_close(True)
        page.set_can_pop(True)
        sp = Adw.StatusPage(icon_name="dialog-error-symbolic", title=title, description=err)
        sp.set_child(vbox(pill("Back", back, suggested=False)))
        page.toolbar.set_content(sp)

    def _almost_done_page(self, page, text, close):
        self.set_can_close(True)
        sp = Adw.StatusPage(icon_name="dialog-warning-symbolic", title="Almost Done", description=text)
        sp.set_child(vbox(pill("Close", close)))
        page.toolbar.set_content(sp)

    # -- sign in
    def _signin_page(self):
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.stack.add_named(vbox(pill("Sign In With Google", self._start_signin)), "button")
        self.wait_label = Gtk.Label(label="Waiting for you to sign in…", css_classes=["dim-label"])
        cancel = Gtk.Button(label="Cancel", css_classes=["flat"], halign=Gtk.Align.CENTER)
        cancel.connect("clicked", lambda *_: self._cancel_signin())
        self.wait_spinner = spinner()
        self.wait_spinner.set_halign(Gtk.Align.CENTER)
        self.cancel_btn = cancel
        self.stack.add_named(vbox(self.wait_spinner, self.wait_label, cancel), "waiting")
        sp = Adw.StatusPage(icon_name="avatar-default-symbolic", title="Sign In to Google",
                            description="Lifeboat only sees the files it creates in your Drive. "
                                        "Your browser opens so you can sign in; no password reaches Lifeboat.")
        sp.set_child(self.stack)
        self.signin_banner = self._banner()
        return self._page("signin", "Sign In", sp, banner=self.signin_banner)

    def _signin_error(self, msg, retry=None):
        self.stack.set_visible_child_name("button")
        self._show_error(self.signin_banner, msg, retry or self._reset_signin)

    def _reset_signin(self):
        self.signin_banner.set_revealed(False)
        self.stack.set_visible_child_name("button")

    def _reset_account(self):
        """Forget everything learned about a previous account, so its passphrase can never reach another one's kit."""
        self.passphrase = None
        self.existing = []
        self.mode = "new"

    def _start_signin(self):
        self._stop_poller()
        self._drop_signin()
        self._reset_account()
        self.token = None
        self.signin_banner.set_revealed(False)
        self.signin = setup.SignIn()
        try:
            self.signin.start()
        except (setup.SignInError, setup.SetupError) as e:
            self.signin = None
            self._signin_error(str(e))
            return
        self._opened = False
        self.wait_label.set_label("Waiting for you to sign in…")
        self.cancel_btn.set_visible(True)
        self.stack.set_visible_child_name("waiting")
        self._poll_id = GLib.timeout_add(250, self._poll_signin)

    def _cancel_signin(self):
        self._stop_poller()
        self._drop_signin()
        self._reset_signin()

    def _poll_signin(self):
        s = self.signin
        if s is None or self.dead:
            self._poll_id = 0
            return False
        if self._polling:
            return True
        if not self._opened and s.auth_url:
            self._opened = True
            UriLauncher.new(s.auth_url).launch(self.win, None, lambda *_: None)
        self._polling = True

        def work():
            try:
                return s.poll(), None
            except (setup.SignInError, setup.SetupError) as e:
                return None, str(e)

        def done(res):
            self._polling = False
            token, err = res
            if self.dead or s is not self.signin:
                return
            if err is None and token is None:
                return
            self._stop_poller()
            self.signin = None
            if err:
                self._signin_error(err)
                return
            self.token = token
            self._discover()

        def fail(_e):
            self._polling = False
            self._stop_poller()
            self.signin = None
            self._signin_error("Sign-in failed.")

        self.win.in_thread(work, done, fail)
        return True

    def _discover(self):
        self._reset_account()
        self.signin_banner.set_revealed(False)
        self.wait_label.set_label("Looking for your backups…")
        self.cancel_btn.set_visible(False)
        self.stack.set_visible_child_name("waiting")

        def work():
            try:
                conf = setup.rclone_conf_path()
                conf.parent.mkdir(parents=True, exist_ok=True)
                if not conf.exists():
                    self._conf_made_here = True
                setup.write_drive_remote(conf, self.token)
                return setup.find_backups(conf), None
            except (setup.SetupError, OSError) as e:
                return None, str(e.strerror or e) if isinstance(e, OSError) else str(e)
            except Exception:  # noqa: BLE001  never echo an unknown error: it could carry the token
                return None, "Something went wrong while looking for your backups."

        def done(res):
            found, err = res
            if self.dead:
                return
            if err:
                self._signin_error(err, self._discover)  # Drive unreachable is never reported as "no backups"
                return
            self.existing = found
            self._discovered(found)
            self._reset_signin()

        self.win.in_thread(work, done, lambda _e: self._signin_error("Something went wrong while looking for your backups.", self._discover))

    def _discovered(self, found):
        raise NotImplementedError

    # -- recovery passphrase of an account that already has backups
    def _passphrase_page(self, description, extra=None):
        self.pw_row = Adw.PasswordEntryRow(title="Recovery passphrase")
        self.pw_error = Gtk.Label(visible=False, wrap=True, xalign=0, css_classes=["error"])
        self.pw_row.connect("entry-activated", lambda *_: self._check_passphrase())
        self.pw_row.connect("changed", lambda *_: self._mark_invalid(self.pw_row, self.pw_error, None))
        lb = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"])
        lb.append(self.pw_row)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_start=12, margin_end=12)
        box.append(lb)
        box.append(self.pw_error)
        if extra is not None:
            box.append(extra)
        sp = Adw.StatusPage(icon_name="dialog-password-symbolic", title="Enter Your Passphrase", description=description)
        sp.set_child(box)
        self.pw_banner = self._banner()
        self.check_btn = pill("Check", self._check_passphrase)
        return self._page("passphrase", "Recovery Passphrase", sp, bottom=self._bar(self.check_btn), banner=self.pw_banner)

    def _passphrase_device(self) -> str:
        """The device whose probe file proves the passphrase."""
        raise NotImplementedError

    def _passphrase_ok(self):
        raise NotImplementedError

    def _check_passphrase(self):
        pw = self.pw_row.get_text()
        if not pw or not self.check_btn.get_sensitive():
            return
        self.pw_banner.set_revealed(False)
        self.check_btn.set_sensitive(False)
        device = self._passphrase_device()

        def work():
            try:
                return setup.check_passphrase(setup.rclone_conf_path(), pw, device), None
            except setup.SetupError as e:
                return None, str(e)

        def done(res):
            ok, err = res
            if self.dead:
                return
            self.check_btn.set_sensitive(True)
            if err:
                self._show_error(self.pw_banner, err, self._check_passphrase)
            elif ok:
                self.passphrase = pw
                self.pw_row.set_text("")
                self._passphrase_ok()
            else:
                self._mark_invalid(self.pw_row, self.pw_error, "That passphrase doesn't match this account's backups")

        def fail(_e):
            if not self.dead:
                self.check_btn.set_sensitive(True)
                self._show_error(self.pw_banner, setup.UNREACHABLE, self._check_passphrase)

        self.win.in_thread(work, done, fail)
