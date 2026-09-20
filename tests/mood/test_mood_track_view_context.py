"""Tests for src/mood/mood_track_view_context.py."""

from types import SimpleNamespace

from PySide6.QtWidgets import QMenu

from src.mood.mood_track_view_context import add_remove_from_mood_action


def test_add_remove_from_mood_action_adds_action_connected_to_callback(qapp):
    menu = QMenu()
    track_view = SimpleNamespace(context_menu=menu)
    calls = []

    add_remove_from_mood_action(track_view, lambda: calls.append(1))

    actions = [action.text() for action in menu.actions()]
    assert actions.count("Remove from This Mood") == 1

    menu.actions()[-1].trigger()
    assert calls == [1]


def test_add_remove_from_mood_action_is_idempotent(qapp):
    """Regression: MoodDialog calls this on every tracks reload (e.g. each
    toggle of recursive mode), so repeated calls must not add duplicates."""
    menu = QMenu()
    track_view = SimpleNamespace(context_menu=menu)

    add_remove_from_mood_action(track_view, lambda: None)
    add_remove_from_mood_action(track_view, lambda: None)
    add_remove_from_mood_action(track_view, lambda: None)

    actions = [action.text() for action in menu.actions()]
    assert actions.count("Remove from This Mood") == 1


def test_add_remove_from_mood_action_without_context_menu_is_a_noop(qapp):
    track_view = SimpleNamespace()

    add_remove_from_mood_action(track_view, lambda: None)
