"""Tab-level tests for the Classical tab's 'Parse Title for Classical Data'
action (docs/specs/classical_metadata_from_title.md, ACs 10-13).

The preview QDialog is stubbed out so nothing blocks; the parser itself is
covered separately in test_classical_title_parser.py.
"""

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.db_tables.base import Base
from src.db.db_tables.track import Track
from src.track.track_edit import TrackEditDialog
import src.track.track_edit_classical as tec
from src.track.track_edit_classical import ClassicalTab

_TITLE = "Symphony No. 5 in C minor, Op. 67: I. Allegro con brio"
_CLASSICAL_ROW = 7  # sidebar index of the Classical tab


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _make_track(session, **overrides):
    track = Track(track_name=_TITLE)
    for field, value in overrides.items():
        setattr(track, field, value)
    session.add(track)
    session.commit()
    return track


@pytest.fixture
def accept_preview(monkeypatch):
    """Stub the preview dialog to auto-accept (exec() -> 1)."""

    def _install(accept: bool):
        class _Stub:
            def __init__(self, *a, **k):
                pass

            def exec(self):
                return 1 if accept else 0

        monkeypatch.setattr(tec, "_ParsePreviewDialog", _Stub)

    return _install


# AC10 ------------------------------------------------------------------------
def test_parse_button_only_in_single_track_mode(qapp, session):
    one = _make_track(session)
    two = _make_track(session, track_name="Another")

    single = ClassicalTab([one], SimpleNamespace())
    assert hasattr(single, "parse_button")

    multi = ClassicalTab([one, two], SimpleNamespace())
    assert not hasattr(multi, "parse_button")


# AC11 ----------------------------------------------------------------------- -
def test_parse_fills_blank_fields_and_marks_them_dirty(qapp, session, accept_preview):
    accept_preview(True)
    track = _make_track(session)
    dlg = TrackEditDialog(track, SimpleNamespace())
    tab = dlg._ensure_tab_built(_CLASSICAL_ROW)

    tab._parse_title()

    changes = tab.collect_changes()
    assert changes == {
        "work_type": "Symphony",
        "work_name": "No. 5",
        "classical_catalog_prefix": "Op.",
        "classical_catalog_number": 67,
        "classical_tempo": "Allegro con brio",
        "movement_name": "Allegro con brio",
        "movement_number": 1,
        "is_classical": True,
    }


# AC12 ----------------------------------------------------------------------- -
def test_parse_never_overwrites_a_field_the_user_filled(qapp, session, accept_preview):
    accept_preview(True)
    track = _make_track(session)
    dlg = TrackEditDialog(track, SimpleNamespace())
    tab = dlg._ensure_tab_built(_CLASSICAL_ROW)

    tab.set_field_value("work_name", "Symphony No. 5")
    tab._dirty.discard("work_name")  # simulate a pre-existing (loaded) value
    assert tab._field_is_filled("work_name") is True

    tab._parse_title()

    changes = tab.collect_changes()
    assert "work_name" not in changes
    assert changes["work_type"] == "Symphony"
    assert changes["classical_catalog_number"] == 67
    assert changes["movement_number"] == 1


# AC13 ----------------------------------------------------------------------- -
def test_confirm_rewrites_title_cancel_is_a_noop(qapp, session, accept_preview):
    track = _make_track(session)

    # Cancel -> nothing changes on either tab.
    accept_preview(False)
    dlg = TrackEditDialog(track, SimpleNamespace())
    basic = dlg._tabs[0]
    tab = dlg._ensure_tab_built(_CLASSICAL_ROW)
    tab._parse_title()
    assert basic._dirty == set()
    assert tab._dirty == set()
    assert dlg.get_live_track_name() == _TITLE

    # Confirm -> title trimmed to the bare movement name, marked dirty.
    accept_preview(True)
    dlg2 = TrackEditDialog(track, SimpleNamespace())
    basic2 = dlg2._tabs[0]
    tab2 = dlg2._ensure_tab_built(_CLASSICAL_ROW)
    tab2._parse_title()
    assert basic2.get_field_value("track_name") == "Allegro con brio"
    assert "track_name" in basic2._dirty
