"""Tests for src/publisher/publisher_musicbrainz_import.py -- the shared,
GUI-free resolve/create logic used both by the interactive "Lookup
MusicBrainz" album review dialog and the headless
scripts/backfill_album_publishers.py script.

Uses a real in-memory SQLite session (same style as tests/db/test_add.py)
so AddToDB/GetFromDB/UpdateDB's actual conflict-checking and composite-PK
dedup logic executes, rather than stubbing them out.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from src.db.db_helpers.add import AddToDB
from src.db.db_helpers.get import GetFromDB
from src.db.db_helpers.update import UpdateDB
from src.db.db_tables.album import Album
from src.db.db_tables.artist import Artist, ArtistAlias
from src.db.db_tables.associations import PublisherFounder
from src.db.db_tables.base import Base
from src.db.db_tables.place import Place, PlaceAssociation
from src.db.db_tables.place_association_type import PlaceAssociationType
from src.db.db_tables.publisher import Publisher, PublisherAlias
from src.musicbrainz.musicbrainz_release import MBFounderRelation, MBLabelInfo
from src.publisher.publisher_musicbrainz_import import (
    apply_publisher_founders,
    apply_publisher_headquarters,
    import_album_labels,
    resolve_or_create_founder_artist,
    resolve_or_create_publisher,
)
from src.db.db_tables.publisher import Publisher, PublisherSplitAlias
from src.musicbrainz.musicbrainz_release import MBLabelInfo
from src.publisher.publisher_musicbrainz_import import (
    import_album_labels,
    resolve_or_create_publishers,
)

# ---- test_publisher_musicbrainz_import__self_base.py -------------------------
class _Controller_base:
    def __init__(self, session):
        self.get = GetFromDB(session)
        self.add = AddToDB(session)
        self.update = UpdateDB(session)

@pytest.fixture
def controller_base():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield _Controller_base(session)
    session.close()

def _label_base(**overrides) -> MBLabelInfo:
    defaults = dict(
        mbid="11111111-1111-1111-1111-111111111111",
        name="Atlantic Records",
        catalog_number="ATL-1",
        disambiguation=None,
        annotation="Founded in New York City.",
        begin_year=1947,
        begin_month=10,
        begin_day=None,
        end_year=None,
        end_month=None,
        end_day=None,
        area_chain=[],
        founders=[],
    )
    defaults.update(overrides)
    return MBLabelInfo(**defaults)

class TestResolveOrCreatePublisher:
    def test_creates_new_publisher_when_no_match(self, controller_base):
        publisher = resolve_or_create_publisher(controller_base, _label_base())

        assert publisher is not None
        assert publisher.publisher_name == "Atlantic Records"
        assert publisher.MBID == "11111111-1111-1111-1111-111111111111"
        assert publisher.description == "Founded in New York City."
        assert publisher.begin_year == 1947
        assert publisher.begin_month == 10

    def test_matches_by_mbid_without_creating_duplicate(self, controller_base):
        existing = controller_base.add.add_entity(
            "Publisher", publisher_name="Some Other Name", MBID=_label_base().mbid
        )

        publisher = resolve_or_create_publisher(controller_base, _label_base())

        assert publisher.publisher_id == existing.publisher_id
        all_publishers = controller_base.get.get_all_entities("Publisher")
        assert len(all_publishers) == 1

    def test_matches_by_name_and_backfills_mbid(self, controller_base):
        existing = controller_base.add.add_entity(
            "Publisher", publisher_name="Atlantic Records", MBID=None
        )

        publisher = resolve_or_create_publisher(controller_base, _label_base())

        assert publisher.publisher_id == existing.publisher_id
        assert publisher.MBID == "11111111-1111-1111-1111-111111111111"
        all_publishers = controller_base.get.get_all_entities("Publisher")
        assert len(all_publishers) == 1

    def test_matches_by_alias(self, controller_base):
        canonical = controller_base.add.add_entity(
            "Publisher", publisher_name="Atlantic Recording Corporation", MBID=None
        )
        controller_base.add.add_entity(
            "PublisherAlias",
            publisher_id=canonical.publisher_id,
            alias_name="Atlantic Records",
        )

        publisher = resolve_or_create_publisher(controller_base, _label_base())

        assert publisher.publisher_id == canonical.publisher_id
        assert publisher.MBID == "11111111-1111-1111-1111-111111111111"

    def test_name_match_with_conflicting_mbid_creates_new_publisher(self, controller_base):
        conflicting = controller_base.add.add_entity(
            "Publisher",
            publisher_name="Atlantic Records",
            MBID="99999999-9999-9999-9999-999999999999",
        )

        publisher = resolve_or_create_publisher(controller_base, _label_base())

        assert publisher.publisher_id != conflicting.publisher_id
        assert publisher.MBID == "11111111-1111-1111-1111-111111111111"
        # The conflicting row must survive untouched -- not merged, not overwritten.
        all_publishers = controller_base.get.get_all_entities("Publisher")
        assert len(all_publishers) == 2
        untouched = controller_base.get.get_entity_object(
            "Publisher", publisher_id=conflicting.publisher_id
        )
        assert untouched.MBID == "99999999-9999-9999-9999-999999999999"

    def test_existing_publisher_fill_blank_only_never_overwrites(self, controller_base):
        existing = controller_base.add.add_entity(
            "Publisher",
            publisher_name="Atlantic Records",
            MBID=_label_base().mbid,
            description="Hand-written local description",
            begin_year=None,
        )

        publisher = resolve_or_create_publisher(controller_base, _label_base())

        assert publisher.publisher_id == existing.publisher_id
        # Already-set field must survive untouched.
        assert publisher.description == "Hand-written local description"
        # Blank field must get filled from the label.
        assert publisher.begin_year == 1947

class TestResolveOrCreateFounderArtist:
    def test_creates_new_artist_when_no_match(self, controller_base):
        founder = MBFounderRelation(mbid="22222222-2222-2222-2222-222222222222", name="Ahmet Ertegun")

        artist = resolve_or_create_founder_artist(controller_base, founder)

        assert artist is not None
        assert artist.artist_name == "Ahmet Ertegun"
        assert artist.MBID == founder.mbid

    def test_matches_existing_artist_by_alias_and_backfills_mbid(self, controller_base):
        canonical = controller_base.add.add_entity("Artist", artist_name="A. Ertegun", MBID=None)
        controller_base.add.add_entity(
            "ArtistAlias", artist_id=canonical.artist_id, alias_name="Ahmet Ertegun"
        )
        founder = MBFounderRelation(mbid="22222222-2222-2222-2222-222222222222", name="Ahmet Ertegun")

        artist = resolve_or_create_founder_artist(controller_base, founder)

        assert artist.artist_id == canonical.artist_id
        assert artist.MBID == founder.mbid

    def test_name_match_with_conflicting_mbid_creates_new_artist(self, controller_base):
        conflicting = controller_base.add.add_entity(
            "Artist", artist_name="Ahmet Ertegun", MBID="different-mbid"
        )
        founder = MBFounderRelation(
            mbid="22222222-2222-2222-2222-222222222222", name="Ahmet Ertegun"
        )

        artist = resolve_or_create_founder_artist(controller_base, founder)

        assert artist.artist_id != conflicting.artist_id
        assert artist.MBID == founder.mbid
        untouched = controller_base.get.get_entity_object(
            "Artist", artist_id=conflicting.artist_id
        )
        assert untouched.MBID == "different-mbid"

class TestApplyPublisherFounders:
    def test_skips_founder_already_linked(self, controller_base):
        publisher = controller_base.add.add_entity("Publisher", publisher_name="Atlantic Records")
        artist = controller_base.add.add_entity("Artist", artist_name="Ahmet Ertegun")
        controller_base.add.add_entity(
            "PublisherFounder", publisher_id=publisher.publisher_id, artist_id=artist.artist_id
        )
        founder = MBFounderRelation(mbid="whatever-mbid", name="Ahmet Ertegun")

        apply_publisher_founders(controller_base, publisher, [founder])

        links = controller_base.get.get_all_entities(
            "PublisherFounder", publisher_id=publisher.publisher_id
        )
        assert len(links) == 1

    def test_adds_new_founder(self, controller_base):
        publisher = controller_base.add.add_entity("Publisher", publisher_name="Atlantic Records")
        founder = MBFounderRelation(mbid="22222222-2222-2222-2222-222222222222", name="Ahmet Ertegun")

        apply_publisher_founders(controller_base, publisher, [founder])

        links = controller_base.get.get_all_entities(
            "PublisherFounder", publisher_id=publisher.publisher_id
        )
        assert len(links) == 1
        assert links[0].artist.artist_name == "Ahmet Ertegun"

class TestApplyPublisherHeadquarters:
    def test_skips_when_headquarters_already_set(self, controller_base):
        publisher = controller_base.add.add_entity("Publisher", publisher_name="Atlantic Records")
        hq_type = controller_base.add.add_entity("PlaceAssociationType", type_name="Headquarters")
        place = controller_base.add.add_entity("Place", place_name="New York City")
        controller_base.add.add_entity(
            "PlaceAssociation",
            entity_id=publisher.publisher_id,
            entity_type="Publisher",
            place_id=place.place_id,
            association_type_id=hq_type.association_type_id,
        )
        chain = [{"mbid": "zzz", "name": "Los Angeles", "type": "City", "latitude": None, "longitude": None}]

        apply_publisher_headquarters(controller_base, publisher, chain, {})

        assocs = controller_base.get.get_all_entities(
            "PlaceAssociation", entity_type="Publisher", entity_id=publisher.publisher_id
        )
        assert len(assocs) == 1
        assert assocs[0].place.place_name == "New York City"

    def test_creates_headquarters_when_none_exists(self, controller_base):
        publisher = controller_base.add.add_entity("Publisher", publisher_name="Atlantic Records")
        chain = [
            {"mbid": "nyc-mbid", "name": "New York City", "type": "City", "latitude": None, "longitude": None}
        ]

        apply_publisher_headquarters(controller_base, publisher, chain, {})

        assocs = controller_base.get.get_all_entities(
            "PlaceAssociation", entity_type="Publisher", entity_id=publisher.publisher_id
        )
        assert len(assocs) == 1
        assert assocs[0].association_type.type_name == "Headquarters"
        assert assocs[0].place.place_name == "New York City"

class TestImportAlbumLabels:
    def test_creates_publisher_and_links_album(self, controller_base):
        album = controller_base.add.add_entity("Album", album_name="Genesis")

        failures = import_album_labels(controller_base, album, [_label_base()], {})

        assert failures == []
        assert len(album.publishers) == 1
        assert album.publishers[0].publisher_name == "Atlantic Records"

    def test_does_not_duplicate_link_for_already_linked_publisher(self, controller_base):
        album = controller_base.add.add_entity("Album", album_name="Genesis")
        publisher = controller_base.add.add_entity(
            "Publisher", publisher_name="Atlantic Records", MBID=_label_base().mbid
        )
        controller_base.add.add_entity(
            "AlbumPublisher", album_id=album.album_id, publisher_id=publisher.publisher_id
        )

        failures = import_album_labels(controller_base, album, [_label_base()], {})

        assert failures == []
        assert len(album.publishers) == 1

# ---- test_mb_import_publisher_split.py ---------------------------------------
# Tests for split-alias awareness when importing MusicBrainz label/publisher
# credits (docs/specs/split_and_merge_aliases.md). A label name that exactly
# matches a PublisherSplitAlias rule should attach every target publisher to
# the album instead of resolve_or_create_publisher recreating/reusing one
# combined Publisher.
class _Controller_ps:
    def __init__(self, session):
        self.get = GetFromDB(session)
        self.add = AddToDB(session)
        self.update = UpdateDB(session)

@pytest.fixture
def controller_ps():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield _Controller_ps(session)
    session.close()

def _label_ps(**overrides) -> MBLabelInfo:
    defaults = dict(
        mbid="11111111-1111-1111-1111-111111111111",
        name="Motown & Stax",
        catalog_number=None,
        disambiguation=None,
        annotation=None,
        begin_year=None,
        begin_month=None,
        begin_day=None,
        end_year=None,
        end_month=None,
        end_day=None,
        area_chain=[],
        founders=[],
    )
    defaults.update(overrides)
    return MBLabelInfo(**defaults)

def test_resolve_or_create_publishers_matches_split_alias(controller_ps):
    session = controller_ps.get.session
    motown = Publisher(publisher_name="Motown")
    stax = Publisher(publisher_name="Stax")
    session.add_all([motown, stax])
    session.commit()
    session.add_all(
        [
            PublisherSplitAlias(
                alias_name="Motown & Stax", publisher_id=motown.publisher_id, sort_order=0
            ),
            PublisherSplitAlias(
                alias_name="Motown & Stax", publisher_id=stax.publisher_id, sort_order=1
            ),
        ]
    )
    session.commit()

    publishers = resolve_or_create_publishers(controller_ps, _label_ps())

    assert [p.publisher_name for p in publishers] == ["Motown", "Stax"]
    combined = session.query(Publisher).filter_by(publisher_name="Motown & Stax").first()
    assert combined is None

def test_import_album_labels_attaches_every_target_publisher(controller_ps):
    session = controller_ps.get.session
    motown = Publisher(publisher_name="Motown")
    stax = Publisher(publisher_name="Stax")
    album = Album(album_name="Various Hits")
    session.add_all([motown, stax, album])
    session.commit()
    session.add_all(
        [
            PublisherSplitAlias(
                alias_name="Motown & Stax", publisher_id=motown.publisher_id, sort_order=0
            ),
            PublisherSplitAlias(
                alias_name="Motown & Stax", publisher_id=stax.publisher_id, sort_order=1
            ),
        ]
    )
    session.commit()

    failures = import_album_labels(controller_ps, album, [_label_ps()], place_cache={})

    assert failures == []
    session.expire_all()
    linked_names = {p.publisher_name for p in album.publishers}
    assert linked_names == {"Motown", "Stax"}

def test_non_matching_label_behaves_as_before(controller_ps):
    """Regression check: a label name with no split-alias rule still
    resolves through the ordinary single find-or-create path."""
    session = controller_ps.get.session
    album = Album(album_name="Solo Release")
    session.add(album)
    session.commit()

    failures = import_album_labels(
        controller_ps, album, [_label_ps(name="Atlantic Records")], place_cache={}
    )

    assert failures == []
    session.expire_all()
    assert {p.publisher_name for p in album.publishers} == {"Atlantic Records"}
