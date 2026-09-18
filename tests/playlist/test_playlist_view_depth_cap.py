"""Regression test: playlists nested past MAX_HIERARCHY_DEPTH used to be
silently left out of the tree with no indication anything was hidden.
_build_tree now logs a warning when it hits the cap and descendants remain.
"""

import logging
from types import SimpleNamespace

import pytest

from src.playlist.playlist_view import PlaylistView


class _FakePlaylist(SimpleNamespace):
    pass


def _deep_chain(depth_count):
    """A straight-line chain of `depth_count` playlists, each parented to the last."""
    playlists = []
    parent_id = None
    for i in range(1, depth_count + 1):
        playlists.append(
            _FakePlaylist(
                playlist_id=i,
                playlist_name=f"level {i}",
                parent_id=parent_id,
                is_smart=False,
                track_count=0,
            )
        )
        parent_id = i
    return playlists


class _FakeController:
    def __init__(self, playlists):
        self._playlists = playlists
        self.get = SimpleNamespace(
            get_all_entities=lambda entity, **kwargs: (
                self._playlists if entity == "Playlist" else []
            ),
            get_entity_object=lambda entity, **kwargs: None,
        )
        self.update = SimpleNamespace()
        self.add = SimpleNamespace()
        self.delete = SimpleNamespace()


def test_playlists_past_max_depth_log_a_warning(qapp, caplog):
    # MAX_HIERARCHY_DEPTH is 8 -- 10 levels guarantees at least one hidden node.
    playlists = _deep_chain(PlaylistView.MAX_HIERARCHY_DEPTH + 2)

    with caplog.at_level(logging.WARNING, logger="musiclib"):
        view = PlaylistView(_FakeController(playlists))

    assert any("beyond the max hierarchy depth" in r.message for r in caplog.records)
    view.deleteLater()
