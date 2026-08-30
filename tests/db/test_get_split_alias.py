"""Tests for GetFromDB.resolve_split_alias (docs/specs/split_and_merge_aliases.md)."""

from src.db.db_helpers.get import GetFromDB
from src.db.db_tables.role import Role, RoleSplitAlias


def test_resolve_split_alias_returns_ordered_targets(session):
    viola = Role(role_name="Viola")
    violin = Role(role_name="Violin")
    session.add_all([viola, violin])
    session.commit()
    session.add_all(
        [
            RoleSplitAlias(alias_name="Viola & Violin", role_id=violin.role_id, sort_order=1),
            RoleSplitAlias(alias_name="Viola & Violin", role_id=viola.role_id, sort_order=0),
        ]
    )
    session.commit()

    getter = GetFromDB(session)
    result = getter.resolve_split_alias("Role", "Viola & Violin")
    assert [r.role_name for r in result] == ["Viola", "Violin"]


def test_resolve_split_alias_returns_none_when_no_rule(session):
    getter = GetFromDB(session)
    assert getter.resolve_split_alias("Role", "Nonexistent Combined Role") is None
