"""Regression tests for bug #247: an extra track on a MusicBrainz release
that has no counterpart in the user's local album must be clearly flagged as
an error, not blended in as an ordinary "no suggestion" unmatched row.

Root cause: _match_tracks() iterates over MB's tracks and computes
remaining_mb/remaining_local, but the review dialog's UI never distinguished
"this MB track failed to auto-match but some local track might still cover
it" from "there is no local track left at all -- this MB track simply isn't
in the user's album." Both rendered as a plain "No suggestion" row.
"""

from types import SimpleNamespace

from PySide6.QtWidgets import QLabel

from src.album.album_musicbrainz_review_dialog import AlbumMusicBrainzReviewDialog
from src.musicbrainz.musicbrainz_client import MBReleaseDetail, MBReleaseTrack


def _local_track(track_id, track_number, title):
    return SimpleNamespace(
        track_id=track_id,
        track_number=track_number,
        track_name=title,
        side=None,
        disc=None,
        track_barcode=None,
        disc_id=1,
        artist_roles=[],
        places=[],
    )


def _mb_track(position, title):
    return MBReleaseTrack(
        disc_number=1,
        disc_title=None,
        track_number=position,
        side=None,
        title=title,
        recording_mbid=f"mbid-{position}",
        absolute_position=position,
    )


def _album(tracks):
    return SimpleNamespace(
        tracks=tracks,
        discs=[],
        album_id=1,
        album_name="Test Album",
        album_aliases=[],
        album_roles=[],
    )


def _detail(tracks):
    return MBReleaseDetail(release_group_mbid="rg-1", tracks=tracks)


def _find_error_label(dialog):
    return next(
        (
            w
            for w in dialog.findChildren(QLabel)
            if "ERROR" in w.text().upper() or "error" in w.text().lower()
        ),
        None,
    )


def test_extra_mb_track_flagged_as_error_when_all_local_tracks_match(qapp):
    """8 local tracks all match perfectly, but MB's release has a 9th track
    -- that 9th track has zero possible local candidates and must be called
    out as an error rather than an ordinary unmatched row."""
    local_tracks = [_local_track(i, i, f"Track {i}") for i in range(1, 9)]
    mb_tracks = [_mb_track(i, f"Track {i}") for i in range(1, 9)]
    mb_tracks.append(_mb_track(9, "Track 9"))

    dialog = AlbumMusicBrainzReviewDialog(
        controller=SimpleNamespace(),
        album=_album(local_tracks),
        detail=_detail(mb_tracks),
        aliases=[],
    )

    assert len(dialog._matched) == 8
    assert dialog._guaranteed_missing == 1
    assert len(dialog._remaining_mb) == 1
    assert dialog._remaining_local_options == []

    warning = _find_error_label(dialog)
    assert warning is not None
    assert "9" in warning.text() or "1" in warning.text()


def test_no_error_flagged_when_track_counts_reconcile(qapp):
    """Same track count on both sides -- nothing is guaranteed missing, so
    no error banner should appear even if matching required manual review."""
    local_tracks = [_local_track(i, i, f"Track {i}") for i in range(1, 9)]
    mb_tracks = [_mb_track(i, f"Track {i}") for i in range(1, 9)]

    dialog = AlbumMusicBrainzReviewDialog(
        controller=SimpleNamespace(),
        album=_album(local_tracks),
        detail=_detail(mb_tracks),
        aliases=[],
    )

    assert dialog._guaranteed_missing == 0
    assert dialog._remaining_mb == []
    assert _find_error_label(dialog) is None


def test_unmatched_table_marks_error_row_confidence_text(qapp):
    """The unmatched-tracks table's confidence column must say something
    distinct from 'No suggestion' for a track that has no possible local
    candidate at all."""
    local_tracks = [_local_track(1, 1, "Track 1")]
    mb_tracks = [_mb_track(1, "Track 1"), _mb_track(2, "Track 2")]

    dialog = AlbumMusicBrainzReviewDialog(
        controller=SimpleNamespace(),
        album=_album(local_tracks),
        detail=_detail(mb_tracks),
        aliases=[],
    )

    assert dialog._guaranteed_missing == 1

    table = next(
        w
        for w in dialog.findChildren(object)
        if w.__class__.__name__ == "QTableWidget"
    )
    conf_item = table.item(0, 2)
    assert "ERROR" in conf_item.text()
