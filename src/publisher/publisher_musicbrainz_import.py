"""GUI-free find-or-create logic that turns MusicBrainz labels into local Publishers."""

from __future__ import annotations

from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from src.foundation.logger_config import logger
from src.musicbrainz.musicbrainz_release import MBFounderRelation, MBLabelInfo
from src.place.place_association_types import fetch_association_types, find_or_create_association_type
from src.place.place_chain_resolver import resolve_place_chain

_HEADQUARTERS_TYPE_NAME = "Headquarters"

# Publisher fields filled from MB label data only while still blank (never overwritten).
# Each maps to the MBLabelInfo attribute of the same name, except description <- annotation.
_FILL_BLANK_FIELDS = ("description", "begin_year", "begin_month", "begin_day", "end_year", "end_month", "end_day")


def resolve_or_create_publisher(controller, label: MBLabelInfo) -> Any | None:
    """Return the Publisher for a label: MBID match, else alias-aware name match, else a new row."""
    # An empty MBID must not be used as a lookup key: it would match an unrelated row.
    if label.mbid:
        publisher = controller.get.get_entity_object("Publisher", MBID=label.mbid)
        if publisher is not None:
            _fill_blank_scalars(controller, publisher, label)
            return publisher

    publisher = controller.get.resolve_entity_or_alias("Publisher", "publisher_name", label.name)
    if publisher is not None and not publisher.MBID and label.mbid:
        controller.update.update_entity("Publisher", publisher.publisher_id, MBID=label.mbid)
        publisher.MBID = label.mbid
        _fill_blank_scalars(controller, publisher, label)
        return publisher
    # A name match whose row already carries a (necessarily different --
    # the MBID lookup above would have caught an equal one) MBID is a
    # distinct real-world entity, not this label -- ignore it and create
    # a new Publisher instead of merging two different entities.

    return controller.add.add_entity(
        "Publisher",
        publisher_name=label.name,
        MBID=label.mbid or None,
        description=label.annotation,
        begin_year=label.begin_year,
        begin_month=label.begin_month,
        begin_day=label.begin_day,
        end_year=label.end_year,
        end_month=label.end_month,
        end_day=label.end_day,
    )


def _fill_blank_scalars(controller, publisher, label: MBLabelInfo) -> None:
    """Copy label values into the publisher fields that are still None."""
    source = {field: getattr(label, "annotation" if field == "description" else field) for field in _FILL_BLANK_FIELDS}
    updates = {field: value for field, value in source.items() if value is not None and getattr(publisher, field, None) is None}
    if not updates:
        return
    try:
        controller.update.update_entity("Publisher", publisher.publisher_id, **updates)
        for field, value in updates.items():
            setattr(publisher, field, value)
    except SQLAlchemyError as e:
        logger.warning(f"Could not fill in MusicBrainz details for publisher '{publisher.publisher_name}': {e}")


def resolve_or_create_founder_artist(controller, founder: MBFounderRelation) -> Any | None:
    """Return the Artist for a founder: MBID match, else alias-aware name match, else a new row."""
    if founder.mbid:
        artist = controller.get.get_entity_object("Artist", MBID=founder.mbid)
        if artist is not None:
            return artist

    artist = controller.get.resolve_entity_or_alias("Artist", "artist_name", founder.name)
    if artist is not None and not artist.MBID and founder.mbid:
        controller.update.update_entity("Artist", artist.artist_id, MBID=founder.mbid)
        artist.MBID = founder.mbid
        return artist
    # A name match whose row already carries a (necessarily different)
    # MBID is a distinct real-world artist, not this founder -- ignore
    # it and create a new Artist instead of merging two different people.

    return controller.add.add_entity("Artist", artist_name=founder.name, MBID=founder.mbid or None)


def resolve_or_create_publishers(controller, label: MBLabelInfo) -> list[Any]:
    """Return every Publisher a label resolves to, honoring split aliases (docs/specs/split_and_merge_aliases.md)."""
    split_targets = controller.get.resolve_split_alias("Publisher", label.name)
    if split_targets:
        for publisher in split_targets:
            _fill_blank_scalars(controller, publisher, label)
        return split_targets

    publisher = resolve_or_create_publisher(controller, label)
    return [publisher] if publisher is not None else []


def apply_publisher_headquarters(controller, publisher, area_chain: list[dict[str, Any]], place_cache: dict[str, Any]) -> None:
    """Add a "Headquarters" PlaceAssociation from area_chain only if the publisher has none yet."""
    if not area_chain:
        return
    try:
        existing = controller.get.get_all_entities("PlaceAssociation", entity_type="Publisher", entity_id=publisher.publisher_id) or []
        if any(a.association_type and a.association_type.type_name == _HEADQUARTERS_TYPE_NAME for a in existing):
            return

        place = resolve_place_chain(controller, area_chain, place_cache)
        if place is None:
            return
        known_types = fetch_association_types(controller)
        hq_type = find_or_create_association_type(controller, _HEADQUARTERS_TYPE_NAME, known_types)
        controller.add.add_entity(
            "PlaceAssociation", entity_id=publisher.publisher_id, entity_type="Publisher", place_id=place.place_id, association_type_id=hq_type.association_type_id if hq_type else None
        )
    except SQLAlchemyError as e:
        logger.warning(f"Could not import headquarters for publisher '{publisher.publisher_name}': {e}")


def apply_publisher_founders(controller, publisher, founders: list[MBFounderRelation]) -> None:
    """Resolve or create each founder Artist and add the missing PublisherFounder rows."""
    if not founders:
        return
    try:
        existing_ids = {assoc.artist_id for assoc in (controller.get.get_all_entities("PublisherFounder", publisher_id=publisher.publisher_id) or [])}
    except SQLAlchemyError as e:
        logger.warning(f"Could not load existing founders for publisher '{publisher.publisher_name}': {e}")
        return

    for founder in founders:
        try:
            artist = resolve_or_create_founder_artist(controller, founder)
            if artist is None or artist.artist_id in existing_ids:
                continue
            controller.add.add_entity("PublisherFounder", publisher_id=publisher.publisher_id, artist_id=artist.artist_id)
            existing_ids.add(artist.artist_id)
        except SQLAlchemyError as e:
            logger.warning(f"Could not import founder '{founder.name}' for publisher '{publisher.publisher_name}': {e}")


def import_album_labels(controller, album, labels: list[MBLabelInfo], place_cache: dict[str, Any]) -> list[str]:
    """Import each label onto the album and return human-readable failure descriptions."""
    failures: list[str] = []
    if not labels:
        return failures

    existing_publisher_ids = {p.publisher_id for p in (album.publishers or [])}

    # A release can list one label several times (one per catalog number); import it once.
    unique_labels = list({(label.mbid or label.name): label for label in labels}.values())

    for label in unique_labels:
        try:
            publishers = resolve_or_create_publishers(controller, label)
        except SQLAlchemyError as e:
            logger.warning(f"Could not import label '{label.name}': {e}")
            failures.append(f"Publisher '{label.name}'")
            continue
        if not publishers:
            failures.append(f"Publisher '{label.name}'")
            continue

        for publisher in publishers:
            if publisher.publisher_id not in existing_publisher_ids:
                _, failed = controller.add.add_entities_with_fallback("AlbumPublisher", [{"album_id": album.album_id, "publisher_id": publisher.publisher_id}])
                if failed:
                    failures.append(f"Album/publisher link for '{label.name}'")
                else:
                    existing_publisher_ids.add(publisher.publisher_id)

            apply_publisher_headquarters(controller, publisher, label.area_chain, place_cache)
            apply_publisher_founders(controller, publisher, label.founders)

    return failures
