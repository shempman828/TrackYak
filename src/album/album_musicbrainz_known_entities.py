"""
album_musicbrainz_known_entities.py

Read-only DB lookups that let fetch_release_detail skip an expensive
MusicBrainz enrichment call (get_label_by_id / get_place_by_id +
resolve_area_chain) when the local database already has that exact
Publisher/Place by MBID -- its founder/headquarters/area-chain data was
already fully imported the first time this MBID was seen, so re-fetching
it from MusicBrainz on every subsequent album is pure waste.

Deliberately kept outside src/musicbrainz/, which has no DB dependency of
its own: these functions build plain frozenset[str] results that
fetch_release_detail takes as parameters, so the fetch layer never needs
to know a database exists. The actual MBID-match/name-match/create
resolution still happens exactly where it always has (publisher_musicbrainz_import
.resolve_or_create_publisher, place_chain_resolver.resolve_place_chain),
deferred to dialog Accept -- this module only short-circuits a redundant
network fetch, it never reads/writes anything write-adjacent itself.
"""

from __future__ import annotations


def known_publisher_mbids(controller) -> frozenset[str]:
    publishers = controller.get.get_all_entities("Publisher", MBID__notnull=True) or []
    return frozenset(p.MBID for p in publishers if p.MBID)


def known_place_mbids(controller) -> frozenset[str]:
    places = controller.get.get_all_entities("Place", MBID__notnull=True) or []
    return frozenset(p.MBID for p in places if p.MBID)
