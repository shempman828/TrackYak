"""
Regression test for AlbumCoverArtMixin._pick_cover.

Motivating bug: saving the "last used art directory" preference only caught
AttributeError, so an OSError (disk full, permission denied) escaped and
aborted the whole cover-pick flow before the image was even read.
"""

from pathlib import Path

import src.album.album_cover_art_mixin as album_cover_art_mixin_module
from src.album.album_cover_art_mixin import AlbumCoverArtMixin


class _RaisingConfig:
    def get_last_art_dir(self):
        return str(Path.home())

    def set_last_art_dir(self, path):
        raise OSError("disk full")

    def save(self):
        pass


class _Host(AlbumCoverArtMixin):
    def __init__(self):
        self._config = _RaisingConfig()
        self.started = []

    def _start_cover_embed(self, cover_type, image_bytes):
        self.started.append((cover_type, image_bytes))


def test_pick_cover_continues_despite_directory_save_os_error(monkeypatch, tmp_path, qapp):
    image_path = tmp_path / "cover.png"
    image_path.write_bytes(b"fake-png-bytes")

    monkeypatch.setattr(
        album_cover_art_mixin_module.QFileDialog,
        "getOpenFileName",
        staticmethod(lambda *a, **k: (str(image_path), "")),
    )

    host = _Host()
    host._pick_cover("front")

    assert host.started == [("front", b"fake-png-bytes")]
