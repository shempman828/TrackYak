"""
Regression tests for RelationshipHelpers (album_editing_relationship_helpers.py).

Motivating bugs:
- add_publisher/add_place/add_album_award dereferenced the newly
  added/looked-up entity without checking it could be None (add_entity
  swallows SQLAlchemyError internally and returns None on failure).
- move_artist_credit/change_credited_alias never checked update_entity's
  return value (it returns False on failure rather than raising), so a
  failed write was silently reported as success.
- move_artist_credit raised an uncaught ValueError if role_assoc was a
  stale reference no longer in the sibling list.
- add_artist_credit applied matched_artist_id/matched_role_id to every
  name even when several names were typed at once, violating its own
  documented precondition.
"""

from types import SimpleNamespace

from PySide6.QtWidgets import QMessageBox, QWidget
import pytest

from src.album.edit.album_editing_relationship_helpers import RelationshipHelpers


class _Add:
    def __init__(self, returns=None):
        self.calls = []
        self._returns = returns or {}

    def add_entity(self, model_name, **kwargs):
        self.calls.append((model_name, kwargs))
        if model_name in self._returns:
            return self._returns[model_name]
        return SimpleNamespace(**kwargs)


class _Get:
    def __init__(self, entity_objects=None):
        self._entity_objects = entity_objects or {}

    def get_entity_object(self, model_name, **_kwargs):
        return self._entity_objects.get(model_name)

    def get_all_entities(self, model_name, **_kwargs):
        return []


class _Update:
    def __init__(self, ok=True):
        self.ok = ok
        self.calls = []

    def update_entity(self, model_name, entity_id, **kwargs):
        self.calls.append((model_name, entity_id, kwargs))
        return self.ok


class _Delete:
    def __init__(self, ok=True):
        self.ok = ok
        self.calls = []

    def delete_entity(self, model_name, *args, **kwargs):
        self.calls.append((model_name, args, kwargs))
        return self.ok


class _Controller:
    def __init__(self, get=None, add=None, update=None, delete=None):
        self.get = get or _Get()
        self.add = add or _Add()
        self.update = update or _Update()
        self.delete = delete or _Delete()


def _helper(controller, album=None):
    refreshed = []
    album = album or SimpleNamespace(album_id=1, album_roles=[], tracks=[])
    helper = RelationshipHelpers(controller, album, lambda: refreshed.append(True), widget=QWidget())
    return helper, refreshed


@pytest.mark.parametrize(
    ("model", "method", "inputs"),
    [
        ("Publisher", "add_publisher", {"publisher_name": "New Publisher"}),
        ("Place", "add_place", {"place_name": "New Place", "association_type": ""}),
        ("Award", "add_album_award", {"award_name": "New Award"}),
    ],
)
def test_failed_create_from_add_entity_does_not_raise_or_refresh(qapp, monkeypatch, model, method, inputs):
    controller = _Controller(get=_Get(entity_objects={model: None}), add=_Add(returns={model: None}))
    helper, refreshed = _helper(controller)
    monkeypatch.setattr("src.album.edit.album_editing_relationship_helpers.AutocompleteDialog.get_inputs", staticmethod(lambda *a, **k: inputs))
    monkeypatch.setattr("src.album.edit.album_editing_relationship_helpers.fetch_association_types", lambda *a, **k: [])
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: None))

    getattr(helper, method)()

    assert refreshed == []


@pytest.mark.parametrize(("update_ok", "expected_refresh"), [(True, [True]), (False, [])])
def test_move_artist_credit_refreshes_only_when_update_succeeds(qapp, monkeypatch, update_ok, expected_refresh):
    ra1 = SimpleNamespace(association_id=1, role_id=10, sort_order=0)
    ra2 = SimpleNamespace(association_id=2, role_id=10, sort_order=1)
    album = SimpleNamespace(album_id=1, album_roles=[ra1, ra2], tracks=[])
    helper, refreshed = _helper(_Controller(update=_Update(ok=update_ok)), album=album)
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: None))

    helper.move_artist_credit(ra1, 1)

    assert refreshed == expected_refresh


