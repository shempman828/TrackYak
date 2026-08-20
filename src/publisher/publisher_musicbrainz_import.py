"""
publisher_musicbrainz_import.py

GUI-free find-or-create logic for turning a MusicBrainz label (MBLabelInfo)
into a local Publisher, plus its headquarters and founder relations. Shared
by AlbumMusicBrainzReviewDialog (interactive, checkbox-gated) and the
one-time scripts/backfill_album_publishers.py script (headless, no review
step) so the matching/creation rules only need to be written once.

Publisher matching mirrors AlbumMusicBrainzReviewDialog._resolve_artist's
credit-resolution order: MBID match, then alias-aware name match (backfilling
the MBID once found), then create new -- and the same order is reused for a
label's founder relations, resolving/creating the counterpart Artist.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from src.core.logger_config import logger
from src.musicbrainz.musicbrainz_release import MBFounderRelation, MBLabelInfo
from src.place.place_association_types import (
    fetch_association_types,
    find_or_create_association_type,
)
from src.place.place_chain_resolver import resolve_place_chain

_HEADQUARTERS_TYPE_NAME = "Headquarters"

# Publisher scalar fields that get filled in from MB label data, but only
# when the local value is still blank -- never overwriting something
# already on file (same fill-blank convention as every other MB scalar
# enrichment path in this codebase).
_FILL_BLANK_FIELDS = (
    "description",
    "begin_year",
    "begin_month",
    "begin_day",
    "end_year",
    "end_month",
    "end_day",
)


def resolve_or_create_publisher(controller, label: MBLabelInfo) -> Any | None:
    """MBID match, then alias-aware name match (backfilling the MBID),
    then create new -- same order as _resolve_artist in
    album_musicbrainz_review_dialog.py, applied to Publisher/PublisherAlias.
    """
    publisher = controller.get.get_entity_object("Publisher", MBID=label.mbid)
    if publisher is not None:
        _fill_blank_scalars(controller, publisher, label)
        return publisher

    publisher = controller.get.resolve_entity_or_alias(
        "Publisher", "publisher_name", label.name
    )
    if publisher is not None:
        if not publisher.MBID:
            controller.update.update_entity(
                "Publisher", publisher.publisher_id, MBID=label.mbid
            )
            publisher.MBID = label.mbid
        _fill_blank_scalars(controller, publisher, label)
        return publisher

    return controller.add.add_entity(
        "Publisher",
        publisher_name=label.name,
        MBID=label.mbid,
        description=label.annotation,
        begin_year=label.begin_year,
        begin_month=label.begin_month,
        begin_day=label.begin_day,
        end_year=label.end_year,
        end_month=label.end_month,
        end_day=label.end_day,
    )


def _fill_blank_scalars(controller, publisher, label: MBLabelInfo) -> None:
    """Update only the fields that are currently None on `publisher`,
    sourced from the matching MBLabelInfo field of the same name (the
    label's `annotation` maps onto Publisher's `description`)."""
    source = {
        "description": label.annotation,
        "begin_year": label.begin_year,
        "begin_month": label.begin_month,
        "begin_day": label.begin_day,
        "end_year": label.end_year,
        "end_month": label.end_month,
        "end_day": label.end_day,
    }
    updates = {
        field: value
        for field, value in source.items()
        if value is not None and getattr(publisher, field, None) is None
    }
    if not updates:
        return
    try:
        controller.update.update_entity(
            "Publisher", publisher.publisher_id, **updates
        )
        for field, value in updates.items():
            setattr(publisher, field, value)
    except SQLAlchemyError as e:
        logger.warning(
            f"Could not fill in MusicBrainz details for publisher "
            f"'{publisher.publisher_name}': {e}"
        )


def resolve_or_create_founder_artist(controller, founder: MBFounderRelation) -> Any | None:
    """Same MBID -> alias-aware name -> create order as
    resolve_or_create_publisher, applied to Artist/ArtistAlias."""
    artist = controller.get.get_entity_object("Artist", MBID=founder.mbid)
    if artist is not None:
        return artist

    artist = controller.get.resolve_entity_or_alias(
        "Artist", "artist_name", founder.name
    )
    if artist is not None:
        if not artist.MBID:
            controller.update.update_entity(
                "Artist", artist.artist_id, MBID=founder.mbid
            )
            artist.MBID = founder.mbid
        return artist

    return controller.add.add_entity(
        "Artist", artist_name=founder.name, MBID=founder.mbid
    )


def resolve_or_create_publishers(controller, label: MBLabelInfo) -> list[Any]:
    """Every Publisher this label's name resolves to. A name that was
    previously split into 2+ publishers (see
    SplitDB._record_split_alias) resolves to that same ordered list
    instead of resolve_or_create_publisher recreating/reusing one
    combined Publisher -- see docs/specs/split_and_merge_aliases.md."""
    split_targets = controller.get.resolve_split_alias("Publisher", label.name)
    if split_targets:
        for publisher in split_targets:
            _fill_blank_scalars(controller, publisher, label)
        return split_targets

    publisher = resolve_or_create_publisher(controller, label)
    return [publisher] if publisher is not None else []


def apply_publisher_headquarters(
    controller, publisher, area_chain: list[dict[str, Any]], place_cache: dict[str, Any]
) -> None:
    """Add a "Headquarters" PlaceAssociation for `publisher` from
    `area_chain`, unless one already exists -- mirrors
    PublisherEditDialog._save_headquarters, but only ever fills a blank
    headquarters rather than replacing an existing one, same as the
    artist birthplace/deathplace "already exists" skip in
    ArtistEnrichmentReviewDialog._existing_place_association_types."""
    if not area_chain:
        return
    try:
        existing = (
            controller.get.get_all_entities(
                "PlaceAssociation",
                entity_type="Publisher",
                entity_id=publisher.publisher_id,
            )
            or []
        )
        if any(
            a.association_type and a.association_type.type_name == _HEADQUARTERS_TYPE_NAME
            for a in existing
        ):
            return

        place = resolve_place_chain(controller, area_chain, place_cache)
        if place is None:
            return
        known_types = fetch_association_types(controller)
        hq_type = find_or_create_association_type(
            controller, _HEADQUARTERS_TYPE_NAME, known_types
        )
        controller.add.add_entity(
            "PlaceAssociation",
            entity_id=publisher.publisher_id,
            entity_type="Publisher",
            place_id=place.place_id,
            association_type_id=hq_type.association_type_id if hq_type else None,
        )
    except SQLAlchemyError as e:
        logger.warning(
            f"Could not import headquarters for publisher "
            f"'{publisher.publisher_name}': {e}"
        )


def apply_publisher_founders(
    controller, publisher, founders: list[MBFounderRelation]
) -> None:
    """Resolve/create each founder Artist, then add any PublisherFounder
    rows not already present -- same diff-against-existing approach as
    PublisherEditDialog._save_founders."""
    if not founders:
        return
    try:
        existing_ids = {
            assoc.artist_id
            for assoc in (
                controller.get.get_all_entities(
                    "PublisherFounder", publisher_id=publisher.publisher_id
                )
                or []
            )
        }
    except SQLAlchemyError as e:
        logger.warning(
            f"Could not load existing founders for publisher "
            f"'{publisher.publisher_name}': {e}"
        )
        return

    for founder in founders:
        try:
            artist = resolve_or_create_founder_artist(controller, founder)
            if artist is None or artist.artist_id in existing_ids:
                continue
            controller.add.add_entity(
                "PublisherFounder",
                publisher_id=publisher.publisher_id,
                artist_id=artist.artist_id,
            )
            existing_ids.add(artist.artist_id)
        except SQLAlchemyError as e:
            logger.warning(
                f"Could not import founder '{founder.name}' for publisher "
                f"'{publisher.publisher_name}': {e}"
            )


def import_album_labels(
    controller, album, labels: list[MBLabelInfo], place_cache: dict[str, Any]
) -> list[str]:
    """Resolve/create a Publisher for each label, link it to `album` via
    AlbumPublisher, and apply its headquarters/founders. Returns
    human-readable failure descriptions instead of raising or touching Qt,
    so both the interactive review dialog and the headless backfill script
    can just log/display whatever comes back."""
    failures: list[str] = []
    if not labels:
        return failures

    existing_publisher_ids = {p.publisher_id for p in (album.publishers or [])}

    for label in labels:
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
                _, failed = controller.add.add_entities_with_fallback(
                    "AlbumPublisher",
                    [{"album_id": album.album_id, "publisher_id": publisher.publisher_id}],
                )
                if failed:
                    failures.append(f"Album/publisher link for '{label.name}'")
                else:
                    existing_publisher_ids.add(publisher.publisher_id)

            apply_publisher_headquarters(controller, publisher, label.area_chain, place_cache)
            apply_publisher_founders(controller, publisher, label.founders)

    return failures
