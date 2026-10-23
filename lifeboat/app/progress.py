"""The "Back up now" dialog: a checklist fed by `lifeboat run --events`, with the full rclone log one tap away."""
import json
from datetime import date
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from lifeboat import engine  # noqa: E402
from lifeboat.app.reader import read_lines  # noqa: E402
from lifeboat.app.widgets import CSS, ICONS, copy_text, fmt_time, spinner  # noqa: E402
from lifeboat.app.wizard_state import job_label  # noqa: E402
from lifeboat.config import ConfigError, load_config  # noqa: E402

FileDialog = Gtk.FileDialog
FINISHING = "Finishing up"
REASONS = {
    "missing": "This folder doesn't exist any more. Was it renamed or moved, or is its drive unplugged? Nothing was changed.",
    "empty_with_backup": "Folder is empty but its backup isn't. Is the drive plugged in? Nothing was changed.",
    "dest_unreadable": "Couldn't check the backup on Google Drive. Check your connection and try again. Nothing was changed.",
    "mkdir_failed": "Couldn't create its folder on Google Drive.",
}


class Step:
    """One checklist row. `state` is waiting, running, ok, skip or error."""

    def __init__(self, title):
        self.title, self.state, self.error, self.mark, self.files = title, "waiting", "", None, 0
        self.row = self._plain()

    def _plain(self):
        self.slot = Gtk.Box(valign=Gtk.Align.CENTER)
        row = Adw.ActionRow(title=self.title, subtitle="Waiting", use_markup=False)
        row.add_suffix(self.slot)
        return row

    def set(self, state, subtitle):
        self.state = state
        self.row.set_subtitle(subtitle)
        self._icon(state)

    def _icon(self, state):
        while (c := self.slot.get_first_child()) is not None:
            self.slot.remove(c)
        if state == "running":
            self.slot.append(spinner())
        elif state != "waiting":
            name, css = {"ok": (ICONS["ok"], CSS["ok"]), "skip": ("list-remove-symbolic", "dim-label"),
                         "error": (ICONS["error"], CSS["error"])}[state]
            self.slot.append(Gtk.Image(icon_name=name, css_classes=[css]))


