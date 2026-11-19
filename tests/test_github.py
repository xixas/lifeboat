import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from lifeboat import github

TOKEN = "ghu_" + "t" * 36


class MemStore:
    def __init__(self):
        self.value = None

    def get(self):
        return self.value

    def set(self, value):
        self.value = value

    def clear(self):
        self.value = None


class Fake:
    """Injectable http: routes (method, url) -> (status, body); records every call."""
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def __call__(self, method, url, headers=None, data=None):
        self.calls.append({"method": method, "url": url, "headers": headers or {}, "data": data})
        r = self.routes[(method, url)]
        r = r.pop(0) if isinstance(r, list) else r
        return r


def gh(routes, now=1000.0):
    store = MemStore()
    return github.GitHub(http=Fake(routes), store=store, now=lambda: now), store


DEV = ("POST", "https://github.com/login/device/code")
TOK = ("POST", "https://github.com/login/oauth/access_token")
GRANT = {"access_token": TOKEN, "refresh_token": "ghr_x", "expires_in": 28800, "refresh_token_expires_in": 15638400}


def test_client_id():
    assert github.CLIENT_ID == "Iv23liecZB4WN7Ij8DiP"


def test_start_device_flow():
    g, _ = gh({DEV: (200, {"device_code": "dc", "user_code": "K7QF-2M9D", "verification_uri": "https://github.com/login/device", "interval": 5, "expires_in": 900})})
    flow = g.start_device_flow()
    assert flow == {"device_code": "dc", "user_code": "K7QF-2M9D", "verification_uri": "https://github.com/login/device", "interval": 5}
    assert g.http.calls[0]["data"]["client_id"] == github.CLIENT_ID


def test_poll_token_states():
    g, store = gh({TOK: [(200, {"error": "authorization_pending"}), (200, {"error": "slow_down", "interval": 10}),
                         (200, {"error": "expired_token"}), (200, {"error": "access_denied"}), (200, GRANT)]})
    assert g.poll_token("dc")["status"] == "pending"
    assert g.poll_token("dc") == {"status": "slow_down", "interval": 10}
    assert g.poll_token("dc")["status"] == "expired"
    assert g.poll_token("dc")["status"] == "denied"
    assert store.get() is None
    assert g.poll_token("dc")["status"] == "ok"
    saved = json.loads(store.get())
    assert saved == {"access_token": TOKEN, "refresh_token": "ghr_x", "expires_at": 1000 + 28800, "refresh_expires_at": 1000 + 15638400}
    assert g.http.calls[-1]["data"]["grant_type"] == "urn:ietf:params:oauth:grant-type:device_code"


def _stored(g, store, expires_at, refresh_expires_at=10**9):
    store.set(json.dumps({"access_token": TOKEN, "refresh_token": "ghr_old", "expires_at": expires_at, "refresh_expires_at": refresh_expires_at}))


def test_token_refreshes_when_under_five_minutes():
    g, store = gh({TOK: (200, {**GRANT, "access_token": "ghu_new", "refresh_token": "ghr_new"})}, now=1000)
    _stored(g, store, expires_at=1000 + 299)
    assert g.token() == "ghu_new"
    assert g.http.calls[0]["data"]["grant_type"] == "refresh_token" and g.http.calls[0]["data"]["refresh_token"] == "ghr_old"
    assert json.loads(store.get())["refresh_token"] == "ghr_new"


def test_token_not_refreshed_when_fresh_and_none_without_login():
    g, store = gh({}, now=1000)
    assert g.token() is None and g.signed_in() is False
    _stored(g, store, expires_at=1000 + 301)
    assert g.token() == TOKEN and g.http.calls == []


def test_token_none_when_refresh_fails():
    g, store = gh({TOK: (200, {"error": "bad_refresh_token"})}, now=1000)
    _stored(g, store, expires_at=1000)
    assert g.token() is None


def test_sign_out_clears():
    g, store = gh({})
    store.set("x")
    g.sign_out()
    assert store.get() is None


INST = ("GET", "https://api.github.com/user/installations")


def signed(routes):
    g, store = gh(routes)
    _stored(g, store, expires_at=10**9)
    return g


def test_installations_and_install_url():
    g = signed({INST: (200, {"total_count": 1, "installations": [{"id": 7, "repository_selection": "selected", "x": 1}]})})
    assert g.installations() == [{"id": 7, "repository_selection": "selected"}]
    assert github.install_url() == "https://github.com/apps/lifeboat-backup/installations/new"
    assert g.http.calls[0]["headers"]["Authorization"] == f"Bearer {TOKEN}"


