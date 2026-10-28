"""Artwork consistency: the per-album front/rear conflict scan and the
review dialog (import mode and scan worker).
"""

import io
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
from PIL import Image
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QMessageBox
import pytest
import soundfile as sf

from src.library.artwork import artwork_consistency_dialog as acd
from src.library.artwork.artwork_consistency_dialog import ArtworkConsistencyDialog
from src.library.artwork.library_artwork_consistency import ArtworkConsistencyChecker
from src.metadata.writers.vorbis.metadata_flac_file_writer import FlacFileWriter

# ---------------------------------------------------------------------------
# Regression tests for ArtworkConsistencyChecker (docs/specs/artwork_consistency_tool.md).
# ---------------------------------------------------------------------------


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
    return SimpleNamespace(get=SimpleNamespace(get_all_entities=lambda entity, load_options=None: list(albums)))


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
        tracks = [_track(aid * 10 + j, _make_flac(tmp_path / f"c{aid}_{j}.flac", art=IMG_A if j == 0 else IMG_B)) for j in range(2)]
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
    tracks = [_track(1, _make_flac(tmp_path / "solo.flac", art=IMG_A)), _track(2, "/music/not-embeddable.wav")]
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


# -- album_ids filter (import-reconciliation AC1) ----------------------—-
def test_album_ids_filter_restricts_scan_to_given_albums(tmp_path):
    # Both albums have a front conflict; the scan is scoped to album A only.
    a_tracks = [_track(1, _make_flac(tmp_path / "a1.flac", art=IMG_A)), _track(2, _make_flac(tmp_path / "a2.flac", art=IMG_B))]
    b_tracks = [_track(3, _make_flac(tmp_path / "b1.flac", art=IMG_A)), _track(4, _make_flac(tmp_path / "b2.flac", art=IMG_B))]
    albums = [_album(100, "A", a_tracks), _album(200, "B", b_tracks)]

    checker = ArtworkConsistencyChecker(_controller(albums), album_ids={100})
    summary = checker.run()

    assert summary["albums_scanned"] == 1
    assert [c["album_id"] for c in checker.conflicts] == [100]


# -- run() called twice on the same instance ---------------------------—-
def test_rerunning_same_checker_does_not_duplicate_conflicts(tmp_path):
    """A second run() on the same instance must not pile duplicate conflicts onto self.conflicts."""
    a_tracks = [_track(1, _make_flac(tmp_path / "a1.flac", art=IMG_A)), _track(2, _make_flac(tmp_path / "a2.flac", art=IMG_B))]
    checker = ArtworkConsistencyChecker(_controller([_album(1, "Split", a_tracks)]))

    first = checker.run()
    second = checker.run()

    assert first == second
    assert len(checker.conflicts) == 1


# -- album_ids default (import-reconciliation AC2) ---------------------—-
def test_no_album_ids_scans_every_album(tmp_path):
    a_tracks = [_track(1, _make_flac(tmp_path / "a1.flac", art=IMG_A)), _track(2, _make_flac(tmp_path / "a2.flac", art=IMG_B))]
    b_tracks = [_track(3, _make_flac(tmp_path / "b1.flac", art=IMG_A)), _track(4, _make_flac(tmp_path / "b2.flac", art=IMG_B))]
    albums = [_album(100, "A", a_tracks), _album(200, "B", b_tracks)]

    checker, summary = _run(albums)  # no album_ids

    assert summary["albums_scanned"] == 2
    assert {c["album_id"] for c in checker.conflicts} == {100, 200}


# ---------------------------------------------------------------------------
# Tests for ArtworkConsistencyDialog's import-reconciliation mode
# ---------------------------------------------------------------------------


IMG_X = _jpeg((200, 10, 10), 24)


IMG_Y = _jpeg((10, 10, 200), 40)


def _flac(path, art):
    sf.write(str(path), np.zeros(2205, dtype="float32"), 44100, format="FLAC")
    assert _WRITER.write_artwork(str(path), "front", art)
    return str(path)


def _conflict(album_id, name, entries):
    return {"album_id": album_id, "album_name": name, "role": "front", "tracks": entries}


