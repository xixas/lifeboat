import os

from lifeboat.app.wizard_state import DEFAULT_TIME, Choices, daily_times, fmt_size, folders_text, tilde, validate_device_name

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