def test_installation_repos_requires_an_installation():
    g = signed({INST: (200, {"total_count": 0, "installations": []})})
    with pytest.raises(github.NotInstalled):
        g.installation_repos()


def test_new_repo_url():
    assert github.new_repo_url() == "https://github.com/new?name=skillshare-source&visibility=private"
    assert github.new_repo_url("my repo") == "https://github.com/new?name=my+repo&visibility=private"
    assert not hasattr(github.GitHub, "create_repo")


def test_installation_repos_lists_every_installation():
    g = signed({INST: (200, {"installations": [{"id": 7, "repository_selection": "selected"}, {"id": 8, "repository_selection": "all"}]}),
                ("GET", "https://api.github.com/user/installations/7/repositories"): (200, {"repositories": [{"full_name": "me/a", "clone_url": "ca", "private": True}]}),
                ("GET", "https://api.github.com/user/installations/8/repositories"): (200, {"repositories": [{"full_name": "me/b", "clone_url": "cb", "private": False}]})})
    assert [r["full_name"] for r in g.installation_repos()] == ["me/a", "me/b"]


REAL_GIT = shutil.which("git")
FAKE_GIT = """#!{python}
import json, os, re, subprocess, sys, time
args = sys.argv[1:]
if "push" not in args:
    os.execv({real!r}, ["git"] + args)
env = os.environ
helper = next((v for k, v in env.items() if k.startswith("GIT_CONFIG_VALUE_") and v.startswith("!f()")), "")
path = re.search(r"cat (\\S+?)\\)", helper).group(1) if helper else ""
rec = {{"argv": args, "env": {{k: v for k, v in env.items() if k.startswith(("GIT_", "HOME", "XDG_"))}}, "home_entries": os.listdir(env["HOME"]),
       "helper_out": subprocess.run(["sh", "-c", helper[1:] + " get"], capture_output=True, text=True).stdout if helper else "",
       "path": path, "mode": oct(os.stat(path).st_mode & 0o777) if path else "", "dir_mode": oct(os.stat(os.path.dirname(path)).st_mode & 0o777) if path else "",
       "content": open(path).read() if path else ""}}
open(env["FAKE_GIT_LOG"], "a").write(json.dumps(rec) + "\\n")
time.sleep(float(env.get("FAKE_GIT_SLEEP", "0")))
sys.stderr.write(env.get("FAKE_GIT_ERR", ""))
sys.exit(int(env.get("FAKE_GIT_RC", "0")))
"""


class FakeGit:
    """A `git` first on PATH that records every `push` (argv, the environment, what the credential helper says) and runs everything else for real."""
    def __init__(self, tmp_path, monkeypatch):
        d = tmp_path / "fakegit-bin"
        d.mkdir()
        (d / "git").write_text(FAKE_GIT.format(python=sys.executable, real=REAL_GIT))
        (d / "git").chmod(0o755)
        self.log_file = tmp_path / "pushes.jsonl"
        monkeypatch.setenv("PATH", f"{d}{os.pathsep}{os.environ['PATH']}")
        monkeypatch.setenv("FAKE_GIT_LOG", str(self.log_file))

    @property
    def pushes(self):
        return [json.loads(ln) for ln in self.log_file.read_text().splitlines()] if self.log_file.exists() else []


