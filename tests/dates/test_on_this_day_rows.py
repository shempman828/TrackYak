"""Regression coverage for the "On This Day" / day-detail event rows.

The old rows repeated themselves (a bold name, a type label, then a
"Album released: <name>" line restating both) and never showed the artist or
cover art. These tests pin the cleaned-up behaviour.
"""

from types import SimpleNamespace

import pytest

from src.dates.dates_calendar import _build_event_row, _event_meta_line, _type_label
from src.dates.dates_view import TimelineView


def _fake_view(entities: dict) -> SimpleNamespace:
    """Stand-in for a TimelineView: the get_*_dates methods only reach through
    self.controller, so an unbound call with this is enough (and skips the
    QWidget machinery)."""
    return SimpleNamespace(
        controller=SimpleNamespace(
            get=SimpleNamespace(get_all_entities=lambda name, **_: entities.get(name, []))
        )
    )


class _ExplodingArtistNames:
    """A fake track whose primary_artist_names access raises -- used to prove
    the dateless fast-path never triggers the relationship walk that froze the
    Timeline view on open."""

    def __init__(self, **fields):
        self.__dict__.update(fields)

    @property
    def primary_artist_names(self):
        raise AssertionError("primary_artist_names accessed for a dateless track")


# ── wording ──────────────────────────────────────────────────────────────────


def test_type_labels_distinguish_people_from_bands():
    assert _type_label("artist_born") == "Artist Born"
    assert _type_label("artist_died") == "Artist Died"
    assert _type_label("band_formed") == "Band Formed"
    assert _type_label("band_formed", 3) == "Bands Formed"
    assert _type_label("band_dissolved") == "Band Broke Up"


def test_meta_line_appends_the_credited_artist():
    line = _event_meta_line(
        {"type": "album_release", "entity_name": "Wish You Were Here", "artist": "Pink Floyd"}
    )
    assert line == "Album Release · Pink Floyd"


def test_meta_line_drops_artist_when_it_only_repeats_the_title():
    # An artist event's "name" is the artist itself -- no "· Queen" tail.
    assert _event_meta_line({"type": "band_formed", "entity_name": "Queen"}) == "Band Formed"
    assert (
        _event_meta_line({"type": "band_formed", "entity_name": "Queen", "artist": "queen"})
        == "Band Formed"
    )


# ── row rendering ────────────────────────────────────────────────────────────


def test_event_row_has_name_and_meta_only_no_redundant_description(qapp):
    from PySide6.QtWidgets import QLabel

    row = _build_event_row(
        {
            "type": "album_release",
            "entity": "Album",
            "entity_name": "Abbey Road",
            "artist": "The Beatles",
        }
    )
    texts = [w.text() for w in row.findChildren(QLabel)]

    assert texts == ["Abbey Road", "Album Release · The Beatles"]
    assert not any("Album released:" in t for t in texts)


# ── event-dict plumbing ─────────────────────────────────────────────────────


def test_album_dates_carry_artist_and_orm_ref_not_a_description():
    album = SimpleNamespace(
        release_year=1969,
        release_month=9,
        release_day=26,
        album_id=1,
        album_name="Abbey Road",
        album_artist_names="The Beatles",
        art_is_explicit=0,
    )
    (entry,) = TimelineView.get_album_dates(_fake_view({"Album": [album]}))

    assert entry["artist"] == "The Beatles"
    assert entry["album"] is album
    assert "description" not in entry


# ── Timeline-load thread lock: track date extraction ────────────────────────


def test_track_dates_query_is_filtered_to_rows_with_a_year():
    """The GUI-thread freeze on Timeline open was a full tracks scan that then
    walked artist_roles per row. The query must push the year filter into SQL."""
    captured = {}

    def fake_get_all_entities(name, **kwargs):
        captured["name"] = name
        captured["kwargs"] = kwargs
        return []

    view = SimpleNamespace(
        controller=SimpleNamespace(get=SimpleNamespace(get_all_entities=fake_get_all_entities))
    )

    TimelineView.get_track_dates(view)

    assert captured["name"] == "Track"
    rendered = str(captured["kwargs"]["filter_expression"])
    for column in ("recorded_year", "composed_year", "first_performed_year", "remaster_year"):
        assert column in rendered


def test_track_dates_skip_relationship_walk_for_dateless_rows():
    """Even if a dateless row slips past the SQL filter, the per-row artist
    lookup (the expensive part) must not run for it."""
    dateless = _ExplodingArtistNames(
        recorded_year=None,
        composed_year=None,
        first_performed_year=None,
        remaster_year=None,
        track_id=1,
        track_name="untitled",
    )

    assert TimelineView.get_track_dates(_fake_view({"Track": [dateless]})) == []


def test_track_dates_emit_one_entry_per_populated_year_field():
    track = SimpleNamespace(
        recorded_year=1971,
        recorded_month=11,
        recorded_day=8,
        composed_year=1970,
        composed_month=None,
        composed_day=None,
        first_performed_year=None,
        remaster_year=2011,
        track_id=7,
        track_name="Stairway to Heaven",
        primary_artist_names="Led Zeppelin",
    )

    entries = TimelineView.get_track_dates(_fake_view({"Track": [track]}))

    assert {e["type"] for e in entries} == {"track_recorded", "track_composed", "track_remastered"}
    assert all(e["artist"] == "Led Zeppelin" for e in entries)


@pytest.mark.parametrize(
    ("isgroup", "begin_type", "end_type"),
    [(0, "artist_born", "artist_died"), (1, "band_formed", "band_dissolved")],
)
def test_artist_dates_pick_type_by_isgroup(isgroup, begin_type, end_type):
    artist = SimpleNamespace(
        begin_year=1970,
        begin_month=None,
        begin_day=None,
        end_year=1991,
        end_month=None,
        end_day=None,
        artist_id=1,
        artist_name="Queen" if isgroup else "Freddie Mercury",
        isgroup=isgroup,
    )
    begin, end = TimelineView.get_artist_dates(_fake_view({"Artist": [artist]}))

    assert begin["type"] == begin_type
    assert end["type"] == end_type
    assert "description" not in begin and "description" not in end
