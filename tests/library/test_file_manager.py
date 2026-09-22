"""Regression tests for FileOrganizer progress reporting.

The organize-files worker used to emit each track's name as the progress
text ("Analyzing: <track name>", "Moving: <track name>"). With large
libraries the worker runs fast enough that this became an unreadable blur
of names flashing past, with no benefit to the user. Progress text must
report counts instead, never a track name.
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from src.library.file_manager import FileOrganizer


def test_analyze_progress_reports_counts_not_track_names(qapp):
    tracks = [SimpleNamespace(track_name="Secret Song", track_file_path=None), SimpleNamespace(track_name="Another Song", track_file_path=None)]
    organizer = FileOrganizer(root=Path("/tmp"), controller=Mock())
    messages = []
    organizer.progress_updated.connect(lambda percent, text: messages.append(text))

    organizer._analyze_organization(tracks)

    assert messages == ["Analyzing files… 1/2", "Analyzing files… 2/2"]
    assert not any(track.track_name in msg for track in tracks for msg in messages)
