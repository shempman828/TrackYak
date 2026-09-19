"""Regression test: TrackImporter._scan_directory must keep files already
found when rglob later raises OSError (e.g. an unreadable subdirectory).
Before the fix, any OSError mid-scan discarded the whole result and
returned an empty list, silently dropping the entire import.
"""

from pathlib import Path

import pytest

from src.importing.library_import import TrackImporter


def test_scan_directory_keeps_partial_results_on_oserror(tmp_path, monkeypatch):
    good1 = tmp_path / "a.mp3"
    good1.touch()
    sub = tmp_path / "sub"
    sub.mkdir()
    good2 = sub / "b.mp3"
    good2.touch()

    def flaky_rglob(self, _pattern):
        yield good1
        yield good2
        raise OSError("Permission denied")

    monkeypatch.setattr(Path, "rglob", flaky_rglob)

    importer = TrackImporter(controller=None)
    result = importer._scan_directory(tmp_path)

    assert set(result) == {str(good1), str(good2)}


def test_scan_directory_returns_empty_list_when_nothing_found_before_error(tmp_path, monkeypatch):
    def flaky_rglob(self, _pattern):
        raise OSError("Permission denied")
        yield  # pragma: no cover - makes this a generator function

    monkeypatch.setattr(Path, "rglob", flaky_rglob)

    importer = TrackImporter(controller=None)
    assert importer._scan_directory(tmp_path) == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
