import pytest
from PySide6.QtCore import QAbstractAnimation

from src.core.splash_screen import StartupSplash


@pytest.fixture
def splash(qapp):
    widget = StartupSplash(min_duration_ms=10_000)
    yield widget
    widget.close()


def test_finish_before_min_duration_defers_exit(splash):
    splash.finish()

    assert splash.finish_requested is True
    assert splash.exit_animation.state() != QAbstractAnimation.State.Running


def test_min_duration_elapsed_starts_deferred_exit(splash):
    splash.finish()
    splash._on_min_duration_elapsed()

    assert splash._min_duration_elapsed is True
    assert splash.exit_animation.state() == QAbstractAnimation.State.Running


def test_min_duration_elapsed_without_finish_does_not_exit(splash):
    splash._on_min_duration_elapsed()

    assert splash._min_duration_elapsed is True
    assert splash.exit_animation.state() != QAbstractAnimation.State.Running


def test_finish_after_min_duration_starts_exit_immediately(splash):
    splash._on_min_duration_elapsed()
    splash.finish()

    assert splash.exit_animation.state() == QAbstractAnimation.State.Running


def test_opacity_property_updates_window_opacity(splash):
    splash.opacity = 0.5

    assert splash.get_opacity() == pytest.approx(0.5)
    assert splash.windowOpacity() == pytest.approx(0.5, abs=1e-2)


def test_scale_property_round_trip(splash):
    splash.scale = 0.95

    assert splash.get_scale() == pytest.approx(0.95)


def test_update_status_stores_message_and_progress(splash):
    splash.update_status("Loading...", progress=42)

    assert splash._message == "Loading..."
    assert splash._progress == 42
