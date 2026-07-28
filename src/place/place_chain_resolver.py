"""
place_chain_resolver.py

Shared find-or-create helper for a MusicBrainz place/area chain (innermost
place or area first, containing areas after -- e.g. city, county, state,
country). Used by both the album and artist MusicBrainz review flows so
the "walk up the chain and find-or-create each level" dedup logic only
needs to be written once.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def resolve_place_chain(
    controller, chain: List[Dict[str, Any]], cache: Dict[str, Any]
) -> Optional[Any]:
    """Find-or-create every level of a place chain, outermost first, so
    each level's parent already exists before the next is resolved.
    Matches by MBID globally, then by name scoped to the already-
    resolved parent's children only -- never a bare global name search,
    so e.g. two same-named places under different parents (a "Paris"
    in Tennessee vs. a "Paris" in France) resolve to distinct rows.

    Returns the innermost (most specific) resolved place -- chain[0]'s
    row -- or None if any level failed to resolve.
    """
    parent = None
    for node in reversed(chain):
        mbid = node["mbid"]
        place = cache.get(mbid)
        if place is None:
            place = controller.get.get_entity_object("Place", MBID=mbid)
        if place is None:
            siblings = (
                controller.get.get_all_entities(
                    "Place", parent_id=parent.place_id if parent else None
                )
                or []
            )
            name_key = (node.get("name") or "").strip().lower()
            place = next(
                (
                    p
                    for p in siblings
                    if (p.place_name or "").strip().lower() == name_key
                ),
                None,
            )
        if place is None:
            place = controller.add.add_entity(
                "Place",
                place_name=node.get("name") or "",
                place_type=node.get("type"),
                MBID=mbid,
                parent_id=parent.place_id if parent else None,
                place_latitude=node.get("latitude"),
                place_longitude=node.get("longitude"),
            )
        if place is None:
            return None
        cache[mbid] = place
        parent = place
    return parent
