"""Unit tests for calculate_gain_factor()'s ReplayGain math and peak clamp.

Regression: the peak limiter only clamped when `track_peak` was truthy, so a
track with a stored `track_gain` but a zero/missing `track_peak` (incomplete
DB metadata) got an unclamped gain_factor applied -- capable of badly
over-amplifying audio instead of being bounded like every other track.
"""

from types import SimpleNamespace

from src.player.core import gain_calculator
from src.player.core.gain_calculator import REPLAYGAIN_REFERENCE_LUFS, calculate_gain_factor


def _controller(track_gain, track_peak):
    track = SimpleNamespace(track_gain=track_gain, track_peak=track_peak)

    class _Get:
        def get_entity_object(self, *args, **kwargs):
            return track

    return SimpleNamespace(get=_Get())


def test_disabled_normalization_returns_unity_gain():
    controller = _controller(track_gain=6.0, track_peak=0.5)
    assert calculate_gain_factor(controller, "file.flac", False, -14.0) == 1.0


def test_no_current_file_returns_unity_gain():
    controller = _controller(track_gain=6.0, track_peak=0.5)
    assert calculate_gain_factor(controller, None, True, -14.0) == 1.0


def test_gain_is_clamped_when_peak_data_is_present_and_would_clip():
    # A large positive track_gain would push max_output well past 0.99;
    # the peak limiter must bring it back down to the 0.99 ceiling.
    controller = _controller(track_gain=20.0, track_peak=0.9)
    gain_factor = calculate_gain_factor(controller, "file.flac", True, REPLAYGAIN_REFERENCE_LUFS)
    assert gain_factor * 0.9 <= 0.99 + 1e-9


def test_gain_is_still_clamped_when_peak_is_zero():
    # track_peak == 0.0 is falsy but must not disable the limiter --
    # otherwise a large gain_db applies with no ceiling at all.
    controller = _controller(track_gain=20.0, track_peak=0.0)
    gain_factor = calculate_gain_factor(controller, "file.flac", True, REPLAYGAIN_REFERENCE_LUFS)
    assert gain_factor <= 0.99 + 1e-9


def test_gain_is_still_clamped_when_peak_is_missing(monkeypatch):
    # get_track_gain_from_db() only ever returns a peak alongside a gain, but
    # calculate_gain_factor()'s own clamp must not rely on that -- it should
    # stay bounded even if track_peak comes back None.
    monkeypatch.setattr(gain_calculator, "get_track_gain_from_db", lambda *a: (20.0, None))
    gain_factor = calculate_gain_factor(controller=None, current_file="file.flac", normalization_enabled=True, normalization_target=REPLAYGAIN_REFERENCE_LUFS)
    assert gain_factor <= 0.99 + 1e-9


def test_quiet_track_is_lifted_without_hitting_the_clamp():
    # A small positive gain with a low peak should be lifted, not clamped.
    controller = _controller(track_gain=3.0, track_peak=0.3)
    gain_factor = calculate_gain_factor(controller, "file.flac", True, REPLAYGAIN_REFERENCE_LUFS)
    assert 1.0 < gain_factor < (10.0 ** (3.0 / 20.0)) + 1e-9
