"""The setup wizard: a thin GTK view over lifeboat.setup (all decisions and rclone calls live there)."""
import threading
from datetime import date
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from lifeboat import APP_ID, setup, timer  # noqa: E402
from lifeboat.app.widgets import ago, copy_text, spinner  # noqa: E402
from lifeboat.app.wizard_state import (  # noqa: E402
    DEFAULT_TIME,
    Choices,
    daily_times,
    fmt_size,
    folders_text,
    tilde,
    validate_device_name,
)
from lifeboat.config import ConfigError, config_path, load_config  # noqa: E402

# Indirection so the Broadway test can swap the pickers for stubs.
UriLauncher = Gtk.UriLauncher
FileDialog = Gtk.FileDialog

CLIPBOARD_CLEAR_SECONDS = 30


def _pill(text, cb, suggested=True):
    b = Gtk.Button(label=text, css_classes=["pill", "suggested-action"] if suggested else ["pill"], halign=Gtk.Align.CENTER)
    b.connect("clicked", lambda *_: cb())
    return b


def _vbox(*children, spacing=12):
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing, halign=Gtk.Align.CENTER)
    for c in children:
        box.append(c)
    return box


class SetupWizard(Adw.Dialog):
    def __init__(self, win):
        super().__init__(title="Set Up Backup", content_width=480, content_height=640, width_request=320, height_request=300)
        self.win = win
        self.mode = "new"  # "add": this account already has backups, so the existing passphrase is asked for
        self.token = None  # kept in memory only, never logged or shown
        self.passphrase = None
        self.existing: list[dict] = []
        self.signin = None
        self.dead = False
        self._silent = False
        self._poll_id = 0
        self._polling = False
        self._conf_made_here = False  # we created rclone.conf in this run, so we may delete it if the user bails out
        self._timer_failed = False
        self._nfolders = 0
        self.choices = Choices([])
        self.rows: dict[str, Adw.SwitchRow] = {}
        self.group_folders = None
        self.device = setup.default_device_name()
        self.daily_at = DEFAULT_TIME
        self.bottom_bars: list[Gtk.Widget] = []
        self.nav = Adw.NavigationView()
        self.set_child(self.nav)
        bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 400sp"))
        self.add_breakpoint(bp)
        self._bp = bp
        self.connect("closed", lambda *_: self._cleanup())
        self.nav.add(self._welcome_page())

    # -- plumbing
    def _cleanup(self):
        self.dead = True
        self._stop_poller()
        self._drop_signin()
        self.token = self.passphrase = None
        if self._conf_made_here and not config_path().exists():
            # the Drive token was written before the user committed to anything: take it back
            setup.rclone_conf_path().unlink(missing_ok=True)
        if self._timer_failed:
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
        b.connect("button-clicked", lambda *_: self._banner_retry())
        self._retry = lambda: None
        return b

    def _banner_retry(self):
        self._retry()

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
        self.nav.push(page)

    # -- 1. welcome
    def _welcome_page(self):
        sp = Adw.StatusPage(icon_name=APP_ID, title="Keep This Laptop Safe to Lose", description="Back up your files to Google Drive, so a lost or broken laptop is not a disaster.")
        sp.set_child(_vbox(_pill("Continue", lambda: self.push(self._signin_page()))))
        return self._page("welcome", "Set Up Backup", sp, back=False)

    # -- 2. sign in
    def _signin_page(self):
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.stack.add_named(_vbox(_pill("Sign In With Google", self._start_signin)), "button")
        self.wait_label = Gtk.Label(label="Waiting for you to sign in…", css_classes=["dim-label"])
        cancel = Gtk.Button(label="Cancel", css_classes=["flat"], halign=Gtk.Align.CENTER)
        cancel.connect("clicked", lambda *_: self._cancel_signin())
        self.wait_spinner = spinner()
        self.wait_spinner.set_halign(Gtk.Align.CENTER)
        self.cancel_btn = cancel
        self.stack.add_named(_vbox(self.wait_spinner, self.wait_label, cancel), "waiting")
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
                self._signin_error(err, self._discover)
                return
            self.existing = found
            if found:
                self.mode = "add"
                self.push(self._existing_page())
            else:
                self.mode = "new"
                self.push(self._folders_page())
            self._reset_signin()

        self.win.in_thread(work, done, lambda _e: self._signin_error("Something went wrong while looking for your backups.", self._discover))

    # -- 2b. existing backups
    def _existing_page(self):
        names = [m["device"] for m in self.existing]
        self.device = setup.unique_device_name(setup.default_device_name(), names)
        pg = Adw.PreferencesPage()
        g = Adw.PreferencesGroup(title="Backups on This Account",
                                 description="This Google account already has backups. Restore one, or add this laptop next to them.")
        leader = Gtk.CheckButton(visible=False)  # a group needs a second member to look like radio buttons, even for one device
        self.picked = names[0]
        for i, m in enumerate(self.existing):
            n = len(m.get("jobs", []))
            r = Adw.ActionRow(title=m["device"], subtitle=f"Last backup {ago(m.get('time'))} · {folders_text(n)}")
            c = Gtk.CheckButton(valign=Gtk.Align.CENTER)
            c.set_group(leader)
            c.set_active(i == 0)
            c.connect("toggled", lambda b, d=m["device"]: b.get_active() and setattr(self, "picked", d))
            r.add_prefix(c)
            r.set_activatable_widget(c)
            g.add(r)
        pg.add(g)
        bottom = self._bar(_pill("Restore From This Backup", self._restore_picked, suggested=False),
                           _pill("Add This Laptop", self._add_laptop))
        return self._page("existing", "Existing Backups", pg, bottom=bottom)

    def _add_laptop(self):
        self.push(self._folders_page())

    def _restore_picked(self):
        self.force_close()
        self.win.open_restore_wizard(self.picked)

    # -- 3. what to back up
    def _folders_page(self):
        suggested = setup.suggest_folders()
        self.choices = Choices(suggested)
        self.rows = {}
        pg = Adw.PreferencesPage()
        self.group_folders = Adw.PreferencesGroup(title="Folders")
        self._nfolders = 0
        for f in suggested:
            if not f["secret"]:
                self._folder_row(f, self.group_folders)
        self.group_folders.set_visible(self._nfolders > 0)  # shown again once a folder is added
        pg.add(self.group_folders)
        keys = [f for f in suggested if f["secret"]]
        if keys:
            gk = Adw.PreferencesGroup(title="Keys", description="Always encrypted")
            for f in keys:
                self._folder_row(f, gk, measure=False)
            pg.add(gk)
        ga = Adw.PreferencesGroup()
        ga.add(self._add_folder_row())
        pg.add(ga)
        gd = Adw.PreferencesGroup(title="This Laptop")
        self.group_device = gd
        self.name_row = Adw.EntryRow(title="Device name", text=self.device)
        self.name_row.connect("changed", lambda *_: self._validate())
        gd.add(self.name_row)
        self.name_err = Gtk.Label(xalign=0, wrap=True, visible=False, css_classes=["error", "caption"], margin_start=12)
        times = daily_times()
        self.time_row = Adw.ComboRow(title="Daily backup time", model=Gtk.StringList.new(times),
                                     selected=times.index(DEFAULT_TIME))
        gd.add(self.time_row)
        gd.add(self.name_err)
        pg.add(gd)
        self.continue_btn = _pill("Continue", self._folders_next)
        page = self._page("folders", "What to Back Up", pg, bottom=self._bar(self.continue_btn))
        self._validate()
        return page

    def _folder_row(self, f, group, measure=True, removable=False):
        row = Adw.SwitchRow(title=f["label"], subtitle=f"{tilde(f['path'])} · {fmt_size(None, False)}" if measure else tilde(f["path"]),
                            active=self.choices.is_on(f["path"]))
        row.connect("notify::active", lambda r, _p, p=f["path"]: self._toggled(p, r.get_active()))
        if removable:
            rm = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER, css_classes=["flat"],
                            tooltip_text="Remove this folder")
            rm.connect("clicked", lambda *_: self._remove_folder(f["path"], row, group))
            row.add_suffix(rm)
        self.rows[f["path"]] = row
        group.add(row)
        if group is self.group_folders:
            self._nfolders += 1
            group.set_visible(True)
        if measure:
            self._measure(f)
        return row

    def _toggled(self, path, on):
        if self._silent:
            return
        self.choices.toggle(path, on)
        self._validate()

    def _measure(self, f):
        path = f["path"]

        def work():
            return setup.folder_size(path)

        def done(res):
            size, complete = res
            row = self.rows.get(path)
            if self.dead or row is None:
                return
            self.choices.set_size(path, size, complete)
            row.set_subtitle(f"{tilde(path)} · {fmt_size(size, complete)}")
            if row.get_active() != self.choices.is_on(path):
                self._silent = True  # a size-driven change must not count as a user toggle
                row.set_active(self.choices.is_on(path))
                self._silent = False
            self._validate()

        self.win.in_thread(work, done, lambda _e: None)  # an unmeasurable folder just stays "calculating…"

    def _add_folder_row(self):
        if hasattr(Adw, "ButtonRow"):
            r = Adw.ButtonRow(title="Add a Folder…", start_icon_name="list-add-symbolic")
            r.connect("activated", lambda *_: self._pick_folder())
            return r
        r = Adw.ActionRow(title="Add a Folder…", activatable=True)
        r.add_prefix(Gtk.Image.new_from_icon_name("list-add-symbolic"))
        r.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
        r.connect("activated", lambda *_: self._pick_folder())
        return r

    def _pick_folder(self):
        dlg = FileDialog(title="Add a Folder")

        def done(d, res):
            try:
                path = d.select_folder_finish(res).get_path()
            except GLib.Error:
                return  # dismissed
            if path and self.choices.add(path):
                f = next(x for x in self.choices.selected() if x["path"] == path)
                self._folder_row(f, self.group_folders, removable=True)
                self._validate()

        dlg.select_folder(self.win, None, done)

    def _remove_folder(self, path, row, group):
        self.choices.remove(path)
        self.rows.pop(path, None)
        group.remove(row)
        if group is self.group_folders:
            self._nfolders -= 1
            group.set_visible(self._nfolders > 0)
        self._validate()

    def _validate(self):
        err = validate_device_name(self.name_row.get_text().strip(), [m["device"] for m in self.existing])
        self._mark_invalid(self.name_row, self.name_err, err)
        self.continue_btn.set_sensitive(err is None and self.choices.can_continue())

    def _folders_next(self):
        self.device = self.name_row.get_text().strip()
        self.daily_at = daily_times()[self.time_row.get_selected()]
        self.push(self._kit_page() if self.mode == "new" else self._passphrase_page())

    # -- 4a. recovery kit (new account)
    def _kit_page(self):
        if self.passphrase is None:
            self.passphrase = setup.new_passphrase()
        self.kit_label = Gtk.Label(label=self.passphrase, selectable=True, wrap=True, justify=Gtk.Justification.CENTER,
                                   css_classes=["title-3", "monospace"])
        save = Gtk.Button(label="Save PDF…", css_classes=["pill"])
        save.connect("clicked", lambda *_: self._save_kit())
        copy = Gtk.Button(label="Copy", css_classes=["pill"])
        copy.connect("clicked", lambda *_: self._copy_passphrase())
        stored = Adw.SwitchRow(title="I've stored my recovery kit")
        lb = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"])
        lb.append(stored)
        go = _pill("Set Up Backup", self._set_up)
        go.set_sensitive(False)
        stored.connect("notify::active", lambda r, _p: go.set_sensitive(r.get_active()))
        self.kit_go = go
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, margin_start=12, margin_end=12)
        buttons = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER)
        buttons.append(save)
        buttons.append(copy)
        for w in (self.kit_label, buttons, lb):
            box.append(w)
        sp = Adw.StatusPage(icon_name="dialog-password-symbolic", title="Your Recovery Kit",
                            description="This passphrase unlocks your encrypted keys. It cannot be recovered, "
                                        "so save the PDF and store it somewhere other than this laptop.")
        sp.set_child(box)
        return self._page("kit", "Recovery Kit", sp, bottom=self._bar(go))

    def _save_kit(self):
        dlg = FileDialog(title="Save Recovery Kit", initial_name=f"Lifeboat-recovery-kit-{self.device}.pdf",
                         initial_folder=Gio.File.new_for_path(str(Path.home())))

        def done(d, res):
            try:
                path = d.save_finish(res).get_path()
            except GLib.Error:
                return
            try:
                setup.kit_pdf(Path(path), None, self.device, self.passphrase, date.today().isoformat())
            except OSError as e:
                self.win.toast(f"Could not save: {e.strerror}")
                return
            self.win.toast("Recovery kit saved")

        dlg.save(self.win, None, done)

    def _copy_passphrase(self):
        text = self.passphrase
        clip = copy_text(self.win, text)
        self.win.toast(f"Copied. Lifeboat tries to clear the clipboard after {CLIPBOARD_CLEAR_SECONDS} seconds")

        def clear():
            def got(c, res):
                try:
                    now = c.read_text_finish(res)
                except GLib.Error:
                    return
                if now == text:
                    c.set_content(None)
            clip.read_text_async(None, got)
            return False

        GLib.timeout_add_seconds(CLIPBOARD_CLEAR_SECONDS, clear)

    # -- 4b. existing passphrase (add this laptop)
    def _passphrase_page(self):
        self.pw_row = Adw.PasswordEntryRow(title="Recovery passphrase")
        self.pw_error = Gtk.Label(visible=False, wrap=True, xalign=0, css_classes=["error"])
        self.pw_row.connect("entry-activated", lambda *_: self._check_passphrase())
        self.pw_row.connect("changed", lambda *_: self._mark_invalid(self.pw_row, self.pw_error, None))
        lb = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"])
        lb.append(self.pw_row)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_start=12, margin_end=12)
        box.append(lb)
        box.append(self.pw_error)
        sp = Adw.StatusPage(icon_name="dialog-password-symbolic", title="Enter Your Passphrase",
                            description="Use the recovery passphrase from your first laptop. "
                                        "All laptops on this account share it.")
        sp.set_child(box)
        self.pw_banner = self._banner()
        self.check_btn = _pill("Check", self._check_passphrase)
        return self._page("passphrase", "Recovery Passphrase", sp, bottom=self._bar(self.check_btn), banner=self.pw_banner)

    def _check_passphrase(self):
        pw = self.pw_row.get_text()
        if not pw or not self.check_btn.get_sensitive():
            return
        self.pw_banner.set_revealed(False)
        self.check_btn.set_sensitive(False)
        device = self.existing[0]["device"]

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
                self._set_up()
            else:
                self._mark_invalid(self.pw_row, self.pw_error, "That passphrase doesn't match this account's backups")

        def fail(_e):
            if not self.dead:
                self.check_btn.set_sensitive(True)
                self._show_error(self.pw_banner, setup.UNREACHABLE, self._check_passphrase)

        self.win.in_thread(work, done, fail)

    # -- 5. setting up
    def _set_up(self):
        self.set_can_close(False)
        sp = Adw.StatusPage(title="Setting Up Backup", description="Creating your backup. This takes a moment.")
        sp.set_child(_vbox(spinner()))
        page = self._page("setting-up", "Setting Up", sp, back=False)
        page.set_can_pop(False)
        self.setup_page = page
        self.push(page)
        token, passphrase = self.token, self.passphrase
        device, choices, daily_at = self.device, self.choices.selected(), self.daily_at
        existing = [m["device"] for m in self.existing]

        def work():
            conf, cfg_file = setup.rclone_conf_path(), config_path()
            try:
                setup.make_remotes(conf, token, passphrase)
            except (setup.SetupError, OSError) as e:
                return None, (f"Could not finish creating your backup remotes: {e}. Some of them may have been written; "
                              "no Lifeboat config was."), None
            try:
                setup.write_wizard_config(cfg_file, device, choices, daily_at, existing_devices=existing)
                cfg = load_config(cfg_file)
            except (setup.SetupError, ConfigError, OSError) as e:
                return None, (f"Could not write the Lifeboat config: {e}. Your backup remotes were created and kept, "
                              "but no config was written, so nothing is scheduled."), None
            try:
                rc = timer.install(cfg)
            except OSError as e:
                return cfg, None, str(e.strerror or e)
            return cfg, None, f"systemd refused it (exit {rc})" if rc else None

        self.win.in_thread(work, self._setup_done,
                           lambda _e: self._setup_failed("Something unexpected went wrong while setting up. "
                                                         "Check Lifeboat's config folder, then try again."))

    def _setup_done(self, res):
        cfg, err, timer_err = res
        if self.dead:
            return
        if err:
            self._setup_failed(err)
            return
        if timer_err:
            self._timer_failed = True
            self.set_can_close(True)
            sp = Adw.StatusPage(icon_name="dialog-warning-symbolic", title="Almost Done",
                                description=f"Your backup is set up, but the daily timer is not: {timer_err}. "
                                            "You can still back up from the Lifeboat window.")
            sp.set_child(_vbox(_pill("Close", self.force_close)))
            self.setup_page.toolbar.set_content(sp)
            return
        self.force_close()
        self.win.refresh()
        self.win.run_cli("Back up now", "run")

    def _setup_failed(self, err):
        self.set_can_close(True)
        self.setup_page.set_can_pop(True)
        sp = Adw.StatusPage(icon_name="dialog-error-symbolic", title="Setup Failed", description=err)
        sp.set_child(_vbox(_pill("Back", lambda: self.nav.pop(), suggested=False)))
        self.setup_page.toolbar.set_content(sp)
