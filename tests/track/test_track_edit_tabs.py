"""Track edit tabs: finalize fixes, read-only value formatting, awards and
places, genre/mood association tabs, and completer context call sites.
"""

import time
from types import SimpleNamespace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.artist.edit.artist_edit_influences import _artist_display, _build_artist_index
from src.common.widgets.entity_completer_context import artist_context_map, track_context_map
from src.common.widgets.entity_completer_edit import EntityCompleterEdit, invalidate_entity_cache
from src.db.db_helpers.add import AddToDB
from src.db.db_helpers.delete import DeleteDB
from src.db.db_helpers.get import GetFromDB
from src.db.db_mapping_tracks import TRACK_FIELDS
from src.db.db_tables.award import Award, AwardAssociation
from src.db.db_tables.base import Base
from src.db.db_tables.place import Place, PlaceAssociation
from src.db.db_tables.place_association_type import PlaceAssociationType
from src.db.db_tables.track import Track
from src.track.edit.track_edit_advanced import AdvancedTab
from src.track.edit.track_edit_album import AlbumsTab
from src.track.edit.track_edit_awards import AwardsTab
from src.track.edit.track_edit_fieldform import _format_readonly
from src.track.edit.track_edit_genres import GenresTab
from src.track.edit.track_edit_lyrics import LyricsTab
from src.track.edit.track_edit_places import PlacesTab
from src.track.edit.track_edit_roles import RolesTab
from src.track.edit.track_edit_samples import _MAX_SEARCH_RESULTS, SamplesTab, _AddSampleBar, _build_track_index, _track_display
from src.track.edit.track_edit_usedin import UsedInTab

# ---------------------------------------------------------------------------
# Regression tests for the 2026-10-09 src/track finalize fixes in the track edit tabs.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_cache(qapp):
    invalidate_entity_cache()
    yield
    qapp.processEvents()
    invalidate_entity_cache()


class _Get:
    """Enough of GetFromDB for the search widgets: empty tables."""

    def __init__(self):
        self.session = SimpleNamespace(expire=lambda *a, **k: None)

    def count_entities(self, model_name):
        return 0

    def get_all_entities(self, model_name, **kwargs):
        return []

    def get_entity_object(self, model_name, **kwargs):
        return None


class _Delete:
    def __init__(self, result=True):
        self.result = result
        self.calls = []

    def delete_entity(self, model_name, **kwargs):
        self.calls.append((model_name, kwargs))
        return self.result


def _controller(**parts):
    return SimpleNamespace(get=parts.get("get", _Get()), delete=parts.get("delete", _Delete()), add=parts.get("add"))


def test_roles_remove_all_uses_one_filtered_delete(qapp):
    controller = _controller()
    tab = RolesTab([SimpleNamespace(track_id=1), SimpleNamespace(track_id=2)], controller)
    tab.load = lambda tracks: None

    tab._remove_all_roles_for_artist(7)

    assert controller.delete.calls == [("TrackArtistRole", {"track_id": [1, 2], "artist_id": 7})]


def test_roles_reload_requested_during_load_runs_afterwards(qapp):
    tab = RolesTab([SimpleNamespace(track_id=1)], _controller())
    reloads = []
    tab.load = lambda tracks: reloads.append(tracks)

    tab._reload_pending = True
    tab._run_pending_reload()
    assert len(reloads) == 1

    tab.cleanup()
    tab._reload_pending = True
    tab._run_pending_reload()
    assert len(reloads) == 1  # no reload after cleanup


def test_roles_remove_failure_warns(qapp, monkeypatch):
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a))
    tab = RolesTab([SimpleNamespace(track_id=1)], _controller(delete=_Delete(result=False)))
    tab.load = lambda tracks: None
    tab._remove_role(7, 3)
    assert warnings


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine, expire_on_commit=False)()
    yield s
    s.close()


def test_sample_search_first_result_can_be_picked(qapp, session):
    t = Track(track_name="Amen Brother")
    session.add(t)
    session.commit()
    bar = _AddSampleBar(SimpleNamespace(get=GetFromDB(session)), on_add=lambda **k: None)

    bar.name_search.setText("Amen")
    assert bar.name_combo.count() == 1
    bar.name_combo.activated.emit(0)  # the user picks the (already current) first row

    assert bar._matched_id == t.track_id


