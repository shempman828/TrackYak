"""
One-off patch for bug #310 (place coordinates not always parsed) and its
follow-on find: some MusicBrainz-linked Place rows never got their parent
chain wired up either, due to earlier iterations of resolve_place_chain's
walk-up logic.

For every Place row with an MBID:
  1. If it's missing latitude/longitude, fetch coordinates directly from
     MusicBrainz and backfill them (only into currently-NULL columns --
     never overwrites a value already on file).
  2. If it's missing parent_id, walk the MusicBrainz area chain upward
     (same resolver used by the normal import path -- see
     src/place/place_chain_resolver.py) and wire parent_id to the
     resolved immediate parent. A place/area with no parent in
     MusicBrainz (e.g. a country) is a genuine top-level entity -- those
     are left alone, not an error.

Run once, manually, from the repo root:

    python scripts/patch_place_mbid_backfill.py
"""

from datetime import datetime
import os
import shutil

import musicbrainzngs
from sqlalchemy.orm import scoped_session, sessionmaker

from src.db.db_helpers import AddToDB, DeleteDB, GetFromDB, MergeDB, SplitDB, UpdateDB
from src.db.db_tables.database import MusicDatabase
from src.foundation.logger_config import logger
from src.musicbrainz.musicbrainz_core import (
    MusicBrainzLookupError,
    _resolve_place_area,
    _to_float,
    configure,
    resolve_area_chain,
)
from src.place.place_chain_resolver import resolve_place_chain

DB_PATH = "music_library.db"


class _MinimalController:
    """Boots only the database layer, same pattern as
    scripts/backfill_artist_religion.py's _MinimalController -- no GUI/audio
    deps needed for a data patch."""

    def __init__(self):
        self.db = MusicDatabase(f"sqlite:///{DB_PATH}")
        self.engine = self.db.engine
        self.SessionFactory = scoped_session(sessionmaker(bind=self.engine))

        self.get = GetFromDB(self.SessionFactory)
        self.add = AddToDB(self.SessionFactory)
        self.update = UpdateDB(self.SessionFactory)
        self.delete = DeleteDB(self.SessionFactory)
        self.split = SplitDB(self.SessionFactory)
        self.merge = MergeDB(self.SessionFactory)

    def close_session(self):
        self.SessionFactory.remove()


def _backup(db_path: str) -> str:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    os.makedirs("backups", exist_ok=True)
    backup_path = f"backups/{os.path.basename(db_path)}.{timestamp}.bak"
    shutil.copy2(db_path, backup_path)
    return backup_path


def _fetch_place_coordinates(mbid: str) -> tuple[float | None, float | None]:
    """Direct MusicBrainz place lookup for coordinates only. Distinct from
    _resolve_place_area (which discards coordinates to get the area) and
    from _parse_recording_location (which only ever sees the reduced place
    stub embedded on a recording relation) -- neither of those is usable
    here since we're starting from a Place row's own MBID, not a release."""
    configure()
    try:
        result = musicbrainzngs.get_place_by_id(mbid)
    except Exception as e:  # ruff: ignore[blind-except]
        logger.warning(f"Could not fetch coordinates for place {mbid}: {e}")
        return None, None
    coords = (result.get("place") or {}).get("coordinates") or {}
    return _to_float(coords.get("latitude")), _to_float(coords.get("longitude"))


def _resolve_parent_chain(
    mbid: str, area_cache: dict[str, list]
) -> list[dict]:
    """Return this place's ancestor chain (immediate parent first, NOT
    including `mbid` itself), or [] if it's a genuine top-level entity with
    no parent in MusicBrainz.

    `mbid` may belong to either a MusicBrainz Place (e.g. a studio) or an
    Area (e.g. a city/state/country stored directly as a Place row) --
    both are stored in the same `places` table locally. Try it as a Place
    first (via its containing area); if that comes back empty, it's either
    a genuine Place with no area on file, or the MBID actually belongs to
    an Area -- try that directly before giving up.
    """
    area_mbid, _area_name = _resolve_place_area(mbid)
    if area_mbid:
        return resolve_area_chain(area_mbid, area_cache)

    try:
        full_chain = resolve_area_chain(mbid, area_cache)
    except MusicBrainzLookupError:
        return []
    return full_chain[1:]


def main():
    backup_path = _backup(DB_PATH)
    print(f"Backed up database to {backup_path}")

    controller = _MinimalController()
    try:
        places = controller.get.get_all_entities("Place") or []
        linked = [p for p in places if p.MBID]
        print(f"{len(linked)} place(s) have an MBID.")

        coords_filled, coords_unavailable, coords_failed = [], [], []
        parents_wired, parents_top_level, parents_failed = [], [], []

        place_cache: dict[str, object] = {p.MBID: p for p in linked}
        area_cache: dict[str, list] = {}

        for place in linked:
            if place.place_latitude is None or place.place_longitude is None:
                lat, lon = _fetch_place_coordinates(place.MBID)
                if lat is None and lon is None:
                    coords_unavailable.append(place.place_name)
                else:
                    updates = {}
                    if place.place_latitude is None and lat is not None:
                        updates["place_latitude"] = lat
                    if place.place_longitude is None and lon is not None:
                        updates["place_longitude"] = lon
                    if updates:
                        ok = controller.update.update_entity(
                            "Place", place.place_id, **updates
                        )
                        if ok:
                            for field, value in updates.items():
                                setattr(place, field, value)
                            coords_filled.append(place.place_name)
                        else:
                            coords_failed.append(place.place_name)
                    else:
                        coords_unavailable.append(place.place_name)

            if place.parent_id is None:
                try:
                    parent_chain = _resolve_parent_chain(place.MBID, area_cache)
                except MusicBrainzLookupError as e:
                    logger.warning(
                        f"Could not resolve parent chain for place "
                        f"{place.place_name!r} ({place.MBID}): {e}"
                    )
                    parents_failed.append(place.place_name)
                    continue

                if not parent_chain:
                    parents_top_level.append(place.place_name)
                    continue

                parent = resolve_place_chain(controller, parent_chain, place_cache)
                if parent is None or parent.place_id == place.place_id:
                    parents_failed.append(place.place_name)
                    continue

                ok = controller.update.update_entity(
                    "Place", place.place_id, parent_id=parent.place_id
                )
                if ok:
                    place.parent_id = parent.place_id
                    parents_wired.append(f"{place.place_name} -> {parent.place_name}")
                else:
                    parents_failed.append(place.place_name)

        print(f"\nCoordinates backfilled for {len(coords_filled)} place(s):")
        for name in coords_filled:
            print(f"  {name}")

        if coords_unavailable:
            print(
                f"\nNo coordinates available from MusicBrainz for "
                f"{len(coords_unavailable)} place(s) (likely an Area, not a Place):"
            )
            for name in coords_unavailable:
                print(f"  {name}")

        if coords_failed:
            print(f"\nFailed to write coordinates for {len(coords_failed)} place(s):")
            for name in coords_failed:
                print(f"  {name}")

        print(f"\nParent wired for {len(parents_wired)} place(s):")
        for line in parents_wired:
            print(f"  {line}")

        if parents_top_level:
            print(
                f"\n{len(parents_top_level)} place(s) are genuine top-level "
                f"entities (no parent in MusicBrainz) -- left alone:"
            )
            for name in parents_top_level:
                print(f"  {name}")

        if parents_failed:
            print(f"\nFailed to resolve/wire parent for {len(parents_failed)} place(s):")
            for name in parents_failed:
                print(f"  {name}")

    finally:
        controller.close_session()


if __name__ == "__main__":
    main()
