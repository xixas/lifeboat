import configparser
import gc
import json
import os
import re
import shutil
import signal
import socket
import stat
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

from lifeboat import config, engine, setup

FIX = Path(__file__).parent / "fixtures"
STDERR = (FIX / "rclone-authorize-drive.stderr").read_text()
STDOUT = (FIX / "rclone-authorize-drive.stdout").read_text()


@pytest.fixture(autouse=True)
def fresh_client_values():
    """client_id()/client_secret() cache what `rclone reveal` printed: fill the cache with the real rclone before any test
    puts a fake rclone on PATH, so the fake never answers `reveal`."""
    setup.client_id.cache_clear()
    setup.client_secret.cache_clear()
    setup.client_id(), setup.client_secret()
    yield
    setup.client_id.cache_clear()
    setup.client_secret.cache_clear()
    setup.rclone_supports_template.cache_clear()


def test_new_passphrase_is_six_groups_of_five_unambiguous_chars():
    p = setup.new_passphrase()
    groups = p.split("-")
    assert len(groups) == 6 and all(len(g) == 5 for g in groups)
    assert not set("".join(groups)) & set("0O1lIo")
    assert re.fullmatch(r"[A-Za-z2-9-]+", p)
    assert setup.new_passphrase() != p


def test_parse_auth_url_reads_the_real_fixture():
    assert setup.parse_auth_url(STDERR) == "http://127.0.0.1:53682/auth?state=STATE"


def test_parse_auth_url_handles_the_please_go_to_wording():
    text = "NOTICE: Please go to the following link: http://127.0.0.1:53682/auth?state=abc_-1\nNOTICE: Log in"
    assert setup.parse_auth_url(text) == "http://127.0.0.1:53682/auth?state=abc_-1"


def test_parse_auth_url_none_when_absent():
    assert setup.parse_auth_url("NOTICE: Waiting for code...\n") is None


def test_parse_token_reads_the_real_fixture():
    tok = setup.parse_token(STDOUT)
    assert tok is not None and json.loads(tok)["refresh_token"] == "REDACTED"


def test_parse_token_none_while_incomplete_or_without_refresh_token():
    assert setup.parse_token("Paste the following into your remote machine --->\n{\"access_token\":\"x\"") is None
    assert setup.parse_token("--->\n{\"access_token\":\"x\"}\n<---End paste") is None
    assert setup.parse_token("--->\nnot json\n<---End paste") is None
    assert setup.parse_token("") is None


# ---------------------------------------------------------------- SignIn with a fake rclone
@pytest.fixture
def fake_rclone(tmp_path, monkeypatch):
    """A fake `rclone` first on PATH. mode: ok (print fixtures), hang (print the link, then wait), busy (port error)."""
    bindir = tmp_path / "fakebin"
    bindir.mkdir()

    def make(mode: str, template: bool = False):
        script = bindir / "rclone"
        script.write_text(f"""#!/bin/sh
[ "$2" = "--help" ] && {{ {'echo "      --template string   path to a Go template"; ' if template else ''}exit 0; }}
echo $$ > "{tmp_path}/fake.pid"
echo "$RCLONE_DRIVE_SCOPE|$RCLONE_DRIVE_CLIENT_ID|$*" > "{tmp_path}/fake.args"
case {mode} in
ok) cat "{FIX}/rclone-authorize-drive.stderr" >&2; sleep 0.3; cat "{FIX}/rclone-authorize-drive.stdout" ;;
hang) cat "{FIX}/rclone-authorize-drive.stderr" >&2; exec sleep 30 ;;
busy) echo "Fatal error: config failed to refresh token: failed to start auth webserver: listen tcp 127.0.0.1:53682: bind: address already in use" >&2; exit 2 ;;
esac
""")
        script.chmod(0o755)
        monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
        return tmp_path

    return make


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def drain(si, seconds=10):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        tok = si.poll()
        if tok is not None:
            return tok
        time.sleep(0.05)
    raise AssertionError("sign-in did not finish")


def test_signin_success_exposes_url_then_token(fake_rclone):
    tmp = fake_rclone("ok")
    si = setup.SignIn()
    si.start()
    try:
        tok = drain(si)
    finally:
        si.cancel()
    assert json.loads(tok)["refresh_token"] == "REDACTED"
    assert si.auth_url == "http://127.0.0.1:53682/auth?state=STATE"
    scope, client_id, args = (tmp / "fake.args").read_text().strip().split("|", 2)
    assert scope == "drive.file" and client_id == setup.client_id()
    assert "authorize drive --auth-no-open-browser --config /dev/null" in args
    assert setup.client_secret() not in args  # never in argv