def test_sample_search_caps_rows_in_the_query(qapp, session, monkeypatch):
    session.add_all([Track(track_name=f"Love {i}") for i in range(_MAX_SEARCH_RESULTS + 10)])
    session.commit()
    get = GetFromDB(session)
    calls = []
    real_query = get.query_entities

    def spy(*args, **kwargs):
        result = real_query(*args, **kwargs)
        calls.append((kwargs.get("limit"), len(result)))
        return result

    monkeypatch.setattr(get, "query_entities", spy)
    bar = _AddSampleBar(SimpleNamespace(get=get), on_add=lambda **k: None)

    bar.name_search.setText("Love")

    assert calls == [(_MAX_SEARCH_RESULTS, _MAX_SEARCH_RESULTS)]
    assert bar.name_combo.count() == _MAX_SEARCH_RESULTS


def test_sample_add_uses_exact_name_match_without_a_pick(qapp, session):
    t = Track(track_name="Amen Brother")
    session.add(t)
    session.commit()
    added = []
    bar = _AddSampleBar(SimpleNamespace(get=GetFromDB(session)), on_add=lambda **k: added.append(k))

    bar.name_search.setText("Amen Brother")
    bar._handle_add()

    assert added == [{"direction": "uses", "matched_track_id": t.track_id}]


def test_samples_multi_placeholder_is_not_selectable(qapp):
    tracks = [SimpleNamespace(track_id=1, track_name="A"), SimpleNamespace(track_id=2, track_name="B")]
    tab = SamplesTab(tracks, _controller())
    tab.load(tracks)
    assert tab._used_list.item(0).flags() == Qt.NoItemFlags


class _Signal:
    def __init__(self):
        self.disconnected = []

    def disconnect(self, slot):
        self.disconnected.append(slot)


class _Scheduler:
    def __init__(self):
        self.signals = SimpleNamespace(track_done=_Signal(), batch_done=_Signal(), all_done=_Signal(), error=_Signal())
        self.is_running = True
        self.stopped = False

    def stop(self):
        self.stopped = True


def test_advanced_cleanup_stops_running_analysis(qapp):
    tab = AdvancedTab.__new__(AdvancedTab)
    scheduler = _Scheduler()
    tab._scheduler = scheduler
    tab._write_worker = None

    AdvancedTab.cleanup(tab)

    assert scheduler.stopped
    assert all(sig.disconnected for sig in vars(scheduler.signals).values())
    assert tab._scheduler is None


def test_tag_tab_remove_button_removes_selected(qapp):
    tracks = [SimpleNamespace(track_id=1, genres=[SimpleNamespace(genre_id=5, genre_name="Jazz")])]
    controller = _controller()
    tab = GenresTab(tracks, controller)
    tab.load(tracks)
    assert not tab._remove_btn.isEnabled()

    tab._list.item(0).setSelected(True)
    assert tab._remove_btn.isEnabled()
    tab._remove_btn.click()

    assert controller.delete.calls == [("TrackGenre", {"track_id": [1], "genre_id": 5})]


def test_lyrics_multi_mixed_explicit_shows_partial_state(qapp):
    tracks = [SimpleNamespace(track_id=1, is_explicit=True, lyrics=None), SimpleNamespace(track_id=2, is_explicit=False, lyrics=None)]
    tab = LyricsTab(tracks, _controller())
    tab.load(tracks)
    assert tab._explicit_widget.checkState() == Qt.PartiallyChecked
    tab.cleanup()


def test_lyrics_search_uses_live_title(qapp):
    track = SimpleNamespace(track_id=1, track_name="Saved", album_name=None, duration=200, primary_artist_names="A", artist_roles=[], is_explicit=None, lyrics=None)
    tab = LyricsTab([track], _controller(), dialog=SimpleNamespace(get_live_track_name=lambda: "Typed"))
    queries = []
    tab._lyric_thread.search = queries.append

    tab._search_lyrics()

    assert queries[0].song == "Typed"
    tab.cleanup()


def test_album_create_from_mb_failure_does_not_crash(qapp, monkeypatch):
    warnings = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a))
    tab = AlbumsTab.__new__(AlbumsTab)
    tab.controller = SimpleNamespace(add=SimpleNamespace(add_entity=lambda *a, **k: None))

    assert tab._canonical_album_resolver().create_album({"album_name": "X", "release_group_mbid": "rg"}, []) is None
    assert warnings


def _usage(uid, description):
    return SimpleNamespace(usage_id=uid, usage_type="Film", title="X", year=2000, description=description, wikipedia_link=None)


