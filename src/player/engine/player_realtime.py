"""player_realtime.py — RTKit real-time thread promotion for the exclusive-mode feeder, and GC suspension while an output stream is live."""

# Split out of player_device.py: thread-scheduling/GC policy, not device
# selection. Expects the host class to provide: self._stream_generation,
# self._feeder_generation, self._feeder_native_tid, self.exclusive_mode.

import gc
import os
import threading
import time

from src.foundation.logger_config import logger

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
# starve it and produce a real device-level underrun. 20 doesn't out-prioritize
# those SCHED_FIFO-50 threads either (RTKit won't grant more), but it does put
# us ahead of all ordinary SCHED_OTHER contention, which is the most RTKit will
# ever hand out to an unprivileged desktop app here.
REALTIME_PROMOTION_PRIORITY = 20
REALTIME_PROMOTION_POLL_TIMEOUT = 0.5  # seconds to wait for the feeder to start


def demote_thread_from_realtime(native_tid: int) -> bool:
    """Move `native_tid` back to the normal SCHED_OTHER scheduler."""
    # No privilege needed to move a same-uid thread *out* of a real-time
    # policy (unlike moving one into it), so this goes straight through
    # sched_setscheduler instead of back through RTKit/D-Bus. Best-effort:
    # a dead/unknown tid, or a non-Linux platform, is a silent no-op.
    if not hasattr(os, "sched_setscheduler"):
        return False
    try:
        os.sched_setscheduler(native_tid, os.SCHED_OTHER, os.sched_param(0))
        return True
    except OSError as exc:
        logger.debug(f"Real-time demotion of tid {native_tid} failed: {exc}")
        return False


class PlayerRealtimeMixin:
    """RTKit real-time thread promotion for the exclusive-mode feeder, plus
    GC suspension for the duration of an open output stream."""

    def _promote_callback_to_realtime(self, native_tid: int, priority: int) -> bool:
        """Ask RTKit to give `native_tid` real-time (SCHED_RR) scheduling."""
        # Same system service PipeWire/JACK use for this -- RTKit performs
        # the privileged sched_setscheduler call on our behalf via polkit,
        # so no root or rtprio ulimits are needed. Best-effort: callers
        # treat any failure (dbus/rtkit unavailable, denied, etc.) the same
        # as "skip it".
        if not DBUS_AVAILABLE:
            return False
        try:
            bus = dbus.SystemBus()
            rtkit = bus.get_object(RTKIT_BUS_NAME, RTKIT_OBJECT_PATH)
            dbus.Interface(rtkit, RTKIT_BUS_NAME).MakeThreadRealtime(dbus.UInt64(native_tid), dbus.UInt32(priority))
            return True
        except dbus.exceptions.DBusException as exc:
            logger.debug(f"RTKit real-time promotion failed: {exc}")
            return False

    def _request_exclusive_realtime_priority(self):
        """Promote the exclusive-mode feeder thread to real-time (SCHED_RR) priority via RTKit."""
        # Exclusive mode talks to the raw ALSA hw: device with no PipeWire
        # mixing layer to absorb scheduling jitter, so a GC pause or CPU
        # pressure can delay the feeder past the hw: device's shallow
        # buffer. Runs in a background thread since play() shouldn't block
        # on it, and the feeder's native id isn't set until the feeder
        # thread starts (see _feeder_loop in player_feeder.py).
        generation = self._stream_generation

        def _worker():
            deadline = time.monotonic() + REALTIME_PROMOTION_POLL_TIMEOUT
            while time.monotonic() < deadline:
                if self._feeder_generation == generation and self._feeder_native_tid is not None:
                    break
                time.sleep(0.01)
            tid = self._feeder_native_tid
            if self._feeder_generation != generation or tid is None:
                logger.debug("Realtime promotion: feeder for this stream never started in time")
                return
            # _feeder_native_tid is never cleared when a feeder exits, so
            # without this generation check a bare "not None" would promote
            # a dead/TID-recycled previous feeder, or a replacement feeder
            # from a rapid exclusive-mode toggle -- leaving a real-time
            # thread on the normal PipeWire path. The matching demotion
            # happens in the feeder's own teardown (see _feeder_loop's
            # finally in player_feeder.py).
            if self._stream_generation != generation or not self.exclusive_mode:
                logger.debug("Realtime promotion: stream/exclusive state changed, skipping")
                return
            if self._promote_callback_to_realtime(tid, REALTIME_PROMOTION_PRIORITY):
                logger.info("Exclusive-mode audio feeder promoted to real-time priority")

        threading.Thread(target=_worker, daemon=True, name="RTKitPromote").start()

    def _suspend_gc_during_playback(self):
        """Turn off automatic cyclic garbage collection while an output stream is live."""
        # CPython's GC is stop-the-world and freezes every thread --
        # including the feeder parked in stream.write() -- for the sweep's
        # duration. A large allocation/free burst anywhere in the process
        # (opening a heavy view, an artist merge, a smart-playlist rebuild)
        # can trip a gen-2 collection long enough to delay the next write
        # past the device buffer, heard as a hitch even with a full ring
        # buffer. Reference counting is unaffected, so this only defers
        # reclaiming reference cycles until _resume_gc() runs at stream
        # close; gc.freeze() at startup (run.py) keeps that sweep cheap.
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

    def _collect_gc_if_paused(self):
        """Run one explicit GC sweep while paused, without re-enabling automatic collection."""
        # _suspend_gc_during_playback() disables cyclic GC for as long as an
        # output stream stays open, and play()'s same-format track changes
        # reuse that stream indefinitely -- _resume_gc() is only reached via
        # _close_stream() (stop/format change/exit), so an uninterrupted
        # session can run for hours without a single collection. Reference
        # cycles (e.g. the now-playing art-slideshow's per-slide
        # QPropertyAnimation, recreated every few seconds during playback)
        # only get reclaimed by the cyclic collector, so they simply pile
        # up. Pausing already aborts the stream and parks the feeder
        # thread, so a sweep here is free.
        if not gc.isenabled():
            gc.collect()
            logger.debug("GC swept during pause (automatic collection still off)")
