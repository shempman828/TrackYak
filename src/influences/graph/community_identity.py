"""Persist user-given Louvain community names across recomputes by membership overlap."""

# Louvain indices change on every recompute, so each name stores its membership
# snapshot and re-attaches to the best Jaccard match. Kept in its own JSON file
# (not config.ini) so growing ID lists don't bloat the ini.

import json
from pathlib import Path

from src.foundation.asset_paths import config as config_path
from src.foundation.logger_config import logger
from src.metadata.writers.metadata_writer_backup import atomic_write

_MATCH_THRESHOLD = 0.5  # minimum Jaccard overlap to treat as "the same" community


def _default_path():
    return Path(config_path("community_identity.json"))


def _load(path=None):
    """Return {level: {name: [member_ids]}}, or {} when the file is missing or malformed."""
    path = path or _default_path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(raw, dict):
        logger.warning(f"Ignoring malformed community identity file: {path}")
        return {}

    data = {}
    for level, entries in raw.items():
        try:
            level_key = int(level)
        except (TypeError, ValueError):
            logger.warning(f"Skipping non-integer community level {level!r} in {path}")
            continue
        if not isinstance(entries, dict):
            continue
        data[level_key] = {name: list(members) for name, members in entries.items() if isinstance(name, str) and isinstance(members, list)}
    return data


def _save(data, path=None):
    path = path or _default_path()
    serializable = {str(level): entries for level, entries in data.items()}
    atomic_write(str(path), json.dumps(serializable, indent=2).encode("utf-8"))


def _jaccard(a, b):
    union = len(a | b)
    return len(a & b) / union if union else 0.0


def _match_level(saved, communities):
    """Match saved {name: members} to {community_index: members} in place; return {community_index: name}."""
    resolved = {}
    used_indices = set()
    for name, member_ids in saved.items():
        saved_members = set(member_ids)
        best_index, best_score = None, 0.0
        for community_index, members in communities.items():
            if community_index in used_indices:
                continue
            score = _jaccard(saved_members, members)
            if score > best_score:
                best_index, best_score = community_index, score
        if best_index is not None and best_score >= _MATCH_THRESHOLD:
            resolved[best_index] = name
            used_indices.add(best_index)
            # Track gradual drift instead of an increasingly stale baseline.
            saved[name] = sorted(communities[best_index])
    return resolved


def resolve_all_levels(communities_by_level, path=None):
    """Return {level: {community_index: name}} for every level, with one file read and at most one write."""
    data = _load(path)
    before = json.dumps({str(k): v for k, v in data.items()}, sort_keys=True)

    resolved = {}
    for level, communities in communities_by_level.items():
        saved = data.get(level)
        resolved[level] = _match_level(saved, communities) if saved else {}

    if json.dumps({str(k): v for k, v in data.items()}, sort_keys=True) != before:
        _save(data, path)
    return resolved


def match_and_resolve_names(level, communities, path=None):
    """Return {community_index: name} for the persisted names that match `communities` at `level`."""
    return resolve_all_levels({level: communities}, path=path).get(level, {})


def persist_renames(renames_by_level, path=None):
    """Apply {level: [(new_name, old_name, members), ...]} with one file read and one write."""
    if not any(renames_by_level.values()):
        return
    data = _load(path)
    for level, renames in renames_by_level.items():
        entries = data.setdefault(level, {})
        # Drop every old name first, so a swap of two names in one batch keeps both.
        for _name, old_name, _members in renames:
            if old_name:
                entries.pop(old_name, None)
        for name, _old_name, members in renames:
            name = (name or "").strip()
            if name:
                entries[name] = sorted(members)
    _save(data, path)


def persist_rename(level, name, old_name, members, path=None):
    """Set, rename, or clear (blank `name`) one community's persisted name at `level`."""
    persist_renames({level: [(name, old_name, members)]}, path=path)


def migrate_legacy_anchor_names(legacy_names_by_anchor, community_levels, path=None):
    """Convert old anchor-keyed config.ini cluster names into membership-keyed entries; no-op when empty."""
    if not legacy_names_by_anchor:
        return

    data = _load(path)
    for level, community_id in enumerate(community_levels):
        members_by_community = {}
        for node_id, community_index in community_id.items():
            members_by_community.setdefault(community_index, set()).add(node_id)

        entries = data.setdefault(level, {})
        for anchor_id, name in legacy_names_by_anchor.items():
            # An anchor no longer in the graph (e.g. deleted artist) is skipped.
            community_index = community_id.get(int(anchor_id))
            if community_index is None:
                continue
            entries[name] = sorted(members_by_community[community_index])

    _save(data, path)
