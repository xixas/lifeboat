import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from lifeboat import github

TOKEN = "ghu_" + "t" * 36


def helper_path(argv):
    """The token file the credential helper in a `git push` argv reads."""
    helper = next(a for a in argv if a.startswith("credential.helper=!f()"))
    return re.search(r"cat (\S+?)\)", helper).group(1)


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


def test_push_passes_the_token_in_a_private_file_never_in_the_environment(tmp_path, monkeypatch):
    runtime = tmp_path / "run"
    runtime.mkdir()
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    seen = {}

    def fake_run(argv, **kw):
        path = helper_path(argv)
        seen.update(argv=argv, env=kw["env"], path=path, mode=oct(os.stat(path).st_mode & 0o777), content=open(path).read())
        return subprocess.CompletedProcess(argv, 0, "", "")
    monkeypatch.setattr(github.subprocess, "run", fake_run)
    r = github.push(tmp_path, TOKEN)
    assert r["ok"] is True
    assert seen["argv"][:3] == ["git", "-C", str(tmp_path)] and seen["argv"][-1] == "push" and "credential.helper=" in seen["argv"]
    assert "x-access-token" in " ".join(seen["argv"]) and not any(TOKEN in a for a in seen["argv"])
    assert seen["mode"] == "0o600" and seen["content"].strip() == TOKEN and seen["path"].startswith(str(runtime))
    assert TOKEN not in "".join(seen["env"].values()) and "LIFEBOAT_GH_TOKEN" not in seen["env"]
    assert seen["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert not os.path.exists(seen["path"])  # gone once the push is over


def test_the_token_file_is_removed_when_the_push_fails_or_crashes(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    paths = []

    def failing(argv, **kw):
        paths.append(helper_path(argv))
        return subprocess.CompletedProcess(argv, 1, "", "denied")
    monkeypatch.setattr(github.subprocess, "run", failing)
    assert github.push(tmp_path, TOKEN)["ok"] is False
    assert not os.path.exists(paths[-1])

    def crashing(argv, **kw):
        paths.append(helper_path(argv))
        raise subprocess.TimeoutExpired(argv, 1)
    monkeypatch.setattr(github.subprocess, "run", crashing)
    assert github.push(tmp_path, TOKEN)["ok"] is False
    assert not os.path.exists(paths[-1])


def test_without_a_runtime_dir_the_file_lives_in_a_private_temp_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    modes = []

    def fake_run(argv, **kw):
        path = helper_path(argv)
        modes.append((oct(os.stat(path).st_mode & 0o777), oct(os.stat(os.path.dirname(path)).st_mode & 0o777)))
        return subprocess.CompletedProcess(argv, 0, "", "")
    monkeypatch.setattr(github.subprocess, "run", fake_run)
    github.push(tmp_path, TOKEN)
    assert modes == [("0o600", "0o700")] and list(tmp_path.iterdir()) == []


def test_a_pre_push_hook_does_not_see_the_token(tmp_path, monkeypatch):
    """The real thing: git runs a hook during the push, and the hook's environment must not hold the token."""
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    bare = tmp_path / "r.git"
    work = tmp_path / "w"
    for cmd in (["git", "init", "-q", "--bare", "-b", "main", str(bare)], ["git", "init", "-q", "-b", "main", str(work)]):
        subprocess.run(cmd, check=True)
    (work / "f").write_text("x")
    for args in (["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "c"], ["remote", "add", "origin", str(bare)]):
        subprocess.run(["git", "-C", str(work), *args], check=True)
    dump = tmp_path / "hook-env.txt"
    hook = work / ".git" / "hooks" / "pre-push"
    hook.write_text(f"#!/bin/sh\nenv > {dump}\nexit 0\n")
    hook.chmod(0o755)
    assert github.push(work, TOKEN, set_upstream="main")["ok"] is True
    assert dump.exists() and TOKEN not in dump.read_text() and "LIFEBOAT_GH_TOKEN" not in dump.read_text()
    assert not [p for p in tmp_path.iterdir() if p.name.startswith("lifeboat")]


def test_push_failure_reports_error_without_token(tmp_path, monkeypatch):
    monkeypatch.setattr(github.subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(argv, 1, "", f"denied for {TOKEN}"))
    r = github.push(tmp_path, TOKEN)
    assert r["ok"] is False and TOKEN not in r["error"]


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
    (fakebin / "git").write_text(f"#!/bin/sh\necho $$ > {gitpid}\ntouch {started}\nexec sleep 60\n")
    (fakebin / "git").chmod(0o755)
    code = "import sys; from lifeboat import github; github.push(sys.argv[1], 'tok-secret-123', timeout=120)"
    env = dict(os.environ, XDG_RUNTIME_DIR=str(runtime), PATH=f"{fakebin}{os.pathsep}{os.environ['PATH']}",
               PYTHONPATH=str(Path(github.__file__).parent.parent))
    child = subprocess.Popen([sys.executable, "-c", code, str(tmp_path)], env=env)
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


def test_the_signal_handlers_are_restored_after_a_push(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    before = (signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGHUP))
    monkeypatch.setattr(github.subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(argv, 0, "", ""))
    github.push(tmp_path, TOKEN)
    assert (signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGHUP)) == before
