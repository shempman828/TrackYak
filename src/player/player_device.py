"""
player_device.py — audio backend init, output device selection, and
exclusive/bit-perfect mode handling for MusicPlayer.

Expects the host class to provide: self.sd, self.sf, self.available_devices,
self.current_device, self.exclusive_mode, self._suspended_sink_name,
self.audio_stream, self.current_file, self.playing, self.paused,
self._position, self.error_occurred/audio_device_changed signals, and
self.stop()/self.seek()/self.play() (transport controls).
"""

import gc
import json
import re
import subprocess
import threading
import time

from src.core.config_setup import app_config
from src.core.logger_config import logger

try:
    import dbus

    DBUS_AVAILABLE = True
except ImportError:
    DBUS_AVAILABLE = False

RTKIT_BUS_NAME = "org.freedesktop.RealtimeKit1"
RTKIT_OBJECT_PATH = "/org/freedesktop/RealtimeKit1"
# RTKit enforces its own ceiling (MaxRealtimePriority, read from the
# RealtimeKit1 D-Bus property) regardless of what we ask for -- confirmed on
# this system to be 20, matching PipeWire's own real-time thread priority.
# Requesting 10 left us below several kernel SCHED_FIFO threads that run at
# priority 50 (the DRM display driver's per-CRTC vblank workers, card1-crtc0..5,
# plus a couple of IRQ threads) -- any of those preempting our audio thread
# during display activity (e.g. compositing while browsing) was enough to
# starve the callback and produce a real paOutputUnderflow, logged via
# player_callback.py's `status` handling. 20 doesn't out-prioritize those
# SCHED_FIFO-50 threads either (RTKit won't grant more), but it does put us
# ahead of all ordinary SCHED_OTHER contention, which is the most RTKit will
# ever hand out to an unprivileged desktop app here.
REALTIME_PROMOTION_PRIORITY = 20
REALTIME_PROMOTION_POLL_TIMEOUT = 0.5  # seconds to wait for the callback to fire


