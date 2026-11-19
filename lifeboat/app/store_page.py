"""The Skills on GitHub page (status, upload, sign out) and the dialog that sets it up: install skillshare, pick the folder,
connect GitHub, save [store] in the config."""
import os
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from lifeboat import catalogue, engine, github, store, store_install  # noqa: E402
from lifeboat.app.flow import AccountDialog, pill, vbox  # noqa: E402
from lifeboat.app.widgets import CSS, ICONS, ago, copy_text, group, row, spinner  # noqa: E402
from lifeboat.app.wizard_state import tilde  # noqa: E402
from lifeboat.config import ConfigError, set_store_text, write_config_text  # noqa: E402

# Indirection so the Broadway tests can swap the network and the keyring for stubs.
GITHUB = github.GitHub
POLL_SLEEP = time.sleep
UI_URL = "http://127.0.0.1:19420"


def gather_store(cfg) -> dict:
    """What the Skills on GitHub page shows; runs off the main thread. Touches the keyring only once a store is set up."""
    if not (cfg.store_source and cfg.store_push):
        return {"set_up": False}
    return {"set_up": True, "git": store.git_state(os.path.expanduser(cfg.store_source)), "signed_in": GITHUB().signed_in()}


def _upload_text(last):
    """Title, subtitle and mark of the status row."""
    if last is None:
        return "Not uploaded yet", "Runs after the daily backup, or press Upload now.", "warn"
    if last.get("blocked_findings"):
        return "Blocked: a secret was found", "Nothing was sent. Remove the secrets below, then press Upload now.", "error"
    if not last["ok"]:
        return "Upload failed", engine.upload_error_text(last.get("error")), "error"
    return ("Uploaded" if last.get("pushed") else "Nothing to upload"), ago(last["time"]).capitalize(), "ok"


def _waiting_text(git):
    n = git["ahead"] + git["dirty_count"]  # unsent commits plus files not committed yet
    return f"{n} change{'s' if n != 1 else ''}" if n else "Nothing waiting"


class StoreMixin:
    def page_store(self):
        if (g := self._gate("store")):
            return g
        cfg, info = self.cfg, self.data["store"]
        if not info["set_up"]:
            sp = Adw.StatusPage(icon_name="folder-publicshare-symbolic", title="Keep your skills on GitHub",
                                description="GitHub keeps your skills with their full history, so you can roll back or set up a new laptop. "
                                            "Secrets and personal files never go there: they stay in your Drive backup.")
            sp.set_child(vbox(self.button("Set up", self.open_store_setup, ("pill", "suggested-action"), False)))
            return sp
        git, last = info["git"], self.st["store_push"]
        pg = Adw.PreferencesPage()
        if not info["signed_in"]:  # nothing can be uploaded: the one thing to do is on the first row
            top = row("Signed out of GitHub", "Uploads are paused")
            top.add_prefix(Gtk.Image(icon_name=ICONS["warn"], css_classes=[CSS["warn"]]))
            top.add_suffix(self._suffix_button("Sign in", self.open_store_signin, True))
            pg.add(group("Last upload", rows=[top]))
        else:
            title, sub, mark = _upload_text(last)
            r = row(title, sub)
            r.add_prefix(Gtk.Image(icon_name=ICONS[mark], css_classes=[CSS[mark]]))
            r.add_suffix(self._suffix_button("Upload now", self.push_store, mark != "ok"))  # not an action button: finishing a task must not re-enable it
            findings = []
            for f in (last or {}).get("blocked_findings", []):
                findings.append(row(f["file"], engine.finding_text(f)))
                findings[-1].add_prefix(Gtk.Image(icon_name=ICONS["error"], css_classes=[CSS["error"]]))
            pg.add(group("Last upload", rows=[r] + findings))
        rows = [row("On GitHub", store.github_repo(git["remote_url"]) or "No GitHub repository yet"),
                row("Waiting to upload", _waiting_text(git)),
                row("Folder", tilde(store_source_path(cfg)))]
        if info["signed_in"]:
            acct = row("GitHub account", "Signed in")
            acct.add_suffix(self._account_menu())
            rows.append(acct)
        pg.add(group("Your skills", rows=rows))
        manage = row("Skillshare", "Add, update or remove the skills in your folder")
        manage.add_suffix(self._suffix_button("Manage skills", self.open_skillshare, False))
        pg.add(group(rows=[manage]))
        return pg

    @staticmethod
    def _suffix_button(text, cb, suggested):
        b = Gtk.Button(label=text, valign=Gtk.Align.CENTER, css_classes=["suggested-action"] if suggested else [])
        b.connect("clicked", lambda *_: cb())
        return b

    def _account_menu(self):
        out = Gtk.Button(label="Sign out", css_classes=["flat"])
        pop = Gtk.Popover(child=out)
        out.connect("clicked", lambda *_: (pop.popdown(), self.sign_out_store()))
        return Gtk.MenuButton(icon_name="view-more-symbolic", popover=pop, valign=Gtk.Align.CENTER, css_classes=["flat"], tooltip_text="Account options")

    def open_store_setup(self):
        StoreSetup(self).present(self)

    def open_store_signin(self):
        StoreSetup(self, signin_only=True).present(self)

    def push_store(self):
        def done(res):
            self.toast("Skills on GitHub: " + ("uploaded" if res and res["pushed"] else "nothing to upload" if res and res["ok"] else "not uploaded"))
            self.refresh()
        self.in_thread(store.push_in_subprocess, done)  # its own process, so a killed push cleans its token file up

    def open_skillshare(self):
        def done(ok):
            if not ok:
                self.toast("skillshare is not installed")
                return
            GLib.timeout_add(1500, lambda: (self.open_url(UI_URL), False)[1])
        self.in_thread(store.start_ui, done)

    def sign_out_store(self):
        self.in_thread(lambda: GITHUB().sign_out(), lambda _r: (self.toast("Signed out of GitHub"), self.refresh()))


