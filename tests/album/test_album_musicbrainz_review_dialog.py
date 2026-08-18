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

from src.album.album_musicbrainz_review_dialog import (
    AlbumMusicBrainzReviewDialog,
    _track_scalar_update,
)
from src.musicbrainz.musicbrainz_release import (
    MBFounderRelation,
    MBLabelInfo,
    MBReleaseDetail,
    MBReleaseTrack,
)


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


def _album(tracks, publishers=None):
    return SimpleNamespace(
        tracks=tracks,
        discs=[],
        album_id=1,
        album_name="Test Album",
        album_aliases=[],
        album_roles=[],
        publishers=publishers or [],
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


def test_side_b_track_renumbers_flat_local_track_on_auto_match(qapp):
    """Bug #253: importing a vinyl release with sides must replace flat
    local numbering (1-16) with MB's side-relative scheme (A1-8, B1-8).
    Matching uses absolute_position to find the right local row (side A
    has 5 tracks, so B1's absolute_position is 6 -> matches local track
    6), but the *written* track_number must be MB's side-relative value
    (1), with side="B" -- turning local track 6 into "Side B, Track 1".
    This must happen on plain auto-match (force=False), since a flat vs.
    side-relative numbering mismatch is expected, not a sign of a bad
    match."""
    local_tracks = [_local_track(i, i, f"Track {i}") for i in range(1, 6)]
    local_track_6 = _local_track(6, 6, "Track 6")
    local_tracks.append(local_track_6)

    mb_track_b1 = MBReleaseTrack(
        disc_number=1,
        disc_title=None,
        track_number=1,
        side="B",
        title="Side B Track 1",
        recording_mbid="mbid-b1",
        absolute_position=6,
    )

    dialog = AlbumMusicBrainzReviewDialog(
        controller=SimpleNamespace(),
        album=_album(local_tracks),
        detail=_detail([mb_track_b1]),
        aliases=[],
    )

    assert dialog._matched.get(id(mb_track_b1)) is local_track_6

    update = _track_scalar_update(local_track_6, mb_track_b1, {}, None)

    assert update is not None
    assert update["track_number"] == 1
    assert update["side"] == "B"


def test_title_beats_same_position_wrong_track(qapp):
    """Track name must be the dominant matching signal: a local track that
    merely sits at the same position as the MusicBrainz track ("Black Dog"
    at position 5) but is a different, only loosely-similar song must lose
    out to the real match sitting one slot away ("Blackbird" at position 6)
    rather than being confidently auto-matched on position alone."""
    local_tracks = [
        _local_track(1, 1, "Come Together"),
        _local_track(2, 2, "Something"),
        _local_track(3, 3, "Octopus's Garden"),
        _local_track(4, 4, "Oh Darling"),
        _local_track(5, 5, "Black Dog"),
        _local_track(6, 6, "Blackbird"),
    ]
    mb_track = _mb_track(5, "Blackbird")

    dialog = AlbumMusicBrainzReviewDialog(
        controller=SimpleNamespace(),
        album=_album(local_tracks),
        detail=_detail([mb_track]),
        aliases=[],
    )

    matched = dialog._matched.get(id(mb_track))
    assert matched is not None
    assert matched.track_name == "Blackbird"
    assert matched.track_id == 6


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


def _label(mbid="label-mbid", name="Atlantic Records", founders=None):
    return MBLabelInfo(mbid=mbid, name=name, founders=founders or [])


def test_publisher_section_shown_for_new_label(qapp):
    local_tracks = [_local_track(1, 1, "Track 1")]
    detail = _detail([_mb_track(1, "Track 1")])
    detail.labels = [_label()]

    dialog = AlbumMusicBrainzReviewDialog(
        controller=SimpleNamespace(),
        album=_album(local_tracks),
        detail=detail,
        aliases=[],
    )

    assert len(dialog._label_checks) == 1
    cb, label = dialog._label_checks[0]
    assert label.name == "Atlantic Records"
    assert cb.isChecked()


def test_publisher_section_shows_founders(qapp):
    local_tracks = [_local_track(1, 1, "Track 1")]
    detail = _detail([_mb_track(1, "Track 1")])
    detail.labels = [
        _label(founders=[MBFounderRelation(mbid="a-1", name="Ahmet Ertegun")])
    ]

    dialog = AlbumMusicBrainzReviewDialog(
        controller=SimpleNamespace(),
        album=_album(local_tracks),
        detail=detail,
        aliases=[],
    )

    cb, _label_obj = dialog._label_checks[0]
    assert "Ahmet Ertegun" in cb.text()


def test_publisher_already_linked_by_mbid_is_not_shown_again(qapp):
    local_tracks = [_local_track(1, 1, "Track 1")]
    detail = _detail([_mb_track(1, "Track 1")])
    label = _label(mbid="already-linked-mbid")
    detail.labels = [label]
    existing_publisher = SimpleNamespace(
        MBID="already-linked-mbid", publisher_name="Atlantic Records"
    )

    dialog = AlbumMusicBrainzReviewDialog(
        controller=SimpleNamespace(),
        album=_album(local_tracks, publishers=[existing_publisher]),
        detail=detail,
        aliases=[],
    )

    assert dialog._label_checks == []
