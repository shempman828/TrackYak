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


# ---- test_track_edit_album_resolve_album.py ----------------------------------
@pytest.fixture(autouse=True)
def _clear_entity_cache():
    # get_cached_entities() memoizes the Album row list at module scope --
    # drop it so each test starts from its own empty in-memory table.
    invalidate_entity_cache("Album")
    yield
    invalidate_entity_cache("Album")


@pytest.fixture
def controller_ra():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield SimpleNamespace(get=GetFromDB(session), add=AddToDB(session), update=UpdateDB(session))
    session.close()


def _tab_ra(controller_ra):
    tab = AlbumsTab.__new__(AlbumsTab)
    tab.controller = controller_ra
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


def test_locked_pick_is_used_without_creating(controller_ra):
    existing = controller_ra.add.add_entity("Album", album_name="Kind of Blue")

    widget = _FakeSearch(text="ignored", matched_id=existing.album_id)
    album = _tab_ra(controller_ra)._resolve_album(widget)

    assert album.album_id == existing.album_id
    assert controller_ra.get.count_entities("Album") == 1


def test_name_match_reuses_existing_case_insensitively(controller_ra):
    existing = controller_ra.add.add_entity("Album", album_name="Kind of Blue")

    widget = _FakeSearch(text="  kind of BLUE ", matched_id=None)
    album = _tab_ra(controller_ra)._resolve_album(widget)

    assert album.album_id == existing.album_id
    assert controller_ra.get.count_entities("Album") == 1
    assert widget.added == []  # not a new row -- nothing to hot-register


def test_new_name_creates_and_hot_registers(controller_ra, qapp):
    widget = _FakeSearch(text="Brand New Record", matched_id=None)
    album = _tab_ra(controller_ra)._resolve_album(widget)

    assert album is not None
    assert album.album_name == "Brand New Record"
    assert controller_ra.get.count_entities("Album") == 1

    # add_to_index is deferred via QTimer.singleShot to dodge a re-entrancy
    # crash when this runs nested in the completer's key handling.
    qapp.processEvents()
    assert widget.added == [("Brand New Record", album.album_id)]


def test_blank_text_resolves_to_none(controller_ra):
    widget = _FakeSearch(text="   ", matched_id=None)
    assert _tab_ra(controller_ra)._resolve_album(widget) is None
    assert controller_ra.get.count_entities("Album") == 0


# ---- test_track_edit_album_resolve_artist_mbid_conflict.py -------------------
# Regression coverage for AlbumsTab._resolve_or_create_artist
# (src/track/track_edit_album.py): same MBID/name-match ordering as the
# other MB-import resolvers -- MBID match first, then a name match backfills
# the MBID only if the row has none yet, and a name match whose row already
# carries a *different* MBID is ignored (not reused) so a new Artist gets
# created instead of merging two people MusicBrainz considers distinct.
@pytest.fixture
def controller_amc():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield SimpleNamespace(get=GetFromDB(session), add=AddToDB(session), update=UpdateDB(session))
    session.close()


def _tab_amc(controller_amc):
    tab = AlbumsTab.__new__(AlbumsTab)
    tab.controller = controller_amc
    return tab


def test_mbid_match_is_used_as_is(controller_amc):
    existing = controller_amc.add.add_entity(
        "Artist", artist_name="Some Other Name", MBID="mbid-new"
    )

    jobs: list[tuple[str, int, str]] = []
    artist = _tab_amc(controller_amc)._resolve_or_create_artist("mbid-new", "John Smith", jobs)

    assert artist.artist_id == existing.artist_id
    # An existing MBID match is left untouched -- no awards enrichment queued.
    assert jobs == []


def test_name_match_with_no_mbid_backfills(controller_amc):
    existing = controller_amc.add.add_entity("Artist", artist_name="John Smith", MBID=None)

    jobs: list[tuple[str, int, str]] = []
    artist = _tab_amc(controller_amc)._resolve_or_create_artist("mbid-new", "John Smith", jobs)

    assert artist.artist_id == existing.artist_id
    assert artist.MBID == "mbid-new"
    # The MBID was just backfilled, so awards enrichment is queued for it
    # (run later on a worker thread by _import_award_data, never inline).
    assert jobs == [("Artist", existing.artist_id, "mbid-new")]


def test_name_match_with_conflicting_mbid_creates_new_artist(controller_amc):
    conflicting = controller_amc.add.add_entity(
        "Artist", artist_name="John Smith", MBID="mbid-different"
    )

    jobs: list[tuple[str, int, str]] = []
    artist = _tab_amc(controller_amc)._resolve_or_create_artist("mbid-new", "John Smith", jobs)

    assert artist.artist_id != conflicting.artist_id
    assert artist.MBID == "mbid-new"
    assert jobs == [("Artist", artist.artist_id, "mbid-new")]
    untouched = controller_amc.get.get_entity_object("Artist", artist_id=conflicting.artist_id)
    assert untouched.MBID == "mbid-different"
