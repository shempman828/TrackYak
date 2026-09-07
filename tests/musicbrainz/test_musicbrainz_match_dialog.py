"""Tests for MusicBrainzMatchDialog (the "pick one of N MusicBrainz matches"
picker shared by the album / track / artist lookup flows).

Regression: the dialog used to open pinned to its 520px minimum width no
matter how long the candidate labels were, so canonical-release rows
(title + artist + date + country + label + catalog# + track count) got
elided instead of the dialog widening to fit them.
"""

import gc
import time

from PySide6.QtWidgets import QApplication

from src.musicbrainz.musicbrainz_core import MBCandidate
from src.musicbrainz.musicbrainz_match_dialog import _DETACHED_WORKERS, MusicBrainzMatchDialog


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


# ---------------------------------------------------------------------------
# Regression: dismissing the dialog while its search worker is still running.
#
# The dialog detaches the running worker (setParent(None)) so a late
# finished/error signal can't call back into dead widgets. That also hands
# the QThread's lifetime to Python -- without a strong reference held
# somewhere, the wrapper was garbage-collected the moment the dialog (its
# last referrer) went away, destroying the still-running C++ QThread and
# aborting the process with "QThread: Destroyed while thread is still
# running". _detach_running_worker now parks the worker in _DETACHED_WORKERS
# until its run() actually returns.
# ---------------------------------------------------------------------------


def test_detached_running_worker_survives_dialog_destruction(qapp):
    _DETACHED_WORKERS.clear()

    def slow_search():
        time.sleep(0.5)
        return []

    dialog = MusicBrainzMatchDialog("artist 'x'", search_call=slow_search, parent=None)
    worker = dialog._worker
    assert worker is not None and worker.isRunning()

    dialog.reject()
    dialog.deleteLater()
    del dialog
    gc.collect()

    # Strong ref parked while the thread is still in flight, and the C++
    # parent link severed.
    assert worker in _DETACHED_WORKERS
    assert worker.parent() is None

    # Once run() returns, the parked reference is released.
    assert worker.wait(3000)
    qapp.processEvents()
    assert worker not in _DETACHED_WORKERS