def store_source_path(cfg) -> str:
    return os.path.expanduser(cfg.store_source)


class StoreSetup(AccountDialog):
    """Install, pick, connect GitHub, done. Each step is a page; a failure stops on that page with the reason."""

    def __init__(self, win, signin_only=False):
        super().__init__(win, "Sign in to GitHub" if signin_only else "Set up skills on GitHub")
        self.gh = GITHUB()
        self.repo = None  # the chosen checkout, None for a new store
        self.source = None  # the folder skillshare ends up using
        self.remote = None  # github.com URL of the store, once known
        self.picked = None
        self.cands = []
        self.existing = None  # the folder skillshare already uses, from an earlier or interrupted setup
        self.nav.add(self._install_page())
        if signin_only:  # the store is set up; only the GitHub sign-in is missing or was revoked
            self.source = store_source_path(win.cfg)
            self.step_row.set_title("Checking your store")
            self.step_row.set_subtitle("")
            self.win.in_thread(lambda: store.git_state(self.source)["remote_url"], self._remote_known)
        else:
            self._run_install()

    # -- 1. install skillshare
    def _install_page(self):
        self.step_row = row("Installing skillshare and the secret scanner", f"skillshare {store_install.SKILLSHARE_VERSION} and gitleaks {store_install.GITLEAKS_VERSION}, each checked against a built-in hash")
        self.step_row.add_suffix(spinner())
        pg = Adw.PreferencesPage()
        pg.add(group("Step 1 of 3", "skillshare keeps your skills in one folder. Lifeboat adds a secret check before anything is uploaded.",
                     [self.step_row]))
        self.install_content = pg
        self.install_page = self._page("install", "Getting ready", pg, back=False)
        return self.install_page

    def _run_install(self):
        def work():
            store_install.ensure_installed()
            return catalogue.scan(Path.home()), store.current()
        self.win.in_thread(work, self._installed, lambda e: self._install_failed(e))

    def _install_failed(self, e):
        if self.dead:
            return
        if not isinstance(e, store_install.InstallError):
            msg = "Could not download skillshare. Check your connection and try again."
        elif "checksum mismatch" in str(e):
            msg = "The download did not match what Lifeboat expected, so nothing was installed."
        else:
            msg = str(e)
        self.set_can_close(True)
        sp = Adw.StatusPage(icon_name="dialog-error-symbolic", title="Could not install skillshare", description=msg)
        sp.set_child(vbox(pill("Try again", self._retry_install), pill("Close", self.force_close, suggested=False)))
        self.install_page.toolbar.set_content(sp)

    def _retry_install(self):
        self.install_page.toolbar.set_content(self.install_content)
        self._run_install()

    def _installed(self, res):
        if self.dead:
            return
        items, self.existing = res
        self.cands = store.candidates(items)
        self.push(self._pick_page())

    # -- 2. pick the store
    def _pick_page(self):
        pg = Adw.PreferencesPage()
        if self.existing:  # skillshare is already set up: carry on with its folder, a second init would fail
            self.picked = self.existing
            options = [(self.existing, tilde(self.existing), "Existing skillshare folder")]
            desc = "skillshare already uses this folder."
        else:
            self.picked = self.cands[0]["path"] if self.cands else None
            options = [(c["path"], f"{tilde(c['path'])} \u00b7 {c['skills']} skills", "Your assistants already use this folder") for c in self.cands]
            options.append((None, "Start with an empty folder", "~/.config/skillshare"))
            desc = "Where do your skills live? Existing skills are never moved or replaced."
        g = Adw.PreferencesGroup(title="Step 2 of 3", description=desc)
        leader = Gtk.CheckButton(visible=False)
        for path, title, sub in options:
            r = Adw.ActionRow(title=title, subtitle=sub, use_markup=False)
            if len(options) > 1:  # a lone radio would offer no choice: show the one option as plain information
                c = Gtk.CheckButton(valign=Gtk.Align.CENTER)
                c.set_group(leader)
                c.set_active(path == self.picked)
                c.connect("toggled", lambda b, p=path: b.get_active() and setattr(self, "picked", p))
                r.add_prefix(c)
                r.set_activatable_widget(c)
            g.add(r)
        pg.add(g)
        self.pick_btn = pill("Continue", self._prepare)
        self.pick_page = self._page("pick", "Pick your skills", pg, bottom=self._bar(self.pick_btn))
        return self.pick_page

    def _prepare(self):
        self.pick_btn.set_sensitive(False)
        repo = self.picked
        self.win.in_thread(lambda: store.prepare(repo), self._prepared, lambda _e: self._prepared({"ok": False, "error": "Unexpected error."}))

    def _prepared(self, res):
        if self.dead:
            return
        self.pick_btn.set_sensitive(True)
        if not res["ok"]:
            self.win.toast(res["error"] or "Could not set up skillshare")
            return
        self.source = res["source"]
        self.repo = res["source"]
        self.win.in_thread(lambda: store.git_state(self.source)["remote_url"], self._remote_known)

    def _remote_known(self, url):
        if self.dead:
            return
        self.remote = url if store.github_repo(url) else None
        self.push(self._connect_page())

    # -- 3. GitHub
    def _connect_page(self):
        pg = Adw.PreferencesPage()
        steps = Adw.PreferencesGroup(title="Step 3 of 3", description="Lifeboat can reach only the repository you choose.")
        self.url_row = Adw.EntryRow(title="2. Paste its address", text=self.remote or "", sensitive=not self.remote)
        self.url_row.connect("changed", lambda *_: self._url_changed())
        n = 1
        if not self.remote:
            create = row("1. Create a private repository")
            create.add_suffix(self._small("Open GitHub", lambda: self.win.open_url(github.new_repo_url())))
            steps.add(create)
            steps.add(self.url_row)
            n = 3
        else:
            steps.add(row("Your skills folder is already on GitHub", store.github_repo(self.remote)))
        inst = row(f"{n}. Let Lifeboat into that repository only" if not self.remote else "Let Lifeboat into that repository only")
        inst.add_suffix(self._small("Open GitHub", lambda: self.win.open_url(github.install_url())))
        steps.add(inst)
        pg.add(steps)
        self.signin_btn = pill("Sign in", self._start_signin)
        self.signin_btn.set_sensitive(bool(self.remote))
        self.err_label = Gtk.Label(wrap=True, xalign=0, css_classes=["error", "caption"], visible=False, margin_start=24, margin_end=24)
        g2 = Adw.PreferencesGroup()
        g2.add(self.err_label)
        pg.add(g2)
        self.connect_page = self._page("connect", "Connect GitHub", pg, bottom=self._bar(self.signin_btn))
        return self.connect_page

    @staticmethod
    def _small(text, cb):
        b = Gtk.Button(label=text, valign=Gtk.Align.CENTER)
        b.connect("clicked", lambda *_: cb())
        return b

    def _url_changed(self):
        url = self.url_row.get_text().strip()
        ok = store.github_repo(url) is not None
        self.signin_btn.set_sensitive(ok)
        self._mark_invalid(self.url_row, self.err_label, "" if ok or not url else "That is not a github.com repository address.")

    def _repo_url(self):
        return self.remote or self.url_row.get_text().strip()

    def _start_signin(self):
        self.signin_btn.set_sensitive(False)
        self.err_label.set_visible(False)
        self.win.in_thread(self.gh.start_device_flow, self._flow_started, lambda e: self._connect_error(e))

    def _connect_error(self, e):
        if self.dead:
            return
        self.signin_btn.set_sensitive(True)
        self.err_label.set_label(str(e) if isinstance(e, github.GitHubError) else "Something went wrong. Try again.")
        self.err_label.set_visible(True)
        if self.nav.get_visible_page() is not self.connect_page:
            self.nav.pop()

    def _flow_started(self, flow):
        if self.dead:
            return
        self.push(self._code_page(flow))
        self.win.open_url(flow["verification_uri"])
        self.win.in_thread(lambda: self._poll(flow), self._signed_in, lambda e: self._connect_error(e))

    def _code_page(self, flow):
        code = Gtk.Label(use_markup=True, label=f'<span font_family="monospace" size="xx-large" weight="bold">{GLib.markup_escape_text(flow["user_code"])}</span>')
        copy = Gtk.Button(label="Copy", css_classes=["pill"])
        copy.connect("clicked", lambda *_: (copy_text(self.win, flow["user_code"]), self.win.toast("Code copied")))
        wait = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
        wait.append(spinner())
        wait.append(Gtk.Label(label="Waiting for you to approve on GitHub…", css_classes=["dim-label"]))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, margin_start=12, margin_end=12)
        for w in (code, vbox(copy), wait):
            box.append(w)
        sp = Adw.StatusPage(icon_name="dialog-password-symbolic", title="Enter this code on GitHub", css_classes=["compact"],
                            description=f"GitHub opened in your browser. If it did not, go to {flow['verification_uri']} and type the code.")
        sp.set_child(box)
        self.code_page = self._page("code", "Sign in to GitHub", sp)
        return self.code_page

    def _poll(self, flow):
        """Runs off the main thread until the user approves, denies or the code expires."""
        interval, end = int(flow.get("interval", 5)), time.monotonic() + 900
        while not self.dead and time.monotonic() < end:
            POLL_SLEEP(interval)
            r = self.gh.poll_token(flow["device_code"])
            if r["status"] == "slow_down":
                interval = int(r.get("interval", interval + 5))
            elif r["status"] != "pending":
                return r
        return {"status": "expired"}

    def _signed_in(self, res):
        if self.dead:
            return
        if res["status"] != "ok":
            words = {"denied": "You declined the sign-in.", "expired": "The code expired."}
            self.nav.pop()
            self._connect_error(github.GitHubError(words.get(res["status"], "GitHub could not sign you in.") + " Try again."))
            return
        url = self._repo_url()
        self.win.in_thread(lambda: store.find_repo(self.gh, url), self._verified, lambda e: self._verify_failed(e))

    def _verify_failed(self, e):
        if self.dead:
            return
        msg = ("The Lifeboat GitHub App is not installed on that repository yet. Install it, then sign in again."
               if isinstance(e, github.NotInstalled) else str(e) if isinstance(e, github.GitHubError) else "Could not reach GitHub.")
        self.nav.pop()
        self._connect_error(github.GitHubError(msg))

    def _verified(self, repo):
        if self.dead:
            return
        if repo is None:
            self._verify_failed(github.NotInstalled())
            return
        self.win.in_thread(lambda: self._save(repo), self._saved, lambda e: self._save_failed(e))

    # -- 4. save and finish
    def _save(self, repo):
        if not self.remote:
            store.connect_remote(self.source, repo["clone_url"])
        path = self.win.cfg.path
        write_config_text(path, set_store_text(path.read_text(encoding="utf-8"), tilde(self.source)))
        store.clear_signed_out()
        return repo

    def _save_failed(self, e):
        if self.dead:
            return
        self.nav.pop()
        self._connect_error(github.GitHubError(str(e) if isinstance(e, ConfigError) else "Could not save your GitHub settings."))

    def _saved(self, repo):
        if self.dead:
            return
        self._refresh_on_close = True
        self.set_can_close(True)
        sp = Adw.StatusPage(icon_name="object-select-symbolic", title="Skills on GitHub is set up",
                            description=f"{repo['full_name']} will get your skills after each daily backup, once the secret check passes.")
        sp.set_child(vbox(pill("Done", self.force_close)))
        page = self._page("done", "Done", sp, back=False)
        page.set_can_pop(False)
        self.push(page)

