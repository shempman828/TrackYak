"""Coverage for src/player/waveform_seekbar.py — see docs/specs/waveform-scrubber.md
and docs/specs/waveform_display_mode.md.

AC6  release emits seek_requested once, mapped from pixel x; nothing with
     duration 0
AC7  set_position moves the playhead without emitting, and is frozen at the
     drag position while dragging
AC8  set_peaks(None) → plain-bar render + still seekable; set_peaks(arr) →
     envelope render

Waveform display mode (docs/specs/waveform_display_mode.md):
AC2  paint-time mode check treats anything other than "log" as linear
AC5  _log_scale fixes 0 -> 0 and +-1 -> +-1
AC6  _log_scale expands near-full-scale gaps, compresses near-silence gaps
AC7  "log" mode visibly changes the rendered envelope vs. "linear"
AC8  "linear" mode never invokes the curve — a true no-op
"""

import numpy as np
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QImage, QMouseEvent
import pytest

from src.player.core.waveform_cache import N_BUCKETS
import src.player.ui.waveform_seekbar as waveform_seekbar_module
from src.player.ui.waveform_seekbar import WaveformSeekBar, _log_scale


@pytest.fixture
def bar(qapp):
    w = WaveformSeekBar()
    w.setGeometry(0, 0, 400, 40)
    w.setEnabled(True)
    return w


def _mouse(w, handler, etype, x, button, buttons):
    pos = QPointF(x, 5)
    handler(QMouseEvent(etype, pos, pos, button, buttons, Qt.NoModifier))


def _press(w, x):
    _mouse(w, w.mousePressEvent, QEvent.MouseButtonPress, x, Qt.LeftButton, Qt.LeftButton)


def _move(w, x):
    _mouse(w, w.mouseMoveEvent, QEvent.MouseMove, x, Qt.NoButton, Qt.LeftButton)


def _release(w, x):
    _mouse(w, w.mouseReleaseEvent, QEvent.MouseButtonRelease, x, Qt.LeftButton, Qt.NoButton)


def _distinct_colours(w):
    img = QImage(w.size(), QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    w.render(img)
    return {img.pixel(x, y) for x in range(0, w.width(), 4) for y in range(0, w.height(), 4)}


# AC6 ---------------------------------------------------------------------------


def test_release_emits_single_seek_mapped_from_pixel(bar):
    bar.set_duration(200_000)
    got = []
    bar.seek_requested.connect(got.append)

    _press(bar, 100)
    _release(bar, 100)

    assert len(got) == 1
    expected = round(100 / bar.width() * 200_000)
    assert abs(got[0] - expected) <= 200_000 / N_BUCKETS + 1


def test_no_seek_emitted_when_duration_is_zero(bar):
    bar.set_duration(0)
    got = []
    bar.seek_requested.connect(got.append)

    _press(bar, 150)
    _release(bar, 150)

    assert got == []


# AC7 ---------------------------------------------------------------------------


def test_set_position_moves_playhead_without_emitting(bar):
    bar.set_duration(100_000)
    got = []
    bar.seek_requested.connect(got.append)

    bar.set_position(50_000)

    assert bar._position_ms == 50_000
    assert got == []


def test_set_position_is_ignored_while_dragging(bar):
    bar.set_duration(100_000)
    _press(bar, 40)
    assert bar.is_dragging
    drag_ms = bar._drag_ms

    bar.set_position(99_000)

    assert bar._position_ms != 99_000
    assert bar._played_ms() == drag_ms


# AC8 ---------------------------------------------------------------------------


def test_fallback_bar_renders_and_stays_seekable(bar):
    bar.set_duration(100_000)
    bar.set_position(50_000)
    bar.set_peaks(None)

    assert bar._peaks is None
    assert len(_distinct_colours(bar)) > 1

    got = []
    bar.seek_requested.connect(got.append)
    _press(bar, 200)
    _release(bar, 200)
    assert len(got) == 1


def test_envelope_path_selected_with_real_peaks(bar):
    bar.set_duration(100_000)
    bar.set_position(50_000)

    peaks = np.zeros((N_BUCKETS, 2), dtype=np.int8)
    peaks[:, 0] = -80
    peaks[:, 1] = 80
    bar.set_peaks(peaks)

    assert bar._peaks is not None
    assert len(_distinct_colours(bar)) > 1


def test_malformed_peaks_fall_back_to_plain_bar(bar):
    bar.set_duration(100_000)
    bar.set_peaks(np.zeros((10, 3), dtype=np.int8))
    assert bar._peaks is None


# Waveform display mode (linear vs. perceptual/log) -----------------------------


@pytest.fixture
def waveform_mode(monkeypatch):
    def _set(mode):
        monkeypatch.setattr(
            waveform_seekbar_module.app_config, "get_waveform_display_mode", lambda: mode
        )

    return _set


def _reject_log_scale(monkeypatch):
    def _boom(*_a, **_k):
        raise AssertionError("_log_scale must not be called")

    monkeypatch.setattr(waveform_seekbar_module, "_log_scale", _boom)


def _render(w):
    img = QImage(w.size(), QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    w.render(img)
    return img


def _loud_peaks():
    peaks = np.full((N_BUCKETS, 2), 115, dtype=np.int8)
    peaks[:, 0] = -115
    return peaks


# AC5 ---------------------------------------------------------------------------


def test_log_scale_fixes_zero_and_full_scale_in_place():
    out = _log_scale(np.array([0.0, 1.0, -1.0]))
    assert out == pytest.approx([0.0, 1.0, -1.0], abs=1e-6)


# AC6 ---------------------------------------------------------------------------


def test_log_scale_expands_top_and_compresses_bottom():
    values = np.array([0.05, 0.10, 0.9, 0.99])
    lo_gap_scaled = _log_scale(values[1:2])[0] - _log_scale(values[0:1])[0]
    hi_gap_scaled = _log_scale(values[3:4])[0] - _log_scale(values[2:3])[0]

    assert hi_gap_scaled > 0.09  # linear gap between 0.9 and 0.99
    assert lo_gap_scaled < 0.05  # linear gap between 0.05 and 0.10


def test_log_scale_is_monotonically_increasing():
    xs = np.linspace(-1, 1, 201)
    ys = _log_scale(xs)
    assert np.all(np.diff(ys) > 0)


# AC7 ---------------------------------------------------------------------------


def test_log_mode_renders_a_different_envelope_than_linear(bar, waveform_mode):
    bar.set_duration(100_000)
    bar.set_position(50_000)
    peaks = _loud_peaks()

    waveform_mode("linear")
    bar.set_peaks(peaks)
    linear_img = _render(bar)

    waveform_mode("log")
    bar.update()
    log_img = _render(bar)

    assert linear_img != log_img


# AC8 ---------------------------------------------------------------------------


def test_linear_mode_never_invokes_the_curve(bar, waveform_mode, monkeypatch):
    _reject_log_scale(monkeypatch)
    waveform_mode("linear")
    bar.set_duration(100_000)
    bar.set_position(50_000)
    bar.set_peaks(_loud_peaks())

    assert len(_distinct_colours(bar)) > 1  # rendered fine, curve never called


# AC2 ---------------------------------------------------------------------------


def test_unknown_display_mode_falls_back_to_linear(bar, waveform_mode, monkeypatch):
    _reject_log_scale(monkeypatch)
    waveform_mode("banana")
    bar.set_duration(100_000)
    bar.set_position(50_000)
    bar.set_peaks(_loud_peaks())

    assert len(_distinct_colours(bar)) > 1  # rendered fine, curve never called
