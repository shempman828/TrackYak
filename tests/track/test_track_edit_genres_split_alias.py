"""Tests for split-alias awareness in the track editor's manual "Add Genre"
field (docs/specs/split_and_merge_aliases.md, GenresTab._find_or_create).
Typing a name that matches a GenreSplitAlias rule should expand into every
target genre, same as the file-tag import path
(tests/importing/test_genre_split_alias.py) and MB import.

GenresTab._find_or_create only touches self.controller/self.model_name/
self.name_field/self._known_entities(), so it's exercised directly on a
lightweight stand-in rather than constructing the real Qt widget.
"""

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.db_helpers.add import AddToDB
from src.db.db_helpers.get import GetFromDB
from src.db.db_tables.base import Base
from src.db.db_tables.genre import Genre, GenreSplitAlias
from src.track.track_edit_genres import GenresTab


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
