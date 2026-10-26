"""The restore wizard: a thin GTK view over lifeboat.setup. Sign-in, discovery and the passphrase check are shared with
the setup wizard (flow.AccountDialog)."""
import sys
from datetime import datetime

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from lifeboat import setup  # noqa: E402
from lifeboat.app.flow import AccountDialog, install_timer, pill, vbox  # noqa: E402
from lifeboat.app.widgets import ago, spinner  # noqa: E402
from lifeboat.app.wizard_state import (  # noqa: E402
    DEFAULT_TIME,
    folders_text,
    job_label,
    keep_backing_up_default,
    since_text,
    tilde,
    validate_device_name,
)
from lifeboat.config import ConfigError, config_path, load_config  # noqa: E402

DONE_ALL = "Log out and back in so every app picks up its settings."
DONE_SOME = "Some folders could not be restored. Nothing was deleted, so it is safe to try again."


class RestoreWizard(AccountDialog):
    def __init__(self, win, device=None, handover=None):
        super().__init__(win, "Restore From a Backup")
        self.picked = None
        self.manifest: dict = {}
        self.checks: dict[str, Gtk.CheckButton] = {}
        self.keep_row = self.name_row = None
        self.cfg = self.rcfg = None  # the config written for this laptop; the in-memory one that reads the old backup
        self.names, self.keep, self.device = [], True, ""
        self.same_laptop = None  # the device the setup wizard recognised as this laptop's own backup
        self.written: set[str] = set()  # jobs in the config written so far (keep-backing-up mode)
        self.cfg_ours = self.timer_done = False
        self.cfg_err = None
        self._mode = None
        self.retired: set[str] = set()
        self.restoring_page = None
        self._runs = 0
        self.results: dict[str, int] = {}
        self.timer_err = None
        if handover:  # the setup wizard already signed in: no second sign-in
            self.token = handover["token"]
            self.existing = handover["existing"]
            self._conf_made_here = handover["conf_made_here"]
            self.same_laptop = handover.get("same_laptop")
            self.mode = "add"
            if device in self._names():
                self._pick(device)
                self.nav.add(self._passphrase_root())
                return
            self.nav.add(self._choose_page(back=False))
            return
        self.nav.add(self._signin_page())

    def _names(self):
        return [m["device"] for m in self.existing]

    def _pick(self, device):
        self.picked = device
        self.manifest = next(m for m in self.existing if m["device"] == device)

    # -- 1. sign in (shared), then what discovery found
    def _discovered(self, found):
        if not found:
            self.push(self._none_page())
        elif len(found) == 1:
            self._pick(found[0]["device"])
            self.push(self._passphrase_root())
        else:
            self.push(self._choose_page())

    def _none_page(self):
        sp = Adw.StatusPage(icon_name="folder-symbolic", title="No Backups Found",
                            description="Lifeboat only sees backups it made with this Google account. "
                                        "If your backup was made with another account, sign in with that one.")
        sp.set_child(vbox(pill("Set Up Backup Instead", self._set_up_instead),
                          pill("Sign In With Another Account", lambda: self.nav.pop_to_tag("signin"), suggested=False)))
        return self._page("none", "No Backups", sp)

    def _set_up_instead(self):
        self.force_close()
        self.win.open_setup_wizard()

    # -- 2. choose a backup
    def _choose_page(self, back=True):
        pg = Adw.PreferencesPage()
        g = Adw.PreferencesGroup(title="Backups on This Account", description="Choose the one to restore.")
        leader = Gtk.CheckButton(visible=False)  # a group needs a second member to look like radio buttons
        self._pick(self._names()[0])
        for i, m in enumerate(self.existing):
            r = Adw.ActionRow(title=m["device"], subtitle=f"Last backup {ago(m.get('time'))} · {folders_text(len(m.get('jobs', [])))}")
            c = Gtk.CheckButton(valign=Gtk.Align.CENTER)
            c.set_group(leader)
            c.set_active(i == 0)
            c.connect("toggled", lambda b, d=m["device"]: b.get_active() and self._pick(d))
            r.add_prefix(c)
            r.set_activatable_widget(c)
            g.add(r)
        pg.add(g)
        self.choose_btn = pill("Continue", lambda: self.push(self._passphrase_root()))
        return self._page("choose", "Choose a Backup", pg, bottom=self._bar(self.choose_btn), back=back)

    # -- 3. recovery passphrase
    def _passphrase_root(self):
        link = Gtk.Button(label="Where's my passphrase?", css_classes=["link"], halign=Gtk.Align.CENTER)
        link.connect("clicked", lambda *_: self._where_is_it())
        return self._passphrase_page("Enter the recovery passphrase for this backup.", link)

    def _where_is_it(self):
        d = Adw.AlertDialog(heading="Where's My Passphrase?",
                            body="It's on the recovery kit you saved or printed when you set up Lifeboat.")
        d.add_response("close", "Close")
        d.present(self)

    def _passphrase_device(self):
        return self.picked

    def _passphrase_ok(self):
        self.push(self._what_page())

    # -- 4. what to restore
    def _what_page(self):
        pg = Adw.PreferencesPage()
        g = Adw.PreferencesGroup(title="What to Restore")
        self.checks = {}
        self.retired = {j["name"] for j in self.manifest.get("retired", [])}
        for j in self.manifest.get("jobs", []) + self.manifest.get("retired", []):
            old = j["name"] in self.retired
            sub = tilde(j["source"]) + (" · Encrypted" if j.get("encrypted") else "")
            if old:
                sub += " · " + since_text(j.get("since"))
            r = Adw.ActionRow(title=job_label(j["name"]), subtitle=sub)
            c = Gtk.CheckButton(valign=Gtk.Align.CENTER, active=not old)  # a retired folder is restorable, but off by default
            c.connect("toggled", lambda *_: self._validate())
            r.add_suffix(c)
            r.set_activatable_widget(c)
            self.checks[j["name"]] = c
            g.add(r)
        pg.add(g)
        note = Gtk.Label(label="Files already on this laptop are kept if they are newer. Nothing is deleted.", wrap=True,
                         xalign=0, css_classes=["dim-label", "caption"], margin_start=12, margin_end=12)
        gn = Adw.PreferencesGroup()
        gn.add(note)
        pg.add(gn)
        ga = Adw.PreferencesGroup(title="After Restoring")
        self.keep_row = Adw.SwitchRow(title="Keep Backing Up to This Backup", subtitle="Turn off if the old laptop is still in use",
                                      active=keep_backing_up_default(self.manifest, datetime.now().astimezone(),
                                                                     self.picked == self.same_laptop))
        self.keep_row.connect("notify::active", lambda *_: self._validate())
        ga.add(self.keep_row)
        default = setup.unique_device_name(setup.default_device_name(), self._names())
        self.name_row = Adw.EntryRow(title="Device name", text=default)
        self.name_row.connect("changed", lambda *_: self._validate())
        ga.add(self.name_row)
        self.name_err = Gtk.Label(xalign=0, wrap=True, visible=False, css_classes=["error", "caption"], margin_start=12)
        ga.add(self.name_err)
        pg.add(ga)
        self.restore_btn = pill("Restore", self._start_restore)
        page = self._page("what", "What to Restore", pg, bottom=self._bar(self.restore_btn))
        self._validate()
        return page

    def _validate(self):
        keep = self.keep_row.get_active()
        self.name_row.set_visible(not keep)
        err = None if keep else validate_device_name(self.name_row.get_text().strip(), self._names())
        self._mark_invalid(self.name_row, self.name_err, err)
        self.restore_btn.set_sensitive(err is None and any(c.get_active() for c in self.checks.values()))

    # -- 5. restoring
    def _start_restore(self):
        self.names = [n for n, c in self.checks.items() if c.get_active()]
        self.keep = self.keep_row.get_active()
        self.device = self.name_row.get_text().strip()
        self.results = {}
        mode = (self.keep, "" if self.keep else self.device)
        if self._mode is not None and mode != self._mode and self.cfg_ours:
            # the mode changed after an earlier attempt: that attempt's config is not what is wanted now, and it is ours to remove
            config_path().unlink(missing_ok=True)
            self.cfg_ours, self.written, self.cfg, self.timer_done, self.timer_err = False, set(), None, False, None
        self._mode = mode
        token, passphrase, manifest, names = self.token, self.passphrase, self.manifest, self.names
        emit = self._emitter()

        def work():
            try:
                setup.make_remotes(setup.rclone_conf_path(), token, passphrase)
                rcfg = setup.restore_config(manifest, setup.rclone_conf_path())
            except (setup.SetupError, ConfigError, OSError) as e:
                return {"err": f"Could not finish creating your backup remotes: {str(e).rstrip('.')}. No Lifeboat config was written."}
            results = self._restore(rcfg, names, emit)
            return self._settled(rcfg, results)

        self._busy(work)

    def _settled(self, rcfg, results):
        self._settle(results)
        return {"rcfg": rcfg, "results": results}

    def _settle(self, results):
        """Write this laptop's config and schedule the daily backup, as far as it is safe. Runs off the main loop.

        Continuing the old device's backup ("keep"): only folders that were ticked AND restored cleanly are written, and
        the timer waits until every ticked folder is back. A folder that is missing or half restored would otherwise be
        synced over its backup, and the old files would end up in _old and be pruned. A new device backs up into new,
        empty folders, so all ticked folders are safe to write at once."""
        core = [n for n in self.names if n not in self.retired]  # a retired folder is restored, never added to the config
        ok = [n for n in core if results.get(n) == 0]
        all_ok = len(ok) == len(core)
        cfg_file = config_path()
        self.cfg_err = None
        try:
            if self.keep and ok and set(ok) != self.written:
                if self.cfg_ours:
                    cfg_file.unlink(missing_ok=True)  # our own earlier, smaller config: replaced by the larger one
                    self.cfg_ours = False
                setup.config_from_manifest(cfg_file, self.manifest, DEFAULT_TIME, names=ok)
                self.cfg_ours, self.written = True, set(ok)
                self.cfg = load_config(cfg_file)
            elif not self.keep and not self.cfg_ours and core:
                choices = [{"key": j["name"], "label": j["name"], "path": j["source"], "default_on": True,
                            "secret": bool(j.get("encrypted"))} for j in self.manifest["jobs"] if j["name"] in core]
                setup.write_wizard_config(cfg_file, self.device, choices, DEFAULT_TIME, existing_devices=self._names())
                self.cfg_ours = True
                self.cfg = load_config(cfg_file)
        except (setup.SetupError, ConfigError, OSError) as e:
            self.cfg_err = f"Lifeboat could not write its config: {str(e).rstrip('.')}."
            return
        if self.cfg is not None and (all_ok or not self.keep) and not self.timer_done:
            self.timer_err, self.timer_done = install_timer(self.cfg), True

    @staticmethod
    def _restore(rcfg, names, emit):
        try:
            return setup.restore_jobs(rcfg, names, emit)
        except Exception as e:  # noqa: BLE001  one bad folder must not hide the others' results
            print("restore failed:", type(e).__name__, file=sys.stderr)
            return {n: -1 for n in names}

    def _emitter(self):
        def emit(line):
            GLib.idle_add(self._log, line)
        return emit

    def _log(self, line):
        if not self.dead:
            self.buf.insert(self.buf.get_end_iter(), line + "\n")
            self.view.scroll_to_mark(self.buf.create_mark(None, self.buf.get_end_iter(), False), 0, False, 0, 0)

    def _busy(self, work):
        """Show the progress view, keep the dialog open while `work` runs off the main loop."""
        self.set_can_close(False)
        self._refresh_on_close = True  # a config may exist from here on
        self.buf = Gtk.TextBuffer()
        self.view = Gtk.TextView(buffer=self.buf, editable=False, cursor_visible=False, monospace=True,
                                 wrap_mode=Gtk.WrapMode.WORD_CHAR, top_margin=8, bottom_margin=8, left_margin=12, right_margin=12)
        scroll = Gtk.ScrolledWindow(child=self.view, vexpand=True, min_content_height=160, css_classes=["card"],
                                    margin_start=12, margin_end=12, margin_bottom=12)
        sp = Adw.StatusPage(title="Restoring Your Files", description="This can take a while. Keep Lifeboat open.")
        sp.set_child(vbox(spinner()))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(sp)
        box.append(scroll)
        if self.restoring_page is not None and self.nav.get_visible_page() is self.restoring_page:
            self.restoring_page.set_title("Restoring")  # Try Again: the same page, back to progress
            self.restoring_page.toolbar.set_content(box)
        else:  # first run, or back from the error page (which popped it): a fresh page, never a hidden one
            self._runs += 1
            page = self._page("restoring" if self._runs == 1 else f"restoring-{self._runs}", "Restoring", box, back=False)
            page.set_can_pop(False)
            self.restoring_page = page
            self.push(page)
        self.win.in_thread(work, self._finished, lambda _e: self._crashed())

    def _crashed(self):
        self._failed_page("Something unexpected went wrong while restoring. Check Lifeboat's config folder, then try again.")

    def _failed_page(self, err):
        self.restoring_page.set_title("Restore Failed")
        self._error_page(self.restoring_page, "Restore Failed", err, lambda: self.nav.pop())

    def _finished(self, res):
        if self.dead:
            return
        if res.get("err"):
            self._failed_page(res["err"])
            return
        self.rcfg = res["rcfg"]
        self.results.update(res["results"])
        self._done_page()

    # -- 6. all done
    def _failed(self):
        return [n for n in self.names if self.results.get(n) != 0]

    def _done_page(self):
        self.set_can_close(True)
        failed = self._failed()
        labels = {j["name"]: job_label(j["name"]) for j in self.manifest.get("jobs", []) + self.manifest.get("retired", [])}
        text = DONE_SOME if failed else DONE_ALL
        # folders this laptop does not back up: the unticked ones, and (keep mode) the ones not restored yet
        skipped = [n for n in labels if n not in self.names or n in self.retired or (self.keep and n in failed)]
        if self.keep and failed:
            text += " Daily backups are not scheduled until every folder you chose is restored."
        if skipped:
            text += " Not backed up from this laptop: " + ", ".join(labels[n] for n in skipped) + "."
        for note in (self.cfg_err, self.timer_err and f"Daily backups are not scheduled: {self.timer_err}"):
            if note:
                text += " " + note
        ok = not failed and not self.timer_err and not self.cfg_err
        title = "Your Files Are Back" if ok else "Almost Done"
        self.restoring_page.set_title("All Done" if ok else "Not Finished" if failed else "Almost Done")
        sp = Adw.StatusPage(icon_name="object-select-symbolic" if ok else "dialog-warning-symbolic", title=title, description=text)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_start=12, margin_end=12)
        if failed:
            g = Adw.PreferencesGroup(title="Not Restored")
            for n in failed:
                rc = self.results.get(n, -1)
                r = Adw.ActionRow(title=labels.get(n, n), subtitle="Could not restore this folder" + (f" (exit {rc})" if rc > 0 else ""),
                                  css_classes=["error"])
                r.add_prefix(Gtk.Image.new_from_icon_name("dialog-error-symbolic"))
                g.add(r)
            box.append(g)
        buttons = [pill("Done", self.force_close, suggested=not failed)]
        if failed:
            buttons.insert(0, pill("Try Again", self._try_again))
        bar = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER)
        for b in buttons:
            bar.append(b)
        box.append(bar)
        sp.set_child(box)
        self.restoring_page.toolbar.set_content(sp)

    def _try_again(self):
        """Restore only the folders that failed; restore never deletes, so repeating it is safe."""
        names, rcfg, emit, before = self._failed(), self.rcfg, self._emitter(), dict(self.results)

        def work():
            results = {**before, **self._restore(rcfg, names, emit)}
            return self._settled(self.rcfg, results)

        self._busy(work)
