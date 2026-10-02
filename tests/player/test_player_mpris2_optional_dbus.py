"""Regression: the MPRIS2 module must import when dbus-python is not installed.

The bug: `_MPRIS2DBusService` subclassed `dbus.service.Object` at module
level, outside the ImportError guard, so a missing dbus-python raised
NameError on import and the app could not start -- although
requirements.txt documents dbus-python and PyGObject as optional.
"""

import importlib
import sys

MODULE = "src.player.core.player_mpris2"


def test_module_imports_and_start_is_noop_without_dbus(monkeypatch):
    for name in ("dbus", "dbus.mainloop", "dbus.mainloop.glib", "dbus.service", "gi", "gi.repository"):
        monkeypatch.setitem(sys.modules, name, None)  # None makes `import name` raise ImportError
    monkeypatch.delitem(sys.modules, MODULE, raising=False)

    mod = importlib.import_module(MODULE)

    assert mod.DBUS_AVAILABLE is False
    player = mod.MPRIS2Player(music_player=object())
    player.start()
    assert player._thread is None

    monkeypatch.delitem(sys.modules, MODULE)  # let later tests import the real module again
