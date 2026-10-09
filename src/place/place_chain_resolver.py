"""Find-or-create helper for a MusicBrainz place/area chain, shared by the album, artist, and publisher imports."""

from __future__ import annotations

from typing import Any


def resolve_place_chain(controller, chain: list[dict[str, Any]], cache: dict[str, Any]) -> Any | None:
    """Find-or-create every level of an innermost-first chain; return chain[0]'s place, or None on failure."""
    # Phase 1 climbs outward until an MBID match (its ancestry is trusted). A name-only
    # match gets its MBID/coordinates backfilled but does not stop the climb.
    anchor = None
    anchor_index = len(chain)
    name_matches: dict[int, Any] = {}
    for i, node in enumerate(chain):
        mbid = node["mbid"]
        place = cache.get(mbid) or controller.get.get_entity_object("Place", MBID=mbid)
        if place is not None:
            cache[mbid] = place
            anchor = place
            anchor_index = i
            break  # trusted existing MBID -- ancestry above it is already correct

        name_key = (node.get("name") or "").strip().lower()
        if name_key:
            candidates = controller.get.get_all_entities("Place") or []
            match = next(
                (
                    p
                    for p in candidates
                    # Only a place with no MBID of its own is a candidate --
                    # one with a *different* MBID is a distinct real-world
                    # place (or a rare MB id reassignment); never merge those.
                    if not p.MBID and (p.place_name or "").strip().lower() == name_key
                ),
                None,
            )
            if match is not None:
                updates: dict[str, Any] = {"MBID": mbid}
                if match.place_latitude is None and node.get("latitude") is not None:
                    updates["place_latitude"] = node["latitude"]
                if match.place_longitude is None and node.get("longitude") is not None:
                    updates["place_longitude"] = node["longitude"]
                controller.update.update_entity("Place", match.place_id, **updates)
                for field, value in updates.items():
                    setattr(match, field, value)
                cache[mbid] = match
                name_matches[i] = match
        # No MBID match and no name match: this level gets created in phase 2.

    # Phase 2 walks back inward: wire the parent of each name match, create each missing level.
    parent = anchor
    for i in range(anchor_index - 1, -1, -1):
        node = chain[i]
        mbid = node["mbid"]
        parent_id = parent.place_id if parent else None
        existing = name_matches.get(i)
        if existing is not None:
            if existing.parent_id != parent_id:
                controller.update.update_entity("Place", existing.place_id, parent_id=parent_id)
                existing.parent_id = parent_id
            place = existing
        else:
            place = controller.add.add_entity(
                "Place", place_name=node.get("name") or "", place_type=node.get("type"), MBID=mbid, parent_id=parent_id, place_latitude=node.get("latitude"), place_longitude=node.get("longitude")
            )
            if place is None:
                return None
        cache[mbid] = place
        parent = place
    return parent
