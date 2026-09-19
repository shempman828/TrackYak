"""Tests for src/mood/mood_dialog.py."""

from types import SimpleNamespace

from src.mood.mood_dialog import MoodDialog


class _Track:
    def __init__(self, track_id):
        self.track_id = track_id
        self.track_name = f"Track {track_id}"


class _Association:
    def __init__(self, track):
        self.track = track


class _StubGet:
    def __init__(self, associations):
        self._associations = associations

    def get_all_entities(self, model_name, **kwargs):
        if model_name == "MoodTrackAssociation":
            return self._associations
        return []


class _StubController:
    def __init__(self, associations=()):
        self.get = _StubGet(list(associations))


def _mood(mood_id=1, name="Happy"):
    return SimpleNamespace(mood_id=mood_id, mood_name=name, mood_description="")


def test_load_associated_tracks_shows_success_label_not_error(qapp):
    """Regression: load_associated_tracks used to always overwrite the
    success label with "Error loading tracks" because that line sat
    outside the try/except block, so it ran even when loading succeeded.
    """
    tracks = [_Track(1), _Track(2)]
    controller = _StubController([_Association(t) for t in tracks])

    dlg = MoodDialog(mood_data=_mood(), controller=controller)
    try:
        assert dlg.track_view.info_label.text() == "Showing 2 tracks"
    finally:
        dlg.deleteLater()


def test_ok_button_disabled_until_name_entered(qapp):
    controller = _StubController()
    dlg = MoodDialog(controller=controller)
    try:
        ok_button = dlg._ok_button
        assert ok_button.isEnabled() is False

        dlg.mood_name_edit.setText("   ")
        assert ok_button.isEnabled() is False

        dlg.mood_name_edit.setText("Chill")
        assert ok_button.isEnabled() is True

        dlg.mood_name_edit.setText("")
        assert ok_button.isEnabled() is False
    finally:
        dlg.deleteLater()