def test_signin_passes_the_branded_page_when_rclone_supports_templates(fake_rclone):
    tmp = fake_rclone("ok", template=True)
    setup.rclone_supports_template.cache_clear()
    si = setup.SignIn()
    si.start()
    drain(si)
    args = (tmp / "fake.args").read_text().split("|", 2)[2].split()
    page = Path(args[args.index("--template") + 1])
    assert page == setup.SIGNIN_PAGE and "Lifeboat" in page.read_text()


def test_signin_omits_template_for_an_older_rclone(fake_rclone):
    tmp = fake_rclone("ok")
    setup.rclone_supports_template.cache_clear()
    si = setup.SignIn()
    si.start()
    drain(si)
    assert "--template" not in (tmp / "fake.args").read_text()


def _rendered_by_rclone(query: str) -> str:
    """Run the real `rclone authorize` with our page, hit its local callback and return the HTML it served."""
    proc = subprocess.Popen(["rclone", "authorize", "drive", "--auth-no-open-browser", "--config", os.devnull,
                             "--template", str(setup.SIGNIN_PAGE)], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, start_new_session=True)
    try:
        url, buf = None, ""
        for line in proc.stderr:
            buf += line
            url = setup.parse_auth_url(buf)
            if url:
                break
        assert url, buf
        state = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["state"][0]
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:53682/?state={state}&{query}", timeout=10) as r:
                return r.read().decode()
        except urllib.error.HTTPError as e:  # the error page is served with a 400
            return e.read().decode()
    finally:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()


needs_template = pytest.mark.skipif(not shutil.which("rclone") or not setup.rclone_supports_template(),
                                    reason="rclone lacks authorize --template")


@needs_template
def test_page_renders_the_success_state_through_real_rclone():
    html = _rendered_by_rclone("code=abc")
    assert "Lifeboat" in html and "signed in" in html and "Sign-in didn" not in html


@needs_template
def test_page_renders_the_error_state_through_real_rclone():
    html = _rendered_by_rclone("error=access_denied")
    assert "Sign-in didn" in html and "access_denied" in html and "signed in" not in html


def test_signin_times_out_and_kills_rclone(fake_rclone):
    tmp = fake_rclone("hang")
    si = setup.SignIn(timeout=0.5)
    si.start()
    with pytest.raises(setup.SignInError, match="timed out"):
        drain(si)
    pid = int((tmp / "fake.pid").read_text())
    assert not pid_alive(pid)


def test_signin_cancel_terminates_the_process(fake_rclone):
    tmp = fake_rclone("hang")
    si = setup.SignIn()
    si.start()
    time.sleep(0.3)
    si.cancel()
    assert not pid_alive(int((tmp / "fake.pid").read_text()))
    si.cancel()  # idempotent
    assert si.poll() is None


def test_signin_port_busy_raises_a_friendly_error(fake_rclone):
    fake_rclone("busy")
    si = setup.SignIn()
    si.start()
    with pytest.raises(setup.SignInError, match="Another sign-in is already running"):
        drain(si)


def test_signin_without_rclone_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(setup.SignInError, match="rclone"):
        setup.SignIn().start()


# ---------------------------------------------------------------- SignIn with the real rclone (no network)
def test_real_rclone_port_busy():
    with socket.socket() as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", 53682))
        except OSError:
            pytest.skip("port 53682 is in use on this machine")
        s.listen()
        si = setup.SignIn()
        si.start()
        with pytest.raises(setup.SignInError, match="Another sign-in is already running"):
            drain(si)


def test_real_rclone_asks_google_for_drive_file_only():
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None

    si = setup.SignIn()
    try:
        si.start()
        end = time.monotonic() + 10
        while si.auth_url is None and time.monotonic() < end:
            si.poll()
            time.sleep(0.05)
        assert si.auth_url, "rclone printed no link"
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.build_opener(NoRedirect).open(si.auth_url)
        q = urllib.parse.parse_qs(urllib.parse.urlparse(e.value.headers["Location"]).query)
        assert q["scope"] == ["https://www.googleapis.com/auth/drive.file"]
        assert q["client_id"] == [setup.client_id()]
    finally:
        si.cancel()


# ---------------------------------------------------------------- remotes
TOKEN = json.dumps({"access_token": "a", "refresh_token": "r", "expiry": "2026-01-01T00:00:00Z"})


def listremotes(conf: Path) -> list[str]:
    out = subprocess.run(["rclone", "listremotes", "--config", str(conf)], capture_output=True, text=True, check=True).stdout.split()
    return [r for r in out if r not in ("localdrv:", "vault:")]  # those two come from the conftest environment


def read_conf(conf: Path) -> configparser.ConfigParser:
    cp = configparser.ConfigParser(interpolation=None, delimiters=("=",))
    cp.read(conf)
    return cp


