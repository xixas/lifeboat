"""The pages of the window. Each page_<id> builds a widget from self.cfg / self.st."""
import shutil
from datetime import date
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from lifeboat import APP_ID, engine, timer  # noqa: E402
from lifeboat.app.widgets import CSS, ICONS, WORDS, ago, fmt_time, group, label, row  # noqa: E402

# pinned: never run an unpinned latest from npm
SKILLS_CLI = "skills@1.7.4"

PAGE_ICONS = {"overview": "go-home-symbolic", "backups": "folder-remote-symbolic", "restore": "document-revert-symbolic",
              "settings": "document-save-symbolic", "skills": "starred-symbolic", "help": "help-about-symbolic",
              "prefs": "emblem-system-symbolic"}
PAGE_NAMES = {"overview": "Overview", "backups": "Backups", "restore": "Restore", "settings": "Settings snapshot",
              "skills": "Skills", "help": "How it works", "prefs": "Preferences"}


class PagesMixin:
    def status_row(self, title, detail, st):
        return row(title, detail, [label(WORDS[st], CSS[st])], ICONS[st])

    def _gate(self, pid):
        """A status page when there is no usable config; None when the real page should show."""
        d = self.data
        if d["kind"] == "ok" or pid in ("help", "prefs"):
            return None
        if d["kind"] == "noconfig":
            sp = Adw.StatusPage(icon_name=APP_ID, title="Not set up yet",
                                description=f"Lifeboat reads its jobs from {d['path']}. "
                                            "Create an example config, then edit it to match your folders and rclone remotes.")
            sp.set_child(self._box([self.button("Create example config", self.create_example,
                                                ("pill", "suggested-action"), False)]))
        else:
            sp = Adw.StatusPage(icon_name="dialog-error-symbolic", title="Config problem", description=d["error"])
            sp.set_child(self._box([self.button("Open config file", lambda: self.open_path(d["path"]), ("pill",), False)]))
        return sp

    def create_example(self):
        src = Path(__file__).resolve().parent.parent.parent / "data" / "config.example.toml"
        dest = self.data["path"]
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.exists():
                shutil.copyfile(src, dest)
        except OSError as e:
            self.toast(f"Could not create config: {e.strerror}")
            return
        self.open_path(dest)
        self.refresh()

    # -- overview
    def page_overview(self):
        if (g := self._gate("overview")):
            return g
        st, v = self.st["status"], self.st["verdict"]
        S = self.st
        pg = Adw.PreferencesPage()
        pg.add(group(rows=[Adw.StatusPage(icon_name=APP_ID, title=v["title"], description=v["weakest"], css_classes=["compact"])]))
        last = S["last"]
        rows = [self.status_row("Backups", "Never run" if not last else
                                f"Last backup {ago(last['time'])}{' (dry run)' if last['dry_run'] else ''} · {len(S['jobs'])} job(s)",
                                st["backups"])]
        if S["settings"]["enabled"]:
            snap = S["settings"]["last"]
            rows.append(self.status_row("Settings snapshot", f"Last snapshot {ago(snap['time'])}" if snap else "Never run", st["settings"]))
        pg.add(group(rows=rows))
        pg.add(group(rows=[self._box([self.button("Back up now", lambda: self.run_cli("Back up now", "run"), ("pill", "suggested-action"))])]))
        return pg

    # -- backups
    def page_backups(self):
        if (g := self._gate("backups")):
            return g
        S = self.st
        if not S["rclone"]["found"]:
            return Adw.StatusPage(icon_name="network-server-symbolic", title="rclone not found",
                                  description="Lifeboat needs rclone to run backups. Install it, then refresh.")
        last, tm = S["last"], S["timers"]["lifeboat"]
        pg = Adw.PreferencesPage()
        nxt = fmt_time(tm["next"]) if tm["installed"] else "No timer installed (see Preferences)"
        pg.add(group(rows=[row("Next run", nxt),
                           row("Last run", (fmt_time(last["time"]) + (" (dry run)" if last["dry_run"] else "")) if last else "Never")]))
        if S["missing_remotes"]:
            pg.add(group(rows=[row("rclone remote not configured", "Missing: " + ", ".join(S["missing_remotes"]) + ". Run rclone config.",
                                   icon=ICONS["warn"])]))
        jr = []
        for j in S["jobs"]:
            sub = f"{j['source']} → {j['dest']}" + (f" · {j['seconds']} s" if j["seconds"] is not None else "")
            if j["rc"] is None:
                badge = label("Not run", "dim-label")
            else:
                badge = label("OK" if j["rc"] == 0 else f"Failed ({j['rc']})", "success" if j["rc"] == 0 else "error")
            r = row(j["name"], sub, [badge])
            if j["encrypted"]:
                r.add_prefix(Gtk.Image.new_from_icon_name("system-lock-screen-symbolic"))
            jr.append(r)
        pg.add(group("Jobs", rows=jr))
        pg.add(group(rows=[self._box([self.button("Back up now", lambda: self.run_cli("Back up now", "run"), ("pill", "suggested-action")),
                                      self.button("Dry run", lambda: self.run_cli("Dry run", "run", "--dry-run"))])]))
        pr, ptm = S["prune"], S["timers"]["lifeboat-prune"]
        pg.add(group("Safety net", rows=[
            row(f"Old versions kept {S['keep_days']} days",
                "Replaced and deleted files are moved aside, not lost. See the Restore page."),
            row("Pruning", f"Next prune {fmt_time(ptm['next'])}" if ptm["installed"] else "No prune timer installed",
                [label("Last: " + ago(pr["time"]), ["dim-label", "caption"])] if pr else None)]))
        return pg

    # -- restore
    def _job_name(self):
        return self.cfg.jobs[min(self.rsel["job"], len(self.cfg.jobs) - 1)].name

    def _fill_versions(self, vrow):
        name = self._job_name()
        self.rsel["row"] = vrow
        stamps = self.versions.get(name)
        vrow.set_model(Gtk.StringList.new(["Latest"] + (stamps or [])))
        vrow.set_subtitle("Listing old versions…" if stamps is None else
                          f"{len(stamps)} old version(s)" if stamps else "No old versions yet")
        if stamps is None:
            cfg = self.cfg

            def done(res):
                self.versions[name] = res
                if self.rsel["row"] is vrow and self._job_name() == name:
                    self._fill_versions(vrow)
            self.in_thread(lambda: engine.list_versions(cfg, name), done)
        else:
            vrow.set_selected(min(self.rsel["ver"], len(stamps)))

    def page_restore(self):
        if (g := self._gate("restore")):
            return g
        cfg, rs = self.cfg, self.rsel
        if rs["target"] is None:
            rs["target"] = str(Path.home() / f"Lifeboat-restore-{date.today().isoformat()}")
        pg = Adw.PreferencesPage()
        jrow = Adw.ComboRow(title="Backup set", model=Gtk.StringList.new([j.name for j in cfg.jobs]),
                            selected=min(rs["job"], len(cfg.jobs) - 1))
        vrow = Adw.ComboRow(title="Version", subtitle="Latest is what the backup holds now")
        trow = row("Restore into", rs["target"])
        pick = Gtk.Button(label="Choose…", valign=Gtk.Align.CENTER)
        pick.connect("clicked", lambda *_: self.choose_folder(trow))
        trow.add_suffix(pick)

        def job_changed(*_):
            rs["job"], rs["ver"] = jrow.get_selected(), 0
            self._fill_versions(vrow)
        jrow.connect("notify::selected", job_changed)
        vrow.connect("notify::selected", lambda *_: rs.__setitem__("ver", vrow.get_selected()))
        pg.add(group("What to restore", "Files are copied into a new folder. Nothing in your home is overwritten.",
                     [jrow, vrow, trow]))
        pg.add(group(rows=[self._box([self.button("Restore", self.do_restore, ("pill", "suggested-action"))])]))
        self._fill_versions(vrow)
        return pg

    def choose_folder(self, trow):
        dlg = Gtk.FileDialog(title="Restore into")

        def done(d, res):
            try:
                f = d.select_folder_finish(res)
            except GLib.Error:
                return
            if f and f.get_path():
                self.rsel["target"] = f.get_path()
                trow.set_subtitle(f.get_path())
        dlg.select_folder(self, None, done)

    def do_restore(self):
        rs = self.rsel
        name = self._job_name()
        stamps = self.versions.get(name) or []
        args = ["restore", name, "--to", rs["target"]]
        if rs["ver"] > 0 and rs["ver"] <= len(stamps):
            args += ["--version", stamps[rs["ver"] - 1]]
        self.run_cli("Restore " + name, *args)

    # -- settings snapshot
    def page_settings(self):
        if (g := self._gate("settings")):
            return g
        s = self.st["settings"]
        snap = s["last"]
        pg = Adw.PreferencesPage()
        pg.add(group("Command", "Runs before the backup jobs, every time.", [row(s["command"])]))
        if snap is None:
            lr = row("Last snapshot", "Never run")
        else:
            lr = row("Last snapshot", f"{ago(snap['time'])} · " + ("OK" if snap.get("ok") else f"Failed (exit {snap.get('rc')})"),
                     [label("OK" if snap.get("ok") else "Failed", "success" if snap.get("ok") else "error")])
        pg.add(group("Status", rows=[lr]))
        if snap and not snap.get("ok") and s["log"]:
            pg.add(group("Output", rows=[row(ln) for ln in s["log"]]))
        pg.add(group(rows=[self._box([self.button("Run now", lambda: self.run_cli("Settings snapshot", "run", "--settings-only"),
                                                  ("pill", "suggested-action"))])]))
        return pg

    # -- skills
    def page_skills(self):
        sk = self.st["skills"]
        pg = Adw.PreferencesPage()
        pg.add(group(f"External · {sk['external_count']} · installed from source",
                     rows=[row(src, ", ".join(n), [label(str(len(n)), ["dim-label", "caption"])]) for src, n in sk["external"].items()]
                     or [row("None")]))
        if shutil.which("npx"):
            pg.add(group(rows=[self._box([self.button("Update external skills", lambda: self.stream(
                "Update external skills", ["sh", "-c", f"npx -y {SKILLS_CLI} update -g </dev/null"]))])]))
        pg.add(group(f"Yours · {len(sk['own'])}", rows=[row(", ".join(sk["own"]) or "None")]))
        return pg

    # -- how it works
    def page_help(self):
        cfg = self.cfg
        pg = Adw.PreferencesPage()
        when = f"every day at {cfg.daily_at}" if cfg else "on a schedule you choose"
        keep = cfg.keep_days if cfg else 90
        pg.add(group("In one sentence", rows=[row(f"Your laptop backs itself up {when}",
            "Lifeboat copies your folders to an rclone remote such as Google Drive. You get a notification only if something fails; silence means it worked.")]))
        pg.add(group("What's protected", rows=[
            row("Backup sets", "Each job in your config copies one folder to one remote. Encrypted sets live on a crypt remote and are scrambled before upload."),
            row("Safety net", f"Anything a backup deletes or overwrites is moved to the old folder and kept {keep} days."),
            row("Settings snapshot", "Optional: a command of your choice (for example a dotfiles save) runs before the jobs.")]))
        pg.add(group("Not backed up", rows=[row("Anything not listed in a job", "Add a job to your config to protect another folder.", icon=ICONS["warn"])]))
        steps = [("Install Lifeboat and rclone", "rclone version", True),
                 ("Recreate your rclone remotes", "rclone config", True),
                 ("Put your config back", "~/.config/lifeboat/config.toml", None),
                 ("Copy your files back", "lifeboat restore JOB --to ~/restored", True)]
        rows = []
        for i, (t, s, cp) in enumerate(steps, 1):
            r = row(t, s, [self.copy_btn(s)] if cp else None)
            r.add_prefix(label(str(i), "dim-label"))
            rows.append(r)
        pg.add(group("If the laptop dies", rows=rows))
        return pg

    # -- preferences
    def page_prefs(self):
        d = self.data
        pg = Adw.PreferencesPage()
        pg.add(group("Config", "Edit this file, then press Refresh.", [
            row("Config file", str(d["path"]) + ("" if d["path"].exists() else " (not created yet)"),
                [self.button("Open config file", lambda: self.open_path(d["path"]) if d["path"].exists() else self.toast("No config yet"),
                             ("pill",), False)]),
            row("State and logs", str(engine.state_dir()))]))
        if self.cfg:
            tm = self.st["timers"]["lifeboat"]
            if tm["installed"]:
                tr = row("Daily timer", f"Installed · next run {fmt_time(tm['next'])}",
                         [self.button("Remove daily timer", self.remove_timer, ("pill",), False)])
            else:
                tr = row("Daily timer", f"Not installed · would run daily at {self.cfg.daily_at}",
                         [self.button("Install daily timer", self.install_timer, ("pill", "suggested-action"), False)])
            pg.add(group("Schedule", "systemd user timers: a daily backup and a weekly prune.", [tr]))
        return pg

    def install_timer(self):
        cfg = self.cfg
        self.in_thread(lambda: timer.install(cfg), lambda rc: (self.toast("Timer installed" if rc == 0 else "Could not install timer"), self.refresh()))

    def remove_timer(self):
        self.in_thread(timer.uninstall, lambda rc: (self.toast("Timer removed" if rc == 0 else "Could not remove timer"), self.refresh()))
