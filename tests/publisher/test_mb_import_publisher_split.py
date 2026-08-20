"""Tests for split-alias awareness when importing MusicBrainz label/publisher
credits (docs/specs/split_and_merge_aliases.md). A label name that exactly
matches a PublisherSplitAlias rule should attach every target publisher to
the album instead of resolve_or_create_publisher recreating/reusing one
combined Publisher.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.db_helpers.add import AddToDB
from src.db.db_helpers.get import GetFromDB
from src.db.db_helpers.update import UpdateDB
from src.db.db_tables.album import Album
from src.db.db_tables.base import Base
from src.db.db_tables.publisher import Publisher, PublisherSplitAlias
from src.musicbrainz.musicbrainz_release import MBLabelInfo
from src.publisher.publisher_musicbrainz_import import (
    import_album_labels,
    resolve_or_create_publishers,
)


class _Controller:
    def __init__(self, session):
        self.get = GetFromDB(session)
        self.add = AddToDB(session)
        self.update = UpdateDB(session)


@pytest.fixture
def controller():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield _Controller(session)
    session.close()


def _label(**overrides) -> MBLabelInfo:
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


def test_resolve_or_create_publishers_matches_split_alias(controller):
    session = controller.get.session
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

    publishers = resolve_or_create_publishers(controller, _label())

    assert [p.publisher_name for p in publishers] == ["Motown", "Stax"]
    combined = session.query(Publisher).filter_by(publisher_name="Motown & Stax").first()
    assert combined is None


def test_import_album_labels_attaches_every_target_publisher(controller):
    session = controller.get.session
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

    failures = import_album_labels(controller, album, [_label()], place_cache={})

    assert failures == []
    session.expire_all()
    linked_names = {p.publisher_name for p in album.publishers}
    assert linked_names == {"Motown", "Stax"}


def test_non_matching_label_behaves_as_before(controller):
    """Regression check: a label name with no split-alias rule still
    resolves through the ordinary single find-or-create path."""
    session = controller.get.session
    album = Album(album_name="Solo Release")
    session.add(album)
    session.commit()

    failures = import_album_labels(
        controller, album, [_label(name="Atlantic Records")], place_cache={}
    )

    assert failures == []
    session.expire_all()
    assert {p.publisher_name for p in album.publishers} == {"Atlantic Records"}