def test_make_remotes_writes_a_private_conf_with_both_remotes(tmp_path):
    conf = tmp_path / "rclone.conf"
    setup.make_remotes(conf, TOKEN, "Secret-pass-phrase")
    assert stat.S_IMODE(conf.stat().st_mode) == 0o600
    assert listremotes(conf) == ["lifeboat:", "lifeboat-secure:"]
    cp = read_conf(conf)
    drive = cp["lifeboat"]
    assert (drive["type"], drive["scope"], drive["client_id"], drive["team_drive"]) == ("drive", "drive.file", setup.client_id(), "")
    assert drive["client_secret"] == setup.client_secret() and json.loads(drive["token"])["refresh_token"] == "r"
    crypt = cp["lifeboat-secure"]
    assert crypt["type"] == "crypt" and crypt["remote"] == "lifeboat:Lifeboat/secure"
    assert "Secret-pass-phrase" not in conf.read_text()  # stored obscured, as rclone does


def test_make_remotes_honours_the_drive_root_env(tmp_path, monkeypatch):
    monkeypatch.setenv("LIFEBOAT_DRIVE_ROOT", "Lifeboat-test")
    setup.make_remotes(tmp_path / "rclone.conf", TOKEN, "pw")
    assert read_conf(tmp_path / "rclone.conf")["lifeboat-secure"]["remote"] == "lifeboat:Lifeboat-test/secure"


def test_make_remotes_keeps_other_remotes_and_replaces_its_own(tmp_path):
    conf = tmp_path / "rclone.conf"
    conf.write_text("[other]\ntype = local\n")
    setup.make_remotes(conf, TOKEN, "one")
    setup.make_remotes(conf, TOKEN, "two")
    assert sorted(listremotes(conf)) == ["lifeboat-secure:", "lifeboat:", "other:"]


def test_crypt_remote_round_trips_and_hides_names(tmp_path):
    conf = tmp_path / "rclone.conf"
    root = tmp_path / "drive"
    conf.write_text("[lifeboat]\ntype = local\n")
    setup.write_crypt_remote(conf, "right-pass", f"lifeboat:{root}/secure")
    subprocess.run(["rclone", "rcat", "lifeboat-secure:laptop/id_rsa", "--config", str(conf)], input="KEY", text=True, check=True)
    names = [p.name for p in (root / "secure").rglob("*")]
    assert names and "id_rsa" not in names and "laptop" not in names
    cat = subprocess.run(["rclone", "cat", "lifeboat-secure:laptop/id_rsa", "--config", str(conf)], capture_output=True, text=True)
    assert cat.stdout == "KEY"


# ---------------------------------------------------------------- discovery and passphrase check
@pytest.fixture
def drive(tmp_path, monkeypatch):
    """A 'Drive' that is a local folder: two devices with manifests, a legacy folder without one, one passphrase."""
    root = tmp_path / "drive" / "Lifeboat"
    monkeypatch.setenv("LIFEBOAT_DRIVE_ROOT", str(root))
    conf = tmp_path / "rclone.conf"
    conf.write_text("[lifeboat]\ntype = local\n")
    setup.write_crypt_remote(conf, "right-pass")
    for dev in ("laptop-a", "laptop-b"):
        (root / dev).mkdir(parents=True)
        (root / dev / "manifest.json").write_text(json.dumps({"format": 1, "device": dev, "jobs": []}))
        subprocess.run(["rclone", "rcat", f"lifeboat-secure:{dev}/.lifeboat-probe", "--config", str(conf)],
                       input="lifeboat-probe 1\n", text=True, check=True)
    (root / "Documents").mkdir()  # the owner's legacy layout: no manifest
    (root / "Documents" / "a.txt").write_text("x")
    (root / "broken").mkdir()
    (root / "broken" / "manifest.json").write_text("{not json")
    return conf


def test_find_backups_returns_each_devices_manifest(drive):
    found = setup.find_backups(drive)
    assert sorted(m["device"] for m in found) == ["laptop-a", "laptop-b"]


def test_find_backups_is_empty_without_a_lifeboat_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("LIFEBOAT_DRIVE_ROOT", str(tmp_path / "nothing-here"))
    conf = tmp_path / "rclone.conf"
    conf.write_text("[lifeboat]\ntype = local\n")
    assert setup.find_backups(conf) == []


def test_check_passphrase_accepts_the_right_one_and_rejects_a_wrong_one(drive):
    assert setup.check_passphrase(drive, "right-pass", "laptop-a") is True
    assert setup.check_passphrase(drive, "wrong-pass", "laptop-a") is False
    assert setup.check_passphrase(drive, "right-pass", "laptop-b") is True