class PlayerDeviceMixin:
    """PortAudio backend lifecycle plus PipeWire/PulseAudio sink handling
    needed to grab a raw hw: device for bit-perfect (exclusive-mode) output."""

    def _initialize_audio_backend(self) -> bool:
        try:
            import sounddevice as sd
            import soundfile as sf

            self.sd = sd
            self.sf = sf
            self.available_devices = list(sd.query_devices())
            logger.info(
                f"Audio backend ready. {len(self.available_devices)} devices found."
            )
            return True
        except ImportError as exc:
            logger.error(f"Audio backend import failed: {exc}")
            self.error_occurred.emit(
                f"Audio library missing: {exc}. Run: pip install sounddevice soundfile"
            )
            return False
        except (OSError, sd.PortAudioError) as exc:
            logger.error(f"Audio backend init error: {exc}")
            self.error_occurred.emit(f"Audio system error: {exc}")
            return False

    def _load_saved_output_device(self):
        """Restore the output device saved in config, if it still exists.

        Runs once at startup, after `available_devices` has been populated,
        so a stale index (device unplugged/renumbered since last run) is
        detected here rather than surfacing as a failed stream open later.
        """
        saved = app_config.get_output_device()
        if not saved or saved == "default":
            return
        try:
            saved_id = int(saved)
        except ValueError:
            return
        if 0 <= saved_id < len(self.available_devices):
            self.current_device = saved_id
        else:
            logger.warning(
                f"Saved output device index {saved_id} no longer exists; "
                "using system default."
            )

    @staticmethod
    def is_direct_output_device(name: str) -> bool:
        """Best-effort check for whether a PortAudio device name is a raw
        ALSA hardware device (e.g. "...(hw:1,0)") as opposed to a shared/
        resampling virtual device (pulse, pipewire, default, sysdefault,
        dmix, ...). Only raw hw devices can be opened bit-perfect —
        anything else is mixed/resampled by the sound server first.
        """
        lname = name.lower()
        if any(
            token in lname
            for token in ("pulse", "pipewire", "sysdefault", "default", "dmix", "jack")
        ):
            return False
        return "hw:" in lname

    def _resolve_exclusive_target(self, device):
        """Resolve `device` (a PortAudio index, or None/"" for the system
        default) to the ALSA (card, device) hw ids plus the name of the
        PipeWire/PulseAudio sink currently backing it, if any.

        PortAudio's device enumeration queries each PCM at startup to learn
        its capabilities; a hw: node that PipeWire already has open (e.g.
        the desktop's default output) fails that query and is silently
        dropped from the list. So the device the user actually wants for
        bit-perfect output may not be selectable/visible at all — this
        looks it up directly from PipeWire's sink properties instead,
        which also lets the caller suspend that sink to free the hw node.

        Returns ((card, dev), sink_name); either half is None if not
        resolvable (pactl unavailable, no PipeWire sink involved, or not a
        hw: device).
        """
        sink_name = None
        hw_ids = None

        # `device` is normally an already-resolved PortAudio index by the time
        # this is called (`_get_device_config` resolves None -> sd.default.device
        # before returning), so "no explicit device" shows up as an index whose
        # *name* is one of ALSA's virtual default aliases, not as None itself.
        device_name = None
        if device is not None and device != "":
            try:
                device_name = self.available_devices[device]["name"]
            except (IndexError, TypeError):
                return None, None

        is_default_alias = (
            device is None
            or device == ""
            or (
                device_name is not None
                and device_name.strip().lower()
                in ("default", "pulse", "pipewire", "sysdefault")
            )
        )

        if is_default_alias:
            try:
                result = subprocess.run(
                    ["pactl", "get-default-sink"],
                    capture_output=True,
                    text=True,
                    timeout=2,
                    check=True,
                )
                sink_name = result.stdout.strip() or None
            except (subprocess.SubprocessError, OSError) as exc:
                logger.debug(f"pactl get-default-sink failed: {exc}")
                return None, None
            if sink_name is None:
                return None, None
        else:
            match = re.search(r"hw:(\d+),(\d+)", device_name)
            if not match:
                return None, None
            hw_ids = (int(match.group(1)), int(match.group(2)))

        try:
            result = subprocess.run(
                ["pactl", "-f", "json", "list", "sinks"],
                capture_output=True,
                text=True,
                timeout=2,
                check=True,
            )
            sinks = json.loads(result.stdout)
        except (subprocess.SubprocessError, OSError, json.JSONDecodeError) as exc:
            logger.debug(f"pactl sink lookup failed: {exc}")
            return None, None

        for sink in sinks:
            if sink_name is not None and sink.get("name") != sink_name:
                continue
            props = sink.get("properties", {})
            card, dev = props.get("alsa.card"), props.get("alsa.device")
            if card is None or dev is None:
                continue
            found_ids = (int(card), int(dev))
            if hw_ids is not None and found_ids != hw_ids:
                continue
            return found_ids, sink.get("name")

        return None, None

    def _suspend_sink(self, sink_name: str, suspend: bool) -> bool:
        """Suspend (or resume) a PipeWire/PulseAudio sink via pactl so its
        underlying ALSA hw node can be grabbed directly (or given back)."""
        try:
            subprocess.run(
                ["pactl", "suspend-sink", sink_name, "1" if suspend else "0"],
                capture_output=True,
                timeout=2,
                check=True,
            )
            return True
        except (subprocess.SubprocessError, OSError) as exc:
            logger.debug(
                f"pactl suspend-sink {sink_name} {'1' if suspend else '0'} failed: {exc}"
            )
            return False

    def _prepare_exclusive_device(self, device) -> int | None:
        """Try to grab the real hw:card,device node behind `device` for
        bit-perfect playback.

        PortAudio's ALSA backend builds its device list once when it's
        initialized; a hw: node that PipeWire has open fails that scan and
        never shows up — even a fresh `query_devices()` call still returns
        the stale list from the last init. So this suspends the owning
        PipeWire/PulseAudio sink (via `_resolve_exclusive_target`), then
        tears down and reinitializes the sounddevice/PortAudio session so
        the now-free node is picked up, and returns its new device index.

        On success, `self._suspended_sink_name` is set so the caller can
        resume the sink once the stream is closed. Returns None if the
        device can't be resolved to a hw: node, or it still isn't visible
        after a few retries — in which case nothing is left suspended.
        """
        hw_ids, sink_name = self._resolve_exclusive_target(device)
        if hw_ids is None or sink_name is None:
            return None

        if not self._suspend_sink(sink_name, True):
            return None
        self._suspended_sink_name = sink_name

        target = f"hw:{hw_ids[0]},{hw_ids[1]}"
        for _attempt in range(5):
            time.sleep(0.15)
            try:
                self.sd._terminate()
                self.sd._initialize()
                self.available_devices = list(self.sd.query_devices())
            except (OSError, RuntimeError, self.sd.PortAudioError) as exc:
                logger.debug(f"PortAudio re-init failed: {exc}")
                continue
            for i, d in enumerate(self.available_devices):
                if target in d["name"]:
                    return i

        # Couldn't grab it — undo the suspend so we don't leave the sink
        # (and the user's system audio) stuck.
        self._suspend_sink(sink_name, False)
        self._suspended_sink_name = None
        return None

    def _promote_callback_to_realtime(self, native_tid: int, priority: int) -> bool:
        """Ask RTKit -- the same system service PipeWire/JACK use for this --
        to give a thread in this process real-time (SCHED_RR) scheduling,
        without needing root or rtprio ulimits; RTKit performs the
        privileged sched_setscheduler call on our behalf via polkit.
        Returns True on success, False on any failure (dbus/rtkit
        unavailable, denied, etc.) -- callers treat this as best-effort.
        """
        if not DBUS_AVAILABLE:
            return False
        try:
            bus = dbus.SystemBus()
            rtkit = bus.get_object(RTKIT_BUS_NAME, RTKIT_OBJECT_PATH)
            dbus.Interface(rtkit, RTKIT_BUS_NAME).MakeThreadRealtime(
                dbus.UInt64(native_tid), dbus.UInt32(priority)
            )
            return True
        except dbus.exceptions.DBusException as exc:
            logger.debug(f"RTKit real-time promotion failed: {exc}")
            return False

    def _request_exclusive_realtime_priority(self):
        """Exclusive mode talks to the raw ALSA hw: device directly, with no
        PipeWire mixing layer underneath to absorb scheduling jitter -- so
        the PortAudio callback thread (ordinary SCHED_OTHER by default) can
        miss its hardware deadline under any system CPU pressure (confirmed:
        opening a browser tab is enough to cause an audible dropout).
        PortAudio's ALSA backend doesn't request real-time scheduling for
        this thread itself, so we do it here via RTKit -- the same service
        PipeWire's own real-time thread already gets its priority from.

        Runs in a background thread: play() shouldn't block on this, and
        the callback thread's native id isn't known until it has fired at
        least once (see `_stamped_callback` in player_transport.py).
        """

        def _worker():
            deadline = time.monotonic() + REALTIME_PROMOTION_POLL_TIMEOUT
            while self._callback_native_tid is None and time.monotonic() < deadline:
                time.sleep(0.01)
            tid = self._callback_native_tid
            if tid is None:
                logger.debug("Realtime promotion: audio callback never fired in time")
                return
            if self._promote_callback_to_realtime(tid, REALTIME_PROMOTION_PRIORITY):
                logger.info("Exclusive-mode audio callback promoted to real-time priority")

        threading.Thread(target=_worker, daemon=True, name="RTKitPromote").start()

    def _get_device_config(self) -> dict:
        try:
            # An empty string ("Default Output Device" in the settings UI) or None
            # both mean "use the system default" — PortAudio treats an empty-string
            # device name as a substring match against every device, which raises
            # "Multiple output devices found for ''" when there's more than one.
            if self.current_device is None or self.current_device == "":
                info = self.sd.default.device
                if info is not None:
                    self.current_device = info[1]

            if self.exclusive_mode and self.current_device is not None:
                # Bit-perfect output is about grabbing the raw hw: node so
                # PipeWire/PulseAudio can't resample or mix it — it has nothing
                # to do with minimizing latency. Requesting "low" here gave
                # PortAudio very little internal buffer to absorb scheduling
                # jitter from the Python-side reader thread, which surfaced as
                # frequent underrun hitches once a hw: device was actually
                # grabbed. "high" keeps the same bit-perfect path with margin.
                return {
                    "device": self.current_device,
                    "latency": "high",
                }

            return {
                "device": self.current_device,
                "latency": "high",
                "clip_off": True,
            }
        except (OSError, self.sd.PortAudioError, IndexError, TypeError) as exc:
            logger.warning(f"Could not determine device config: {exc}")
            return {"device": None, "latency": "high"}

    def _suspend_gc_during_playback(self):
        """Turn off automatic cyclic garbage collection while an output stream
        is live.

        CPython's GC is stop-the-world: a collection freezes every Python
        thread -- including PortAudio's real-time callback thread -- for the
        full duration of the sweep. A large allocation/free burst anywhere in
        the process (opening a heavy view on the main thread, an artist merge
        or smart-playlist rebuild on a worker thread) trips a gen-2 collection
        long enough to blow a callback deadline, heard as a hitch even though
        the audio ring buffer is full. Reference-count reclamation is
        unaffected, so this only defers reclaiming reference cycles until
        _resume_gc() runs at stream close. gc.freeze() at startup (run.py)
        keeps that deferred sweep cheap.
        """
        if gc.isenabled():
            gc.disable()
            logger.debug("Automatic GC disabled for playback")

    def _resume_gc(self):
        """Re-enable automatic GC and run one explicit sweep, now that no
        real-time stream is open (so the collection pause can't be heard)."""
        if not gc.isenabled():
            gc.enable()
            gc.collect()
            logger.debug("Automatic GC re-enabled after playback")

    def _close_stream(self):
        if self.audio_stream is not None:
            stream_exceptions = (OSError, RuntimeError)
            if hasattr(self, "sd") and hasattr(self.sd, "PortAudioError"):
                stream_exceptions += (self.sd.PortAudioError,)
            try:
                self.audio_stream.stop()
                self.audio_stream.close()
            except stream_exceptions as exc:
                logger.debug(f"audio stream stop/close failed: {exc}")
            finally:
                self.audio_stream = None
        self._resume_gc()
        if self._suspended_sink_name is not None:
            self._suspend_sink(self._suspended_sink_name, False)
            self._suspended_sink_name = None

    def _restart_playback_if_active(self):
        if self.current_file and (self.playing or self.paused):
            saved_pos = self._position
            was_playing = self.playing and not self.paused
            self.stop()
            self.seek(saved_pos)
            if was_playing:
                self.play()

    def set_audio_device(self, device_name: str):
        if device_name == self.current_device:
            return
        self.current_device = device_name
        self.audio_device_changed.emit(device_name)
        self._restart_playback_if_active()

    def set_exclusive_mode(self, enabled: bool):
        if enabled == self.exclusive_mode:
            return
        self.exclusive_mode = enabled
        self._restart_playback_if_active()

    def get_audio_devices(self) -> list:
        try:
            return [
                {
                    "id": i,
                    "name": d["name"],
                    "default": d.get("default", False),
                    "direct": self.is_direct_output_device(d["name"]),
                }
                for i, d in enumerate(self.available_devices)
                if d["max_output_channels"] > 0
            ]
        except (KeyError, TypeError, AttributeError) as exc:
            logger.error(f"get_audio_devices error: {exc}")
            return []
