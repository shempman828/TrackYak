"""Regression coverage for artwork_cache.all_album_tracks
(src/image/artwork_cache.py): the album-art embed pass
(AlbumCoverArtMixin._start_cover_embed) and _pick_representative_track must
see every track that belongs to an album, including any reachable only
through Album.discs -> Disc.tracks -- a track detached from its album but
left sitting on one of its discs. If such a track is skipped, "Remove album
art" never strips its file and the stale cover later resurfaces as art on
whatever album the track is added to next.
"""

from types import SimpleNamespace

from src.image.artwork_cache import all_album_tracks


def _track(tid):
    return SimpleNamespace(track_id=tid)


def test_unions_direct_and_disc_only_tracks():
    t1, t2, t3 = _track(1), _track(2), _track(3)
    disc = SimpleNamespace(tracks=[t2, t3])
    album = SimpleNamespace(tracks=[t1], discs=[disc])

    ids = sorted(t.track_id for t in all_album_tracks(album))
    assert ids == [1, 2, 3]


def test_deduplicates_tracks_reachable_both_ways():
    shared = _track(1)
    disc = SimpleNamespace(tracks=[shared, _track(2)])
    album = SimpleNamespace(tracks=[shared], discs=[disc])

    result = all_album_tracks(album)
    assert sorted(t.track_id for t in result) == [1, 2]


def test_direct_instance_wins_on_dedup():
    direct = _track(1)
    disc = SimpleNamespace(tracks=[_track(1)])
    album = SimpleNamespace(tracks=[direct], discs=[disc])

    result = all_album_tracks(album)
    assert result == [direct]
    assert result[0] is direct


def test_handles_missing_or_empty_relationships():
    assert all_album_tracks(SimpleNamespace(tracks=None, discs=None)) == []
    assert all_album_tracks(SimpleNamespace()) == []
    album = SimpleNamespace(tracks=[_track(5)], discs=[])
    assert [t.track_id for t in all_album_tracks(album)] == [5]


def test_skips_tracks_without_an_id():
    album = SimpleNamespace(tracks=[_track(7), SimpleNamespace()], discs=[])
    assert [t.track_id for t in all_album_tracks(album)] == [7]
