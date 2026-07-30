"""Tests for bug #244-followup: removing a track from an album (via the
album editor's Tracks tab) left other already-built tabs -- Genres, Track
Credits -- holding a stale snapshot of album.tracks taken when they were
built. The next genre/credit write against the removed track's stale
track_id then failed for real (not the dedup false-failure fixed in
add.py), because nothing told the editor those tabs needed rebuilding:
track membership changes aren't routed through RelationshipHelpers the way
credit/place/award changes are.

Covers:
  - DiscManagementView.refresh_view() (src/disc/disc_view.py) now emits a
    tracks_changed signal after every local reload, regardless of what
    triggered it (track edit/delete, disc add/remove/renumber).
  - TracksTab.build() (src/album/base_album_edit_tabs.py) wires that signal
    to the embedding AlbumEditor's own refresh_view, so Genres/Track
    Credits/Publishers && Places get rebuilt whenever the Tracks tab
    changes anything -- not just on RelationshipHelpers-mediated changes.
"""

from src.album.base_album_edit_tabs import TracksTab
from src.disc.disc_view import DiscManagementView

# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class _StubAlbum:
    def __init__(self, album_id=1, album_name="Test Album"):
        self.album_id = album_id
        self.album_name = album_name


class _StubGet:
    """DiscManagementView.load_data() asks for Track/AlbumVirtualTrack/Disc
    rows scoped to the album; an empty library is enough to exercise the
    refresh/signal wiring without a real DB."""

    def get_all_entities(self, model_name, **kwargs):
        return []


class _StubController:
    def __init__(self):
        self.get = _StubGet()


class _StubEditor:
    def __init__(self):
        self.album = _StubAlbum()
        self.controller = _StubController()
        self.refresh_view_calls = 0

    def refresh_view(self):
        self.refresh_view_calls += 1


# ---------------------------------------------------------------------------
# DiscManagementView.tracks_changed
# ---------------------------------------------------------------------------


def test_refresh_view_emits_tracks_changed(qapp):
    album = _StubAlbum()
    controller = _StubController()
    view = DiscManagementView(album, controller)

    emitted = []
    view.tracks_changed.connect(lambda: emitted.append(True))

    view.refresh_view()

    assert emitted == [True]


# ---------------------------------------------------------------------------
# TracksTab wiring
# ---------------------------------------------------------------------------


def test_tracks_tab_forwards_track_changes_to_editor_refresh(qapp):
    editor = _StubEditor()
    tab = TracksTab(editor)

    built = tab.build()
    try:
        disc_view = built.findChild(DiscManagementView)
        assert disc_view is not None

        disc_view.refresh_view()

        assert editor.refresh_view_calls == 1
    finally:
        built.deleteLater()
