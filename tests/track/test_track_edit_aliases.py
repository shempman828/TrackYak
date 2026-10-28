"""Alias-aware resolution in the track editor's manual Add Genre and Add
Role fields.
"""

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.db_helpers.add import AddToDB
from src.db.db_helpers.get import GetFromDB
from src.db.db_tables.artist import Artist, ArtistAlias
from src.db.db_tables.base import Base
from src.db.db_tables.genre import Genre, GenreSplitAlias
from src.track.edit.track_edit_genres import GenresTab
from src.track.edit.track_edit_roles import RolesTab

# ---------------------------------------------------------------------------
# Tests for split-alias awareness in the track editor's manual "Add Genre"
# ---------------------------------------------------------------------------


class _Controller:
    def __init__(self, session):
        self.get = GetFromDB(session)
        self.add = AddToDB(session)


@pytest.fixture
def controller():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield _Controller(session)
    session.close()


def _tab_stand_in(controller):
    return SimpleNamespace(
        controller=controller,
        model_name=GenresTab.model_name,
        name_field=GenresTab.name_field,
        _known_entities=lambda: [],
    )


def test_find_or_create_returns_every_split_alias_target(controller):
    session = controller.get.session
    rock = Genre(genre_name="Rock")
    metal = Genre(genre_name="Metal")
    session.add_all([rock, metal])
    session.commit()
    session.add_all(
        [
            GenreSplitAlias(alias_name="Rock/Metal", genre_id=rock.genre_id, sort_order=0),
            GenreSplitAlias(alias_name="Rock/Metal", genre_id=metal.genre_id, sort_order=1),
        ]
    )
    session.commit()

    result = GenresTab._find_or_create(_tab_stand_in(controller), "Rock/Metal")

    assert isinstance(result, list)
    assert {g.genre_name for g in result} == {"Rock", "Metal"}


def test_find_or_create_non_matching_name_returns_single_entity(controller):
    result = GenresTab._find_or_create(_tab_stand_in(controller), "Jazz")

    assert not isinstance(result, list)
    assert result.genre_name == "Jazz"


# ---------------------------------------------------------------------------
# Alias-aware resolution for the track editor's manual "Add role" field
# ---------------------------------------------------------------------------


class _ControllerAlias:
    def __init__(self, session):
        self.get = GetFromDB(session)
        self.add = AddToDB(session)


@pytest.fixture
def controller_alias():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield _ControllerAlias(session)
    session.close()


class _StubArtistSearch:
    def __init__(self):
        self.indexed = []

    def add_to_index(self, name, artist_id):
        self.indexed.append((name, artist_id))


def _tab_stand_in_alias(controller_alias):
    return SimpleNamespace(controller=controller_alias, _artist_search=_StubArtistSearch())


def test_resolve_artist_uses_alias_instead_of_creating_duplicate(controller_alias):
    session = controller_alias.get.session
    canonical = Artist(artist_name="Ahmet Ertegun")
    session.add(canonical)
    session.commit()
    session.add(ArtistAlias(artist_id=canonical.artist_id, alias_name="A. Ertegun"))
    session.commit()

    tab = _tab_stand_in_alias(controller_alias)
    artist = RolesTab._resolve_artist(tab, "A. Ertegun")

    assert artist.artist_id == canonical.artist_id
    assert session.query(Artist).count() == 1
    assert tab._artist_search.indexed == [("Ahmet Ertegun", canonical.artist_id)]


def test_resolve_artist_creates_new_artist_when_no_match(controller_alias):
    session = controller_alias.get.session

    tab = _tab_stand_in_alias(controller_alias)
    artist = RolesTab._resolve_artist(tab, "Brand New Artist")

    assert artist is not None
    assert artist.artist_name == "Brand New Artist"
    assert session.query(Artist).count() == 1


def test_resolve_artist_with_matched_id_bypasses_name_lookup(controller_alias):
    session = controller_alias.get.session
    existing = Artist(artist_name="Some Artist")
    session.add(existing)
    session.commit()

    tab = _tab_stand_in_alias(controller_alias)
    artist = RolesTab._resolve_artist(tab, "irrelevant text", matched_id=existing.artist_id)

    assert artist.artist_id == existing.artist_id


def test_role_chip_label_keeps_ampersand_unescaped(qapp):
    """A role name like "R&B" goes straight into a QLabel chip. Unlike
    QPushButton, a plain QLabel without a buddy doesn't treat a lone '&' as
    a mnemonic prefix, so it must be passed through as-is (doubling it would
    render literally as "R&&B")."""
    from PySide6.QtWidgets import QLabel

    tab = SimpleNamespace(_sorted_roles=RolesTab._sorted_roles, _on_convert_to_album=None)
    tab._build_role_chip = lambda *args: RolesTab._build_role_chip(tab, *args)
    cell = RolesTab._build_roles_cell(tab, artist_id=1, artist_name="Nina Simone", roles={7: "R&B"})

    labels = [w.text() for w in cell.findChildren(QLabel)]
    assert "R&B" in labels
