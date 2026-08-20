"""Tests for the manual key_confidence override in the track edit dialog
(docs/specs/key_confidence_manual_override.md, Option C). Each test maps
1:1 to a numbered acceptance criterion in that spec.

key_confidence's FieldSpec flipped from editable=False (read-only label)
to editable=True (plain QLineEdit via create_nullable_float_field, same
0.0-1.0 convention as every other confidence/analysis float column), so
these tests exercise FieldFormTab's already-generic editable-float path
plus the downstream consumers that read key_confidence off the DB.
"""

from PySide6.QtGui import QValidator
from PySide6.QtWidgets import QLineEdit
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.db_tables.base import Base
from src.db.db_tables.track import Track
from src.metadata.metadata_mapping import VORBIS_TRACK_MAPPINGS
from src.statistics.analysis_cache import REQUIRED_ANALYSIS_FIELDS, track_needs_analysis
from src.statistics.stats.audio import AudioStats
from src.track.track_edit_fieldform import FieldFormTab


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()


def _make_track(session, **overrides):
    track = Track(track_name="Test Track", key="C", mode="Major", key_confidence=0.62)
    for field, value in overrides.items():
        setattr(track, field, value)
    session.add(track)
    session.commit()
    return track


class _Controller:
    """FieldFormTab only stores this; none of the paths under test call it."""


def _make_tab(tracks):
    """Mirrors real dialog usage: construct, then load() to populate widgets
    from the track(s) -- __init__ alone only builds empty widgets."""
    tab = FieldFormTab("Properties", tracks, _Controller())
    tab.load(tracks)
    return tab


# AC1 -----------------------------------------------------------------------
def test_key_confidence_field_is_editable_and_prefilled(qapp, session):
    track = _make_track(session, key_confidence=0.62)
    tab = _make_tab([track])
    widget = tab._widgets.get("key_confidence")
    assert isinstance(widget, QLineEdit)
    assert widget.text() == "0.6200"


# AC2 -------------------------------------------------------------------------
def test_editing_to_one_and_saving_sets_key_confidence(qapp, session):
    track = _make_track(session, key_confidence=0.62)
    tab = _make_tab([track])
    tab._widgets["key_confidence"].setText("1.0")
    changes = tab.collect_changes()
    assert changes["key_confidence"] == 1.0

    for field, value in changes.items():
        setattr(track, field, value)
    session.commit()

    reloaded = session.get(Track, track.track_id)
    assert reloaded.key_confidence == 1.0


# AC3 -------------------------------------------------------------------------
def test_validator_rejects_out_of_range_value(qapp, session):
    track = _make_track(session)
    tab = _make_tab([track])
    validator = tab._widgets["key_confidence"].validator()
    state, _, _ = validator.validate("100", 0)
    assert state != QValidator.State.Acceptable


# AC4 -------------------------------------------------------------------------
def test_editing_key_confidence_does_not_touch_key_or_mode(qapp, session):
    track = _make_track(session, key="C", mode="Major", key_confidence=0.62)
    tab = _make_tab([track])
    tab._widgets["key_confidence"].setText("1.0")
    changes = tab.collect_changes()
    assert changes == {"key_confidence": 1.0}
    assert "key" not in changes
    assert "mode" not in changes


# AC5 -------------------------------------------------------------------------
def test_clearing_field_sets_key_confidence_to_null(qapp, session):
    track = _make_track(session, key_confidence=0.62)
    tab = _make_tab([track])
    tab._widgets["key_confidence"].setText("")
    changes = tab.collect_changes()
    assert changes["key_confidence"] is None

    setattr(track, "key_confidence", changes["key_confidence"])
    session.commit()
    reloaded = session.get(Track, track.track_id)
    assert reloaded.key_confidence is None


