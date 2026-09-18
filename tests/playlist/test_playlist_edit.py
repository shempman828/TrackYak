"""Regression test for EditPlaylist pre-filling the wrong attribute.

EditPlaylist read `playlist.description`, which does not exist on the
Playlist model (the real field is `playlist_description`). The description
box was always blank, so saving without retyping it erased the real value.
"""

from types import SimpleNamespace

from src.playlist.playlist_edit import EditPlaylist


class _FakeController:
    pass


def test_description_field_is_prefilled_from_playlist_description(qapp):
    playlist = SimpleNamespace(
        playlist_id=1,
        playlist_name="My Playlist",
        playlist_description="Some existing description",
        is_smart=False,
    )

    dialog = EditPlaylist(_FakeController(), playlist)

    assert dialog.desc_edit.toPlainText() == "Some existing description"
