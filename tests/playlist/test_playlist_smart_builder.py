"""Smart playlist criteria evaluation and storage, including Bool-typed
criteria casting.
"""

from PySide6.QtWidgets import QMessageBox
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from src.db.db_helpers.add import AddToDB
from src.db.db_helpers.delete import DeleteDB
from src.db.db_helpers.get import GetFromDB
from src.db.db_helpers.update import UpdateDB
from src.db.db_tables.base import Base
from src.db.db_tables.genre import Genre
from src.db.db_tables.playlist import Playlist, PlaylistTracks, SmartPlaylist, SmartPlaylistCriteria
from src.db.db_tables.track import Track
from src.playlist import playlist_track_sync
from src.playlist.smart.playlist_smart_builder import SmartPlaylistBuilder, condition_to_row_fields
from src.playlist.smart.playlist_smart_criteria_fields import CRITERIA_FIELDS, is_queryable_track_field
from src.playlist.smart.playlist_smart_edit import SmartPlaylistEditDialog

# ---------------------------------------------------------------------------
# Tests for smart playlist criteria evaluation and storage, against a real in-memory sqlite session.
# ---------------------------------------------------------------------------


class StubController:
    def __init__(self, session):
        self.get = GetFromDB(session)
        self.add = AddToDB(session)
        self.update = UpdateDB(session)
        self.delete = DeleteDB(session)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture
def controller(session):
    return StubController(session)


@pytest.fixture
def library(session):
    """Four tracks: bpm 80/100/120/140; genres Rock, Rock+Jazz, Jazz, none."""
    rock, jazz = Genre(genre_name="Rock"), Genre(genre_name="Jazz")
    tracks = [Track(track_name=f"T{bpm}", bpm=bpm) for bpm in (80, 100, 120, 140)]
    tracks[0].genres = [rock]
    tracks[1].genres = [rock, jazz]
    tracks[2].genres = [jazz]
    session.add_all([rock, jazz, *tracks])
    session.commit()
    return tracks


def _ids(tracks):
    return {t.track_id for t in tracks}


def test_every_offered_field_is_queryable():
    assert all(is_queryable_track_field(name) for name, *_ in CRITERIA_FIELDS)
    offered = {name for name, *_ in CRITERIA_FIELDS}
    assert not offered & {"artist_names", "mood_name", "primary_artist_names", "disc_number"}


def test_non_queryable_field_matches_nothing_in_and(controller, library):
    builder = SmartPlaylistBuilder(controller)
    conditions = [{"field": "primary_artist_names", "comparison": "eq", "value": "X", "type": "String"}]

    assert builder._get_matching_track_ids(conditions, "AND") == []


def test_numeric_range_matches_inclusive_bounds(controller, library):
    builder = SmartPlaylistBuilder(controller)
    conditions = [{"field": "bpm", "comparison": "range", "value": "120|100", "type": "Float"}]

    assert set(builder._get_matching_track_ids(conditions, "AND")) == _ids(library[1:3])


def test_malformed_numeric_range_matches_nothing(controller, library):
    builder = SmartPlaylistBuilder(controller)
    conditions = [{"field": "bpm", "comparison": "range", "value": "100", "type": "Float"}]

    assert builder._get_matching_track_ids(conditions, "AND") == []


def test_and_with_repeated_key_intersects_both_conditions(controller, library):
    builder = SmartPlaylistBuilder(controller)
    conditions = [{"field": "genre_names", "comparison": "contains", "value": "Rock", "type": "List"}, {"field": "genre_names", "comparison": "contains", "value": "Jazz", "type": "List"}]

    assert set(builder._get_matching_track_ids(conditions, "AND")) == {library[1].track_id}


def test_list_contains_accepts_a_list_value(controller, library):
    builder = SmartPlaylistBuilder(controller)
    conditions = [{"field": "genre_names", "comparison": "contains", "value": ["Jazz"], "type": "List"}]

    assert set(builder._get_matching_track_ids(conditions, "AND")) == _ids(library[1:3])


def test_list_value_round_trips_through_storage(session, controller, library):
    playlist = Playlist(playlist_name="Rocky", is_smart=1)
    session.add(playlist)
    session.commit()
    session.add(SmartPlaylist(playlist_id=playlist.playlist_id, logic="AND"))
    session.commit()
    condition = {"field": "genre_names", "comparison": "in", "value": ["Rock", "Pop"], "type": "List"}

    row = controller.add.add_entity("SmartPlaylistCriteria", smart_playlist_id=playlist.playlist_id, **condition_to_row_fields(condition))
    assert row is not None
    assert row.value == "Rock, Pop"

    assert SmartPlaylistBuilder(controller).refresh_playlist(playlist.playlist_id) is True
    stored = {pt.track_id for pt in session.query(PlaylistTracks).filter_by(playlist_id=playlist.playlist_id)}
    assert stored == _ids(library[:2])


def test_no_criteria_refresh_reports_sync_failure(controller, monkeypatch, session):
    playlist = Playlist(playlist_name="Empty", is_smart=1)
    session.add(playlist)
    session.commit()
    session.add(SmartPlaylist(playlist_id=playlist.playlist_id, logic="AND"))
    session.commit()
    monkeypatch.setattr("src.playlist.smart.playlist_smart_builder.sync_playlist_tracks", lambda *a: None)

    assert SmartPlaylistBuilder(controller).refresh_playlist(playlist.playlist_id) is False


