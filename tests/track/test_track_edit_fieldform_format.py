"""Tests for FieldFormTab's read-only value formatter (_format_readonly).

Regression: file paths carry their identifying part (artist/album/title) at
the end, so the generic 80-char front-truncation collapsed every track's
Track File Path down to the same useless "/music/Music Library/Music/..."
prefix. track_file_path is now exempt from truncation (its display label
word-wraps).
"""

from src.db.db_mapping_tracks import TRACK_FIELDS
from src.track.track_edit_fieldform import _format_readonly

_LONG_PATH = (
    "/music/Music Library/Music/Soloists of the Chamber Orchestra/"
    "Zelenka: Six Trio Sonatas Z 181/07 - Trio Sonata No. 2 in G Minor: III. Andante.flac"
)


def test_long_track_file_path_is_not_truncated():
    cfg = TRACK_FIELDS["track_file_path"]
    assert len(_LONG_PATH) > 80
    assert _format_readonly(_LONG_PATH, cfg, "track_file_path") == _LONG_PATH


def test_two_long_paths_stay_distinguishable():
    cfg = TRACK_FIELDS["track_file_path"]
    base = "/music/Music Library/Music/Frank Sinatra/In the Wee Small Hours/"
    a = base + "01 - In the Wee Small Hours of the Morning.flac"
    b = base + "12 - This Love of Mine.flac"
    assert len(a) > 80 and len(b) > 80
    assert _format_readonly(a, cfg, "track_file_path") != _format_readonly(
        b, cfg, "track_file_path"
    )


def test_other_long_string_fields_still_truncate():
    long_text = "x" * 200
    out = _format_readonly(long_text, None, "some_other_field")
    assert out == "x" * 77 + "..."


def test_short_track_file_path_unchanged():
    cfg = TRACK_FIELDS["track_file_path"]
    short = "/music/a.flac"
    assert _format_readonly(short, cfg, "track_file_path") == short
