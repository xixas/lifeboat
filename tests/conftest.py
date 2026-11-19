import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from lifeboat import engine, setup
from lifeboat.config import parse

if not shutil.which("rclone"):
    pytest.skip("rclone is required for the engine tests", allow_module_level=True)


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    """Isolated state/config dirs and rclone remotes backed by local folders (no network)."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))  # Lifeboat's bin folder (its pinned gitleaks) is never the real one
    monkeypatch.setenv("RCLONE_CONFIG", str(tmp_path / "no-rclone.conf"))
    monkeypatch.setenv("RCLONE_CONFIG_LOCALDRV_TYPE", "local")
    pw = subprocess.run(["rclone", "obscure", "test-password"], capture_output=True, text=True, check=True).stdout.strip()
    monkeypatch.setenv("RCLONE_CONFIG_VAULT_TYPE", "crypt")
    monkeypatch.setenv("RCLONE_CONFIG_VAULT_REMOTE", f"localdrv:{tmp_path}/crypt")
    monkeypatch.setenv("RCLONE_CONFIG_VAULT_PASSWORD", pw)
    return tmp_path


@pytest.fixture
def make_cfg(tmp_path):
    def make(keep_days=90, crypt=False, **extra):
        job = {"name": "docs", "source": str(tmp_path / "src"), "old": f"localdrv:{tmp_path}/old"}
        job["dest"] = "vault:docs" if crypt else f"localdrv:{tmp_path}/dest"
        if crypt:
            job.update(old="vault:_old", encrypted=True)
        (tmp_path / "src").mkdir(exist_ok=True)
        data = {"schedule": {"keep_days": keep_days}, "jobs": [job]}
        data.update(extra)
        return parse(data, tmp_path / "config" / "lifeboat" / "config.toml")
    return make


@pytest.fixture
def clock():
    """Distinct stamps for back-to-back runs."""
    base = datetime(2026, 1, 1, 12, 0, 0)
    return lambda n=0: base + timedelta(seconds=n)


def stamps(old_dir):
    return sorted(p.name for p in old_dir.iterdir() if engine.STAMP_RE.match(p.name)) if old_dir.exists() else []


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setattr(setup, "_glib_user_dir", lambda name: None)  # force the user-dirs.dirs / defaults path
    return home


FAKE_GITLEAKS = r"""#!/bin/sh
# Stand-in for the pinned gitleaks: logs each call and writes the canned report for `git` or `dir`.
self="$0"; sub="$1"; out=""; prev=""
for a in "$@"; do
  case "$a" in --report-path=*) out="${a#--report-path=}";; esac
  [ "$prev" = "--report-path" ] && out="$a"
  prev="$a"
done
{ echo "$sub|$PWD|$*"; if [ "$sub" = dir ]; then find . -type f | sort | sed 's/^/FILE /'; fi; } >> "$self.log"
rc=0; [ -f "$self.rc" ] && rc=$(cat "$self.rc")
[ -f "$self.$sub.json" ] && cp "$self.$sub.json" "$out" || { [ "$rc" = 0 ] && echo '[]' > "$out"; }
exit "$rc"
"""


@pytest.fixture
def fake_gitleaks(tmp_path_factory, monkeypatch):
    """Make `store_install.gitleaks_path()` return a fake gitleaks. Call it with git=[...] / dir=[...] report entries or rc=N; its .log has every call."""
    from lifeboat import store_install
    exe = tmp_path_factory.mktemp("fake-gitleaks") / "gitleaks"  # not the test's tmp_path: that is often the repo being scanned
    exe.write_text(FAKE_GITLEAKS)
    exe.chmod(0o755)
    monkeypatch.setattr(store_install, "gitleaks_path", lambda: exe)

    def configure(git=None, dir=None, rc=None):  # noqa: A002
        import json
        for name, val in (("git", git), ("dir", dir)):
            if val is not None:
                Path(f"{exe}.{name}.json").write_text(json.dumps(val))
        if rc is not None:
            Path(f"{exe}.rc").write_text(str(rc))
        return exe
    configure.log = lambda: Path(f"{exe}.log").read_text() if Path(f"{exe}.log").exists() else ""
    return configure
