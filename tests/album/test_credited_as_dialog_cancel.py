"""Regression: cancelling the "Credit <artist> as…" dialog must abort the
credit that triggered it, not silently fall back to the artist's canonical
name.

Before the fix, `_prompt_credited_alias` returned the (unchanged)
`current_alias_id` on both "picked the canonical name" and "hit Cancel", so
the callers in album_editing_relationship_helpers.add_artist_credit and
track_edit_roles could not tell a cancel apart from a real choice and added
the credit regardless. It now returns ``(accepted, alias_id)`` and the
callers bail when ``accepted`` is False.
"""

from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QDialog, QWidget

from src.album.album_editing_relationship_helpers import RelationshipHelpers
from src.track.track_edit_roles import RolesTab


# ---------------------------------------------------------------------------
# Shared stubs
# ---------------------------------------------------------------------------


def _fake_dialog(*, accept, alias_id=None):
    """A stand-in for CreditedAsDialog whose exec() result is fixed."""

    def _factory(*_args, **_kwargs):
        return SimpleNamespace(
            exec=lambda: QDialog.Accepted if accept else QDialog.Rejected,
            selected_alias_id=lambda: alias_id,
        )

    return _factory


class _Get:
    def __init__(self, artist=None, role=None, aliases=()):
        self._artist = artist
        self._role = role
        self._aliases = list(aliases)

    def get_entity_object(self, model_name, **_filters):
        if model_name == "Artist":
            return self._artist
        if model_name == "Role":
            return self._role
        return None

    def get_all_entities(self, model_name, **_kwargs):
        if model_name == "ArtistAlias":
            return list(self._aliases)
        return []

    # entity_completer_edit preload path used when RolesTab builds its widgets
    def count_entities(self, _model_name):
        return 0


class _Add:
    def __init__(self):
        self.calls = []

    def add_entity(self, model_name, **kwargs):
        self.calls.append((model_name, kwargs))
        return SimpleNamespace(**kwargs)


class _Controller:
    def __init__(self, get):
        self.get = get
        self.add = _Add()


# ---------------------------------------------------------------------------
# Album flow: RelationshipHelpers.add_artist_credit
# ---------------------------------------------------------------------------


def _album_helper(aliases):
    artist = SimpleNamespace(artist_id=1, artist_name="Prince")
    role = SimpleNamespace(role_id=2, role_name="Performer")
    controller = _Controller(_Get(artist=artist, role=role, aliases=aliases))
    album = SimpleNamespace(album_id=5, album_roles=[])
    refreshed = []
    helper = RelationshipHelpers(
        controller, album, lambda: refreshed.append(True), widget=QWidget()
    )
    return helper, controller, refreshed


def test_add_artist_credit_aborts_when_credited_as_dialog_cancelled(qapp, monkeypatch):
    helper, controller, _ = _album_helper(
        aliases=[SimpleNamespace(alias_id=9, alias_name="TAFKAP")]
    )
    monkeypatch.setattr(
        "src.album.album_editing_relationship_helpers.CreditedAsDialog",
        _fake_dialog(accept=False),
    )

    helper.add_artist_credit(
        ["Prince"], ["Performer"], matched_artist_id=1, matched_role_id=2
    )

    assert not any(
        model == "AlbumRoleAssociation" for model, _ in controller.add.calls
    ), "credit was added even though the Credit-as dialog was cancelled"


def test_add_artist_credit_proceeds_when_credited_as_dialog_accepted(qapp, monkeypatch):
    helper, controller, _ = _album_helper(
        aliases=[SimpleNamespace(alias_id=9, alias_name="TAFKAP")]
    )
    monkeypatch.setattr(
        "src.album.album_editing_relationship_helpers.CreditedAsDialog",
        _fake_dialog(accept=True, alias_id=9),
    )

    helper.add_artist_credit(
        ["Prince"], ["Performer"], matched_artist_id=1, matched_role_id=2
    )

    credit_calls = [
        kwargs for model, kwargs in controller.add.calls if model == "AlbumRoleAssociation"
    ]
    assert len(credit_calls) == 1
    assert credit_calls[0]["credited_alias_id"] == 9


# ---------------------------------------------------------------------------
# Track flow: RolesTab._prompt_add_role_for_artist
# ---------------------------------------------------------------------------


def _roles_tab(monkeypatch, aliases):
    artist = SimpleNamespace(artist_id=1, artist_name="Prince")
    controller = _Controller(_Get(artist=artist, aliases=aliases))
    tab = RolesTab([SimpleNamespace(track_id=1, artist_roles=[])], controller)

    tab._role_edit = SimpleNamespace(
        split_names=lambda: ["Producer"],
        matched_id=lambda: None,
        reset=lambda: None,
        setFocus=lambda: None,
    )
    tab._resolve_role = lambda name, matched_id=None: SimpleNamespace(role_id=2)
    tab.load = lambda tracks: None

    added = []
    tab._batch_add_track_artist_role = (
        lambda artist_id, role_id, credited_alias_id=None: added.append(
            (artist_id, role_id, credited_alias_id)
        )
    )
    return tab, added


def test_track_add_role_aborts_when_credited_as_dialog_cancelled(qapp, monkeypatch):
    tab, added = _roles_tab(
        monkeypatch, aliases=[SimpleNamespace(alias_id=9, alias_name="TAFKAP")]
    )
    monkeypatch.setattr(
        "src.track.track_edit_roles.CreditedAsDialog", _fake_dialog(accept=False)
    )
    try:
        tab._prompt_add_role_for_artist(1, "Prince")
        assert added == [], "role was added even though the Credit-as dialog was cancelled"
    finally:
        tab.cleanup()


def test_track_add_role_proceeds_when_credited_as_dialog_accepted(qapp, monkeypatch):
    tab, added = _roles_tab(
        monkeypatch, aliases=[SimpleNamespace(alias_id=9, alias_name="TAFKAP")]
    )
    monkeypatch.setattr(
        "src.track.track_edit_roles.CreditedAsDialog",
        _fake_dialog(accept=True, alias_id=9),
    )
    try:
        tab._prompt_add_role_for_artist(1, "Prince")
        assert added == [(1, 2, 9)]
    finally:
        tab.cleanup()
