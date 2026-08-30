"""Regression coverage for AlbumsTab._resolve_album
(src/track/track_edit_album.py): the Album / Virtual-Appearance search
fields use the shared entity completer (build_entity_search_widget), so
resolution now goes: the completer's locked pick (matched_id) first, else
find-or-create by the typed name -- an existing same-named album (any case)
always wins over creating a duplicate, and a genuinely new name creates one
and hot-registers it into the completer index + shared cache.
"""

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.common.entity_completer_edit import invalidate_entity_cache
from src.db.db_helpers.add import AddToDB
from src.db.db_helpers.get import GetFromDB
from src.db.db_helpers.update import UpdateDB
from src.db.db_tables.base import Base
from src.track.track_edit_album import AlbumsTab


@pytest.fixture(autouse=True)
def _clear_entity_cache():
    # get_cached_entities() memoizes the Album row list at module scope --
    # drop it so each test starts from its own empty in-memory table.
    invalidate_entity_cache("Album")
    yield
    invalidate_entity_cache("Album")


@pytest.fixture
def controller():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield SimpleNamespace(get=GetFromDB(session), add=AddToDB(session), update=UpdateDB(session))
    session.close()


def _tab(controller):
    tab = AlbumsTab.__new__(AlbumsTab)
    tab.controller = controller
    return tab


class _FakeSearch:
    """Stands in for EntityCompleterEdit/BoundedSearchEdit -- only the
    surface _resolve_album() touches."""

    def __init__(self, text: str = "", matched_id=None):
        self._text = text
        self._matched_id = matched_id
        self.added: list[tuple[str, int]] = []

    def text(self) -> str:
        return self._text

    def matched_id(self):
        return self._matched_id

    def known_matches(self) -> list:
        return []

    def add_to_index(self, display: str, entity_id) -> None:
        self.added.append((display, entity_id))


def test_locked_pick_is_used_without_creating(controller):
    existing = controller.add.add_entity("Album", album_name="Kind of Blue")

    widget = _FakeSearch(text="ignored", matched_id=existing.album_id)
    album = _tab(controller)._resolve_album(widget)

    assert album.album_id == existing.album_id
    assert controller.get.count_entities("Album") == 1


def test_name_match_reuses_existing_case_insensitively(controller):
    existing = controller.add.add_entity("Album", album_name="Kind of Blue")

    widget = _FakeSearch(text="  kind of BLUE ", matched_id=None)
    album = _tab(controller)._resolve_album(widget)

    assert album.album_id == existing.album_id
    assert controller.get.count_entities("Album") == 1
    assert widget.added == []  # not a new row -- nothing to hot-register


def test_new_name_creates_and_hot_registers(controller, qapp):
    widget = _FakeSearch(text="Brand New Record", matched_id=None)
    album = _tab(controller)._resolve_album(widget)

    assert album is not None
    assert album.album_name == "Brand New Record"
    assert controller.get.count_entities("Album") == 1

    # add_to_index is deferred via QTimer.singleShot to dodge a re-entrancy
    # crash when this runs nested in the completer's key handling.
    qapp.processEvents()
    assert widget.added == [("Brand New Record", album.album_id)]


def test_blank_text_resolves_to_none(controller):
    widget = _FakeSearch(text="   ", matched_id=None)
    assert _tab(controller)._resolve_album(widget) is None
    assert controller.get.count_entities("Album") == 0
