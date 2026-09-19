"""Read-only DB lookups so fetch_release_detail can skip a redundant
MusicBrainz refetch for MBIDs already known locally.
"""

from __future__ import annotations

from sqlalchemy.exc import SQLAlchemyError

from src.foundation.logger_config import logger


def known_publisher_mbids(controller) -> frozenset[str]:
    """Return the MBIDs of all Publishers already linked in the local DB."""
    try:
        publishers = controller.get.get_all_entities("Publisher", MBID__notnull=True) or []
    except SQLAlchemyError as e:
        logger.warning(f"Failed to load known publisher MBIDs: {e}")
        return frozenset()
    return frozenset(p.MBID for p in publishers if p.MBID)


def known_place_mbids(controller) -> frozenset[str]:
    """Return the MBIDs of all Places already linked in the local DB."""
    try:
        places = controller.get.get_all_entities("Place", MBID__notnull=True) or []
    except SQLAlchemyError as e:
        logger.warning(f"Failed to load known place MBIDs: {e}")
        return frozenset()
    return frozenset(p.MBID for p in places if p.MBID)
