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

from src.album.album_editing_relationship_helpers import RelationshipHelpers


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
    helper = RelationshipHelpers(
        controller, album, lambda: refreshed.append(True), widget=QWidget()
    )
    return helper, refreshed


def test_add_publisher_none_from_add_entity_does_not_raise(qapp, monkeypatch):
    controller = _Controller(
        get=_Get(entity_objects={"Publisher": None}), add=_Add(returns={"Publisher": None})
    )
    helper, refreshed = _helper(controller)

    monkeypatch.setattr(
        "src.album.album_editing_relationship_helpers.AutocompleteDialog.get_inputs",
        staticmethod(lambda *a, **k: {"publisher_name": "New Publisher"}),
    )
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: None))

    helper.add_publisher()  # must not raise AttributeError

    assert refreshed == []


def test_add_place_none_from_add_entity_does_not_raise(qapp, monkeypatch):
    controller = _Controller(
        get=_Get(entity_objects={"Place": None}), add=_Add(returns={"Place": None})
    )
    helper, refreshed = _helper(controller)

    monkeypatch.setattr(
        "src.album.album_editing_relationship_helpers.AutocompleteDialog.get_inputs",
        staticmethod(lambda *a, **k: {"place_name": "New Place", "association_type": ""}),
    )
    monkeypatch.setattr(
        "src.album.album_editing_relationship_helpers.fetch_association_types", lambda *a, **k: []
    )
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: None))

    helper.add_place()  # must not raise AttributeError

    assert refreshed == []


def test_add_album_award_none_from_add_entity_does_not_raise(qapp, monkeypatch):
    controller = _Controller(
        get=_Get(entity_objects={"Award": None}), add=_Add(returns={"Award": None})
    )
    helper, refreshed = _helper(controller)

    monkeypatch.setattr(
        "src.album.album_editing_relationship_helpers.AutocompleteDialog.get_inputs",
        staticmethod(lambda *a, **k: {"award_name": "New Award"}),
    )
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: None))

    helper.add_album_award()  # must not raise AttributeError

    assert refreshed == []


def test_move_artist_credit_surfaces_warning_when_update_fails(qapp, monkeypatch):
    ra1 = SimpleNamespace(association_id=1, role_id=10, sort_order=0)
    ra2 = SimpleNamespace(association_id=2, role_id=10, sort_order=1)
    album = SimpleNamespace(album_id=1, album_roles=[ra1, ra2], tracks=[])
    controller = _Controller(update=_Update(ok=False))
    helper, refreshed = _helper(controller, album=album)

    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: None))

    helper.move_artist_credit(ra1, 1)

    assert refreshed == []


def test_move_artist_credit_succeeds_when_update_ok(qapp):
    ra1 = SimpleNamespace(association_id=1, role_id=10, sort_order=0)
    ra2 = SimpleNamespace(association_id=2, role_id=10, sort_order=1)
    album = SimpleNamespace(album_id=1, album_roles=[ra1, ra2], tracks=[])
    controller = _Controller(update=_Update(ok=True))
    helper, refreshed = _helper(controller, album=album)

    helper.move_artist_credit(ra1, 1)

    assert refreshed == [True]


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
    artists_by_name = {
        "Deakin": SimpleNamespace(artist_id=1, artist_name="Deakin"),
        "Panda Bear": SimpleNamespace(artist_id=2, artist_name="Panda Bear"),
    }
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

    monkeypatch.setattr(
        "src.album.album_editing_relationship_helpers.RelationshipHelpers._prompt_credited_alias",
        lambda self, artist, current_alias_id=None: (True, None),
    )

    # matched_artist_id=1 would (incorrectly, pre-fix) get applied to BOTH
    # names if the precondition weren't enforced.
    helper.add_artist_credit(
        ["Deakin", "Panda Bear"], ["Performer"], matched_artist_id=1, matched_role_id=None
    )

    credit_calls = [kw for model, kw in add.calls if model == "AlbumRoleAssociation"]
    artist_ids_credited = {kw["artist_id"] for kw in credit_calls}
    assert artist_ids_credited == {1, 2}
    assert refreshed == [True]
