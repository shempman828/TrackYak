"""
mpris2_integration.py

Registers TrackYak as an MPRIS2 media player on D-Bus so that Wayland
desktop environments (GNOME, KDE, etc.) route media key presses to this
app instead of handling them at the OS level.

MPRIS2 is the standard Linux protocol for media player control. When you
press a media key on Wayland, the desktop sends a D-Bus message to whatever
app has registered under org.mpris.MediaPlayer2 — not a keypress event.
Qt's ApplicationShortcut cannot intercept this, so we register here instead.
"""

import threading

from src.logger_config import logger

try:
    import dbus
    import dbus.mainloop.glib
    import dbus.service
    from gi.repository import GLib

    DBUS_AVAILABLE = True
except ImportError:
    DBUS_AVAILABLE = False
    logger.warning(
        "dbus-python or PyGObject not installed — media keys will not work on Wayland.\n"
        "Fix with:  sudo apt install python3-dbus python3-gi"
    )

try:
    from PySide6.QtCore import QMetaObject, Qt

    QT_AVAILABLE = True
except ImportError:
    QT_AVAILABLE = False


MPRIS2_OBJECT_PATH = "/org/mpris/MediaPlayer2"
MPRIS2_IFACE = "org.mpris.MediaPlayer2"
MPRIS2_PLAYER_IFACE = "org.mpris.MediaPlayer2.Player"
DBUS_PROPS_IFACE = "org.freedesktop.DBus.Properties"
BUS_NAME = "org.mpris.MediaPlayer2.trackyak"


def _invoke_on_main_thread(obj, method_name):
    """
    Safely call a no-argument method on a QObject from any thread.

    Uses Qt.QueuedConnection so the call is posted to Qt's event queue
    and executed on the main thread on the next event loop iteration.
    This is the correct way to trigger Qt/QObject methods from a non-Qt thread.
    """
    QMetaObject.invokeMethod(obj, method_name, Qt.ConnectionType.QueuedConnection)


class MPRIS2Player:
    """
    Spins up a GLib main loop on a background daemon thread and registers
    this app as an MPRIS2 media player on D-Bus.

    Media key presses from the desktop are forwarded to the MusicPlayer
    instance via Qt's queued connection mechanism so they always run on
    the correct (main) thread.
    """

    def __init__(self, music_player):
        """
        Args:
            music_player: Your MusicPlayer instance (player_util.MusicPlayer).
        """
        self._player = music_player
        self._thread = None
        self._loop = None
        self._service = None

    def start(self):
        """Start the MPRIS2 D-Bus service on a background thread."""
        if not DBUS_AVAILABLE:
            logger.warning("MPRIS2 not started — dbus-python unavailable.")
            return
        if not QT_AVAILABLE:
            logger.warning("MPRIS2 not started — PySide6 unavailable.")
            return

        self._thread = threading.Thread(
            target=self._run_dbus_loop,
            name="MPRIS2-DBus",
            daemon=True,  # Killed automatically when the Qt app exits
        )
        self._thread.start()
        logger.info("MPRIS2 media key integration started.")

    def stop(self):
        """Cleanly stop the GLib loop. Call this in closeEvent."""
        if self._loop and self._loop.is_running():
            self._loop.quit()
        logger.info("MPRIS2 media key integration stopped.")

    def _run_dbus_loop(self):
        """Background thread entry point."""
        try:
            dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
            session_bus = dbus.SessionBus()
            _bus_name = dbus.service.BusName(BUS_NAME, bus=session_bus)
            self._service = _MPRIS2DBusService(session_bus, self._player)

            logger.debug(f"MPRIS2: registered on D-Bus as '{BUS_NAME}'")

            self._loop = GLib.MainLoop()
            self._loop.run()  # Blocks this thread only

        except Exception as e:
            logger.error(f"MPRIS2 D-Bus loop failed: {e}")


