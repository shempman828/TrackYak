"""Tests for src/artist/artist_edit_basic.py::BasicTab.

Currently focused on the Sort Name field added per
docs/specs/artist_sort_name.md: it loads from the artist row, saves only
when changed via collect_changes(), and MB enrichment fills it only when
the widget is blank (set_if_empty).
"""

from types import SimpleNamespace

import pytest

from src.artist.artist_edit_basic import BasicTab


class _FakeGet:
    def get_all_entities(self, model_name):
        return []


class _FakeController:
    def __init__(self):
        self.get = _FakeGet()


def _artist(**overrides):
    base = {
        "artist_id": 1,
        "artist_name": "Miles Davis",
        "sort_name": None,
        "disambiguation": None,
        "isgroup": 0,
        "gender": None,
        "religion": None,
        "types": [],
        "begin_year": None,
        "begin_month": None,
        "begin_day": None,
        "end_year": None,
        "end_month": None,
        "end_day": None,
        "profile_pic_path": None,
        "MBID": None,
        "wikipedia_url": None,
        "age": None,
        "career_span": None,
        "track_count": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture
def tab(qapp):
    widget = BasicTab(_FakeController(), _artist())
    yield widget
    widget.deleteLater()


# AC4 -- editor loads existing value --------------------------------------
def test_load_populates_sort_name(tab):
    tab.load(_artist(sort_name="Björk Guðmundsdóttir"))
    assert tab.sort_name_edit.text() == "Björk Guðmundsdóttir"


def test_load_none_shows_empty(tab):
    tab.load(_artist(sort_name=None))
    assert tab.sort_name_edit.text() == ""


# AC5 -- editor saves a change ------------------------------------------
def test_collect_changes_emits_typed_sort_name(tab):
    tab.load(_artist(sort_name=None))
    tab.sort_name_edit.setText("Davis, Miles")
    assert tab.collect_changes()["sort_name"] == "Davis, Miles"


def test_collect_changes_omits_untouched_sort_name(tab):
    tab.load(_artist(sort_name="Davis, Miles"))
    assert "sort_name" not in tab.collect_changes()


def test_collect_changes_emits_none_when_cleared(tab):
    tab.load(_artist(sort_name="Davis, Miles"))
    tab.sort_name_edit.clear()
    changes = tab.collect_changes()
    assert "sort_name" in changes and changes["sort_name"] is None


# AC6 -- MB fill respects existing input --------------------------------
def test_set_if_empty_fills_blank_sort_name(tab):
    tab.load(_artist(sort_name=None))
    tab.set_if_empty({"sort_name": "Hendrix, Jimi"})
    assert tab.sort_name_edit.text() == "Hendrix, Jimi"


def test_set_if_empty_leaves_filled_sort_name_untouched(tab):
    tab.load(_artist(sort_name=None))
    tab.sort_name_edit.setText("My Own Value")
    tab.set_if_empty({"sort_name": "From, MusicBrainz"})
    assert tab.sort_name_edit.text() == "My Own Value"