class BackupProgress:
    def __init__(self, win, argv, env, jobs=(), first=False, daily_at="", kit=False):
        """`first` is the run that ends setup: it reads as "you're all set" and says when the daily backup happens.
        `kit` adds the reminder to keep the recovery kit (some folder is encrypted)."""
        self.win, self.argv, self.env = win, argv, env
        self.first, self.daily_at, self.kit = first, daily_at, kit
        title = "First Backup" if first else "Back Up Now"
        self.steps: dict[str, Step] = {}
        self.finishing = Step(FINISHING)
        self.wt = Adw.WindowTitle(title=title, subtitle="")
        self.progress_btn = Gtk.ToggleButton(label="Progress", active=True)
        self.log_btn = Gtk.ToggleButton(label="Log", group=self.progress_btn)
        switch = Gtk.Box(css_classes=["linked"])
        switch.append(self.progress_btn)
        switch.append(self.log_btn)
        hb = Adw.HeaderBar(title_widget=self.wt)
        hb.pack_start(switch)
        self.stack = Gtk.Stack()
        self.stack.add_named(self._progress_page(), "progress")
        self.stack.add_named(self._log_page(), "log")
        for b, name in ((self.progress_btn, "progress"), (self.log_btn, "log")):
            b.connect("toggled", lambda b, name=name: b.get_active() and self.stack.set_visible_child_name(name))
        tv = Adw.ToolbarView(content=self.stack)
        tv.add_top_bar(hb)
        self.dlg = Adw.Dialog(title=title, child=tv, content_width=720, content_height=520, can_close=False)
        self._names = list(jobs)
        self._reset()

    # -- layout
    def _progress_page(self):
        self.summary = Gtk.Label(css_classes=["title-2"], wrap=True, justify=Gtk.Justification.CENTER)
        self.summary_sub = Gtk.Label(css_classes=["dim-label"], wrap=True, justify=Gtk.Justification.CENTER)
        self.head = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, visible=False)
        self.head.append(self.summary)
        self.head.append(self.summary_sub)
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"])
        self.kit_note = Gtk.Label(label="Keep your recovery kit somewhere other than this laptop.", css_classes=["dim-label", "caption"],
                                  wrap=True, justify=Gtk.Justification.CENTER, visible=False)
        self.head.append(self.kit_note)
        self.done_btn = Gtk.Button(label="Done", css_classes=["pill", "suggested-action"], visible=False)
        self.done_btn.connect("clicked", lambda *_: self.dlg.close())
        self.retry = Gtk.Button(label="Try Again", css_classes=["pill", "suggested-action"], visible=False)
        self.retry.connect("clicked", lambda *_: self.start())
        self.close_btn = Gtk.Button(label="Close", css_classes=["pill"], visible=False)
        self.close_btn.connect("clicked", lambda *_: self.dlg.close())
        actions = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER, margin_top=6, margin_bottom=18)  # outside the scroll: always in view
        for b in (self.done_btn, self.retry, self.close_btn):
            actions.append(b)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, margin_top=18, margin_bottom=18, margin_start=18, margin_end=18)
        box.append(self.head)
        box.append(self.list)
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        page.append(Gtk.ScrolledWindow(child=Adw.Clamp(child=box), vexpand=True))
        page.append(actions)
        return page

    def _log_page(self):
        self.buf = Gtk.TextBuffer()
        self.view = Gtk.TextView(buffer=self.buf, editable=False, cursor_visible=False, monospace=True,
                                 wrap_mode=Gtk.WrapMode.WORD_CHAR, top_margin=8, bottom_margin=8, left_margin=12, right_margin=12)
        copy = Gtk.Button(label="Copy")
        copy.connect("clicked", lambda *_: self._copy_log())
        save = Gtk.Button(label="Save Log…")
        save.connect("clicked", lambda *_: self._save_log())
        bar = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER, margin_top=8, margin_bottom=8)
        bar.append(copy)
        bar.append(save)
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        page.append(Gtk.ScrolledWindow(child=self.view, vexpand=True))
        page.append(bar)
        return page

    # -- state
    def _reset(self):
        self.list.remove_all()
        self.steps.clear()
        self.buf.set_text("")
        self.finishing = Step(FINISHING)
        for n in self._names:
            self._step(n)
        self.head.set_visible(False)
        for b in (self.done_btn, self.retry, self.close_btn):
            b.set_visible(False)
        self.finished, self.verdict = False, None
        self.gen = getattr(self, "gen", 0) + 1  # callbacks of the run before this one ignore themselves
        self.progress_btn.set_active(True)
        self._update_title()

    def _step(self, name):
        if name not in self.steps:
            self.steps[name] = step = Step(job_label(name))
            self.list.insert(step.row, len(self.steps) - 1)
        return self.steps[name]

    def _update_title(self):
        if self.finished:
            return
        rows = self._rows()
        done = sum(s.state in ("ok", "skip", "error") for s in rows)
        self.wt.set_subtitle(f"{done} of {len(rows)} done" if rows else "Running…")

    def _rows(self):
        return [*self.steps.values(), *([self.finishing] if self.finishing.row.get_parent() else [])]

    def on_event(self, line):
        try:
            self._event(json.loads(line))
        except (ValueError, KeyError, TypeError, AttributeError):
            self.on_log(line)

    def _event(self, e):
        kind = e["event"]
        if kind == "job_start":
            step = self._step(e["name"])
            step.mark = self.buf.create_mark(None, self.buf.get_end_iter(), True)
            step.set("running", "Backing up…")
        elif kind == "job_end":
            self._job_end(self._step(e["name"]), e)
        elif kind == "finishing_start":
            self.finishing.set("running", "Backing up…")
        elif kind == "finishing_end":
            if e.get("ok"):
                self.finishing.set("ok", "Manifest saved")  # not in the list: housekeeping only shows when it fails
            else:
                self._fail(self.finishing, "Could not save the backup's manifest or passphrase check.", "Could not finish")
        elif kind == "done":
            self.verdict = bool(e.get("ok"))  # the run is over when the process is, see _ended
        self._update_title()

    def _job_end(self, step, e):
        files = step.files = e.get("files", 0)
        if e.get("skipped"):
            step.set("skip", "Nothing to back up yet")
        elif e.get("rc") == 0:
            step.set("ok", f"Copied {files} file{'s' if files != 1 else ''}" if files else "Already up to date")
        else:
            error = e.get("error") or f"Failed (exit {e.get('rc')})"
            self._fail(step, error, REASONS.get(e.get("reason"), error))

    def _fail(self, step, error, subtitle):
        """Swap the row for one that opens by itself, holding the error and what to do about it."""
        step.state, step.error = "error", error
        old, row = step.row, Adw.ExpanderRow(title=step.title, subtitle=subtitle, use_markup=False)
        step.slot = Gtk.Box(valign=Gtk.Align.CENTER)
        row.add_suffix(step.slot)
        step._icon("error")
        text = Gtk.Label(label=error, wrap=True, selectable=True, xalign=0, css_classes=["monospace", "caption"],
                         margin_top=8, margin_start=12, margin_end=12)
        copy = Gtk.Button(label="Copy Error")
        copy.connect("clicked", lambda *_: self._copy(error))
        show = Gtk.Button(label="Show in Log")
        show.connect("clicked", lambda *_: self.show_log(step))
        bar = Gtk.Box(spacing=12, halign=Gtk.Align.END, margin_top=8, margin_bottom=8, margin_end=12)
        bar.append(copy)
        bar.append(show)
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        inner.append(text)
        inner.append(bar)
        row.add_row(inner)
        row.set_expanded(True)
        if old.get_parent() is None:
            self.list.append(row)
        else:
            index = old.get_index()
            self.list.remove(old)
            self.list.insert(row, index)
        step.row = row

    def _finish(self, ok, stopped=False):
        """`ok` is the run's own verdict; `stopped` means the process ended without giving one."""
        if self.finished:
            return
        self.finished = True
        self.dlg.set_can_close(True)  # first, so nothing below can leave the dialog stuck open
        self.win.set_busy(False)
        for s in self._rows():
            if s.state in ("waiting", "running") and not ok:
                self._fail(s, "Stopped before this finished.", "Did not finish")
        failed = sum(s.state == "error" for s in self.steps.values())
        if ok:
            files = sum(s.files for s in self.steps.values())
            self.copied = f"{files} file{'s' if files != 1 else ''} copied"
            self.safe = f"{files} file{'s are' if files != 1 else ' is'} safe on Google Drive."
            self.summary.set_label("You're All Set" if self.first else "Backed Up")
            self.summary_sub.set_label(self.safe if self.first else self.copied)
            self.kit_note.set_visible(self.first and self.kit)
            gen = self.gen
            self.win.in_thread(engine.timers, lambda t: gen == self.gen and self._next_backup(t["lifeboat"]), lambda _e: None)
        else:
            if stopped:
                self.summary.set_label("Backup didn't finish")
                self.summary_sub.set_label("See the Log for details")
            elif not failed:
                self.summary.set_label("Some steps didn't finish")
                self.summary_sub.set_label("Your folders were copied. See the Log for details")
            else:
                self.summary.set_label(f"{failed} folder{'s' if failed != 1 else ''} not backed up")
                self.summary_sub.set_label("The others are safe on Google Drive")
        self.head.set_visible(True)
        self.done_btn.set_visible(ok)
        self.retry.set_visible(not ok)
        self.close_btn.set_visible(not ok)
        self.wt.set_subtitle("Finished" if ok else "Finished with problems")
        self.win.refresh()

    def _next_backup(self, timer):
        """The summary's last line, from the daily timer's own next time: never worked out here."""
        if not timer["installed"]:
            when = "No daily backup scheduled (see Preferences)"
        elif self.first and self.daily_at:
            when = f"Lifeboat backs up every day at {self.daily_at} by itself. You don't need to keep it open."
        elif timer["next"]:
            when = f"Next backup {fmt_time(timer['next'])}"
        else:
            return
        self.summary_sub.set_label(f"{self.safe}\n{when}" if self.first else f"{self.copied} · {when}")

    # -- log
    def on_log(self, line):
        self.buf.insert(self.buf.get_end_iter(), line + "\n")
        self.view.scroll_to_mark(self.buf.create_mark(None, self.buf.get_end_iter(), False), 0, False, 0, 0)

    def show_log(self, step):
        self.log_btn.set_active(True)
        if step.mark is not None:  # once the Log page has been laid out, or the view scrolls past short content
            GLib.idle_add(lambda: self.view.scroll_to_mark(step.mark, 0, True, 0, 0) and False)

    def _copy(self, text):
        copy_text(self.win, text)
        self.win.toast("Copied")

    def _copy_log(self):
        self._copy(self.buf.get_text(self.buf.get_start_iter(), self.buf.get_end_iter(), False))

    def _save_log(self):
        dlg = FileDialog(title="Save Log", initial_name=f"lifeboat-{date.today().isoformat()}.log",
                         initial_folder=Gio.File.new_for_path(str(Path.home())))

        def done(d, res):
            try:
                path = d.save_finish(res).get_path()
            except GLib.Error:
                return
            text = self.buf.get_text(self.buf.get_start_iter(), self.buf.get_end_iter(), False)
            try:
                Path(path).write_text(text)
            except OSError as e:
                self.win.toast(f"Could not save: {e.strerror}")
                return
            self.win.toast("Log saved")

        dlg.save(self.win, None, done)

    # -- process
    def _spawn(self):
        launcher = Gio.SubprocessLauncher.new(Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE)
        launcher.setenv("PYTHONUNBUFFERED", "1", True)
        for k, v in self.env.items():
            launcher.setenv(k, v, True)
        return launcher.spawnv(self.argv)

    def start(self):
        if self.win.busy:
            return
        self.win.set_busy(True)
        self._reset()
        gen = self.gen
        self.dlg.set_can_close(False)
        if self.dlg.get_parent() is None:
            self.dlg.present(self.win)
        try:
            proc = self._spawn()
        except GLib.Error as e:
            self.on_log(f"Could not start: {e.message}")
            self._ended(gen)
            return
        open_pipes = [2]

        def eof():
            open_pipes[0] -= 1
            if not open_pipes[0] and gen == self.gen:
                proc.wait_async(None, lambda p, res: (p.wait_finish(res), self._ended(gen)))

        def current(on_line):
            return lambda line: on_line(line) if gen == self.gen else None

        read_lines(proc.get_stdout_pipe(), current(self.on_event), eof)
        read_lines(proc.get_stderr_pipe(), current(self.on_log), eof)

    def _ended(self, gen=None):
        """The process is gone and both pipes are drained: only now is the run over. A stale run's callback is ignored."""
        if gen is not None and gen != self.gen:
            return
        self._finish(self.verdict is True, stopped=self.verdict is None)


def plan():
    """What the dialog needs from the config on disk: job names, daily time, and whether any folder is encrypted."""
    try:
        cfg = load_config()
    except ConfigError:
        return {"jobs": [], "daily_at": "", "kit": False}
    return {"jobs": [j.name for j in cfg.jobs], "daily_at": cfg.daily_at, "kit": any(j.encrypted for j in cfg.jobs)}
