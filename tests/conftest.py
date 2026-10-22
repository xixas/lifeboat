import shutil
import subprocess
from datetime import datetime, timedelta

import pytest

from lifeboat import engine
from lifeboat.config import parse

if not shutil.which("rclone"):
    pytest.skip("rclone is required for the engine tests", allow_module_level=True)


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    """Isolated state/config dirs and rclone remotes backed by local folders (no network)."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
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