def test_move_artist_credit_stale_role_assoc_does_not_raise(qapp):
    ra1 = SimpleNamespace(association_id=1, role_id=10, sort_order=0)
    stale = SimpleNamespace(association_id=99, role_id=10, sort_order=0)
    album = SimpleNamespace(album_id=1, album_roles=[ra1], tracks=[])
    controller = _Controller()
    helper, refreshed = _helper(controller, album=album)

    helper.move_artist_credit(stale, 1)  # must not raise ValueError

    assert refreshed == []


def test_change_credited_alias_surfaces_warning_when_update_fails(qapp, monkeypatch):
    artist = SimpleNamespace(artist_id=1, artist_name="Prince")
    role_assoc = SimpleNamespace(association_id=1, artist=artist, credited_alias_id=None)
    controller = _Controller(update=_Update(ok=False))
    helper, refreshed = _helper(controller)

    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: None))

    helper.change_credited_alias(role_assoc)

    assert refreshed == []


def test_add_artist_credit_ignores_matched_id_when_multiple_names_typed(qapp, monkeypatch):
    """matched_artist_id only makes sense for a single typed name; with
    several names it must not be silently applied to all of them."""
    artists_by_name = {"Deakin": SimpleNamespace(artist_id=1, artist_name="Deakin"), "Panda Bear": SimpleNamespace(artist_id=2, artist_name="Panda Bear")}
    role = SimpleNamespace(role_id=5, role_name="Performer")

    class _MultiGet(_Get):
        def get_entity_object(self, model_name, **kwargs):
            if model_name == "Artist":
                return artists_by_name.get(kwargs.get("artist_name"))
            if model_name == "Role":
                return role
            return None

    add = _Add()
    controller = _Controller(get=_MultiGet(), add=add)
    album = SimpleNamespace(album_id=1, album_roles=[], tracks=[])
    helper, refreshed = _helper(controller, album=album)

    monkeypatch.setattr("src.album.edit.album_editing_relationship_helpers.RelationshipHelpers._prompt_credited_alias", lambda self, artist, current_alias_id=None: (True, None))

    # matched_artist_id=1 would (incorrectly, pre-fix) get applied to BOTH
    # names if the precondition weren't enforced.
    helper.add_artist_credit(["Deakin", "Panda Bear"], ["Performer"], matched_artist_id=1, matched_role_id=None)

    credit_calls = [kw for model, kw in add.calls if model == "AlbumRoleAssociation"]
    artist_ids_credited = {kw["artist_id"] for kw in credit_calls}
    assert artist_ids_credited == {1, 2}
    assert refreshed == [True]


# ---------------------------------------------------------------------------
# Credit conversion, association writes and sort order.
# ---------------------------------------------------------------------------


class _ConvertGet(_Get):
    def __init__(self, track_rows):
        super().__init__()
        self._track_rows = track_rows

    def get_all_entities(self, model_name, **_kwargs):
        return self._track_rows if model_name == "TrackArtistRole" else []


def _convert_setup(track_rows, delete_ok=True):
    add = _Add()
    add.add_entities = lambda model, rows: []
    delete = _Delete(ok=delete_ok)
    controller = _Controller(get=_ConvertGet(track_rows), add=add, delete=delete)
    tracks = [SimpleNamespace(track_id=1), SimpleNamespace(track_id=2)]
    album = SimpleNamespace(album_id=1, album_roles=[], tracks=tracks)
    role_assoc = SimpleNamespace(association_id=9, artist=object(), role=object(), artist_id=3, role_id=4, credited_alias_id=None)
    helper, refreshed = _helper(controller, album=album)
    return helper, refreshed, delete, role_assoc


def test_convert_to_per_track_keeps_album_credit_when_track_rows_are_missing(qapp, monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: shown.append(a[2])))
    helper, _refreshed, delete, role_assoc = _convert_setup(track_rows=[SimpleNamespace(track_id=1)])

    helper.convert_credit_to_per_track(role_assoc)

    assert delete.calls == []
    assert len(shown) == 1


