"""Tests for the per-track auto-fill of is_explicit in the track edit
dialog's Lyrics tab (docs/specs/explicit_content_detection.md, Option 2).
Each test maps 1:1 to a numbered acceptance criterion in that spec.

is_explicit is an existing, fully-editable manual checkbox field
(LyricsTab, src/track/track_edit_lyrics.py) with no separate manual/auto
provenance flag anywhere in the schema -- NULL is the only "never
determined" signal available, since QCheckBox.isChecked() is always
True/False, never None. collect_changes() therefore only auto-fills
is_explicit when it is currently NULL and the user didn't touch the
checkbox this session; any other state is left alone.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.core import censor
from src.db.db_tables.base import Base
from src.db.db_tables.track import Track
from src.metadata.metadata_mapping import VORBIS_TRACK_MAPPINGS
from src.track.track_edit_lyrics import LyricsTab


@pytest.fixture(autouse=True)
def _isolated_wordlist(tmp_path, monkeypatch):
    wordlist = tmp_path / "explicit_words.txt"
    wordlist.write_text("shit\nfuck\n")
    monkeypatch.setattr(censor, "_WORDLIST_PATH", wordlist)
    censor._cache["mtime"] = None
    censor._cache["pattern"] = None
    yield
    censor._cache["mtime"] = None
    censor._cache["pattern"] = None


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()


def _make_track(session, **overrides):
    track = Track(track_name="Test Track")
    for field, value in overrides.items():
        setattr(track, field, value)
    session.add(track)
    session.commit()
    return track


class _Controller:
    """LyricsTab only stores this; none of the paths under test call it."""


def _make_tab(tracks):
    tab = LyricsTab(tracks, _Controller())
    tab.load(tracks)
    return tab


# AC1 -------------------------------------------------------------------------
def test_saving_explicit_lyrics_sets_is_explicit_true_when_null(qapp, session):
    track = _make_track(session, is_explicit=None)
    tab = _make_tab([track])
    tab._edit.setPlainText("this shit is real")
    changes = tab.collect_changes()
    assert changes["is_explicit"] is True

    for field, value in changes.items():
        setattr(track, field, value)
    session.commit()
    reloaded = session.get(Track, track.track_id)
    # is_explicit is Column(Integer), not Boolean -- SQLite round-trips
    # Python True as the int 1, so check truthiness, not identity.
    assert reloaded.is_explicit


# AC2 -------------------------------------------------------------------------
def test_saving_clean_lyrics_sets_is_explicit_false_when_null(qapp, session):
    track = _make_track(session, is_explicit=None)
    tab = _make_tab([track])
    tab._edit.setPlainText("a perfectly clean lyric line")
    changes = tab.collect_changes()
    assert changes["is_explicit"] is False

    for field, value in changes.items():
        setattr(track, field, value)
    session.commit()
    reloaded = session.get(Track, track.track_id)
    # is_explicit is Column(Integer): False round-trips as 0, which is
    # falsy but not `is False` -- distinct from "still NULL".
    assert reloaded.is_explicit is not None
    assert not reloaded.is_explicit


# AC3 -------------------------------------------------------------------------
def test_saving_new_lyrics_does_not_overwrite_existing_is_explicit(qapp, session):
    track = _make_track(session, lyrics="old clean lyrics", is_explicit=False)
    tab = _make_tab([track])
    tab._edit.setPlainText("this shit is real")
    changes = tab.collect_changes()
    assert changes["lyrics"] == "this shit is real"
    assert "is_explicit" not in changes


# AC4 -------------------------------------------------------------------------
def test_manual_checkbox_choice_wins_over_autofill_same_session(qapp, session):
    from src.track.track_edit_fieldform import _read_widget

    track = _make_track(session, is_explicit=None)
    tab = _make_tab([track])
    # Clean lyrics -- auto-fill would compute False -- but the user
    # manually checks the box to True in the same session before Save.
    tab._edit.setPlainText("a perfectly clean lyric line")
    tab._explicit_widget.setChecked(True)
    tab._mark_dirty("is_explicit")

    assert _read_widget(tab._explicit_widget) is True
    changes = tab.collect_changes()
    assert changes["is_explicit"] is True


# AC7 -------------------------------------------------------------------------
def test_multi_track_mode_never_autofills(qapp, session):
    t1 = _make_track(session, track_name="Track 1", is_explicit=None)
    t2 = _make_track(session, track_name="Track 2", is_explicit=None, lyrics="shit")
    tab = _make_tab([t1, t2])
    # Multi-track mode blanks the lyrics box on load and disables search;
    # nothing types "dirty" lyrics here, so nothing should be computed.
    changes = tab.collect_changes()
    assert "is_explicit" not in changes


# AC13 (single-track path) -----------------------------------------------------
def test_empty_lyrics_never_autofills(qapp, session):
    track = _make_track(session, lyrics=None, is_explicit=None)
    tab = _make_tab([track])
    # No edits made at all -- lyrics stay empty/None.
    changes = tab.collect_changes()
    assert "is_explicit" not in changes


# AC12 --------------------------------------------------------------------------
def test_metadata_tag_mapping_covers_is_explicit():
    assert VORBIS_TRACK_MAPPINGS["EXPLICIT"] == {
        "field": "is_explicit",
        "type": int,
        "entity": "Track",
    }