class _MPRIS2DBusService(dbus.service.Object):
    """
    The D-Bus object that implements the MPRIS2 spec.

    Every player method uses _invoke_on_main_thread() to hand off work to
    Qt's main thread. This is non-negotiable: MusicPlayer uses QTimers,
    Qt signals, and sounddevice streams — all must run on the main thread.
    """

    def __init__(self, bus, music_player):
        super().__init__(bus, MPRIS2_OBJECT_PATH)
        self._player = music_player

    # ── org.mpris.MediaPlayer2 (root interface — required by spec) ────────────

    @dbus.service.method(MPRIS2_IFACE)
    def Raise(self):
        pass  # Optional: could raise the main window

    @dbus.service.method(MPRIS2_IFACE)
    def Quit(self):
        pass  # Optional: we don't allow D-Bus to quit the app

    # ── org.mpris.MediaPlayer2.Player ─────────────────────────────────────────

    @dbus.service.method(MPRIS2_PLAYER_IFACE)
    def PlayPause(self):
        logger.debug("MPRIS2 → PlayPause")
        _invoke_on_main_thread(self._player, "toggle_play_pause")

    @dbus.service.method(MPRIS2_PLAYER_IFACE)
    def Play(self):
        logger.debug("MPRIS2 → Play")
        _invoke_on_main_thread(self._player, "play")

    @dbus.service.method(MPRIS2_PLAYER_IFACE)
    def Pause(self):
        logger.debug("MPRIS2 → Pause")
        _invoke_on_main_thread(self._player, "pause")

    @dbus.service.method(MPRIS2_PLAYER_IFACE)
    def Stop(self):
        logger.debug("MPRIS2 → Stop")
        _invoke_on_main_thread(self._player, "stop")

    @dbus.service.method(MPRIS2_PLAYER_IFACE)
    def Next(self):
        logger.debug("MPRIS2 → Next")
        _invoke_on_main_thread(self._player, "play_next")

    @dbus.service.method(MPRIS2_PLAYER_IFACE)
    def Previous(self):
        logger.debug("MPRIS2 → Previous")
        _invoke_on_main_thread(self._player, "play_previous")

    @dbus.service.method(MPRIS2_PLAYER_IFACE, in_signature="x")
    def Seek(self, offset_microseconds):
        logger.debug(f"MPRIS2 → Seek {offset_microseconds}µs")
        # Seek needs an argument so we can't use invokeMethod directly.
        # QTimer.singleShot(0, callable) is safe to call from any thread —
        # it posts the callable to the main thread's event queue.
        try:
            from PySide6.QtCore import QTimer

            offset_ms = int(offset_microseconds) // 1000
            target_ms = max(0, self._player.position + offset_ms)
            QTimer.singleShot(0, lambda: self._player.seek(target_ms))
        except Exception as e:
            logger.error(f"MPRIS2 Seek error: {e}")

    # ── org.freedesktop.DBus.Properties ──────────────────────────────────────

    @dbus.service.method(DBUS_PROPS_IFACE, in_signature="ss", out_signature="v")
    def Get(self, interface, prop):
        return self._get_props(interface).get(prop, dbus.String(""))

    @dbus.service.method(DBUS_PROPS_IFACE, in_signature="s", out_signature="a{sv}")
    def GetAll(self, interface):
        return self._get_props(interface)

    @dbus.service.method(DBUS_PROPS_IFACE, in_signature="ssv")
    def Set(self, interface, prop, value):
        pass

    def _get_props(self, interface):
        """Return the properties the desktop queries to know what we support."""
        if interface == MPRIS2_IFACE:
            return {
                "CanQuit": dbus.Boolean(False),
                "CanRaise": dbus.Boolean(False),
                "HasTrackList": dbus.Boolean(False),
                "Identity": dbus.String("TrackYak"),
                "DesktopEntry": dbus.String("trackyak"),
                "SupportedUriSchemes": dbus.Array([], signature="s"),
                "SupportedMimeTypes": dbus.Array([], signature="s"),
            }
        if interface == MPRIS2_PLAYER_IFACE:
            try:
                volume = self._player.volume_level / 100.0
                position_us = self._player.position * 1000  # ms → µs
                status = self._playback_status()
            except Exception:
                volume = 0.75
                position_us = 0
                status = "Stopped"

            return {
                "PlaybackStatus": dbus.String(status),
                "LoopStatus": dbus.String("None"),
                "Rate": dbus.Double(1.0),
                "Shuffle": dbus.Boolean(False),
                "Metadata": dbus.Dictionary({}, signature="sv"),
                "Volume": dbus.Double(volume),
                "Position": dbus.Int64(position_us),
                "MinimumRate": dbus.Double(1.0),
                "MaximumRate": dbus.Double(1.0),
                "CanGoNext": dbus.Boolean(True),
                "CanGoPrevious": dbus.Boolean(True),
                "CanPlay": dbus.Boolean(True),
                "CanPause": dbus.Boolean(True),
                "CanSeek": dbus.Boolean(True),
                "CanControl": dbus.Boolean(True),
            }
        return {}

    def _playback_status(self):
        state = self._player.state  # "playing" | "paused" | "stopped"
        return {"playing": "Playing", "paused": "Paused"}.get(state, "Stopped")
