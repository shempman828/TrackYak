"""Tests for ArtworkConsistencyDialog's import-reconciliation mode
(docs/specs/import_art_reconciliation.md).

When constructed with `initial_conflicts`, the dialog skips the
library-scan controls and populates its tree directly; the
"Use for all tracks" resolve path is the same code the Tools-menu scan
uses.

  AC9   initial_conflicts -> tree populated, no scan, scan controls hidden.
  AC10  "Use for all tracks" in import mode runs a CoverEmbedWorker for the
        album/role and, on completion, removes the row and invalidates the
        cache (embed worker + cache stubbed).
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

from src.library import artwork_consistency_dialog as acd
from src.library.artwork_consistency_dialog import ArtworkConsistencyDialog
from src.metadata.metadata_flac_file_writer import FlacFileWriter

_WRITER = FlacFileWriter()


def _jpeg(color, size=24):
    buf = io.BytesIO()
    Image.new("RGB", (size, size), color).save(buf, format="JPEG")
    return buf.getvalue()


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
    dialog = ArtworkConsistencyDialog(
        SimpleNamespace(), parent=None, initial_conflicts=[two_variant_conflict]
    )
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


def test_import_mode_resolve_runs_embed_worker_and_invalidates(
    qapp, monkeypatch, two_variant_conflict
):
    album = SimpleNamespace(album_id=7, album_name="Split", tracks=[SimpleNamespace(track_id=1)])
    controller = SimpleNamespace(get=SimpleNamespace(get_entity_object=lambda *_a, **_k: album))
    fake_cache = Mock()
    monkeypatch.setattr(acd, "get_artwork_cache", lambda: fake_cache)
    monkeypatch.setattr(acd, "CoverEmbedWorker", _FakeEmbedWorker)
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))

    dialog = ArtworkConsistencyDialog(
        controller, parent=None, initial_conflicts=[two_variant_conflict]
    )
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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
