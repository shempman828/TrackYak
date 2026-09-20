"""Adds the "Remove from This Mood" action to a BaseTrackView's existing
context menu, for use by MoodDialog's associated-tracks tab."""

_REMOVE_ACTION_TEXT = "Remove from This Mood"


def add_remove_from_mood_action(track_view, remove_callback):
    """Ensure track_view's context menu has a "Remove from This Mood" action.

    Safe to call repeatedly (e.g. on every tracks reload): it only adds the
    action, and its separator, the first time.
    """
    if not hasattr(track_view, "context_menu"):
        return

    existing_actions = [action.text() for action in track_view.context_menu.actions()]
    if _REMOVE_ACTION_TEXT in existing_actions:
        return

    track_view.context_menu.addSeparator()
    remove_action = track_view.context_menu.addAction(_REMOVE_ACTION_TEXT)
    remove_action.triggered.connect(remove_callback)
