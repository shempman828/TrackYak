"""Regression test for view_artist_tracks() silently doing nothing when an
artist has no tracks in the current role -- the user right-clicks "View
Tracks" and sees no feedback at all. See src/role/role_detail_tab.py
RoleDetailTab.view_artist_tracks().
"""

from unittest.mock import patch

from src.role.role_detail_tab import RoleDetailTab


class _StubGet:
    """No albums/tracks/artists for any query -- exercises the empty path."""

    def get_all_entities(self, *args, **kwargs):
        return []

    def get_entity_object(self, *args, **kwargs):
        return None


class _Controller_dt:
    def __init__(self):
        self.get = _StubGet()


def test_view_artist_tracks_with_no_tracks_shows_status_message(qapp):
    controller = _Controller_dt()
    tab = RoleDetailTab(controller, role_id=1)

    with patch("src.role.role_detail_tab.show_status_message") as mock_status:
        tab.view_artist_tracks(artist_id=1)

    mock_status.assert_called_once()
    assert "No tracks found" in mock_status.call_args.args[1]
