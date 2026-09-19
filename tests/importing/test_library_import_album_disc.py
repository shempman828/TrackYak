"""Regression test: AlbumImporter._get_or_create_disc must raise instead of
silently returning None when disc creation fails, matching how
_get_or_create_album/_create_track already treat a failed create_entity
call. Before the fix, a failed disc creation was indistinguishable from
"no disc number in metadata" (the legitimate case where None is expected).
"""

import pytest

from src.importing.library_import_album import AlbumImporter


class _FakeGet:
    def get_entity_object(self, *_a, **_k):
        return None


class _FakeAdd:
    def add_entity(self, *_a, **_k):
        return None


class _FakeController:
    def __init__(self):
        self.get = _FakeGet()
        self.add = _FakeAdd()


def test_failed_disc_creation_raises_instead_of_returning_none():
    importer = AlbumImporter(_FakeController())

    with pytest.raises(RuntimeError):
        importer._get_or_create_disc(album_id=1, metadata={"disc_number": 1})


def test_missing_disc_number_still_returns_none():
    importer = AlbumImporter(_FakeController())

    assert importer._get_or_create_disc(album_id=1, metadata={}) is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
