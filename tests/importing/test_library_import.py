"""TrackImporter/AlbumImporter file-tag import paths: genre/role aliases and
split aliases, the role parse-ignore list, directory scan robustness,
per-track transaction rollback, and disc creation.
"""

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.db_helpers.add import AddToDB
from src.db.db_helpers.get import GetFromDB
from src.db.db_tables.artist import Artist
from src.db.db_tables.associations import TrackArtistRole
from src.db.db_tables.base import Base
from src.db.db_tables.genre import Genre, GenreSplitAlias
from src.db.db_tables.role import Role, RoleAlias, RoleSplitAlias
from src.db.db_tables.track import Track
from src.importing import library_import
from src.importing.library_import import ImportResult, TrackImporter
from src.importing.library_import_album import AlbumImporter

# ---------------------------------------------------------------------------
# Tests for RoleAlias / RoleSplitAlias awareness in the file-tag import
# ---------------------------------------------------------------------------


def _artist(session, name):
    artist = Artist(artist_name=name)
    session.add(artist)
    session.commit()
    return artist


def _track(session, name="Some Track"):
    track = Track(track_name=name, track_file_path=f"/music/{name}.flac")
    session.add(track)
    session.commit()
    return track


def _roles_on(session, track):
    rows = session.query(TrackArtistRole).filter_by(track_id=track.track_id).all()
    return {
        session.get(Role, r.role_id).role_name: session.get(Artist, r.artist_id).artist_name
        for r in rows
    }


def test_aliased_role_name_resolves_to_canonical_role(controller):
    session = controller.get.session
    mixing = Role(role_name="Mixing")
    session.add(mixing)
    session.commit()
    session.add(RoleAlias(alias_name="Mixer", role_id=mixing.role_id))
    session.commit()

    track = _track(session)
    artist = _artist(session, "Some Person")

    TrackImporter(controller)._create_track_artist_relationships(track, {"Mixer": [artist]}, {})
    session.commit()

    assert _roles_on(session, track) == {"Mixing": "Some Person"}
    # No duplicate "Mixer" Role was created.
    assert session.query(Role).filter_by(role_name="Mixer").first() is None


def test_unaliased_role_name_still_created_as_before(controller):
    session = controller.get.session
    track = _track(session)
    artist = _artist(session, "Some Person")

    TrackImporter(controller)._create_track_artist_relationships(track, {"Producer": [artist]}, {})
    session.commit()

    assert _roles_on(session, track) == {"Producer": "Some Person"}
    assert session.query(Role).filter_by(role_name="Producer").one()


def test_split_alias_role_name_attaches_every_target_role(controller):
    session = controller.get.session
    mixing = Role(role_name="Mixing")
    mastering = Role(role_name="Mastering")
    session.add_all([mixing, mastering])
    session.commit()
    session.add_all(
        [
            RoleSplitAlias(alias_name="Mixing & Mastering", role_id=mixing.role_id, sort_order=0),
            RoleSplitAlias(
                alias_name="Mixing & Mastering", role_id=mastering.role_id, sort_order=1
            ),
        ]
    )
    session.commit()

    track = _track(session)
    artist = _artist(session, "Some Person")

    TrackImporter(controller)._create_track_artist_relationships(
        track, {"Mixing & Mastering": [artist]}, {}
    )
    session.commit()

    assert _roles_on(session, track) == {"Mixing": "Some Person", "Mastering": "Some Person"}
    # No combined "Mixing & Mastering" Role was created.
    assert session.query(Role).filter_by(role_name="Mixing & Mastering").first() is None


def test_split_alias_expands_once_per_artist_on_the_credit(controller):
    session = controller.get.session
    viola = Role(role_name="Viola")
    violin = Role(role_name="Violin")
    session.add_all([viola, violin])
    session.commit()
    session.add_all(
        [
            RoleSplitAlias(alias_name="Viola & Violin", role_id=viola.role_id, sort_order=0),
            RoleSplitAlias(alias_name="Viola & Violin", role_id=violin.role_id, sort_order=1),
        ]
    )
    session.commit()

    track = _track(session)
    a1 = _artist(session, "Player One")
    a2 = _artist(session, "Player Two")

    TrackImporter(controller)._create_track_artist_relationships(
        track, {"Viola & Violin": [a1, a2]}, {}
    )
    session.commit()

    rows = session.query(TrackArtistRole).filter_by(track_id=track.track_id).all()
    pairs = {
        (session.get(Artist, r.artist_id).artist_name, session.get(Role, r.role_id).role_name)
        for r in rows
    }
    assert pairs == {
        ("Player One", "Viola"),
        ("Player One", "Violin"),
        ("Player Two", "Viola"),
        ("Player Two", "Violin"),
    }


# ---------------------------------------------------------------------------
# Tests for split-alias awareness in the file-tag genre import path
# ---------------------------------------------------------------------------


def test_matching_genre_name_attaches_every_target_genre(controller):
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

    track = _track(session)
    importer = TrackImporter(controller)
    importer._create_track_genre_relationships(track, {"genre_name": ["Rock/Metal"]})
    session.commit()

    session.expire_all()
    genre_names = {g.genre_name for g in track.genres}
    assert genre_names == {"Rock", "Metal"}
    combined = session.query(Genre).filter_by(genre_name="Rock/Metal").first()
    assert combined is None


def test_non_matching_genre_name_behaves_as_before(controller):
    """Regression check: a genre name with no split-alias rule still
    resolves through the ordinary single find-or-create path."""
    session = controller.get.session
    track = _track(session)
    importer = TrackImporter(controller)
    importer._create_track_genre_relationships(track, {"genre_name": ["Jazz"]})
    session.commit()

    session.expire_all()
    assert {g.genre_name for g in track.genres} == {"Jazz"}