def test_used_in_multi_remove_matches_the_whole_entry(qapp):
    tracks = [SimpleNamespace(track_id=1, usages=[_usage(1, "a"), _usage(3, "b")]), SimpleNamespace(track_id=2, usages=[_usage(2, "a")])]
    controller = _controller()
    tab = UsedInTab(tracks, controller)
    tab.load(tracks)
    assert tab._table.rowCount() == 1

    tab._remove_row(0)

    assert controller.delete.calls == [("TrackUsage", {"entity_ids": [1, 2]})]


# ---------------------------------------------------------------------------
# Tests for FieldFormTab's read-only value formatter (_format_readonly).
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# AwardsTab and PlacesTab against a real in-memory DB: link, show, de-duplicate and remove.
# ---------------------------------------------------------------------------


@pytest.fixture
def session_places(qapp):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine, expire_on_commit=False)()
    invalidate_entity_cache()
    yield s
    qapp.processEvents()  # flush deferred add_to_index() calls while widgets are alive
    invalidate_entity_cache()
    s.close()


class _Controller:
    def __init__(self, session_places):
        self.get = GetFromDB(session_places)
        self.add = AddToDB(session_places)
        self.delete = DeleteDB(session_places)


def _tracks(session_places, n=2):
    tracks = [Track(track_name=f"T{i}") for i in range(n)]
    session_places.add_all(tracks)
    session_places.commit()
    return tracks


def _rows(table):
    return [tuple(table.item(r, c).text() for c in range(table.columnCount() - 1)) for r in range(table.rowCount())]


def test_awards_load_shows_category_and_year_from_award(qapp, session_places):
    tracks = _tracks(session_places, 1)
    award = Award(award_name="Grammy", award_category="Best Song", award_year=1999)
    session_places.add(award)
    session_places.commit()
    session_places.add(AwardAssociation(award_id=award.award_id, entity_id=tracks[0].track_id, entity_type="Track"))
    session_places.commit()

    tab = AwardsTab(tracks, _Controller(session_places))
    tab.load(tracks)

    assert _rows(tab._table) == [("Grammy", "Best Song", "1999")]
    assert not tab._table.item(0, 0).flags() & Qt.ItemIsEditable


def test_awards_link_creates_award_once_per_track_without_duplicates(qapp, session_places):
    tracks = _tracks(session_places, 2)
    tab = AwardsTab(tracks, _Controller(session_places))
    tab.load(tracks)

    for _ in range(2):  # the second link of the same award must not add duplicate rows
        tab._search.setText("Mercury Prize")
        tab._add()

    assocs = session_places.query(AwardAssociation).all()
    assert sorted(a.entity_id for a in assocs) == sorted(t.track_id for t in tracks)
    assert _rows(tab._table) == [("Mercury Prize", "", "")]


def test_awards_remove_unlinks_only_that_award(qapp, session_places):
    tracks = _tracks(session_places, 2)
    a1, a2 = Award(award_name="A1"), Award(award_name="A2")
    session_places.add_all([a1, a2])
    session_places.commit()
    for t in tracks:
        session_places.add_all([AwardAssociation(award_id=a.award_id, entity_id=t.track_id, entity_type="Track") for a in (a1, a2)])
    session_places.commit()

    tab = AwardsTab(tracks, _Controller(session_places))
    tab.load(tracks)
    tab._remove_award(a1.award_id)

    assert {a.award_id for a in session_places.query(AwardAssociation).all()} == {a2.award_id}
    assert _rows(tab._table) == [("A2", "", "")]


def _place_with_two_types(session_places, tracks):
    place = Place(place_name="Abbey Road")
    rec, origin = PlaceAssociationType(type_name="Recording Location"), PlaceAssociationType(type_name="Origin")
    session_places.add_all([place, rec, origin])
    session_places.commit()
    for t in tracks:
        for typ in (rec, origin):
            session_places.add(PlaceAssociation(place_id=place.place_id, entity_id=t.track_id, entity_type="Track", association_type_id=typ.association_type_id))
    session_places.commit()
    return place, rec, origin


def test_places_remove_matches_association_type(qapp, session_places):
    tracks = _tracks(session_places, 2)
    _place, rec, origin = _place_with_two_types(session_places, tracks)

    tab = PlacesTab(tracks, _Controller(session_places))
    tab.load(tracks)
    assert sorted(_rows(tab._table)) == [("Abbey Road", "Origin"), ("Abbey Road", "Recording Location")]

    row = next(r for r in range(tab._table.rowCount()) if tab._table.item(r, 1).text() == "Origin")
    tab._remove_row(row)

    remaining = session_places.query(PlaceAssociation).all()
    assert {a.association_type_id for a in remaining} == {rec.association_type_id}
    assert len(remaining) == len(tracks)
    assert _rows(tab._table) == [("Abbey Road", "Recording Location")]
    assert origin.association_type_id not in {a.association_type_id for a in remaining}


