"""Tests for community_identity.py -- Jaccard-based re-attachment of
persisted cluster names after a Louvain recompute, and migration of the
old anchor-keyed names out of config.ini.

See docs/specs/tiered_community_naming.md, acceptance criteria 5-7.
"""

import json

from src.influences import community_identity


def test_match_and_resolve_names_stable_across_noop_recompute(tmp_path):
    path = tmp_path / "community_identity.json"
    communities = {0: {1, 2, 3}, 1: {4, 5, 6}}
    community_identity.persist_rename(0, "Bebop", None, communities[0], path=path)
    community_identity.persist_rename(0, "Crooners", None, communities[1], path=path)

    resolved = community_identity.match_and_resolve_names(0, communities, path=path)

    assert resolved == {0: "Bebop", 1: "Crooners"}


def test_match_and_resolve_names_survives_minor_membership_drift(tmp_path):
    path = tmp_path / "community_identity.json"
    community_identity.persist_rename(0, "Bebop", None, {1, 2, 3, 4}, path=path)

    # One member left, one new member joined -- still mostly the same community.
    drifted = {0: {1, 2, 3, 5}}
    resolved = community_identity.match_and_resolve_names(0, drifted, path=path)

    assert resolved == {0: "Bebop"}


def test_match_and_resolve_names_split_keeps_name_on_larger_overlap_child(tmp_path):
    path = tmp_path / "community_identity.json"
    original = {1, 2, 3, 4, 5, 6}
    community_identity.persist_rename(0, "Jazz", None, original, path=path)

    # Community splits into two children; child 0 keeps 5/6 members (high
    # overlap), child 1 is a genuinely new grouping (low/no overlap).
    split = {0: {1, 2, 3, 4, 5}, 1: {7, 8, 9}}
    resolved = community_identity.match_and_resolve_names(0, split, path=path)

    assert resolved == {0: "Jazz"}
    assert 1 not in resolved


def test_match_and_resolve_names_no_saved_entries_is_noop(tmp_path):
    path = tmp_path / "community_identity.json"
    resolved = community_identity.match_and_resolve_names(0, {0: {1, 2}}, path=path)
    assert resolved == {}
    assert not path.exists()


def test_persist_rename_then_clear_removes_entry(tmp_path):
    path = tmp_path / "community_identity.json"
    community_identity.persist_rename(0, "Bebop", None, {1, 2, 3}, path=path)
    community_identity.persist_rename(0, "", "Bebop", {1, 2, 3}, path=path)

    data = json.loads(path.read_text())
    assert data.get("0", {}) == {}


def test_persist_rename_renaming_replaces_old_name(tmp_path):
    path = tmp_path / "community_identity.json"
    community_identity.persist_rename(0, "Bebop", None, {1, 2, 3}, path=path)
    community_identity.persist_rename(0, "Cool Jazz", "Bebop", {1, 2, 3}, path=path)

    data = json.loads(path.read_text())
    assert data["0"] == {"Cool Jazz": [1, 2, 3]}


def test_migrate_legacy_anchor_names_converts_matching_anchors(tmp_path):
    path = tmp_path / "community_identity.json"
    legacy = {"3": "Bebop"}  # anchor artist id 3, as stored in config.ini (string keys)
    community_levels = [{1: 0, 2: 0, 3: 0, 4: 1, 5: 1}]

    community_identity.migrate_legacy_anchor_names(legacy, community_levels, path=path)

    data = json.loads(path.read_text())
    assert data["0"] == {"Bebop": [1, 2, 3]}


def test_migrate_legacy_anchor_names_skips_missing_anchor(tmp_path):
    path = tmp_path / "community_identity.json"
    legacy = {"999": "Ghost Cluster"}  # anchor no longer present in the graph
    community_levels = [{1: 0, 2: 0}]

    community_identity.migrate_legacy_anchor_names(legacy, community_levels, path=path)

    data = json.loads(path.read_text()) if path.exists() else {}
    assert data.get("0", {}) == {}


def test_migrate_legacy_anchor_names_empty_dict_writes_nothing(tmp_path):
    path = tmp_path / "community_identity.json"
    community_identity.migrate_legacy_anchor_names({}, [{1: 0}], path=path)
    assert not path.exists()
