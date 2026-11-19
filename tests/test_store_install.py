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
