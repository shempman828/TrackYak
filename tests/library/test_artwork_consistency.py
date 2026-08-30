"""Regression tests for ArtworkConsistencyChecker (docs/specs/artwork_consistency_tool.md).

The old ArtworkConsistencyChecker was deleted unused (bugs.md 455); a
full-library scan then confirmed 20 real albums whose tracks disagree on
embedded front art, so it was resurrected as the engine behind the
Tools -> "Artwork Conflicts…" dialog. These tests exercise the real
detection path: real (tiny) FLAC files, the real FlacFileWriter embedding
real JPEG bytes, and the real ArtworkExtractor reading them back.

Each test maps 1:1 to an acceptance criterion:

  AC1  All tracks carrying byte-identical front art -> no conflict.
  AC2  Tracks split across two front images -> one front conflict, and the
       per-track hashes group the tracks into the two correct sets.
  AC3  Some tracks with front art, some with none -> one front conflict
       whose track list has a None-hash entry per artless track.
  AC4  An is_cancelled predicate that flips true mid-scan stops the scan.
  AC5  An album with a single embeddable track is skipped, never a conflict.
  AC6  rear/liner agreement is judged independently of front.
"""

import io
from types import SimpleNamespace

import numpy as np
from PIL import Image
import pytest
import soundfile as sf

from src.library.library_artwork_consistency import ArtworkConsistencyChecker
from src.metadata.metadata_flac_file_writer import FlacFileWriter

_WRITER = FlacFileWriter()


def _jpeg(color, size=24):
    buf = io.BytesIO()
    Image.new("RGB", (size, size), color).save(buf, format="JPEG")
    return buf.getvalue()


IMG_A = _jpeg((200, 10, 10), 24)
IMG_B = _jpeg((10, 10, 200), 40)
IMG_REAR = _jpeg((10, 200, 10), 16)


def _make_flac(path, art=None, rear_art=None):
    sf.write(str(path), np.zeros(2205, dtype="float32"), 44100, format="FLAC")
    if art is not None:
        assert _WRITER.write_artwork(str(path), "front", art)
    if rear_art is not None:
        assert _WRITER.write_artwork(str(path), "rear", rear_art)
    return str(path)


def _track(track_id, path):
    return SimpleNamespace(track_id=track_id, track_file_path=path)


def _album(album_id, name, tracks):
    return SimpleNamespace(album_id=album_id, album_name=name, tracks=tracks)


def _controller(albums):
    return SimpleNamespace(get=SimpleNamespace(get_all_entities=lambda entity: list(albums)))


def _run(albums, **kwargs):
    checker = ArtworkConsistencyChecker(_controller(albums))
    summary = checker.run(**kwargs)
    return checker, summary


# -- AC1 ------------------------------------------------------------------
def test_consistent_album_has_no_conflict(tmp_path):
    tracks = [_track(i, _make_flac(tmp_path / f"a{i}.flac", art=IMG_A)) for i in range(1, 4)]
    checker, summary = _run([_album(1, "Agree", tracks)])

    assert checker.conflicts == []
    assert summary["conflicts_found"] == 0
    assert summary["albums_scanned"] == 1


# -- AC2 ----------------------------------------------------------------—-
def test_two_distinct_front_images_flagged_and_grouped(tmp_path):
    a_tracks = [_track(i, _make_flac(tmp_path / f"a{i}.flac", art=IMG_A)) for i in (1, 2, 3)]
    b_tracks = [_track(i, _make_flac(tmp_path / f"b{i}.flac", art=IMG_B)) for i in (4, 5)]
    checker, summary = _run([_album(7, "Split", a_tracks + b_tracks)])

    assert summary["conflicts_found"] == 1
    (conflict,) = checker.conflicts
    assert conflict["album_id"] == 7
    assert conflict["role"] == "front"

    hash_by_track = {t["track_id"]: t["hash"] for t in conflict["tracks"]}
    assert hash_by_track[1] == hash_by_track[2] == hash_by_track[3]
    assert hash_by_track[4] == hash_by_track[5]
    assert hash_by_track[1] != hash_by_track[4]
    assert None not in hash_by_track.values()


# -- AC3 ----------------------------------------------------------------—-
def test_present_absent_split_flagged_with_none_entries(tmp_path):
    with_art = [_track(i, _make_flac(tmp_path / f"w{i}.flac", art=IMG_A)) for i in (1, 2)]
    without = [_track(i, _make_flac(tmp_path / f"n{i}.flac")) for i in (3, 4, 5)]
    checker, _ = _run([_album(9, "Partial", with_art + without)])

    (conflict,) = checker.conflicts
    assert conflict["role"] == "front"
    hash_by_track = {t["track_id"]: t["hash"] for t in conflict["tracks"]}
    assert hash_by_track[1] == hash_by_track[2]
    assert hash_by_track[3] is None
    assert hash_by_track[4] is None
    assert hash_by_track[5] is None


# -- AC4 ----------------------------------------------------------------—-
def test_cancel_predicate_stops_scan_early(tmp_path):
    albums = []
    for aid in range(1, 6):
        tracks = [
            _track(
                aid * 10 + j,
                _make_flac(tmp_path / f"c{aid}_{j}.flac", art=IMG_A if j == 0 else IMG_B),
            )
            for j in range(2)
        ]
        albums.append(_album(aid, f"Album {aid}", tracks))

    seen = []

    def progress(scanned, total):
        seen.append(scanned)

    # is_cancelled is polled at the top of each album's turn; it trips once
    # two albums have been reported through progress, so the 3rd never runs.
    _, summary = _run(albums, progress_callback=progress, is_cancelled=lambda: len(seen) >= 2)

    assert summary["albums_scanned"] == 2
    assert seen == [1, 2]  # albums 3-5 never processed


# -- AC5 ----------------------------------------------------------------—-
def test_single_embeddable_track_album_skipped(tmp_path):
    # The .wav track is filtered out, leaving a single embeddable track.
    tracks = [
        _track(1, _make_flac(tmp_path / "solo.flac", art=IMG_A)),
        _track(2, "/music/not-embeddable.wav"),
    ]
    checker, summary = _run([_album(3, "Solo", tracks)])

    assert checker.conflicts == []
    assert summary["albums_skipped_insufficient_tracks"] == 1
    assert summary["albums_scanned"] == 1


# -- AC6 ----------------------------------------------------------------—-
def test_rear_consistency_judged_independently_of_front(tmp_path):
    # front disagrees, rear is identical on every track.
    t1 = _track(1, _make_flac(tmp_path / "r1.flac", art=IMG_A, rear_art=IMG_REAR))
    t2 = _track(2, _make_flac(tmp_path / "r2.flac", art=IMG_B, rear_art=IMG_REAR))
    checker, summary = _run([_album(5, "Rears agree", [t1, t2])])

    assert summary["conflicts_found"] == 1
    assert {c["role"] for c in checker.conflicts} == {"front"}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