@pytest.fixture
def two_variant_conflict(tmp_path):
    p1 = _flac(tmp_path / "t1.flac", IMG_X)
    p2 = _flac(tmp_path / "t2.flac", IMG_X)
    p3 = _flac(tmp_path / "t3.flac", IMG_Y)
    entries = [
        {"track_id": 1, "track_path": p1, "hash": "hx", "dimensions": {"width": 24, "height": 24}},
        {"track_id": 2, "track_path": p2, "hash": "hx", "dimensions": {"width": 24, "height": 24}},
        {"track_id": 3, "track_path": p3, "hash": "hy", "dimensions": {"width": 40, "height": 40}},
    ]
    return _conflict(7, "Split", entries)


# -- AC9 -------------------------------------------------------------------
def test_import_mode_populates_tree_without_scan(qapp, two_variant_conflict):
    dialog = ArtworkConsistencyDialog(SimpleNamespace(), parent=None, initial_conflicts=[two_variant_conflict])
    try:
        assert dialog._import_mode is True
        assert dialog._scan_worker is None
        assert not dialog._scan_btn.isVisibleTo(dialog)
        assert not dialog._cancel_btn.isVisibleTo(dialog)
        assert not dialog._progress_bar.isVisibleTo(dialog)

        assert dialog._tree.topLevelItemCount() == 1
        top = dialog._tree.topLevelItem(0)
        # one row per distinct variant: "hx" group + "hy" group
        assert top.childCount() == 2
        assert dialog.windowTitle() == "Reconcile Imported Artwork"
    finally:
        dialog.deleteLater()


# -- AC10 ----------------------------------------------------------------—-
class _FakeEmbedWorker(QObject):
    completed = Signal(list, object)
    error = Signal(str)
    finished = Signal()

    def __init__(self, album, tracks, cache, writer, role, image_bytes):
        super().__init__()
        self.args = (album, tracks, cache, writer, role, image_bytes)
        self.started = False

    def start(self):
        self.started = True


def test_import_mode_resolve_runs_embed_worker_and_invalidates(qapp, monkeypatch, two_variant_conflict):
    album = SimpleNamespace(album_id=7, album_name="Split", tracks=[SimpleNamespace(track_id=1)])
    controller = SimpleNamespace(get=SimpleNamespace(get_entity_object=lambda *_a, **_k: album))
    fake_cache = Mock()
    monkeypatch.setattr(acd, "get_artwork_cache", lambda: fake_cache)
    monkeypatch.setattr(acd, "CoverEmbedWorker", _FakeEmbedWorker)
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))

    dialog = ArtworkConsistencyDialog(controller, parent=None, initial_conflicts=[two_variant_conflict])
    try:
        conflict = dialog._conflicts[0]
        # resolve the "hy" variant (track 3)
        hy_entries = [e for e in conflict["tracks"] if e["hash"] == "hy"]
        dialog._resolve(conflict, "hy", hy_entries)

        worker = dialog._embed_worker
        assert isinstance(worker, _FakeEmbedWorker)
        assert worker.started is True
        _al, _tr, cache_arg, _wr, role_arg, image_bytes = worker.args
        assert role_arg == "front"
        assert cache_arg is fake_cache
        assert image_bytes == IMG_Y  # bytes read from track 3's file

        worker.completed.emit([], None)  # simulate a successful embed

        fake_cache.invalidate.assert_called_once_with(7)
        assert dialog._tree.topLevelItemCount() == 0
        assert dialog._conflicts == []
    finally:
        dialog.deleteLater()


# -- cancel must not leave the dialog's controls stuck --------------------
def test_scan_worker_emits_finished_even_when_cancelled(qapp, monkeypatch):
    """A cancelled scan must still emit `finished` (with partial results), or the dialog's Scan/Cancel controls never reset (regression)."""

    class _FakeChecker:
        def __init__(self, controller):
            self.conflicts = [{"album_id": 1, "role": "front"}]

        def run(self, progress_callback=None, is_cancelled=None):
            is_cancelled()  # simulate the scan noticing cancellation and stopping
            return {}

    monkeypatch.setattr(acd, "ArtworkConsistencyChecker", _FakeChecker)

    worker = acd.ArtworkConsistencyScanWorker(SimpleNamespace())
    worker.request_cancel()

    received = []
    worker.finished.connect(received.append)
    worker.run()

    assert received == [[{"album_id": 1, "role": "front"}]]