def test_check_passphrase_leaves_no_temp_config_and_does_not_touch_the_real_one(drive, tmp_path):
    before = drive.read_text()
    tmpdir = tmp_path / "tmp"
    tmpdir.mkdir()
    old = tempfile.tempdir
    tempfile.tempdir = str(tmpdir)
    try:
        setup.check_passphrase(drive, "wrong-pass", "laptop-a")
    finally:
        tempfile.tempdir = old
    assert list(tmpdir.iterdir()) == []
    assert drive.read_text() == before


# ---------------------------------------------------------------- folder suggestions
def test_folder_size_sums_files_and_skips_symlinks(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "one").write_bytes(b"x" * 100)
    (tmp_path / "two").write_bytes(b"y" * 50)
    (tmp_path / "link").symlink_to(tmp_path / "a")
    (tmp_path / "flink").symlink_to(tmp_path / "two")
    assert setup.folder_size(tmp_path) == (150, True)


def test_folder_size_stops_at_the_time_limit(tmp_path):
    (tmp_path / "f").write_bytes(b"x")
    size, complete = setup.folder_size(tmp_path, limit_s=-1)
    assert complete is False and size == 0


def test_folder_size_of_a_missing_folder_is_zero_and_complete(tmp_path):
    assert setup.folder_size(tmp_path / "nope") == (0, True)


def test_with_size_turns_large_or_unfinished_folders_off():
    f = {"key": "videos", "default_on": True}
    assert setup.with_size(f, 10, True) == {"key": "videos", "default_on": True, "size": 10, "size_complete": True}
    assert setup.with_size(f, 6 * 1024**3, True)["default_on"] is False
    assert setup.with_size(f, 10, False)["default_on"] is False
    assert f == {"key": "videos", "default_on": True}  # not mutated


def test_with_size_hints_that_an_empty_folder_stays_on():
    f = {"key": "pictures", "default_on": True}
    e = setup.with_size(f, 0, True)
    assert e["default_on"] is True and e["hint"] == "Empty — backed up once you add files"
    assert "hint" not in setup.with_size(f, 0, False)


def test_parse_user_dirs_reads_home_relative_and_absolute_paths():
    text = '# comment\nXDG_DOCUMENTS_DIR="$HOME/Docs"\nXDG_MUSIC_DIR="/data/music"\nXDG_VIDEOS_DIR="$HOME/"\nbad line\n'
    assert setup.parse_user_dirs(text, "/home/u") == {"DOCUMENTS": "/home/u/Docs", "MUSIC": "/data/music", "VIDEOS": "/home/u"}


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setattr(setup, "_glib_user_dir", lambda name: None)  # force the user-dirs.dirs / defaults path
    return home


def test_suggest_folders_falls_back_to_defaults_and_lists_only_existing(fake_home):
    for d in ("Documents", "Pictures", ".ssh"):
        (fake_home / d).mkdir()
    got = {f["key"]: f for f in setup.suggest_folders()}
    assert list(got) == ["documents", "pictures", "ssh"]
    assert got["documents"] == {"key": "documents", "label": "Documents", "path": str(fake_home / "Documents"),
                                "default_on": True, "secret": False}
    assert got["ssh"]["secret"] is True and got["ssh"]["path"] == str(fake_home / ".ssh") and got["ssh"]["default_on"] is True


def test_suggest_folders_uses_user_dirs_dirs(fake_home):
    (fake_home / ".config").mkdir()
    (fake_home / ".config" / "user-dirs.dirs").write_text('XDG_DOCUMENTS_DIR="$HOME/Papers"\n')
    (fake_home / "Papers").mkdir()
    (fake_home / "Documents").mkdir()  # the default is ignored once the file names another folder
    assert [f["path"] for f in setup.suggest_folders()] == [str(fake_home / "Papers")]


def test_suggest_folders_prefers_glib_and_skips_the_home_folder(fake_home, monkeypatch):
    (fake_home / "Docs").mkdir()
    monkeypatch.setattr(setup, "_glib_user_dir", lambda name: str(fake_home / "Docs") if name == "DOCUMENTS" else str(fake_home))
    assert [f["key"] for f in setup.suggest_folders()] == ["documents"]


def test_default_device_name_is_a_valid_sanitized_hostname(monkeypatch):
    for host, want in [("my-laptop", "my-laptop"), ("Vikas's Laptop.local", "Vikas-s-Laptop"), ("", "laptop"), ("-_-", "laptop")]:
        monkeypatch.setattr(socket, "gethostname", lambda h=host: h)
        assert setup.default_device_name() == want
        assert config.NAME_RE.match(want)


# ---------------------------------------------------------------- config writer
def folder(key, label, path, secret=False):
    return {"key": key, "label": label, "path": path, "default_on": True, "secret": secret}


