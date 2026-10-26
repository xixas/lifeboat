import json
import subprocess
from datetime import datetime

from lifeboat.runlog import Rclone, RunLog, human_size


def rec(**kw):
    return json.dumps({"time": "2026-10-23T19:20:54+05:30", "level": "info", **kw})


def test_human_size():
    assert [human_size(n) for n in (0, 46, 1024, 1536, 5 * 1024**2)] == ["0 B", "46 B", "1.0 KiB", "1.5 KiB", "5.0 MiB"]


def test_copied_lines_count_files_and_bytes_and_say_new_or_replaced():
    r = Rclone()
    assert r.feed(rec(msg="Copied (new)", size=46, object="hello.txt")) == "copied hello.txt (new, 46 B)"
    assert r.feed(rec(msg="Copied (replaced existing)", size=7, object="notes/n1.md")) == "copied notes/n1.md (replaced, 7 B)"
    assert (r.files, r.bytes, r.error) == (2, 53, "")


def test_noise_is_dropped():
    r = Rclone()
    for noise in (rec(msg="Set directory modification time (using DirSetModTime)", object="notes"),
                  rec(msg="There was nothing to transfer"), rec(msg="\nTransferred: 0 B\n", stats={"transfers": 0})):
        assert r.feed(noise) is None
    assert r.files == 0


def test_errors_keep_rclones_text_and_ignore_fs_descriptions():
    r = Rclone()
    out = r.feed(rec(level="error", msg="error reading source root directory: directory not found",
                     object="Local file system at /tmp/x", objectType="*local.Fs"))
    assert out == "ERROR error reading source root directory: directory not found"
    assert r.feed(rec(level="error", msg="Failed to copy: boom", object="a.txt", objectType="*local.Object")) == "ERROR a.txt: Failed to copy: boom"
    assert r.error == "a.txt: Failed to copy: boom"
    r.feed(rec(level="error", msg="Attempt 3/3 failed with 1 errors and: boom"))
    assert r.error == "a.txt: Failed to copy: boom"  # the retry summary is not the cause


def test_unknown_lines_pass_through_never_dropped():
    r = Rclone()
    assert r.feed("Enter configuration password:") == "Enter configuration password:"
    assert r.feed("panic: runtime error") == "panic: runtime error"
    assert r.feed("[1, 2]") == "[1, 2]"
    assert r.feed(rec(level="warning", msg="Duplicate", object="x")) == "WARNING x: Duplicate"
    assert r.feed(rec(level="notice", msg="Skipped copy as --dry-run is set (size 3)", object="a.txt")) == "a.txt: Skipped copy as --dry-run is set (size 3)"


def test_runlog_stamps_time_and_label_into_log_and_emit():
    lines, out = [], []

    class F:
        def write(self, s): lines.append(s)
        def flush(self): pass

    rl = RunLog(F(), out.append, clock=lambda: datetime(2026, 10, 23, 19, 20, 54))
    rl.line("Documents", "done 2 files")
    rl.line("", "Finished")
    assert out == ["19:20:54  Documents  done 2 files", "19:20:54  Finished"]
    assert lines == [x + "\n" for x in out]
    rl.raw("Lifeboat 0.4.0 · backup of x")
    assert out[-1] == "Lifeboat 0.4.0 · backup of x"


def test_dry_run_notices_from_real_rclone_count_files_that_would_be_copied(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.txt").write_text("one")
    (tmp_path / "src" / "b.txt").write_text("three")
    p = subprocess.run(["rclone", "copy", str(tmp_path / "src"), str(tmp_path / "dest"), "--dry-run", "--use-json-log", "--log-level", "INFO",
                        "--stats", "0"], capture_output=True, text=True, check=True)
    r = Rclone()
    for line in p.stderr.splitlines():
        r.feed(line)
    assert (r.files, r.would, r.would_bytes) == (0, 2, 8) and not (tmp_path / "dest").exists()