def test_convert_to_per_track_deletes_album_credit_once_every_track_has_it(qapp):
    rows = [SimpleNamespace(track_id=1), SimpleNamespace(track_id=2)]
    helper, refreshed, delete, role_assoc = _convert_setup(track_rows=rows)

    helper.convert_credit_to_per_track(role_assoc)

    assert delete.calls == [("AlbumRoleAssociation", (9,), {})]
    assert refreshed == [True]


def test_convert_to_album_level_warns_when_track_rows_not_removed(qapp, monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: shown.append(a[1])))
    controller = _Controller(delete=_Delete(ok=False))
    album = SimpleNamespace(album_id=1, album_roles=[], tracks=[SimpleNamespace(track_id=1)])
    helper, refreshed = _helper(controller, album=album)

    helper.convert_credit_to_album_level(3, 4)

    assert shown == ["Partly Converted"]
    assert refreshed == [True]


@pytest.mark.parametrize(
    ("method", "inputs", "link_model"),
    [
        ("add_publisher", {"publisher_name": "P"}, "AlbumPublisher"),
        ("add_place", {"place_name": "Pl", "association_type": ""}, "PlaceAssociation"),
        ("add_album_award", {"award_name": "A"}, "AwardAssociation"),
    ],
)
def test_rejected_association_write_warns_and_does_not_refresh(qapp, monkeypatch, method, inputs, link_model):
    shown = []
    parents = {"Publisher": SimpleNamespace(publisher_id=1), "Place": SimpleNamespace(place_id=1), "Award": SimpleNamespace(award_id=1)}
    controller = _Controller(get=_Get(entity_objects=parents), add=_Add(returns={link_model: None}))
    helper, refreshed = _helper(controller)
    monkeypatch.setattr("src.album.edit.album_editing_relationship_helpers.AutocompleteDialog.get_inputs", staticmethod(lambda *a, **k: inputs))
    monkeypatch.setattr("src.album.edit.album_editing_relationship_helpers.fetch_association_types", lambda *a, **k: [])
    monkeypatch.setattr("src.album.edit.album_editing_relationship_helpers.find_or_create_association_type", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: shown.append(a[1])))

    getattr(helper, method)()

    assert shown == ["Not Added"]
    assert refreshed == []


def test_add_place_cancel_is_silent(qapp, monkeypatch):
    toasts = []
    helper, refreshed = _helper(_Controller())
    monkeypatch.setattr("src.album.edit.album_editing_relationship_helpers.AutocompleteDialog.get_inputs", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr("src.album.edit.album_editing_relationship_helpers.fetch_association_types", lambda *a, **k: [])
    monkeypatch.setattr("src.album.edit.album_editing_relationship_helpers.show_status_message", lambda *a, **k: toasts.append(a))

    helper.add_place()

    assert toasts == []
    assert refreshed == []


def test_add_artist_credit_gives_each_new_artist_its_own_sort_order(qapp, monkeypatch):
    artists = {"A": SimpleNamespace(artist_id=1), "B": SimpleNamespace(artist_id=2)}
    role = SimpleNamespace(role_id=5)

    class _NameGet(_Get):
        def get_entity_object(self, model_name, **kwargs):
            return artists.get(kwargs.get("artist_name")) if model_name == "Artist" else role

    add = _Add()
    existing = SimpleNamespace(role_id=5, sort_order=3)
    album = SimpleNamespace(album_id=1, album_roles=[existing], tracks=[])
    helper, _refreshed = _helper(_Controller(get=_NameGet(), add=add), album=album)
    monkeypatch.setattr(RelationshipHelpers, "_prompt_credited_alias", lambda self, artist, current_alias_id=None: (True, None))

    helper.add_artist_credit(["A", "B"], ["Producer"])

    orders = [kw["sort_order"] for model, kw in add.calls if model == "AlbumRoleAssociation"]
    assert orders == [4, 5]
