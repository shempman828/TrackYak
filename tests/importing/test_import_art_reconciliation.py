"""Tests for the end-of-import album-art reconciliation step
(docs/specs/import_art_reconciliation.md).

After the file loop, ImportWorker scans the albums it added tracks to for
tracks that disagree on embedded cover art and emits `art_conflicts` with
the conflict list, then `finished`. A cancelled import still reconciles
the albums it touched before the cancel.

Each test maps to a numbered acceptance criterion in that spec:

  AC3   touched_album_ids accumulates IMPORTED albums only (not SKIPPED).
  AC4   TrackImporter.last_imported_album_id is reset to None on a
        SKIPPED / FAILED add_track call.
  AC5   Two different front images among imported tracks -> one front
        conflict on art_conflicts; finished still emitted after.
  AC6   Incoming art vs art already on the album's existing tracks ->
        one front conflict covering all tracks.
  AC7   All art byte-identical -> art_conflicts emits an empty list.
  AC8   Cancelled import still runs the scoped scan and emits conflicts.
  AC11  A .wav-only import emits an empty list (never hashed/compared).
"""

import io
from types import SimpleNamespace

import numpy as np
from PIL import Image
import pytest
import soundfile as sf

from src.importing import library_import
from src.importing.library_import import ImportResult, ImportWorker, TrackImporter
from src.metadata.metadata_flac_file_writer import FlacFileWriter

_WRITER = FlacFileWriter()


def _jpeg(color, size=24):
    buf = io.BytesIO()
    Image.new("RGB", (size, size), color).save(buf, format="JPEG")
    return buf.getvalue()


IMG_X = _jpeg((200, 10, 10), 24)
IMG_Y = _jpeg((10, 10, 200), 40)


def _make_flac(path, art=None):
    sf.write(str(path), np.zeros(2205, dtype="float32"), 44100, format="FLAC")
    if art is not None:
        assert _WRITER.write_artwork(str(path), "front", art)
    return str(path)


def _make_wav(path):
    sf.write(str(path), np.zeros(2205, dtype="float32"), 44100, format="WAV")
    return str(path)


def _track(track_id, file_path):
    return SimpleNamespace(track_id=track_id, track_file_path=file_path)


def _album(album_id, name, tracks):
    return SimpleNamespace(album_id=album_id, album_name=name, tracks=tracks)


class _FakeGet:
    def __init__(self, albums):
        self._albums = list(albums)
        self.session = SimpleNamespace(expunge_all=lambda: None)

    def get_all_entities(self, entity, **_kw):
        return list(self._albums) if entity == "Album" else []

    def get_entity_object(self, *_a, **_k):
        return None


class _FakeController:
    def __init__(self, albums=()):
        self.get = _FakeGet(albums)


class _FakeImporter:
    """Stand-in for TrackImporter: `script` is one (ImportResult, album_id)
    per add_track call, in order. Optionally cancels the worker after N
    calls to exercise the cancel path."""

    def __init__(self, script, worker=None, cancel_after=None):
        self._script = list(script)
        self._i = 0
        self._worker = worker
        self._cancel_after = cancel_after
        self.last_imported_album_id = None
        self.seen_paths = []

    def process_path(self, path):
        return [path]

    def add_track(self, file_path):
        self.seen_paths.append(file_path)
        result, album_id = self._script[self._i]
        self._i += 1
        self.last_imported_album_id = album_id if result is ImportResult.IMPORTED else None
        if self._cancel_after is not None and self._i >= self._cancel_after and self._worker:
            self._worker.request_cancel()
        return result


def _run_worker(tmp_path, script, albums=(), n_files=None, cancel_after=None):
    n_files = n_files if n_files is not None else len(script)
    paths = [_make_wav(tmp_path / f"in{i}.wav") for i in range(n_files)]
    worker = ImportWorker(_FakeController(albums), paths)
    worker.importer = _FakeImporter(script, worker=worker, cancel_after=cancel_after)

    conflicts_seen = []
    finished_seen = []
    worker.art_conflicts.connect(conflicts_seen.append)
    worker.finished.connect(finished_seen.append)

    worker.run()
    return worker, conflicts_seen, finished_seen


# -- AC3 ---------------------------------------------------------------—-
def test_touched_album_ids_accumulates_imported_only(tmp_path):
    worker, _c, finished = _run_worker(
        tmp_path,
        script=[
            (ImportResult.IMPORTED, 10),
            (ImportResult.IMPORTED, 20),
            (ImportResult.SKIPPED, None),
        ],
    )
    assert worker.touched_album_ids == {10, 20}
    assert finished == [2]


# -- AC4 ---------------------------------------------------------------—-
def test_last_imported_album_id_reset_on_skipped(monkeypatch):
    # An existing-track lookup that returns truthy -> add_track short-circuits
    # to SKIPPED before touching an album.
    controller = SimpleNamespace(get=SimpleNamespace(get_entity_object=lambda *_a, **_k: object()))
    importer = TrackImporter(controller)
    importer.last_imported_album_id = 999

    assert importer.add_track("/music/already-there.flac") is ImportResult.SKIPPED
    assert importer.last_imported_album_id is None


