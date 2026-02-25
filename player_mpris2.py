"""
mpris2_integration.py

Registers TrackYak as an MPRIS2 media player on D-Bus so that Wayland
desktop environments (GNOME, KDE, etc.) route media key presses to this
app instead of handling them at the OS level.

MPRIS2 is the standard Linux protocol for media player control. When you
press a media key on Wayland, the desktop sends a D-Bus message to whatever
app has registered under org.mpris.MediaPlayer2 — not a keypress event.
Qt's ApplicationShortcut cannot intercept this, so we register here instead.

Requirements:
    pip install dbus-python
    # OR on Ubuntu/Debian:
    sudo apt install python3-dbus

Usage (in run.py or main_window.py after the player is created):
    from mpris2_integration import MPRIS2Player
    mpris = MPRIS2Player(controller.mediaplayer)
    mpris.start()

    # On app close:
    mpris.stop()
"""

import threading

from logger_config import logger

try:
    import dbus
    import dbus.mainloop.glib
    import dbus.service
    from gi.repository import GLib

    DBUS_AVAILABLE = True
except ImportError:
    DBUS_AVAILABLE = False
    logger.warning(
        "dbus-python or PyGObject not installed. Media keys will not work on Wayland. "
        "Install with: pip install dbus-python  or  sudo apt install python3-dbus"
    )


MPRIS2_OBJECT_PATH = "/org/mpris/MediaPlayer2"
MPRIS2_IFACE = "org.mpris.MediaPlayer2"
MPRIS2_PLAYER_IFACE = "org.mpris.MediaPlayer2.Player"
DBUS_PROPS_IFACE = "org.freedesktop.DBus.Properties"
BUS_NAME = "org.mpris.MediaPlayer2.trackyak"


class MPRIS2Player:
    """
    Thin wrapper that spins up a GLib main loop in a background thread and
    registers the MPRIS2 D-Bus service. All media key presses from the
    desktop are forwarded to the MusicPlayer instance you pass in.

    The GLib loop runs on its own thread so it never blocks Qt's event loop.
    """

    def __init__(self, music_player):
        """
        Args:
            music_player: Your MusicPlayer instance from player_util.py.
                          Needs: toggle_play_pause(), play_next(),
                                 play_previous(), stop()
        """
        self._player = music_player
        self._thread = None
        self._loop = None
        self._service = None

    def start(self):
        """Start the MPRIS2 service in a background thread."""
        if not DBUS_AVAILABLE:
            logger.warning("MPRIS2 not started — dbus-python unavailable.")
            return

        self._thread = threading.Thread(
            target=self._run_dbus_loop,
            name="MPRIS2-DBus",
            daemon=True,  # Dies automatically when the main app exits
        )
        self._thread.start()
        logger.info("MPRIS2 media key integration started.")

    def stop(self):
        """Cleanly stop the GLib loop. Call this in your closeEvent."""
        if self._loop and self._loop.is_running():
            self._loop.quit()
        logger.info("MPRIS2 media key integration stopped.")

    def _run_dbus_loop(self):
        """Entry point for the background thread."""
        try:
            # Tell dbus-python to use the GLib main loop for signals
            dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)

            session_bus = dbus.SessionBus()

            # Claim the bus name so GNOME/KDE can find us
            dbus.service.BusName(BUS_NAME, bus=session_bus)

            # Create the D-Bus service object
            self._service = _MPRIS2DBusService(session_bus, self._player)

            # Run the GLib event loop (blocks this thread only)
            self._loop = GLib.MainLoop()
            self._loop.run()

        except Exception as e:
            logger.error(f"MPRIS2 D-Bus loop error: {e}")