# AC6 -------------------------------------------------------------------------
def test_editing_without_applying_changes_leaves_db_value_untouched(qapp, session):
    track = _make_track(session, key_confidence=0.62)
    tab = _make_tab([track])
    tab._widgets["key_confidence"].setText("1.0")
    # Simulates clicking Cancel: collect_changes()'s output is simply never
    # applied back to the track/session.
    session.expire(track)
    reloaded = session.get(Track, track.track_id)
    assert reloaded.key_confidence == 0.62


# AC7 -------------------------------------------------------------------------
def test_key_confidence_hidden_in_multi_track_mode(qapp, session):
    t1 = _make_track(session, track_name="Track 1", key_confidence=0.62)
    t2 = _make_track(session, track_name="Track 2", key_confidence=0.40)
    tab = _make_tab([t1, t2])
    assert "key_confidence" not in tab._widgets
    assert "key_confidence" not in tab._labels


# AC8 -------------------------------------------------------------------------
def test_manual_override_clears_needs_analysis_flag_for_key_confidence(qapp, session):
    track = _make_track(session, key_confidence=None)
    for field in REQUIRED_ANALYSIS_FIELDS:
        if field != "key_confidence":
            setattr(track, field, 1.0)
    session.commit()

    assert track_needs_analysis(track) is True

    tab = _make_tab([track])
    tab._widgets["key_confidence"].setText("1.0")
    changes = tab.collect_changes()
    for field, value in changes.items():
        setattr(track, field, value)
    session.commit()

    assert track_needs_analysis(track) is False


# AC9 -------------------------------------------------------------------------
def test_manual_override_moves_track_into_confident_bucket(qapp, session):
    track = _make_track(session, key="C", mode="Major", key_confidence=0.2)
    stats = AudioStats(lambda: session)

    before = stats._key_distribution(session)
    assert "C Major" not in before["confident"]
    assert before["all"].get("C Major") == 1

    tab = _make_tab([track])
    tab._widgets["key_confidence"].setText("1.0")
    for field, value in tab.collect_changes().items():
        setattr(track, field, value)
    session.commit()

    after = stats._key_distribution(session)
    assert after["confident"].get("C Major") == 1


# AC10 ------------------------------------------------------------------------
def test_forced_reanalysis_still_overwrites_manual_key_confidence(session):
    # Documents the known, accepted limitation from the spec: forced
    # re-analysis paths (analysis_dialog.py's "Re-analyze all tracks"
    # checkbox and "Force Re-analyse This Track" context action) bypass
    # track_needs_analysis() entirely -- _on_track_done() unconditionally
    # setattr()s every field the worker returns, including key/mode/
    # key_confidence, regardless of any manual edit. Calls the real
    # AnalysisDialog._on_track_done on a lightweight stand-in exposing only
    # what that method touches, so this pins actual production behavior
    # rather than a re-description of it.
    from src.statistics.analysis_dialog import AudioAnalysisDialog

    track = _make_track(session, key="C", mode="Major", key_confidence=1.0)

    class _Get:
        def get_entity_object(self, _entity, track_id):
            return track

    class _Controller:
        get = _Get()

    class _Scheduler:
        progress = (0, 0)

    stand_in = type(
        "StandIn",
        (),
        {
            "controller": _Controller(),
            "_track_id_to_item": {},
            "_scheduler": _Scheduler(),
            "_update_progress_display": lambda self, *a: None,
        },
    )()

    fresh_analysis_result = {"key": "G", "mode": "Minor", "key_confidence": 0.55}
    AudioAnalysisDialog._on_track_done(stand_in, track.track_id, fresh_analysis_result)

    assert track.key_confidence == 0.55
    assert track.key == "G"
    assert track.mode == "Minor"


# AC11 ------------------------------------------------------------------------
def test_metadata_tag_mapping_still_covers_key_confidence():
    assert VORBIS_TRACK_MAPPINGS["KEYCONFIDENCE"] == {
        "field": "key_confidence",
        "type": float,
        "entity": "Track",
    }
