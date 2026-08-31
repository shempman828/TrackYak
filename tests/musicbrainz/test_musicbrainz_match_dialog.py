"""Tests for MusicBrainzMatchDialog (the "pick one of N MusicBrainz matches"
picker shared by the album / track / artist lookup flows).

Regression: the dialog used to open pinned to its 520px minimum width no
matter how long the candidate labels were, so canonical-release rows
(title + artist + date + country + label + catalog# + track count) got
elided instead of the dialog widening to fit them.
"""

from PySide6.QtWidgets import QApplication

from src.musicbrainz.musicbrainz_core import MBCandidate
from src.musicbrainz.musicbrainz_match_dialog import MusicBrainzMatchDialog


def _dialog_with_candidates(qapp, candidates):
    # search_call returns [] so the real worker settles fast and harmlessly;
    # the sizing path is driven directly off _on_search_finished.
    dialog = MusicBrainzMatchDialog("album 'x'", search_call=lambda: [], parent=None)
    if dialog._worker is not None:
        dialog._worker.wait(2000)
    dialog._on_search_finished(candidates)
    return dialog


def test_widens_to_fit_long_candidate_labels(qapp):
    short = [MBCandidate(id="1", label="Short")]
    long_label = (
        "A Very Long Canonical Release Title — Some Artist — 1999 — "
        "US — Big Label Records — CAT-12345 — 12 tracks"
    )
    long_candidates = [MBCandidate(id="2", label=long_label)]

    narrow = _dialog_with_candidates(qapp, short).width()
    wide = _dialog_with_candidates(qapp, long_candidates).width()

    assert wide > narrow


def test_never_shrinks_below_minimum(qapp):
    dialog = _dialog_with_candidates(qapp, [MBCandidate(id="1", label="x")])
    assert dialog.width() >= dialog.minimumWidth()


def test_clamped_to_available_screen_width(qapp):
    absurd = "z" * 4000
    dialog = _dialog_with_candidates(qapp, [MBCandidate(id="1", label=absurd)])

    screen = dialog.screen() or QApplication.primaryScreen()
    assert dialog.width() <= int(screen.availableGeometry().width() * 0.9) + 1