@pytest.fixture
def wiz(fake_home):
    return fake_home / ".config" / "lifeboat" / "config.toml"


def test_write_wizard_config_writes_a_loadable_config_with_device_and_jobs(wiz, fake_home):
    choices = [folder("documents", "Documents", str(fake_home / "Documents")),
               folder("ssh", "SSH keys", str(fake_home / ".ssh"), secret=True)]
    setup.write_wizard_config(wiz, "laptop-a", choices, "13:30")
    cfg = config.load_config(wiz)
    assert (cfg.device_name, cfg.daily_at) == ("laptop-a", "13:30")
    root = setup.drive_root()
    assert cfg.manifest == f"lifeboat:{root}/laptop-a/manifest.json"
    assert cfg.probe == "lifeboat-secure:laptop-a/.lifeboat-probe"
    assert cfg.rclone_config == str(wiz.parent / "rclone.conf")
    docs, ssh = cfg.jobs
    assert (docs.name, docs.source, docs.dest, docs.old, docs.encrypted) == (
        "documents", "~/Documents", f"lifeboat:{root}/laptop-a/Documents", f"lifeboat:{root}/laptop-a/_old", False)
    assert (ssh.name, ssh.source, ssh.dest, ssh.old, ssh.encrypted) == (
        "ssh-keys", "~/.ssh", "lifeboat-secure:laptop-a/ssh-keys", "lifeboat-secure:laptop-a/_old", True)


def test_every_job_excludes_the_recovery_kit_and_junk(wiz, fake_home):
    setup.write_wizard_config(wiz, "d", [folder("p", "Pictures", str(fake_home / "Pictures"))], "13:00")
    ex = config.load_config(wiz).jobs[0].exclude
    for pat in ("Lifeboat-recovery-kit*.pdf", ".cache/**", "node_modules/**", ".venv/**", "__pycache__/**"):
        assert pat in ex


def test_write_wizard_config_refuses_to_overwrite(wiz, fake_home):
    wiz.parent.mkdir(parents=True)
    wiz.write_text("mine")
    with pytest.raises(config.ConfigError, match="already exists"):
        setup.write_wizard_config(wiz, "d", [folder("p", "Pictures", "/x")], "13:00")
    assert wiz.read_text() == "mine"


@pytest.mark.parametrize("device,choices,daily", [("bad name", [folder("p", "P", "/x")], "13:00"),
                                                  ("d", [folder("p", "P", "/x")], "25:00"),
                                                  ("d", [], "13:00"),
                                                  ("d", [folder("p", "P", "/x"), folder("p2", "P", "/y")], "13:00")])
def test_write_wizard_config_validates_before_writing(wiz, device, choices, daily):
    with pytest.raises(config.ConfigError):
        setup.write_wizard_config(wiz, device, choices, daily)
    assert not wiz.exists()


def test_user_added_folder_gets_a_safe_name(wiz, fake_home):
    setup.write_wizard_config(wiz, "d", [folder("x", "My Stuff (old)", str(fake_home / "My Stuff"))], "13:00")
    job = config.load_config(wiz).jobs[0]
    assert job.name == "my-stuff-old" and job.dest.endswith("/d/My-Stuff-old")


# ---------------------------------------------------------------- restore
def restore_cfg(fake_home, tmp_path):
    jobs = [{"name": name, "source": f"~/{rel}", "dest": f"localdrv:{tmp_path}/remote/{name}", "old": f"localdrv:{tmp_path}/remote/_old"}
            for name, rel in (("documents", "Documents"), ("ssh-keys", ".ssh"), ("gnupg", ".gnupg"))]
    return config.parse({"jobs": jobs}, tmp_path / "config.toml")


def test_restore_jobs_restores_in_place_keeps_newer_and_fixes_key_permissions(fake_home, tmp_path):
    cfg = restore_cfg(fake_home, tmp_path)
    for rel, name in (("Documents", "a.txt"), (".ssh", "id_ed25519"), (".gnupg/private-keys-v1.d", "k.key")):
        (fake_home / rel).mkdir(parents=True, exist_ok=True)
        (fake_home / rel / name).write_text("backed up")
    assert engine.run_backup(cfg)["ok"]
    shutil.rmtree(fake_home / ".ssh")
    shutil.rmtree(fake_home / ".gnupg")
    (fake_home / "Documents" / "a.txt").write_text("edited since")
    os.utime(fake_home / "Documents" / "a.txt", (time.time() + 3600,) * 2)
    lines = []
    assert setup.restore_jobs(cfg, ["documents", "ssh-keys", "gnupg"], lines.append) == {"documents": 0, "ssh-keys": 0, "gnupg": 0}
    assert (fake_home / "Documents" / "a.txt").read_text() == "edited since"
    key = fake_home / ".ssh" / "id_ed25519"
    assert key.read_text() == "backed up"
    assert stat.S_IMODE(key.stat().st_mode) == 0o600 and stat.S_IMODE(key.parent.stat().st_mode) == 0o700
    gpg = fake_home / ".gnupg" / "private-keys-v1.d"
    assert stat.S_IMODE(gpg.stat().st_mode) == 0o700 and stat.S_IMODE((gpg / "k.key").stat().st_mode) == 0o600
    assert stat.S_IMODE((fake_home / ".gnupg").stat().st_mode) == 0o700
    assert lines  # progress was emitted


