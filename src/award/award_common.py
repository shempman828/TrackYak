"""Shared award constants and helpers for the award views and dialogs."""

from typing import Any

# entity_type -> (name_attr, id_attr). Must match the CHECK constraint on award_associations.entity_type.
ENTITY_FIELDS: dict[str, tuple[str, str]] = {
    "Artist": ("artist_name", "artist_id"),
    "Album": ("album_name", "album_id"),
    "Track": ("track_name", "track_id"),
    "Publisher": ("publisher_name", "publisher_id"),
}

# "recipient" is the winner marker that Award.recipients and the MusicBrainz importer use.
RECIPIENT = "recipient"
ASSOCIATION_TYPES = [RECIPIENT, "nominee", "honoree", "finalist", "presenter", "judge", "host", "sponsor", "organizer"]

YEAR_MIN, YEAR_MAX = 1000, 2999


def award_display_text(award: Any) -> str:
    """Label for an award: "[year] name - category"."""
    text = award.award_name
    if award.award_year:
        text = f"[{award.award_year}] {text}"
    if award.award_category:
        text = f"{text} - {award.award_category}"
    return text


def entity_display_name(entity_type: str, entity: Any) -> str:
    """Display name for an award-linkable entity (albums also show their release year)."""
    name = getattr(entity, ENTITY_FIELDS[entity_type][0], None) or "(unnamed)"
    if entity_type == "Album" and getattr(entity, "release_year", None):
        return f"{name} ({entity.release_year})"
    return name


def parse_award_year(text: str) -> int | None:
    """Parse an optional award year; raise ValueError with a user-facing message when invalid."""
    text = text.strip()
    if not text:
        return None
    try:
        year = int(text)
    except ValueError:
        raise ValueError("Year must be a whole number.") from None
    if not YEAR_MIN <= year <= YEAR_MAX:
        raise ValueError(f"Year must be between {YEAR_MIN} and {YEAR_MAX}.")
    return year


def delete_awards(controller: Any, award_ids: list[int]) -> bool:
    """Delete awards and their associations; child awards become top-level awards."""
    if not award_ids:
        return True
    # Associations first: their FK has no ON DELETE, so the award delete would fail on them.
    if not controller.delete.delete_entity("AwardAssociation", award_id=list(award_ids)):
        return False
    # awards.parent_id is ON DELETE SET NULL, so children survive as roots.
    return bool(controller.delete.delete_entity("Award", entity_ids=list(award_ids)))
