"""Read a subprocess pipe line by line without blocking the main loop."""
from collections.abc import Callable

from gi.repository import Gio, GLib

from lifeboat.engine import ANSI


def read_lines(stream: Gio.InputStream, on_line: Callable[[str], None], on_eof: Callable[[], None]) -> None:
    """Read raw chunks: an empty chunk is the only unambiguous end of output
    (DataInputStream.read_line returns b"" for both a blank line and EOF in PyGObject)."""
    pending = bytearray()

    def emit(raw: bytes) -> None:
        on_line(ANSI.sub("", raw.decode("utf-8", "replace")))

    def on_chunk(st, res):
        try:
            data = st.read_bytes_finish(res).get_data() or b""
        except GLib.Error:
            data = b""
        if not data:
            if pending:
                emit(bytes(pending))
            on_eof()
            return
        pending.extend(data)
        *lines, rest = pending.split(b"\n")
        pending[:] = rest
        for ln in lines:
            emit(ln)
        st.read_bytes_async(4096, GLib.PRIORITY_DEFAULT, None, on_chunk)

    stream.read_bytes_async(4096, GLib.PRIORITY_DEFAULT, None, on_chunk)