def test_restore_jobs_leaves_other_folder_permissions_alone_and_and_rejects_unknown_names(fake_home, tmp_path):
    cfg = restore_cfg(fake_home, tmp_path)
    (fake_home / "Documents").mkdir()
    (fake_home / "Documents" / "a.txt").write_text("x")
    assert engine.run_backup(cfg, settings=False)["jobs"][0]["rc"] == 0
    (fake_home / "Documents" / "a.txt").unlink()
    old = os.umask(0o022)
    try:
        assert setup.restore_jobs(cfg, ["documents"], lambda _l: None) == {"documents": 0}
    finally:
        os.umask(old)
    assert stat.S_IMODE((fake_home / "Documents" / "a.txt").stat().st_mode) == 0o644
    assert stat.S_IMODE((fake_home / "Documents").stat().st_mode) != 0o700
    with pytest.raises(config.ConfigError, match="no job named"):
        setup.restore_jobs(cfg, ["nope"], lambda _l: None)


# ---------------------------------------------------------------- recovery kit PDF
def make_kit(tmp_path, email="me@example.com", passphrase="Abcde-fghjk-mnpqr-stuvw-xyz23-45678"):
    out = tmp_path / "Lifeboat-recovery-kit.pdf"
    setup.kit_pdf(out, email, "laptop-a", passphrase, "2026-10-23")
    return out


def test_kit_pdf_is_a_structurally_valid_pdf(tmp_path):
    data = make_kit(tmp_path).read_bytes()
    assert data.startswith(b"%PDF-1.4") and data.rstrip().endswith(b"%%EOF")
    assert len(re.findall(rb"/Type /Page(?!s)", data)) == 1  # one page
    data.decode("ascii")
    start = int(re.search(rb"startxref\n(\d+)\n%%EOF", data).group(1))
    assert data[start:start + 4] == b"xref"
    head, *rows = data[start:].split(b"\n")[1:]
    first, count = map(int, head.split())
    assert first == 0 and count == len(re.findall(rb"\n\d+ 0 obj\n", data)) + 1
    for n, row in enumerate(rows[:count]):
        assert len(row) + 1 == 20
        offset, _gen, kind = row.split()
        if n == 0:
            assert kind == b"f"
        else:
            assert data[int(offset):].startswith(f"{n} 0 obj\n".encode())
    trailer = data[start:].split(b"trailer")[1]
    assert f"/Size {count}".encode() in trailer and b"/Root 1 0 R" in trailer


def test_kit_pdf_stream_length_matches(tmp_path):
    data = make_kit(tmp_path).read_bytes()
    length = int(re.search(rb"/Length (\d+)", data).group(1))
    body = data.split(b"stream\n", 1)[1].split(b"\nendstream", 1)[0]
    assert len(body) == length


def test_kit_pdf_carries_every_fact_and_is_private(tmp_path):
    out = make_kit(tmp_path)
    assert stat.S_IMODE(out.stat().st_mode) == 0o600
    text = out.read_text()
    for want in ("Abcde-fghjk-mnpqr-stuvw-xyz23-45678", "laptop-a", "2026-10-23", "me@example.com", setup.client_id(),
                 "Restore from a backup", "drive.google.com", "encryption", "standard"):
        assert want in text


def test_kit_pdf_without_account_and_with_awkward_characters(tmp_path):
    out = tmp_path / "k.pdf"
    setup.kit_pdf(out, None, "d(1)\\x", "pa(ss)\\word-é", "2026-10-23")
    text = out.read_text()
    assert "pa\\(ss\\)\\\\word-?" in text and "d\\(1\\)\\\\x" in text
    assert "Account" in text and "me@" not in text


@pytest.mark.skipif(not shutil.which("pdftotext"), reason="pdftotext not installed")
def test_kit_pdf_text_extracts_with_pdftotext(tmp_path):
    out = make_kit(tmp_path)
    text = subprocess.run(["pdftotext", "-layout", str(out), "-"], capture_output=True, text=True, check=True).stdout
    for want in ("Abcde-fghjk-mnpqr-stuvw-xyz23-45678", "laptop-a", "me@example.com", setup.client_id(), "Restore from a backup"):
        assert want in text


