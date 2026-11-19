"""Install pinned skillshare and gitleaks builds. The expected sha256 is built in: a checksum file fetched from the same release
proves nothing if the release itself is swapped. No GTK."""
from __future__ import annotations

import hashlib
import io
import os
import platform
import subprocess
import tarfile
import urllib.request
from pathlib import Path

SKILLSHARE_VERSION = "0.30.11"
CHECKSUMS = {  # sha256 of skillshare_<version>_linux_<arch>.tar.gz
    "amd64": "0c908145dd517925cd0c27e0abde7f69f64fd457ca3fef34db0ccb7bc2350eb0",
    "arm64": "b7a2f7b16691227510bcf4aab34ee7b671eb3bad70cd93a8060251f1f6dff2ed",
}
URL = "https://github.com/runkids/skillshare/releases/download/v{v}/skillshare_{v}_linux_{arch}.tar.gz"
MAX_BYTES = 100 * 1024 * 1024


GITLEAKS_VERSION = "8.30.1"  # released 2026-03-21
GITLEAKS_CHECKSUMS = {  # sha256 of gitleaks_<version>_linux_<x64|arm64>.tar.gz, from the release's gitleaks_8.30.1_checksums.txt
    "amd64": "551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb",
    "arm64": "e4a487ee7ccd7d3a7f7ec08657610aa3606637dab924210b3aee62570fb4b080",
}
GITLEAKS_URL = "https://github.com/gitleaks/gitleaks/releases/download/v{v}/gitleaks_{v}_linux_{arch}.tar.gz"
GITLEAKS_ARCH = {"amd64": "x64", "arm64": "arm64"}  # gitleaks names its amd64 build x64


class InstallError(RuntimeError):
    pass


def arch() -> str:
    return {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(platform.machine(), platform.machine())


def bin_dir() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "lifeboat" / "bin"


def skillshare_path() -> Path | None:
    p = bin_dir() / "skillshare"
    return p if p.is_file() else None


def gitleaks_path() -> Path | None:
    """Lifeboat's own pinned gitleaks, and only that one: never a gitleaks found on PATH, so the rules are predictable."""
    p = bin_dir() / "gitleaks"
    return p if p.is_file() else None


def _download(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as r:  # noqa: S310 - fixed https URL
        data = r.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise InstallError("download is too large")
    return data


def _install(name: str, url: str, expected: str, dest_dir: Path | str | None) -> Path:
    """Download `url`, verify it against the built-in sha256 `expected`, extract only the member `name` (mode 755)."""
    dest = Path(dest_dir) if dest_dir else bin_dir()
    dest.mkdir(parents=True, exist_ok=True)
    part = dest / f"{name}.part"
    try:
        blob = _download(url)
        if hashlib.sha256(blob).hexdigest() != expected:
            raise InstallError("checksum mismatch: refusing to install")
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as t:
            member = next((m for m in t.getmembers() if m.name == name and m.isfile()), None)
            f = t.extractfile(member) if member else None
            if f is None:
                raise InstallError(f"no {name} binary in the archive")
            part.write_bytes(f.read())
        part.chmod(0o755)
        part.replace(dest / name)
    finally:
        part.unlink(missing_ok=True)
    return dest / name


def install(dest_dir: Path | str | None = None) -> Path:
    """Download the pinned skillshare tarball, verify it against the built-in hash, extract only `skillshare`."""
    a = arch()
    if a not in CHECKSUMS:
        raise InstallError(f"unsupported architecture: {a}")
    return _install("skillshare", URL.format(v=SKILLSHARE_VERSION, arch=a), CHECKSUMS[a], dest_dir)


def install_gitleaks(dest_dir: Path | str | None = None) -> Path:
    """The same for the pinned gitleaks release."""
    a = arch()
    if a not in GITLEAKS_CHECKSUMS:
        raise InstallError(f"unsupported architecture: {a}")
    return _install("gitleaks", GITLEAKS_URL.format(v=GITLEAKS_VERSION, arch=GITLEAKS_ARCH[a]), GITLEAKS_CHECKSUMS[a], dest_dir)


def ensure_installed() -> None:
    """What the store setup runs: whichever of skillshare and gitleaks is not in Lifeboat's bin folder yet."""
    if skillshare_path() is None:
        install()
    if gitleaks_path() is None:
        install_gitleaks()


def run_skillshare(binary: Path | str, args: list[str], home: Path | str | None = None, cwd: Path | str | None = None,
                   timeout: int = 60) -> subprocess.CompletedProcess:
    """Run skillshare with HOME passed through, captured output and a timeout. Never raises on a bad exit."""
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    if home is not None:
        env.update(HOME=str(home), XDG_CONFIG_HOME=str(Path(home) / ".config"))
    try:
        return subprocess.run([str(binary), *args], env=env, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        return subprocess.CompletedProcess([str(binary), *args], 127, "", str(e))


def disable_update_check(binary: Path | str | None = None, home: Path | str | None = None) -> bool:
    """`skillshare updates off` saves updates.auto_check: false (needs an existing config). True on success."""
    binary = binary or skillshare_path()
    return bool(binary) and run_skillshare(binary, ["updates", "off"], home=home).returncode == 0
