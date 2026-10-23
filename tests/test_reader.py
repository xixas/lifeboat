import pytest

gi = pytest.importorskip("gi")
from gi.repository import Gio, GLib  # noqa: E402

from lifeboat.app.reader import read_lines  # noqa: E402


def test_reader_handles_blank_lines_partial_last_line_and_exit_code():
    proc = Gio.Subprocess.new(["sh", "-c", 'echo a; echo; echo b; printf "c\\n\\nlast"; exit 3'],
                              Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_MERGE)
    loop, lines, out = GLib.MainLoop(), [], {}

    def done(p, res):
        p.wait_finish(res)
        out["rc"] = p.get_exit_status()
        loop.quit()

    read_lines(proc.get_stdout_pipe(), lines.append, lambda: proc.wait_async(None, done))
    GLib.timeout_add_seconds(10, loop.quit)
    loop.run()
    assert lines == ["a", "", "b", "c", "", "last"]
    assert out["rc"] == 3


def test_reader_keeps_reading_when_the_line_callback_raises(capsys):
    proc = Gio.Subprocess.new(["sh", "-c", "echo a; echo SECRET; echo c"], Gio.SubprocessFlags.STDOUT_PIPE)
    loop, lines, eof = GLib.MainLoop(), [], []

    def on_line(line):
        if line == "SECRET":
            raise ValueError("SECRET")
        lines.append(line)

    read_lines(proc.get_stdout_pipe(), on_line, lambda: (eof.append(1), loop.quit()))
    GLib.timeout_add_seconds(10, loop.quit)
    loop.run()
    assert lines == ["a", "c"] and eof
    err = capsys.readouterr().err
    assert "ValueError" in err and "SECRET" not in err