def _smart_playlist_with_criterion(session):
    playlist = Playlist(playlist_name="Smart", is_smart=1)
    session.add(playlist)
    session.commit()
    session.add(SmartPlaylist(playlist_id=playlist.playlist_id, logic="AND"))
    session.add(SmartPlaylistCriteria(smart_playlist_id=playlist.playlist_id, field_name="bpm", comparison="gt", value="90", type="Float"))
    session.commit()
    return playlist


def test_edit_dialog_saves_a_list_criterion(qapp, session, controller, monkeypatch):
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: pytest.fail("save failed"))
    playlist = _smart_playlist_with_criterion(session)
    dialog = SmartPlaylistEditDialog(controller, playlist.playlist_id)
    dialog.criteria_widgets[0].set_criteria({"field": "genre_names", "comparison": "in", "value": "Rock, Jazz", "type": "List"})

    dialog._on_ok_clicked()

    rows = session.query(SmartPlaylistCriteria).filter_by(smart_playlist_id=playlist.playlist_id).all()
    assert [(r.field_name, r.value) for r in rows] == [("genre_names", "Rock, Jazz")]


def test_edit_dialog_keeps_old_criteria_when_insert_fails(qapp, session, controller, monkeypatch):
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: None)
    playlist = _smart_playlist_with_criterion(session)
    dialog = SmartPlaylistEditDialog(controller, playlist.playlist_id)

    def _fail_commit():
        raise SQLAlchemyError("boom")

    monkeypatch.setattr(session, "commit", _fail_commit)
    assert dialog._replace_criteria([{"field": "bpm", "comparison": "lt", "value": 50, "type": "Float"}]) is False
    monkeypatch.undo()

    rows = session.query(SmartPlaylistCriteria).filter_by(smart_playlist_id=playlist.playlist_id).all()
    assert [(r.comparison, r.value) for r in rows] == [("gt", "90")]


def test_track_sync_rolls_back_delete_when_insert_fails(session, controller, library, monkeypatch):
    playlist = Playlist(playlist_name="P")
    session.add(playlist)
    session.commit()
    session.add(PlaylistTracks(playlist_id=playlist.playlist_id, track_id=library[0].track_id, position=1))
    session.commit()

    def _fail_bulk_save(objects):
        raise SQLAlchemyError("boom")

    monkeypatch.setattr(session, "bulk_save_objects", _fail_bulk_save)
    assert playlist_track_sync.sync_playlist_tracks(controller, playlist.playlist_id, [library[1].track_id]) is None

    remaining = {pt.track_id for pt in session.query(PlaylistTracks).filter_by(playlist_id=playlist.playlist_id)}
    assert remaining == {library[0].track_id}


# ---------------------------------------------------------------------------
# Regression tests for Bool-typed smart playlist criteria casting.
# ---------------------------------------------------------------------------


class StubControllerBool:
    def __init__(self, session_bool):
        self.get = GetFromDB(session_bool)
        self.add = AddToDB(session_bool)
        self.update = UpdateDB(session_bool)


@pytest.fixture
def session_bool():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session_bool = Session()
    yield session_bool
    session_bool.close()


@pytest.fixture
def controller_bool(session_bool):
    return StubControllerBool(session_bool)


@pytest.mark.parametrize(
    ("stored_value", "expected"),
    [(True, True), (False, False), ("True", True), ("False", False), ("1", True), ("0", False)],
)
def test_cast_value_handles_bool_representations(controller_bool, stored_value, expected):
    builder = SmartPlaylistBuilder(controller_bool)

    assert builder._cast_value(stored_value, "Bool", "eq") is expected


def test_bool_criterion_matches_correctly_when_stored_as_true_false_text(session_bool, controller_bool):
    """Simulates a value that was stored as literal "True"/"False" text --
    the case _cast_value's old str() fallback got wrong."""
    explicit_track = Track(track_name="Explicit", is_explicit=1)
    clean_track = Track(track_name="Clean", is_explicit=0)
    session_bool.add_all([explicit_track, clean_track])
    session_bool.commit()

    playlist = Playlist(playlist_name="Explicit tracks", is_smart=1)
    session_bool.add(playlist)
    session_bool.commit()
    smart = SmartPlaylist(playlist_id=playlist.playlist_id, logic="AND")
    session_bool.add(smart)
    session_bool.commit()
    # Insert via raw SQL to force the literal text "True", bypassing the
    # String column's usual int-affinity round-trip.
    session_bool.execute(
        text(
            "INSERT INTO smart_playlist_criteria "
            "(smart_playlist_id, field_name, comparison, value, type) "
            "VALUES (:pid, 'is_explicit', 'eq', 'True', 'Bool')"
        ),
        {"pid": playlist.playlist_id},
    )
    session_bool.commit()

    builder = SmartPlaylistBuilder(controller_bool)
    assert builder.refresh_playlist(playlist.playlist_id) is True

    matched_ids = {
        pt.track_id
        for pt in controller_bool.get.get_all_entities(
            "PlaylistTracks", playlist_id=playlist.playlist_id
        )
    }
    assert matched_ids == {explicit_track.track_id}