def _current_umask() -> int:
    old = os.umask(0)
    os.umask(old)
    return old


def test_restore_jobs_fixes_permissions_even_when_rclone_fails_and_uses_a_private_umask(fake_home, tmp_path, monkeypatch):
    cfg = restore_cfg(fake_home, tmp_path)
    seen = {}

    def partial(cfg_, job, to, emit=None, keep_newer=False):
        seen[job.name] = _current_umask()
        d = Path(os.path.expanduser(to))
        d.mkdir(parents=True, exist_ok=True)
        (d / "key").write_text("k")
        os.chmod(d / "key", 0o644)  # as if the copy had left it loose
        return 1  # rclone failed part way

    monkeypatch.setattr(setup.engine, "restore", partial)
    before = _current_umask()
    assert setup.restore_jobs(cfg, ["documents", "ssh-keys"], lambda _l: None) == {"documents": 1, "ssh-keys": 1}
    assert seen["ssh-keys"] == 0o077 and seen["documents"] == before
    assert _current_umask() == before
    key = fake_home / ".ssh" / "key"
    assert stat.S_IMODE(key.stat().st_mode) == 0o600 and stat.S_IMODE(key.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE((fake_home / "Documents" / "key").stat().st_mode) == 0o644


def test_restore_jobs_restores_the_umask_when_restore_raises(fake_home, tmp_path, monkeypatch):
    cfg = restore_cfg(fake_home, tmp_path)

    def boom(*a, **k):
        raise RuntimeError("x")

    monkeypatch.setattr(setup.engine, "restore", boom)
    before = _current_umask()
    with pytest.raises(RuntimeError):
        setup.restore_jobs(cfg, ["ssh-keys"], lambda _l: None)
    assert _current_umask() == before


def test_check_passphrase_raises_when_the_backup_cannot_be_reached(drive, tmp_path, monkeypatch):
    no_drive = tmp_path / "empty.conf"
    no_drive.write_text("")  # the lifeboat remote the crypt remote sits on does not exist: rclone fails, not "wrong key"
    with pytest.raises(setup.SetupError, match="Couldn't reach your backup"):
        setup.check_passphrase(no_drive, "right-pass", "laptop-a")
    monkeypatch.setenv("PATH", str(tmp_path / "no-bin"))
    with pytest.raises(setup.SetupError, match="Couldn't reach your backup"):
        setup.check_passphrase(drive, "right-pass", "laptop-a")


def test_check_passphrase_raises_on_timeout(drive, monkeypatch):
    monkeypatch.setattr(setup.engine, "rclone", lambda *a, **k: (127, ""))  # engine's code for timeout / missing rclone
    with pytest.raises(setup.SetupError, match="Couldn't reach your backup"):
        setup.check_passphrase(drive, "right-pass", "laptop-a")


def test_check_passphrase_unknown_device_is_wrong_not_unreachable(drive):
    assert setup.check_passphrase(drive, "right-pass", "no-such-laptop") is False


def test_unique_device_name_adds_a_counter_on_collision():
    assert setup.unique_device_name("laptop", []) == "laptop"
    assert setup.unique_device_name("laptop", ["other"]) == "laptop"
    assert setup.unique_device_name("laptop", ["laptop"]) == "laptop-2"
    assert setup.unique_device_name("laptop", ["laptop", "laptop-2"]) == "laptop-3"


def test_write_wizard_config_refuses_a_device_name_already_in_the_account(wiz, fake_home):
    with pytest.raises(setup.SetupError, match="already used"):
        setup.write_wizard_config(wiz, "laptop-a", [folder("p", "P", "/x")], "13:00", existing_devices=["laptop-a", "b"])
    assert not wiz.exists()
    setup.write_wizard_config(wiz, "laptop-c", [folder("p", "P", "/x")], "13:00", existing_devices=["laptop-a"])
    assert wiz.exists()


def test_write_section_is_atomic_private_and_leaves_no_temp_files(tmp_path, monkeypatch):
    conf = tmp_path / "rclone.conf"
    conf.write_text("[keep]\ntype = local\n")
    conf.chmod(0o644)
    setup.write_drive_remote(conf, TOKEN)
    assert stat.S_IMODE(conf.stat().st_mode) == 0o600
    assert "[keep]" in conf.read_text() and sorted(p.name for p in tmp_path.iterdir()) == ["rclone.conf"]
    before = conf.read_text()

    def fail(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError):
        setup.write_drive_remote(conf, TOKEN.replace("refresh_token", "x"))
    assert conf.read_text() == before and sorted(p.name for p in tmp_path.iterdir()) == ["rclone.conf"]


def test_abandoned_signin_does_not_orphan_rclone(fake_rclone):
    tmp = fake_rclone("hang")
    si = setup.SignIn()
    si.start()
    time.sleep(0.3)
    pid = int((tmp / "fake.pid").read_text())
    assert pid_alive(pid)
    del si
    gc.collect()
    assert not pid_alive(pid)


# ---------------------------------------------------------------- config from a manifest (restore, keep backing up)
def manifest_for(device="old-laptop", root=None):
    root = root or setup.drive_root()
    base, secure = f"lifeboat:{root}/{device}", f"lifeboat-secure:{device}"
    return {"format": 1, "device": device, "time": "2026-10-01T10:00:00+00:00", "ok": True, "jobs": [
        {"name": "documents", "source": "~/Documents", "dest": f"{base}/Documents", "old": f"{base}/_old", "encrypted": False},
        {"name": "ssh-keys", "source": "~/.ssh", "dest": f"{secure}/ssh-keys", "old": f"{secure}/_old", "encrypted": True}]}


def test_config_from_manifest_round_trips_device_and_jobs(wiz):
    m = manifest_for()
    setup.config_from_manifest(wiz, m, "09:00")
    cfg = config.load_config(wiz)
    assert (cfg.device_name, cfg.daily_at) == ("old-laptop", "09:00")
    assert cfg.manifest == f"lifeboat:{setup.drive_root()}/old-laptop/manifest.json"
    assert cfg.probe == "lifeboat-secure:old-laptop/.lifeboat-probe"
    assert cfg.rclone_config == str(wiz.parent / "rclone.conf")
    assert [(j.name, j.source, j.dest, j.old, j.encrypted) for j in cfg.jobs] == [
        (j["name"], j["source"], j["dest"], j["old"], j["encrypted"]) for j in m["jobs"]]
    assert all(j.source.startswith("~/") for j in cfg.jobs)  # never expanded to this machine's home
    assert "Lifeboat-recovery-kit*.pdf" in cfg.jobs[0].exclude


def test_config_from_manifest_refuses_an_existing_config(wiz):
    wiz.parent.mkdir(parents=True)
    wiz.write_text("mine")
    with pytest.raises(config.ConfigError, match="already exists"):
        setup.config_from_manifest(wiz, manifest_for(), "13:00")
    assert wiz.read_text() == "mine"


@pytest.mark.parametrize("edit", [lambda m: m.update(device="bad name"), lambda m: m.update(jobs=[]),
                                  lambda m: m["jobs"][0].pop("dest"), lambda m: m.pop("device")])
def test_config_from_manifest_validates_before_writing(wiz, edit):
    m = manifest_for()
    edit(m)
    with pytest.raises(config.ConfigError):
        setup.config_from_manifest(wiz, m, "13:00")
    assert not wiz.exists()


def test_restore_config_reads_from_the_manifests_own_locations(fake_home):
    conf = fake_home / "rclone.conf"
    cfg = setup.restore_config(manifest_for("old-laptop"), conf)
    assert cfg.rclone_config == str(conf) and cfg.device_name == "old-laptop"
    assert [j.name for j in cfg.jobs] == ["documents", "ssh-keys"]
    assert cfg.jobs[0].dest == f"lifeboat:{setup.drive_root()}/old-laptop/Documents"  # the OLD device's folder, whatever this laptop is called
    with pytest.raises(config.ConfigError):
        setup.restore_config({"device": "x"}, conf)


def test_config_from_manifest_keeps_only_the_named_jobs(wiz):
    setup.config_from_manifest(wiz, manifest_for(), "13:00", names=["ssh-keys"])
    cfg = config.load_config(wiz)
    assert [j.name for j in cfg.jobs] == ["ssh-keys"] and cfg.device_name == "old-laptop"


@pytest.mark.parametrize("names", [[], ["nope"]])
def test_config_from_manifest_refuses_a_selection_with_no_jobs(wiz, names):
    with pytest.raises(config.ConfigError):
        setup.config_from_manifest(wiz, manifest_for(), "13:00", names=names)
    assert not wiz.exists()


def test_retired_jobs_are_restorable_but_never_written_to_a_config(wiz, fake_home):
    m = manifest_for()
    m["retired"] = [{**m["jobs"][0], "name": "pictures", "source": "~/Pictures", "since": "2026-10-02T09:00:00+00:00"}]
    assert [j.name for j in setup.restore_config(m, fake_home / "r.conf").jobs] == ["documents", "ssh-keys", "pictures"]
    setup.config_from_manifest(wiz, m, "13:00")
    assert [j.name for j in config.load_config(wiz).jobs] == ["documents", "ssh-keys"]
