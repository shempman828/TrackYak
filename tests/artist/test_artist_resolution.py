"""Regression coverage for the alias-aware Artist resolution shared by
file-tag import and manual track-role editing (see
docs/specs/split_and_merge_aliases.md's Non-goals section, which flagged
library_import.py::_get_or_create_artist,
library_import_album.py::_process_artist_name, and
track_edit_roles.py::_resolve_artist as exact-match-only, no alias
fallback). All three now delegate to
src.artist.artist_resolution.resolve_or_create_artist.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.artist.artist_resolution import resolve_or_create_artist
from src.db.db_helpers.add import AddToDB
from src.db.db_helpers.get import GetFromDB
from src.db.db_tables.artist import Artist, ArtistAlias
from src.db.db_tables.base import Base
from src.importing.library_import import TrackImporter
from src.importing.library_import_album import AlbumImporter


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


def _add_aliased_artist(session, canonical_name, alias_name):
    canonical = Artist(artist_name=canonical_name)
    session.add(canonical)
    session.commit()
    session.add(ArtistAlias(artist_id=canonical.artist_id, alias_name=alias_name))
    session.commit()
    return canonical


# ---------------------------------------------------------------------------
# resolve_or_create_artist itself
# ---------------------------------------------------------------------------


def test_resolves_existing_artist_by_exact_name(controller):
    session = controller.get.session
    existing = Artist(artist_name="Radiohead")
    session.add(existing)
    session.commit()

    artist = resolve_or_create_artist(controller, "Radiohead")

    assert artist.artist_id == existing.artist_id


def test_resolves_via_alias_instead_of_creating_duplicate(controller):
    session = controller.get.session
    canonical = _add_aliased_artist(session, "Ahmet Ertegun", "A. Ertegun")

    artist = resolve_or_create_artist(controller, "A. Ertegun")

    assert artist.artist_id == canonical.artist_id
    assert session.query(Artist).count() == 1


def test_creates_new_artist_with_isgroup_when_no_match(controller):
    artist = resolve_or_create_artist(controller, "Some New Band", isgroup=1)

    assert artist is not None
    assert artist.artist_name == "Some New Band"
    assert artist.isgroup == 1


def test_blank_name_returns_none(controller):
    assert resolve_or_create_artist(controller, "   ") is None
    assert resolve_or_create_artist(controller, "") is None


# ---------------------------------------------------------------------------
# TrackImporter._get_or_create_artist (file-tag track import)
# ---------------------------------------------------------------------------


def test_track_importer_resolves_artist_via_alias(controller):
    session = controller.get.session
    canonical = _add_aliased_artist(session, "The Beach Boys", "Beach Boys")

    importer = TrackImporter(controller)
    artist = importer._get_or_create_artist("Beach Boys")

    assert artist.artist_id == canonical.artist_id
    assert session.query(Artist).count() == 1


# ---------------------------------------------------------------------------
# AlbumImporter._process_artist_name (file-tag album-artist import)
# ---------------------------------------------------------------------------


def test_album_importer_resolves_artist_via_alias(controller):
    session = controller.get.session
    canonical = _add_aliased_artist(session, "Various Artists", "V.A.")

    album_importer = AlbumImporter(controller)
    artist_id = album_importer._process_artist_name("V.A.", set())

    assert artist_id == canonical.artist_id
    assert session.query(Artist).count() == 1
