from types import SimpleNamespace

import pytest

pytest.importorskip("gi")
from gi.repository import Gtk  # noqa: E402

from lifeboat.app.widgets import spinner_class  # noqa: E402


def test_spinner_falls_back_to_gtk_on_libadwaita_older_than_1_6():
    assert spinner_class(SimpleNamespace()) is Gtk.Spinner  # Ubuntu 24.04 ships libadwaita 1.5: no Adw.Spinner
    new = object()
    assert spinner_class(SimpleNamespace(Spinner=new)) is new
