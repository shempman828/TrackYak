"""Tests for the shared award helpers in src/award/award_common.py."""

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.award.award_common import award_display_text, delete_awards, entity_display_name, parse_award_year
from src.db.db_helpers.delete import DeleteDB
from src.db.db_tables.award import Award, AwardAssociation
from src.db.db_tables.base import Base


@pytest.fixture
def session():
    # base.py turns PRAGMA foreign_keys on for every connection, as in the app.
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    yield session
    session.close()


@pytest.mark.parametrize(("text", "expected"), [("", None), ("  ", None), ("1959", 1959), (" 2024 ", 2024), ("1000", 1000), ("2999", 2999)])
def test_parse_award_year_accepts_blank_and_in_range(text, expected):
    assert parse_award_year(text) == expected


@pytest.mark.parametrize(("text", "message"), [("abc", "whole number"), ("19.5", "whole number"), ("999", "between"), ("3000", "between")])
def test_parse_award_year_rejects_invalid(text, message):
    with pytest.raises(ValueError, match=message):
        parse_award_year(text)


@pytest.mark.parametrize(
    ("year", "category", "expected"), [(None, None, "Grammy"), (2020, None, "[2020] Grammy"), (None, "Best Album", "Grammy - Best Album"), (2020, "Best Album", "[2020] Grammy - Best Album")]
)
def test_award_display_text(year, category, expected):
    assert award_display_text(SimpleNamespace(award_name="Grammy", award_year=year, award_category=category)) == expected


def test_entity_display_name_handles_missing_name_and_album_year():
    assert entity_display_name("Artist", SimpleNamespace(artist_name=None)) == "(unnamed)"
    assert entity_display_name("Album", SimpleNamespace(album_name="Blue", release_year=1971)) == "Blue (1971)"


def test_delete_awards_removes_associations_and_keeps_children(session):
    parent = Award(award_name="Grammy")
    session.add(parent)
    session.flush()
    child = Award(award_name="Best Album", parent_id=parent.award_id)
    session.add(child)
    session.flush()
    session.add(AwardAssociation(award_id=parent.award_id, entity_id=1, entity_type="Artist", association_type="recipient"))
    session.commit()
    parent_id, child_id = parent.award_id, child.award_id

    controller = SimpleNamespace(delete=DeleteDB(session))
    assert delete_awards(controller, [parent_id]) is True

    session.expire_all()
    assert session.get(Award, parent_id) is None
    assert session.query(AwardAssociation).count() == 0
    assert session.get(Award, child_id).parent_id is None


def test_delete_awards_reports_failure():
    controller = SimpleNamespace(delete=SimpleNamespace(delete_entity=lambda *a, **k: False))
    assert delete_awards(controller, [1]) is False
