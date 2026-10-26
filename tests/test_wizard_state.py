import os
from datetime import UTC, datetime, timedelta

from lifeboat.app.wizard_state import (
    DEFAULT_TIME,
    Choices,
    daily_times,
    fmt_size,
    folders_text,
    job_label,
    keep_backing_up_default,
    same_laptop_backup,
    since_text,
    tilde,
    validate_device_name,
)

F = {"key": "documents", "label": "Documents", "path": "/h/Documents", "default_on": True, "secret": False}
K = {"key": "ssh", "label": "SSH keys", "path": "/h/.ssh", "default_on": True, "secret": True}


def test_daily_times():
    t = daily_times()
    assert t[0] == "00:00" and t[-1] == "23:00" and len(t) == 24
    assert DEFAULT_TIME == "13:00" and DEFAULT_TIME in t


def test_device_name_validation():
    assert validate_device_name("laptop", []) is None
    assert validate_device_name("", []) is not None
    assert validate_device_name("bad name", []) is not None
    assert validate_device_name("-x", []) is not None
    assert "already" in validate_device_name("laptop", ["laptop"])


def test_fmt_size():
    assert fmt_size(None, False) == "calculating…"
    assert fmt_size(1_200_000_000, True) == "1.2 GB"
    assert fmt_size(14 * 10**9, False) == "about 14 GB+"
    assert fmt_size(512, True) == "512 B"
    assert fmt_size(3_400_000, True) == "3.4 MB"


def test_folders_text():
    assert folders_text(1) == "1 folder" and folders_text(3) == "3 folders"


def test_defaults_and_selection():
    c = Choices([F, K])
    assert [x["path"] for x in c.selected()] == ["/h/Documents", "/h/.ssh"]
    assert c.can_continue()


def test_size_update_turns_off_big_folder_unless_user_decided():
    c = Choices([F, K])
    c.set_size("/h/Documents", 50 * 1024**3, True)
    assert not c.is_on("/h/Documents")
    c2 = Choices([F])
    c2.toggle("/h/Documents", True)
    c2.set_size("/h/Documents", 50 * 1024**3, True)
    assert c2.is_on("/h/Documents")  # the user's choice wins


def test_nothing_selected_blocks_continue():
    c = Choices([F])
    c.toggle("/h/Documents", False)
    assert not c.can_continue()


def test_add_and_remove_folder():
    c = Choices([F])
    assert c.add("/h/Projects")
    assert c.is_on("/h/Projects")
    assert not c.add("/h/Projects")  # duplicate
    assert not c.add("/h/Documents")
    c.remove("/h/Projects")
    assert [x["path"] for x in c.selected()] == ["/h/Documents"]


def test_added_labels_stay_unique():
    c = Choices([F])
    c.add("/a/Documents")
    labels = [x["label"] for x in c.selected()]
    assert len({n.lower() for n in labels}) == 2


def test_tilde():
    home = os.path.expanduser("~")
    assert tilde(home + "/Documents") == "~/Documents"
    assert tilde("/srv/data") == "/srv/data"


def test_job_label_names_a_manifest_job_for_people():
    assert job_label("documents") == "Documents"
    assert job_label("ssh-keys") == "SSH keys"
    assert job_label("gpg-keys") == "GPG keys"
    assert job_label("my-stuff-old") == "My stuff old"


def test_keep_backing_up_defaults_on_only_when_the_old_laptop_looks_gone():
    now = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
    iso = lambda h: (now - timedelta(hours=h)).isoformat()  # noqa: E731
    assert keep_backing_up_default({"time": iso(30)}, now) is True
    assert keep_backing_up_default({"time": iso(2)}, now) is False
    assert keep_backing_up_default({"time": iso(24.5)}, now) is True
    assert keep_backing_up_default({}, now) is True  # no readable time: nobody is backing up


def test_since_text():
    assert since_text("2026-10-02T09:00:00+00:00") == "Not backed up since 2 Oct 2026"
    assert since_text(None) == "Not backed up from the old laptop"


def test_same_laptop_matches_the_manifest_host():
    mine = {"device": "work", "host": "fedora-box"}
    assert same_laptop_backup([{"device": "other", "host": "x"}, mine], "fedora-box", "fedora-box") == "work"
    assert same_laptop_backup([{"device": "other", "host": "x"}], "fedora-box", "fedora-box") is None


def test_same_laptop_ignores_a_distro_default_hostname():
    # 'fedora' is every fresh Fedora install: matching it would point a new laptop at a stranger's backup
    for host in ("fedora", "localhost.localdomain", "localhost", "ubuntu", "nobara"):
        assert same_laptop_backup([{"device": host, "host": host}], host, host) is None, host


def test_keep_backing_up_defaults_on_for_this_laptops_own_backup():
    # a reinstall: the backup is this laptop's, so a fresh last run does not mean another laptop is still using it
    now = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
    ago = lambda h: {"time": (now - timedelta(hours=h)).isoformat()}  # noqa: E731
    assert keep_backing_up_default(ago(2), now, same_laptop=True) is True
    # but not minutes ago: a reinstall cannot have backed up that recently, a cloned disk still running can
    assert keep_backing_up_default(ago(0.5), now, same_laptop=True) is False