# ---------------------------------------------------------------------------
# Tests for the role parse-ignore list in the file-tag import path
# ---------------------------------------------------------------------------


@pytest.fixture
def set_excluded_roles(monkeypatch):
    def _set(names):
        monkeypatch.setattr(library_import.app_config, "get_excluded_roles", lambda: list(names))

    return _set


def test_excluded_role_creates_no_role_or_junction_row(controller, set_excluded_roles):
    session = controller.get.session
    set_excluded_roles(["Engineer"])
    track = _track(session)
    eng = _artist(session, "Some Engineer")

    TrackImporter(controller)._create_track_artist_relationships(track, {"Engineer": [eng]}, {})
    session.commit()

    assert _roles_on(session, track) == {}
    assert session.query(Role).filter_by(role_name="Engineer").first() is None


def test_non_excluded_sibling_credit_still_created(controller, set_excluded_roles):
    session = controller.get.session
    set_excluded_roles(["Engineer"])
    track = _track(session)
    eng = _artist(session, "Some Engineer")
    comp = _artist(session, "Some Composer")

    TrackImporter(controller)._create_track_artist_relationships(
        track, {"Engineer": [eng], "Composer": [comp]}, {}
    )
    session.commit()

    assert _roles_on(session, track) == {"Composer": "Some Composer"}


def test_role_name_match_is_case_insensitive(controller, set_excluded_roles):
    session = controller.get.session
    set_excluded_roles(["engineer"])
    track = _track(session)
    eng = _artist(session, "Some Engineer")

    TrackImporter(controller)._create_track_artist_relationships(track, {"Engineer": [eng]}, {})
    session.commit()

    assert _roles_on(session, track) == {}


def test_empty_exclusion_list_changes_nothing(controller, set_excluded_roles):
    session = controller.get.session
    set_excluded_roles([])
    track = _track(session)
    eng = _artist(session, "Some Engineer")
    comp = _artist(session, "Some Composer")

    TrackImporter(controller)._create_track_artist_relationships(
        track, {"Engineer": [eng], "Composer": [comp]}, {}
    )
    session.commit()

    assert _roles_on(session, track) == {"Engineer": "Some Engineer", "Composer": "Some Composer"}


# ---------------------------------------------------------------------------
# Regression test: TrackImporter._scan_directory must keep files already
# ---------------------------------------------------------------------------


def test_scan_directory_keeps_partial_results_on_oserror(tmp_path, monkeypatch):
    good1 = tmp_path / "a.mp3"
    good1.touch()
    sub = tmp_path / "sub"
    sub.mkdir()
    good2 = sub / "b.mp3"
    good2.touch()

    def flaky_rglob(self, _pattern):
        yield good1
        yield good2
        raise OSError("Permission denied")

    monkeypatch.setattr(Path, "rglob", flaky_rglob)

    importer = TrackImporter(controller=None)
    result = importer._scan_directory(tmp_path)

    assert set(result) == {str(good1), str(good2)}


def test_scan_directory_returns_empty_list_when_nothing_found_before_error(tmp_path, monkeypatch):
    def flaky_rglob(self, _pattern):
        raise OSError("Permission denied")
        yield  # pragma: no cover - makes this a generator function

    monkeypatch.setattr(Path, "rglob", flaky_rglob)

    importer = TrackImporter(controller=None)
    assert importer._scan_directory(tmp_path) == []


# ---------------------------------------------------------------------------
# Regression test: TrackImporter.add_track must roll back and return
# ---------------------------------------------------------------------------


def _metadata(track_name):
    return {
        "track_name": track_name,
        "album_album_name": "Some Album",
        "artist_name": "Some Artist",
    }


def test_non_sqlalchemy_exception_rolls_back_and_does_not_break_later_tracks(
    controller, monkeypatch, tmp_path
):
    files = [tmp_path / f"t{i}.mp3" for i in range(3)]
    for f in files:
        f.touch()

    metadatas = iter([_metadata("Track 1"), _metadata("Track 2"), _metadata("Track 3")])

    class _StubExtractor:
        def extract_metadata(self, _fp):
            return next(metadatas)

    monkeypatch.setattr(library_import, "MetadataExtractor", _StubExtractor)

    importer = TrackImporter(controller)

    assert importer.add_track(str(files[0])) is ImportResult.IMPORTED

    original_genre_fn = TrackImporter._create_track_genre_relationships
    monkeypatch.setattr(
        TrackImporter,
        "_create_track_genre_relationships",
        lambda *a, **k: (_ for _ in ()).throw(TypeError("boom")),
    )
    assert importer.add_track(str(files[1])) is ImportResult.FAILED

    monkeypatch.setattr(TrackImporter, "_create_track_genre_relationships", original_genre_fn)
    assert importer.add_track(str(files[2])) is ImportResult.IMPORTED


# ---------------------------------------------------------------------------
# Regression test: AlbumImporter._get_or_create_disc must raise instead of
# ---------------------------------------------------------------------------


class _FakeGet:
    def get_entity_object(self, *_a, **_k):
        return None


class _FakeAdd:
    def add_entity(self, *_a, **_k):
        return None


class _FakeController:
    def __init__(self):
        self.get = _FakeGet()
        self.add = _FakeAdd()


def test_failed_disc_creation_raises_instead_of_returning_none():
    importer = AlbumImporter(_FakeController())

    with pytest.raises(RuntimeError):
        importer._get_or_create_disc(album_id=1, metadata={"disc_number": 1})


def test_missing_disc_number_still_returns_none():
    importer = AlbumImporter(_FakeController())

    assert importer._get_or_create_disc(album_id=1, metadata={}) is None