def test_places_add_skips_existing_links(qapp, session_places):
    tracks = _tracks(session_places, 2)
    _place_with_two_types(session_places, tracks)
    tab = PlacesTab(tracks, _Controller(session_places))
    tab.load(tracks)
    before = session_places.query(PlaceAssociation).count()

    tab._search.setText("Abbey Road")
    tab._type_edit.setText("Origin")
    tab._add()

    assert session_places.query(PlaceAssociation).count() == before


# ---------------------------------------------------------------------------
# Tests for bug #240: a stale track_id in a genre/mood tab's track list
# ---------------------------------------------------------------------------


class _StubTrack:
    def __init__(self, track_id, genres=None):
        self.track_id = track_id
        self.genres = genres or []


class _StubGenre:
    def __init__(self, genre_id, genre_name):
        self.genre_id = genre_id
        self.genre_name = genre_name


class _StubSession:
    def expire(self, instance, attribute_names=None):
        pass


class _StubGet:
    def __init__(self, entity_object_return=None):
        self.entity_object_return = entity_object_return
        self.session = _StubSession()

    def count_entities(self, model_name):
        return 0

    def get_all_entities(self, model_name, **kwargs):
        return []

    def get_entity_object(self, model_name, **filters):
        return self.entity_object_return

    def resolve_entity_or_alias(self, model_name, name_field, name):
        return None

    def resolve_split_alias(self, model_name, name):
        return None


class _StubAdd:
    def __init__(self, failed_track_ids=None, new_entity=None):
        self._failed_track_ids = set(failed_track_ids or [])
        self._new_entity = new_entity
        self.add_entities_with_fallback_calls = []

    def add_entities_with_fallback(self, model_name, rows):
        self.add_entities_with_fallback_calls.append((model_name, list(rows)))
        succeeded, failed = [], []
        for row in rows:
            (failed if row["track_id"] in self._failed_track_ids else succeeded).append(row)
        return succeeded, failed

    def add_entity(self, model_name, **kwargs):
        return self._new_entity


class _StubController:
    def __init__(self, get=None, add=None):
        self.get = get or _StubGet()
        self.add = add or _StubAdd()


class _StubSearch:
    """Stands in for the completer widget: a pre-matched genre by id, so
    _add() takes the get_entity_object lookup path instead of find/create."""

    def __init__(self, text, matched_id):
        self._text = text
        self._matched_id = matched_id
        self.reset_calls = 0
        self.add_to_index_calls = []

    def text(self):
        return self._text

    def matched_id(self):
        return self._matched_id

    def split_names(self):
        return [part.strip() for part in self._text.split(";") if part.strip()]

    def reset(self):
        self.reset_calls += 1

    def add_to_index(self, display, entity_id):
        self.add_to_index_calls.append((display, entity_id))


def test_add_does_not_drop_valid_tracks_when_one_track_id_is_stale(qapp, monkeypatch):
    invalidate_entity_cache("Genre")
    warning_calls = []
    monkeypatch.setattr(
        "src.track.edit.tag_association_tab.QMessageBox.warning",
        lambda *args, **kwargs: warning_calls.append(args),
    )

    genre = _StubGenre(416, "Jazz-Rock")
    # track_id 3 stands in for a deleted/stale track no longer in the DB --
    # the scenario from bug #240 (FK violation on one row of a 16-row batch).
    tracks = [_StubTrack(1), _StubTrack(2), _StubTrack(3)]
    stub_add = _StubAdd(failed_track_ids={3})
    controller = _StubController(get=_StubGet(entity_object_return=genre), add=stub_add)

    tab = GenresTab(tracks, controller)
    try:
        tab._search = _StubSearch("Jazz-Rock", matched_id=416)
        tab._add()

        # All three rows were attempted in one batch...
        assert len(stub_add.add_entities_with_fallback_calls) == 1
        model_name, rows = stub_add.add_entities_with_fallback_calls[0]
        assert model_name == "TrackGenre"
        assert rows == [
            {"track_id": 1, "genre_id": 416},
            {"track_id": 2, "genre_id": 416},
            {"track_id": 3, "genre_id": 416},
        ]

        # ...and the caller was told exactly which one was dropped, instead
        # of the whole batch silently vanishing (the old add_entities()
        # behavior: roll back everything, log, return [], nothing surfaced).
        assert len(warning_calls) == 1
    finally:
        tab.cleanup()


