import hashlib
import io
import tarfile

import pytest

from lifeboat import store_install as si


def _tarball(payload=b"#!/bin/sh\necho hi\n"):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for name, data in (("README.md", b"readme"), ("../evil", b"x"), ("skillshare", payload)):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
    return buf.getvalue()


@pytest.fixture
def pinned(monkeypatch):
    blob = _tarball()
    urls = []
    monkeypatch.setattr(si, "CHECKSUMS", {"amd64": hashlib.sha256(blob).hexdigest(), "arm64": "0" * 64})
    monkeypatch.setattr(si, "_download", lambda url: urls.append(url) or blob)
    monkeypatch.setattr(si, "arch", lambda: "amd64")
    return urls


def test_constants():
    assert si.SKILLSHARE_VERSION == "0.30.11"
    assert si.CHECKSUMS["amd64"] == "0c908145dd517925cd0c27e0abde7f69f64fd457ca3fef34db0ccb7bc2350eb0"
    assert len(si.CHECKSUMS["arm64"]) == 64


def test_install_extracts_only_the_binary(tmp_path, pinned):
    dest = tmp_path / "bin"
    path = si.install(dest)
    assert path == dest / "skillshare"
    assert path.read_bytes().startswith(b"#!/bin/sh")
    assert path.stat().st_mode & 0o777 == 0o755
    assert [p.name for p in dest.iterdir()] == ["skillshare"]
    assert pinned == ["https://github.com/runkids/skillshare/releases/download/v0.30.11/skillshare_0.30.11_linux_amd64.tar.gz"]


def test_install_refuses_hash_mismatch_and_leaves_nothing(tmp_path, pinned, monkeypatch):
    monkeypatch.setitem(si.CHECKSUMS, "amd64", "f" * 64)
    with pytest.raises(si.InstallError, match="checksum"):
        si.install(tmp_path / "bin")
    assert not list((tmp_path / "bin").glob("*")) if (tmp_path / "bin").exists() else True


def test_install_mismatch_removes_existing_partial(tmp_path, pinned, monkeypatch):
    dest = tmp_path / "bin"
    dest.mkdir()
    (dest / "skillshare.part").write_text("partial")
    monkeypatch.setitem(si.CHECKSUMS, "amd64", "f" * 64)
    with pytest.raises(si.InstallError):
        si.install(dest)
    assert list(dest.iterdir()) == []


def test_unsupported_arch(monkeypatch, tmp_path):
    monkeypatch.setattr(si, "arch", lambda: "riscv")
    with pytest.raises(si.InstallError, match="architecture"):
        si.install(tmp_path)


def test_skillshare_path(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert si.skillshare_path() is None
    p = tmp_path / "lifeboat" / "bin" / "skillshare"
    p.parent.mkdir(parents=True)
    p.write_text("x")
    assert si.skillshare_path() == p


def _fake(tmp_path, body):
    p = tmp_path / "skillshare"
    p.write_text("#!/bin/sh\n" + body)
    p.chmod(0o755)
    return p


def test_disable_update_check_runs_updates_off_in_given_home(tmp_path):
    out = tmp_path / "out"
    fake = _fake(tmp_path, f'echo "$HOME $*" > {out}\n')
    assert si.disable_update_check(fake, home=tmp_path / "h") is True
    assert out.read_text().strip() == f"{tmp_path / 'h'} updates off"


# ---------------------------------------------------------------- gitleaks
def _gl_tarball(payload=b"#!/bin/sh\necho gl\n"):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for name, data in (("LICENSE", b"l"), ("../evil", b"x"), ("gitleaks", payload)):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
    return buf.getvalue()


@pytest.fixture
def gl_pinned(monkeypatch):
    blob = _gl_tarball()
    urls = []
    monkeypatch.setattr(si, "GITLEAKS_CHECKSUMS", {"amd64": hashlib.sha256(blob).hexdigest(), "arm64": "0" * 64})
    monkeypatch.setattr(si, "_download", lambda url: urls.append(url) or blob)
    monkeypatch.setattr(si, "arch", lambda: "amd64")
    return urls


def test_gitleaks_constants():
    assert si.GITLEAKS_VERSION == "8.30.1"
    assert si.GITLEAKS_CHECKSUMS == {"amd64": "551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb",
                                     "arm64": "e4a487ee7ccd7d3a7f7ec08657610aa3606637dab924210b3aee62570fb4b080"}


def test_install_gitleaks_extracts_only_the_binary_from_the_pinned_release(tmp_path, gl_pinned):
    dest = tmp_path / "bin"
    path = si.install_gitleaks(dest)
    assert path == dest / "gitleaks"
    assert path.read_bytes().startswith(b"#!/bin/sh")
    assert path.stat().st_mode & 0o777 == 0o755
    assert [p.name for p in dest.iterdir()] == ["gitleaks"]
    assert gl_pinned == ["https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/gitleaks_8.30.1_linux_x64.tar.gz"]


def test_install_gitleaks_uses_the_arm64_name_on_arm(tmp_path, gl_pinned, monkeypatch):
    monkeypatch.setattr(si, "arch", lambda: "arm64")
    monkeypatch.setitem(si.GITLEAKS_CHECKSUMS, "arm64", hashlib.sha256(_gl_tarball()).hexdigest())
    si.install_gitleaks(tmp_path / "bin")
    assert gl_pinned[0].endswith("gitleaks_8.30.1_linux_arm64.tar.gz")


def test_install_gitleaks_refuses_hash_mismatch_and_leaves_nothing(tmp_path, gl_pinned, monkeypatch):
    monkeypatch.setitem(si.GITLEAKS_CHECKSUMS, "amd64", "f" * 64)
    dest = tmp_path / "bin"
    with pytest.raises(si.InstallError, match="checksum"):
        si.install_gitleaks(dest)
    assert list(dest.iterdir()) == []


def test_gitleaks_path_is_only_lifeboats_own_binary_never_one_on_path(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    other = tmp_path / "onpath"
    other.mkdir()
    (other / "gitleaks").write_text("#!/bin/sh\n")
    (other / "gitleaks").chmod(0o755)
    monkeypatch.setenv("PATH", str(other))
    assert si.gitleaks_path() is None
    own = tmp_path / "share" / "lifeboat" / "bin" / "gitleaks"
    own.parent.mkdir(parents=True)
    own.write_text("x")
    assert si.gitleaks_path() == own


def test_ensure_installed_fetches_whatever_is_missing_and_only_that(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    got = []
    monkeypatch.setattr(si, "install", lambda *a: got.append("skillshare"))
    monkeypatch.setattr(si, "install_gitleaks", lambda *a: got.append("gitleaks"))
    si.ensure_installed()
    assert got == ["skillshare", "gitleaks"]
    got.clear()
    (tmp_path / "lifeboat" / "bin").mkdir(parents=True)
    (tmp_path / "lifeboat" / "bin" / "skillshare").write_text("x")
    si.ensure_installed()
    assert got == ["gitleaks"]
