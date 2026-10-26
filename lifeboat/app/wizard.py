import socket
from datetime import date
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from lifeboat import APP_ID, setup  # noqa: E402
from lifeboat.app.flow import AccountDialog, install_timer, pill, vbox  # noqa: E402
from lifeboat.app.widgets import ago, copy_text, spinner  # noqa: E402
from lifeboat.app.wizard_state import (  # noqa: E402
    DEFAULT_TIME,
    Choices,
    daily_times,
    fmt_size,
    folders_text,
    same_laptop_backup,
    tilde,
    validate_device_name,
)
from lifeboat.config import ConfigError, config_path, load_config  # noqa: E402

# Indirection so the Broadway test can swap the file picker for a stub.
FileDialog = Gtk.FileDialog

CLIPBOARD_CLEAR_SECONDS = 30


class SetupWizard(AccountDialog):
    def __init__(self, win):
        super().__init__(win, "Set Up Backup")
        self._silent = False
        self._nfolders = 0
        self.choices = Choices([])
        self.rows: dict[str, Adw.SwitchRow] = {}
        self.group_folders = None
        self.device = setup.default_device_name()
        self.daily_at = DEFAULT_TIME
        self.nav.add(self._welcome_page())

    # -- 1. welcome
    def _welcome_page(self):
        sp = Adw.StatusPage(icon_name=APP_ID, title="Keep This Laptop Safe to Lose", description="Back up your files to Google Drive, so a lost or broken laptop is not a disaster.")
        sp.set_child(vbox(pill("Continue", lambda: self.push(self._signin_page()))))
        return self._page("welcome", "Set Up Backup", sp, back=False)

    # -- 2. sign in (the page itself is shared: flow.AccountDialog)
    def _discovered(self, found):
        if found:
            self.mode = "add"
            self.push(self._existing_page())
        else:
            self.mode = "new"
            self.push(self._folders_page())

    # -- 2b. existing backups
    def _existing_page(self):
        names = [m["device"] for m in self.existing]
        self.device = setup.unique_device_name(setup.default_device_name(), names)
        pg = Adw.PreferencesPage()
        g = Adw.PreferencesGroup(title="Backups on This Account",
                                 description="This Google account already has backups. Restore one, or add this laptop next to them.")
        leader = Gtk.CheckButton(visible=False)  # a group needs a second member to look like radio buttons, even for one device
        self.same = same_laptop_backup(self.existing, socket.gethostname(), setup.default_device_name())
        self.picked = self.same or names[0]
        for m in self.existing:
            n = len(m.get("jobs", []))
            sub = f"Last backup {ago(m.get('time'))} · {folders_text(n)}"
            r = Adw.ActionRow(title=m["device"], subtitle=f"Looks like this laptop · {sub}" if m["device"] == self.same else sub)
            c = Gtk.CheckButton(valign=Gtk.Align.CENTER)
            c.set_group(leader)
            c.set_active(m["device"] == self.picked)
            c.connect("toggled", lambda b, d=m["device"]: b.get_active() and self._pick(d))
            r.add_prefix(c)
            r.set_activatable_widget(c)
            g.add(r)
        pg.add(g)
        if self.same:  # recommend restoring: that button goes last and suggested
            self.restore_btn = pill(f"Restore From {self.picked}", self._restore_picked)
            bottom = self._bar(pill("Add This Laptop", self._add_laptop, suggested=False), self.restore_btn)
        else:
            self.restore_btn = pill("Restore From This Backup", self._restore_picked, suggested=False)
            bottom = self._bar(self.restore_btn, pill("Add This Laptop", self._add_laptop))
        return self._page("existing", "Existing Backups", pg, bottom=bottom)

    def _pick(self, device):
        self.picked = device
        if self.same:
            self.restore_btn.set_label(f"Restore From {device}")

    def _add_laptop(self):
        self.push(self._folders_page())

    def _restore_picked(self):
        """Hand the signed-in account over, so the restore wizard does not ask to sign in again."""
        handover = {"token": self.token, "existing": self.existing, "conf_made_here": self._conf_made_here,
                    "same_laptop": self.picked == self.same}  # a reinstall carries on with its own backup
        self._conf_made_here = False  # the restore wizard owns the conf now: closing this one must not delete it
        self.force_close()
        self.win.open_restore_wizard(self.picked, handover)

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
        self.continue_btn = pill("Continue", self._folders_next)
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
            row.set_subtitle(f"{tilde(path)} · {self.choices.hint(path) or fmt_size(size, complete)}")
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
        self.push(self._kit_page() if self.mode == "new" else self._passphrase_page(
            "Use the recovery passphrase from your first laptop. All laptops on this account share it."))

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
        go = pill("Set Up Backup", self._set_up)
        go.set_sensitive(False)
        stored.connect("notify::active", lambda r, _p: go.set_sensitive(r.get_active()))
        self.kit_go = go
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, margin_start=12, margin_end=12)
        buttons = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER)
        buttons.append(save)
        buttons.append(copy)
        for w in (self.kit_label, buttons, lb):
            box.append(w)
        sp = Adw.StatusPage(icon_name="dialog-password-symbolic", title="Your Recovery Kit", css_classes=["compact"],
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
    def _passphrase_device(self):
        return self.existing[0]["device"]

    def _passphrase_ok(self):
        self._set_up()

    # -- 5. setting up
    def _set_up(self):
        self.set_can_close(False)
        sp = Adw.StatusPage(title="Setting Up Backup", description="Creating your backup. This takes a moment.")
        sp.set_child(vbox(spinner()))
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
            return cfg, None, install_timer(cfg)

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
            self._refresh_on_close = True
            self._almost_done_page(self.setup_page, f"Your backup is set up, but the daily timer is not. {timer_err}", self.force_close)
            return
        self.force_close()
        self.win.refresh()
        self.win.run_backup(first=True)

    def _setup_failed(self, err):
        self._error_page(self.setup_page, "Setup Failed", err, lambda: self.nav.pop())
