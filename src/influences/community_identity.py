"""
community_identity.py

Persists user-given names for Louvain communities across recomputes.

Raw Louvain community indices are reassigned every recompute, so a name
can't be pinned to one. Instead, each named community's membership set is
snapshotted at naming time and matched against the next recompute's
communities by Jaccard overlap (|intersection| / |union|) -- whichever
community has the highest overlap above _MATCH_THRESHOLD is treated as
"the same" community and keeps the name. This survives a community
splitting (the name settles on whichever child retains the larger share
of the original membership) in a way a single-anchor-node scheme can't.

Persisted to its own JSON file (community_identity.json) rather than
config.ini -- this codebase already hit and fixed the same shape of
problem once before: growing per-item ID lists bloated config.ini for
queue/history state, which was moved to queue_state.json (see
Config._migrate_legacy_queue_keys in config_setup.py). Same pattern here,
via the same config_path()/atomic_write() helpers queue_state.json uses.
"""

import json
from pathlib import Path

from src.core.asset_paths import config as config_path
from src.metadata.metadata_writer_backup import atomic_write

_MATCH_THRESHOLD = 0.5  # minimum Jaccard overlap to treat as "the same" community


def _default_path():
    return Path(config_path("community_identity.json"))


def _load(path=None):
    path = path or _default_path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    return {int(level): entries for level, entries in raw.items()}


def _save(data, path=None):
    path = path or _default_path()
    serializable = {str(level): entries for level, entries in data.items()}
    atomic_write(str(path), json.dumps(serializable, indent=2).encode("utf-8"))


def _jaccard(a, b):
    if not a and not b:
        return 0.0
    union = len(a | b)
    return len(a & b) / union if union else 0.0


def match_and_resolve_names(level, communities, path=None):
    """Re-attach persisted names to this recompute's communities at `level`.

    `communities`: dict[community_index, set[node_id]] for this level.

    Returns dict[community_index, name] for every community matched to a
    saved entry above the match threshold. Matched entries have their
    stored membership snapshot updated to the new community's membership,
    so future comparisons track gradual drift rather than an
    increasingly stale baseline.
    """
    data = _load(path)
    saved = data.get(level, {})
    if not saved:
        return {}

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
            saved[name] = sorted(communities[best_index])

    data[level] = saved
    _save(data, path)
    return resolved


def persist_rename(level, name, old_name, members, path=None):
    """Set, rename, or clear a community's persisted name at `level`.

    `old_name`: this community's current persisted name, if any (None if
    unnamed) -- storage is keyed by name, not community index, so the
    caller looks this up first (it already has it from the last
    match_and_resolve_names result). `name`: the new name, or a blank
    string to clear it. `members`: set[node_id], the community's current
    membership, stored as the new matching baseline.
    """
    data = _load(path)
    entries = data.setdefault(level, {})
    if old_name and old_name in entries:
        del entries[old_name]
    name = (name or "").strip()
    if name:
        entries[name] = sorted(members)
    _save(data, path)


def migrate_legacy_anchor_names(legacy_names_by_anchor, community_levels, path=None):
    """One-time best-effort conversion of the old anchor-keyed cluster
    names (config.ini's `[influences] cluster_names`) into this module's
    membership-keyed store.

    For each legacy `{anchor_artist_id: name}` entry, finds whichever
    community currently contains that anchor at each dendrogram level and
    seeds a persisted entry there. An anchor no longer present in the
    current graph (e.g. the artist was deleted) is skipped.

    `community_levels`: list[dict[node_id, community_index]], one per
    dendrogram level, as returned by assign_louvain_communities. A no-op
    (including on an empty legacy dict) writes no file.
    """
    if not legacy_names_by_anchor:
        return

    data = _load(path)
    for level, community_id in enumerate(community_levels):
        members_by_community = {}
        for node_id, community_index in community_id.items():
            members_by_community.setdefault(community_index, set()).add(node_id)

        entries = data.setdefault(level, {})
        for anchor_id, name in legacy_names_by_anchor.items():
            community_index = community_id.get(int(anchor_id))
            if community_index is None:
                continue
            entries[name] = sorted(members_by_community[community_index])

    _save(data, path)