def test_add_shows_no_warning_when_all_rows_succeed(qapp, monkeypatch):
    invalidate_entity_cache("Genre")
    warning_calls = []
    monkeypatch.setattr(
        "src.track.edit.tag_association_tab.QMessageBox.warning",
        lambda *args, **kwargs: warning_calls.append(args),
    )

    genre = _StubGenre(416, "Jazz-Rock")
    tracks = [_StubTrack(1), _StubTrack(2)]
    stub_add = _StubAdd(failed_track_ids=set())
    controller = _StubController(get=_StubGet(entity_object_return=genre), add=stub_add)

    tab = GenresTab(tracks, controller)
    try:
        tab._search = _StubSearch("Jazz-Rock", matched_id=416)
        tab._add()

        assert len(stub_add.add_entities_with_fallback_calls) == 1
        assert warning_calls == []
    finally:
        tab.cleanup()


def test_add_defers_completer_rebuild_for_newly_created_entity(qapp):
    """_add() can run nested inside EntityCompleterEdit's own keyPressEvent
    (Enter -> returnPressed fires *during* that native call). Calling
    add_to_index() synchronously from there rebuilds/replaces the QCompleter
    object while Qt's own key handling is still mid-execution on it,
    corrupting Qt's internals and crashing the process with no traceback.

    _add() must defer that call (e.g. via QTimer.singleShot(0, ...)) so it
    runs on the next event-loop turn, off the returnPressed call stack.
    """
    invalidate_entity_cache("Genre")

    new_genre = _StubGenre(9001, "Latin Jazz")
    tracks = [_StubTrack(1), _StubTrack(2)]
    stub_add = _StubAdd(new_entity=new_genre)
    controller = _StubController(get=_StubGet(entity_object_return=None), add=stub_add)

    tab = GenresTab(tracks, controller)
    try:
        search = _StubSearch("Latin Jazz", matched_id=None)
        tab._search = search
        tab._add()

        # Not called synchronously within _add() itself...
        assert search.add_to_index_calls == []

        # ...only after the event loop gets a turn.
        for _ in range(50):
            if search.add_to_index_calls:
                break
            qapp.processEvents()
            time.sleep(0.01)
        assert search.add_to_index_calls == [("Latin Jazz", 9001)]
    finally:
        tab.cleanup()


# ---------------------------------------------------------------------------
# AC7 regression: the call sites that used to bake context into the
# ---------------------------------------------------------------------------


class _Type:
    def __init__(self, type_name):
        self.type_name = type_name


class _Artist:
    def __init__(self, artist_id, artist_name, types=(), disambiguation=None):
        self.artist_id = artist_id
        self.artist_name = artist_name
        self.types = list(types)
        self.disambiguation = disambiguation
        self.isgroup = 1
        self.career_span = None


class _Track:
    def __init__(self, track_id, track_name, album_name=None, primary_artists=()):
        self.track_id = track_id
        self.track_name = track_name
        self.album_name = album_name
        self.primary_artists = list(primary_artists)


def test_influences_artist_display_is_bare_name():
    a = _Artist(1, "Portishead", types=[_Type("Group"), _Type("Trip Hop")])
    assert _artist_display(a) == "Portishead"
    assert _build_artist_index([a]) == {"Portishead": 1}


def test_samples_track_display_is_bare_name():
    t = _Track(5, "Glory Box", album_name="Dummy")
    assert _track_display(t) == "Glory Box"
    assert _build_track_index([t]) == {"Glory Box": 5}


def test_influences_pick_feeds_bare_name_into_field(qapp):
    a = _Artist(1, "Portishead", types=[_Type("Group")], disambiguation="Bristol trip hop")
    index = _build_artist_index([a])
    widget = EntityCompleterEdit()
    widget.set_index(index, artist_context_map([a]))

    widget._completer.activated.emit(next(iter(index)))

    assert widget.text() == "Portishead"
    assert widget.split_names() == ["Portishead"]
    assert widget.matched_id() == 1
    # context is carried, just not in the field text
    assert widget._display_to_context["Portishead"] == "Bristol trip hop"


def test_samples_context_map_has_artist_and_album():
    class _Name:
        def __init__(self, n):
            self.artist_name = n

    t = _Track(5, "Glory Box", album_name="Dummy", primary_artists=[_Name("Portishead")])
    assert track_context_map([t]) == {5: "Portishead · Dummy"}
