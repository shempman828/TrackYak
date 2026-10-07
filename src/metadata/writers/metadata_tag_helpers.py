"""Value-formatting helpers shared by the ID3 and Vorbis tag builders."""

from typing import Any


def format_track_number(track: Any) -> str | None:
    """Build the track-number tag text, prefixed with the vinyl side when set (side "B" + 1 -> "B1")."""
    track_number = getattr(track, "track_number", None)
    if track_number is None:
        return None
    side = getattr(track, "side", None)
    if side:
        return f"{side}{track_number}"
    return str(track_number)


def build_iso_date_string(entity: Any, fields: list[str]) -> str | None:
    """Build a YYYY[-MM[-DD]] string from the year/month/day columns named by fields, or None."""
    if not fields:
        return None

    parts = []
    for i, field in enumerate(fields):
        value = getattr(entity, field, None)
        if not value:
            break  # stop at the first gap, so a year-only row gives "YYYY"
        width = 4 if i == 0 else 2
        parts.append(str(value).zfill(width))

    return "-".join(parts) if parts else None


def group_artists_by_tag(artist_role_data: list[dict[str, Any]], role_to_tag: dict[str, str], id_tag_map: dict[str, str], dedupe: bool = False) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Group artist credits by output tag, returning (names_by_tag, mbids_by_id_tag)."""
    names_by_tag: dict[str, list[str]] = {}
    mbids_by_tag: dict[str, list[str]] = {}

    for artist_data in artist_role_data:
        role_name = artist_data["role"].role_name
        tag = role_to_tag.get(role_name)
        name = artist_data.get("credited_name")
        if not tag or not name:
            continue

        # dedupe=True for Vorbis (one entry per artist); ID3 joins names into one frame and keeps repeats.
        names = names_by_tag.setdefault(tag, [])
        if not dedupe or name not in names:
            names.append(name)

        id_tag = id_tag_map.get(tag)
        mbid = artist_data.get("artist_mbid")
        if id_tag and mbid:
            mbids = mbids_by_tag.setdefault(id_tag, [])
            if not dedupe or mbid not in mbids:
                mbids.append(mbid)

    return names_by_tag, mbids_by_tag