@pytest.fixture
def fakegit(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    (tmp_path / "run").mkdir()
    return FakeGit(tmp_path, monkeypatch)


def mkrepo(tmp_path, origin="https://github.com/o/r.git"):
    work = tmp_path / "work"
    for args in (["init", "-q", "-b", "main"], ["remote", "add", "origin", origin]):
        subprocess.run([REAL_GIT, "-C", str(work.mkdir(exist_ok=True) or work), *args], check=True)
    (work / "f").write_text("x")
    for args in (["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "c"]):
        subprocess.run([REAL_GIT, "-C", str(work), *args], check=True)
    return work


@pytest.mark.parametrize("origin", ["git@github.com:o/r.git", "https://bob:hunter2pw@github.com/o/r.git", "https://github.com/o/r.git", "/some/local/path.git"])
def test_the_push_goes_to_the_https_url_with_lifeboats_token_whatever_the_user_has_configured(tmp_path, monkeypatch, fakegit, origin):
    work = mkrepo(tmp_path, origin)
    subprocess.run([REAL_GIT, "-C", str(work), "config", "http.extraHeader", "Authorization: Basic cmVwby1sZXZlbA=="], check=True)
    subprocess.run([REAL_GIT, "-C", str(work), "config", "credential.helper", "store"], check=True)
    home = tmp_path / "userhome"
    home.mkdir()
    (home / ".netrc").write_text("machine github.com login bob password netrc-secret\n")
    (home / ".gitconfig").write_text('[url "ssh://evil.example/"]\n\tinsteadOf = https://github.com/\n[http]\n\textraHeader = Authorization: Basic Z2xvYmFs\n')
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(home / ".gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "url.ssh://evil2.example/.insteadOf")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "https://github.com/")
    assert github.push(work, TOKEN, "o/r")["ok"] is True
    (rec,) = fakegit.pushes
    assert rec["argv"][-3:] == ["push", "https://github.com/o/r.git", "refs/heads/main:refs/heads/main"] and "hunter2pw" not in str(rec["argv"])
    e = rec["env"]
    assert e["GIT_CONFIG_NOSYSTEM"] == "1" and e["GIT_CONFIG_GLOBAL"] == "/dev/null" and e["GIT_TERMINAL_PROMPT"] == "0"
    assert e["HOME"] != str(home) and rec["home_entries"] == []  # an empty home: no ~/.netrc, no ~/.gitconfig
    assert "evil" not in " ".join(e.values()) and e["GIT_CONFIG_COUNT"] == "3"
    assert rec["helper_out"] == f"username=x-access-token\npassword={TOKEN}\n"
    effective = subprocess.run([REAL_GIT, "-C", str(work), "ls-remote", "--get-url", "https://github.com/o/r.git"], env={**e, "PATH": os.environ["PATH"]}, capture_output=True, text=True).stdout.strip()
    assert effective == "https://github.com/o/r.git"  # no insteadOf from the user's config applies
    header = subprocess.run([REAL_GIT, "-C", str(work), "config", "--get", "http.extraHeader"], env={**e, "PATH": os.environ["PATH"]}, capture_output=True, text=True).stdout.strip()
    assert header == ""  # the repo's and the user's extra header are reset
    helpers = subprocess.run([REAL_GIT, "-C", str(work), "config", "--get-all", "credential.helper"], env={**e, "PATH": os.environ["PATH"]}, capture_output=True, text=True).stdout.splitlines()
    assert [h for h in helpers if h][-1].startswith("!f()") and helpers.index("") < len(helpers) - 1  # reset first, then ours


@pytest.mark.parametrize("key,value", [("url.ssh://git@github.com/.insteadOf", "https://github.com/"), ("url.ssh://git@github.com/.pushInsteadOf", "https://github.com/o/"),
                                       ("http.https://github.com/.extraHeader", "Authorization: Basic eHg="), ("credential.https://github.com.helper", "store")])
def test_a_repo_setting_that_would_reroute_or_re_authenticate_the_push_refuses_it(tmp_path, fakegit, key, value):
    work = mkrepo(tmp_path)
    subprocess.run([REAL_GIT, "-C", str(work), "config", key, value], check=True)
    r = github.push(work, TOKEN, "o/r")
    assert r["ok"] is False and key.lower() in r["error"] and fakegit.pushes == [] and TOKEN not in r["error"]


def test_a_repo_rewrite_of_another_host_does_not_matter(tmp_path, fakegit):
    work = mkrepo(tmp_path)
    subprocess.run([REAL_GIT, "-C", str(work), "config", "url.ssh://git@example.com/.insteadOf", "https://example.com/"], check=True)
    assert github.push(work, TOKEN, "o/r")["ok"] is True


@pytest.mark.parametrize("name", ["../x", "o/r/extra", "o", "o/r; rm -rf", ""])
def test_only_an_owner_and_name_is_pushed(tmp_path, fakegit, name):
    r = github.push(mkrepo(tmp_path), TOKEN, name)
    assert r["ok"] is False and fakegit.pushes == []


def test_a_detached_head_is_not_pushed(tmp_path, fakegit):
    work = mkrepo(tmp_path)
    subprocess.run([REAL_GIT, "-C", str(work), "checkout", "-q", "--detach"], check=True)
    r = github.push(work, TOKEN, "o/r")
    assert r["ok"] is False and "branch" in r["error"] and fakegit.pushes == []


def test_the_token_is_in_a_private_file_never_in_argv_or_the_environment(tmp_path, monkeypatch, fakegit):
    work = mkrepo(tmp_path)
    runtime = tmp_path / "run"
    seen = {}
    orig = github._remove

    def spy(path, folder):
        seen["existed"] = os.path.exists(path)
        orig(path, folder)
    monkeypatch.setattr(github, "_remove", spy)
    assert github.push(work, TOKEN, "o/r")["ok"] is True
    (rec,) = fakegit.pushes
    assert rec["mode"] == "0o600" and rec["content"].strip() == TOKEN and rec["path"].startswith(str(runtime))
    assert TOKEN not in str(rec["argv"]) and TOKEN not in "".join(rec["env"].values()) and "LIFEBOAT_GH_TOKEN" not in rec["env"]
    assert not os.path.exists(rec["path"])  # gone once the push is over


def test_the_token_file_is_removed_when_the_push_fails_or_crashes(tmp_path, monkeypatch, fakegit):
    work = mkrepo(tmp_path)
    monkeypatch.setenv("FAKE_GIT_RC", "1")
    monkeypatch.setenv("FAKE_GIT_ERR", "denied")
    assert github.push(work, TOKEN, "o/r")["ok"] is False
    assert not os.path.exists(fakegit.pushes[-1]["path"])
    monkeypatch.setenv("FAKE_GIT_SLEEP", "30")
    assert github.push(work, TOKEN, "o/r", timeout=1)["ok"] is False  # TimeoutExpired
    assert not os.path.exists(fakegit.pushes[-1]["path"])


def test_without_a_runtime_dir_the_file_lives_in_a_private_temp_dir(tmp_path, monkeypatch, fakegit):
    work = mkrepo(tmp_path)
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    tmp = tmp_path / "tmp"
    tmp.mkdir()
    monkeypatch.setenv("TMPDIR", str(tmp))
    github.push(work, TOKEN, "o/r")
    (rec,) = fakegit.pushes
    assert (rec["mode"], rec["dir_mode"]) == ("0o600", "0o700") and list(tmp.iterdir()) == []


def test_a_pre_push_hook_does_not_see_the_token(tmp_path, monkeypatch):
    """The real thing: git runs a hook during the push, and the hook's environment must not hold the token."""
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    bare = tmp_path / "hub" / "o" / "r.git"
    bare.parent.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    monkeypatch.setattr(github, "GIT_HOST", f"file://{tmp_path / 'hub'}")  # the test's stand-in for github.com
    work = mkrepo(tmp_path)
    dump = tmp_path / "hook-env.txt"
    hook = work / ".git" / "hooks" / "pre-push"
    hook.write_text(f"#!/bin/sh\nenv > {dump}\nprintf '%s\\n' \"$@\" >> {dump}\nps -o args= -p $PPID >> {dump}\nexit 0\n")
    hook.chmod(0o755)
    assert github.push(work, TOKEN, "o/r")["ok"] is True
    assert dump.exists() and TOKEN not in dump.read_text() and "LIFEBOAT_GH_TOKEN" not in dump.read_text()
    assert subprocess.run(["git", "-C", str(bare), "rev-parse", "main"], capture_output=True, text=True).returncode == 0
    assert not [p for p in tmp_path.iterdir() if p.name.startswith("lifeboat")]


def test_push_failure_reports_error_without_token(tmp_path, monkeypatch, fakegit):
    monkeypatch.setenv("FAKE_GIT_RC", "1")
    monkeypatch.setenv("FAKE_GIT_ERR", f"denied for {TOKEN}")
    r = github.push(mkrepo(tmp_path), TOKEN, "o/r")
    assert r["ok"] is False and TOKEN not in r["error"] and "denied" in r["error"]


def test_keyring_store_uses_secret_tool_with_stdin(monkeypatch):
    calls = []

    def fake_run(argv, **kw):
        calls.append((argv, kw))
        return subprocess.CompletedProcess(argv, 0, "VALUE", "")
    monkeypatch.setattr(github.subprocess, "run", fake_run)
    s = github.KeyringStore()
    s.set("secret-blob")
    assert calls[0][0][:1] == ["secret-tool"] and calls[0][0][1] == "store"
    assert calls[0][0][-4:] == ["service", "lifeboat", "kind", "github"]
    assert calls[0][1]["input"] == "secret-blob" and "secret-blob" not in calls[0][0]
    assert s.get() == "VALUE"
    assert calls[1][0] == ["secret-tool", "lookup", "service", "lifeboat", "kind", "github"]
    s.clear()
    assert calls[2][0][:2] == ["secret-tool", "clear"]


def test_keyring_store_without_secret_tool(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError
    monkeypatch.setattr(github.subprocess, "run", boom)
    s = github.KeyringStore()
    assert s.get() is None
    with pytest.raises(github.GitHubError, match="secret-tool"):
        s.set("x")


def test_a_token_without_expiry_is_kept_as_never_expiring_and_never_refreshed():
    g, store = gh({}, now=1000)
    g.http.routes[TOK] = (200, {"access_token": TOKEN, "token_type": "bearer"})
    assert g.poll_token("dc") == {"status": "ok"}
    saved = json.loads(store.get())
    assert saved["expires_at"] is None and saved["refresh_token"] == ""
    g.now = lambda: 10**12  # far in the future
    assert g.token() == TOKEN
    assert g.http.calls[-1]["data"]["grant_type"] != "refresh_token"
    assert [c for c in g.http.calls if c["data"].get("grant_type") == "refresh_token"] == []
    assert g.signed_in()


def test_an_old_saved_token_without_expires_at_is_not_refreshed():
    g, store = gh({}, now=10**9)
    store.set(json.dumps({"access_token": TOKEN, "refresh_token": ""}))
    assert g.token() == TOKEN and g.http.calls == []


def test_an_expiring_token_with_no_refresh_token_means_sign_in_again():
    g, store = gh({}, now=1000)
    store.set(json.dumps({"access_token": TOKEN, "refresh_token": "", "expires_at": 1000}))
    assert g.token() is None and g.http.calls == []


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGHUP])
def test_the_token_file_is_removed_when_the_process_gets_sigterm_or_sighup_mid_push(tmp_path, sig):
    runtime = tmp_path / "run"
    runtime.mkdir()
    fakebin = tmp_path / "bin"
    fakebin.mkdir()
    started, gitpid = tmp_path / "started", tmp_path / "gitpid"
    (fakebin / "git").write_text(f'#!/bin/sh\ncase " $* " in *" push "*) ;; *) exec {REAL_GIT} "$@";; esac\necho $$ > {gitpid}\ntouch {started}\nexec sleep 60\n')
    (fakebin / "git").chmod(0o755)
    work = mkrepo(tmp_path)
    code = "import sys; from lifeboat import github; github.push(sys.argv[1], 'tok-secret-123', 'o/r', timeout=120)"
    env = dict(os.environ, XDG_RUNTIME_DIR=str(runtime), PATH=f"{fakebin}{os.pathsep}{os.environ['PATH']}",
               PYTHONPATH=str(Path(github.__file__).parent.parent))
    child = subprocess.Popen([sys.executable, "-c", code, str(work)], env=env)
    try:
        deadline = time.time() + 15
        while not started.exists() and time.time() < deadline:
            time.sleep(0.05)
        assert started.exists() and [p for p in runtime.iterdir() if p.name.startswith("lifeboat-gh-")]
        child.send_signal(sig)
        assert child.wait(timeout=15) == -sig  # the default behaviour was re-raised
        assert list(runtime.iterdir()) == []
    finally:
        child.kill() if child.poll() is None else None
        if gitpid.exists():
            os.kill(int(gitpid.read_text()), signal.SIGKILL)  # the fake git this test started


def test_the_signal_handlers_are_restored_after_a_push(tmp_path, monkeypatch, fakegit):
    before = (signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGHUP))
    github.push(mkrepo(tmp_path), TOKEN, "o/r")
    assert (signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGHUP)) == before


# ---------------------------------------------------------------- a token file left behind by a push that could not clean up
def _aged(path, seconds):
    t = time.time() - seconds
    os.utime(path, (t, t))


def test_stale_token_files_in_the_runtime_dir_are_deleted_and_nothing_else(tmp_path, monkeypatch):
    runtime = tmp_path / "run"
    runtime.mkdir()
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    for name in ("lifeboat-gh-token-abc", "lifeboat-gh-xyz"):  # the current and the earlier prefix
        (runtime / name).write_text(TOKEN)
        _aged(runtime / name, 3600)
    (runtime / "lifeboat-gh-token-live").write_text(TOKEN)  # a push in another process is using this one right now
    (runtime / "other.txt").write_text("keep")
    _aged(runtime / "other.txt", 3600)
    github.clear_stale_token_files()
    assert sorted(p.name for p in runtime.iterdir()) == ["lifeboat-gh-token-live", "other.txt"]


def test_clearing_stale_token_files_without_a_runtime_dir_does_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    github.clear_stale_token_files()
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "missing"))
    github.clear_stale_token_files()


def test_the_token_file_name_starts_with_lifeboat_gh_token(tmp_path, fakegit):
    github.push(mkrepo(tmp_path), TOKEN, "o/r")
    assert os.path.basename(fakegit.pushes[0]["path"]).startswith("lifeboat-gh-token-")