class _MPRIS2DBusService(dbus.service.Object):
    """
    The actual D-Bus object that implements the MPRIS2 spec.

    Only the methods and properties that desktop environments actually
    call for media key routing are implemented. The full MPRIS2 spec has
    many more optional properties (metadata, shuffle, etc.) which you can
    add later if you want desktop widgets to show track info.
    """

    def __init__(self, bus, music_player):
        super().__init__(bus, MPRIS2_OBJECT_PATH)
        self._player = music_player

    # ── org.mpris.MediaPlayer2 (root interface) ───────────────────────────────
    # These are required by the spec but not used for media keys.

    @dbus.service.method(MPRIS2_IFACE)
    def Raise(self):
        """Bring the app window to the front. Optional — we ignore it."""
        pass

    @dbus.service.method(MPRIS2_IFACE)
    def Quit(self):
        """Quit the application. Optional — we ignore it."""
        pass

    # ── org.mpris.MediaPlayer2.Player (the part media keys talk to) ──────────

    @dbus.service.method(MPRIS2_PLAYER_IFACE)
    def PlayPause(self):
        """Called when the Play/Pause media key is pressed."""
        logger.debug("MPRIS2: PlayPause received")
        try:
            self._player.toggle_play_pause()
        except Exception as e:
            logger.error(f"MPRIS2 PlayPause error: {e}")

    @dbus.service.method(MPRIS2_PLAYER_IFACE)
    def Play(self):
        """Called when a Play-only signal is sent."""
        logger.debug("MPRIS2: Play received")
        try:
            self._player.play()
        except Exception as e:
            logger.error(f"MPRIS2 Play error: {e}")

    @dbus.service.method(MPRIS2_PLAYER_IFACE)
    def Pause(self):
        """Called when a Pause-only signal is sent."""
        logger.debug("MPRIS2: Pause received")
        try:
            self._player.pause()
        except Exception as e:
            logger.error(f"MPRIS2 Pause error: {e}")

    @dbus.service.method(MPRIS2_PLAYER_IFACE)
    def Stop(self):
        """Called when the Stop media key is pressed."""
        logger.debug("MPRIS2: Stop received")
        try:
            self._player.stop()
        except Exception as e:
            logger.error(f"MPRIS2 Stop error: {e}")

    @dbus.service.method(MPRIS2_PLAYER_IFACE)
    def Next(self):
        """Called when the Next Track media key is pressed."""
        logger.debug("MPRIS2: Next received")
        try:
            self._player.play_next()
        except Exception as e:
            logger.error(f"MPRIS2 Next error: {e}")

    @dbus.service.method(MPRIS2_PLAYER_IFACE)
    def Previous(self):
        """Called when the Previous Track media key is pressed."""
        logger.debug("MPRIS2: Previous received")
        try:
            self._player.play_previous()
        except Exception as e:
            logger.error(f"MPRIS2 Previous error: {e}")

    @dbus.service.method(MPRIS2_PLAYER_IFACE, in_signature="x")
    def Seek(self, offset_microseconds):
        """Called when a seek action is sent (less common on keyboards)."""
        logger.debug(f"MPRIS2: Seek {offset_microseconds}µs")
        try:
            offset_ms = offset_microseconds // 1000
            current_ms = self._player.position
            self._player.seek(max(0, current_ms + offset_ms))
        except Exception as e:
            logger.error(f"MPRIS2 Seek error: {e}")

    # ── org.freedesktop.DBus.Properties ──────────────────────────────────────
    # The desktop queries these to know what the player supports.

    @dbus.service.method(DBUS_PROPS_IFACE, in_signature="ss", out_signature="v")
    def Get(self, interface, prop):
        return self._get_props(interface).get(prop, "")

    @dbus.service.method(DBUS_PROPS_IFACE, in_signature="s", out_signature="a{sv}")
    def GetAll(self, interface):
        return self._get_props(interface)

    @dbus.service.method(DBUS_PROPS_IFACE, in_signature="ssv")
    def Set(self, interface, prop, value):
        pass  # We don't support setting props from D-Bus

    def _get_props(self, interface):
        """Return properties for the requested interface."""
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
            return {
                "PlaybackStatus": dbus.String(self._get_playback_status()),
                "LoopStatus": dbus.String("None"),
                "Rate": dbus.Double(1.0),
                "Shuffle": dbus.Boolean(False),
                "Metadata": dbus.Dictionary({}, signature="sv"),
                "Volume": dbus.Double(self._player.volume_level / 100.0),
                "Position": dbus.Int64(self._player.position * 1000),  # µs
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

    def _get_playback_status(self):
        """Map internal player state to MPRIS2 playback status string."""
        try:
            state = self._player.state  # "playing", "paused", or "stopped"
            return {"playing": "Playing", "paused": "Paused"}.get(state, "Stopped")
        except Exception:
            return "Stopped"
