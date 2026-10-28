"""ORM model sanity: mapper configuration, class-level descriptors, and the
Tag/TagType hierarchy and uniqueness rules (docs/specs/artist_tags.md)."""

import subprocess
import sys

import pytest
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.exc import IntegrityError

from src.db.db_helpers.registry import MODEL_REGISTRY
from src.db.db_tables import Artist, ArtistTagAssociation, Tag, TagType

MAPPED = {name: cls for name, cls in MODEL_REGISTRY.items() if sa_inspect(cls, raiseerr=False) is not None}

# configure_mappers() validates only once per process, so the SAWarning check
# runs in a fresh interpreter regardless of what other tests configured.
_MAPPER_SCRIPT = (
    "import warnings\n"
    "from sqlalchemy.exc import SAWarning\n"
    "from sqlalchemy.orm import configure_mappers\n"
    "import src.db.db_tables  # noqa: F401\n"
    "with warnings.catch_warnings(record=True) as caught:\n"
    "    warnings.simplefilter('always')\n"
    "    configure_mappers()\n"
    "sa_warnings = [str(w.message) for w in caught if issubclass(w.category, SAWarning)]\n"
    "print('\\n'.join(sa_warnings))\n"
    "raise SystemExit(1 if sa_warnings else 0)\n"
)


def test_configure_mappers_raises_no_sawarning():
    result = subprocess.run([sys.executable, "-c", _MAPPER_SCRIPT], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout or result.stderr


@pytest.mark.parametrize("name", sorted(MAPPED))
def test_all_orm_descriptors_resolve_at_class_level(name):
    # An ORM bulk UPDATE reads every descriptor at class level; a Python-only
    # hybrid_property raises there and breaks update_entities_bulk.
    cls = MAPPED[name]
    for key, _descriptor in sa_inspect(cls).all_orm_descriptors.items():
        if key != "__mapper__":
            getattr(cls, key)


def test_tag_full_path_walks_up_the_parent_chain(session):
    tag_type = TagType(type_name="Religion")
    session.add(tag_type)
    session.flush()
    christian = Tag(tag_name="Christian", tag_type_id=tag_type.tag_type_id)
    session.add(christian)
    session.flush()
    catholic = Tag(tag_name="Catholic", tag_type_id=tag_type.tag_type_id, parent_id=christian.tag_id)
    session.add(catholic)
    session.flush()

    assert christian.full_tag_path == "Christian"
    assert catholic.full_tag_path == "Christian > Catholic"


def test_tag_name_is_unique_per_tag_type_only(session):
    vibe = TagType(type_name="Vibe")
    era = TagType(type_name="Era")
    session.add_all([vibe, era])
    session.flush()
    session.add_all([Tag(tag_name="Classic", tag_type_id=vibe.tag_type_id), Tag(tag_name="Classic", tag_type_id=era.tag_type_id)])
    session.commit()

    session.add(Tag(tag_name="Classic", tag_type_id=vibe.tag_type_id))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_deleting_tag_type_cascades_to_its_tags_and_artist_associations(session):
    artist = Artist(artist_name="Miles Davis")
    tag_type = TagType(type_name="Religion")
    session.add_all([artist, tag_type])
    session.flush()
    tag = Tag(tag_name="Jewish", tag_type_id=tag_type.tag_type_id)
    session.add(tag)
    session.flush()
    session.add(ArtistTagAssociation(artist_id=artist.artist_id, tag_id=tag.tag_id))
    session.commit()

    session.delete(tag_type)
    session.commit()

    assert session.query(Tag).count() == 0
    assert session.query(ArtistTagAssociation).count() == 0
    assert session.query(Artist).count() == 1