def test_last_imported_album_id_reset_on_failed(monkeypatch):
    controller = SimpleNamespace(get=SimpleNamespace(get_entity_object=lambda *_a, **_k: None))

    class _NoMetadata:
        def extract_metadata(self, _fp):
            return {}

    monkeypatch.setattr(library_import, "MetadataExtractor", _NoMetadata)
    importer = TrackImporter(controller)
    importer.last_imported_album_id = 999

    assert importer.add_track("/music/broken.flac") is ImportResult.FAILED
    assert importer.last_imported_album_id is None


# -- AC5 ---------------------------------------------------------------—-
def test_two_front_images_emit_one_conflict_then_finished(tmp_path, qapp):
    tracks = [
        _track(1, _make_flac(tmp_path / "a1.flac", art=IMG_X)),
        _track(2, _make_flac(tmp_path / "a2.flac", art=IMG_X)),
        _track(3, _make_flac(tmp_path / "a3.flac", art=IMG_Y)),
    ]
    album = _album(10, "Split", tracks)
    _worker, conflicts_seen, finished = _run_worker(
        tmp_path, script=[(ImportResult.IMPORTED, 10)] * 3, albums=[album]
    )

    assert len(conflicts_seen) == 1
    (payload,) = conflicts_seen
    assert [c["role"] for c in payload] == ["front"]
    assert payload[0]["album_id"] == 10
    hash_by_track = {t["track_id"]: t["hash"] for t in payload[0]["tracks"]}
    assert hash_by_track[1] == hash_by_track[2] != hash_by_track[3]
    assert finished == [3]


# -- AC6 ---------------------------------------------------------------—-
def test_incoming_art_conflicts_with_existing_album_tracks(tmp_path, qapp):
    # Track 1 was already in the library (image X); the import adds 2 and 3
    # (image Y). The post-import scan sees all three.
    existing = _track(1, _make_flac(tmp_path / "old.flac", art=IMG_X))
    new1 = _track(2, _make_flac(tmp_path / "new1.flac", art=IMG_Y))
    new2 = _track(3, _make_flac(tmp_path / "new2.flac", art=IMG_Y))
    album = _album(42, "Grew", [existing, new1, new2])

    _w, conflicts_seen, _f = _run_worker(
        tmp_path, script=[(ImportResult.IMPORTED, 42), (ImportResult.IMPORTED, 42)], albums=[album]
    )

    (payload,) = conflicts_seen
    assert [c["role"] for c in payload] == ["front"]
    hash_by_track = {t["track_id"]: t["hash"] for t in payload[0]["tracks"]}
    assert set(hash_by_track) == {1, 2, 3}
    assert hash_by_track[2] == hash_by_track[3] != hash_by_track[1]


# -- AC7 ---------------------------------------------------------------—-
def test_identical_art_emits_empty_list(tmp_path, qapp):
    tracks = [_track(i, _make_flac(tmp_path / f"s{i}.flac", art=IMG_X)) for i in range(1, 4)]
    album = _album(10, "Agree", tracks)
    _w, conflicts_seen, _f = _run_worker(
        tmp_path, script=[(ImportResult.IMPORTED, 10)] * 3, albums=[album]
    )
    assert conflicts_seen == [[]]


# -- AC8 ---------------------------------------------------------------—-
def test_cancelled_import_still_reconciles_touched_albums(tmp_path, qapp):
    tracks = [
        _track(1, _make_flac(tmp_path / "c1.flac", art=IMG_X)),
        _track(2, _make_flac(tmp_path / "c2.flac", art=IMG_Y)),
    ]
    album = _album(77, "Cancelled", tracks)
    # Three input files, but the worker cancels itself after the first
    # add_track; the loop breaks, yet run() still reconciles album 77.
    worker, conflicts_seen, finished = _run_worker(
        tmp_path, script=[(ImportResult.IMPORTED, 77)] * 3, albums=[album], cancel_after=1
    )

    assert worker.is_cancelled
    assert worker.touched_album_ids == {77}
    (payload,) = conflicts_seen
    assert [c["role"] for c in payload] == ["front"]
    assert len(finished) == 1


# -- AC11 --------------------------------------------------------------—-
def test_wav_only_import_emits_empty_list(tmp_path, qapp):
    tracks = [_track(1, _make_wav(tmp_path / "w1.wav")), _track(2, _make_wav(tmp_path / "w2.wav"))]
    album = _album(10, "Wavs", tracks)
    _w, conflicts_seen, _f = _run_worker(
        tmp_path, script=[(ImportResult.IMPORTED, 10)] * 2, albums=[album]
    )
    assert conflicts_seen == [[]]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
