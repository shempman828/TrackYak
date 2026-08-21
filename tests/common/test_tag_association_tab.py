"""Tests for bug #240: a stale track_id in a genre/mood tab's track list
made the whole batch-add of TrackGenre rows fail on a FOREIGN KEY
constraint, and _add() silently swallowed that failure (add_entities()
rolls back and returns [] on any error, so every legitimately-taggable
track lost the genre too, with no UI feedback).

Covers _BaseTrackAssociationTab._add() (src/common/tag_association_tab.py)
via its GenresTab subclass: it must use add_entities_with_fallback so one
bad row doesn't sink the others, and must surface any dropped rows to the
user instead of only logging.
"""

import time

from src.common.entity_completer_edit import invalidate_entity_cache
from src.track.track_edit_genres import GenresTab

# ---------------------------------------------------------------------------
# Stubs
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


# ---------------------------------------------------------------------------
# _add()
# ---------------------------------------------------------------------------


def test_add_does_not_drop_valid_tracks_when_one_track_id_is_stale(qapp, monkeypatch):
    invalidate_entity_cache("Genre")
    warning_calls = []
    monkeypatch.setattr(
        "src.common.tag_association_tab.QMessageBox.warning",
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
        "src.common.tag_association_tab.QMessageBox.warning",
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


# ---------------------------------------------------------------------------
# _add() -- bug #250: crash on Enter for a brand-new (unmatched) name
# ---------------------------------------------------------------------------


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
