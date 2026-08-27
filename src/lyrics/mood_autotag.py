"""
mood_autotag.py

Shared "score lyrics, write associations" path used by both the per-track
auto-tag wiring (LyricsTab, on lyrics save/search) and the library-wide
MoodAutoTagWorker, so both write through one place and can't drift apart.

Writes are additive only: a mood/place association is only ever added,
never removed or overwritten, whatever its origin (manual or auto). There
is no manual/auto provenance column (a deliberate simplification, see
docs/specs/lyrics_mood_tagging.md) -- consequence: a manually-removed
auto-tag can reappear on a later recalculation if its keywords still
match. Mood writes lean on MoodTrackAssociation's composite primary key
for free dedup (add_entities_with_fallback); PlaceAssociation has a
surrogate primary key, so it does NOT get that for free -- this module
does its own existence check before inserting.

Each newly-written MoodTrackAssociation row also carries a `score` (the
mood's lyrics-match density from score_moods_detailed), consumed by the
"most representative tracks per mood" statistic. It rides along in the row
dict and doesn't affect composite-PK dedup -- a row that already exists
keeps whatever score it was created with (additive-only: this path never
rewrites an existing association).
"""

from dataclasses import dataclass, field

from sqlalchemy.exc import SQLAlchemyError

from src.core.logger_config import logger
from src.lyrics.mood_scoring import known_mood_names, score_moods_detailed
from src.lyrics.place_matching import detect_known_places
from src.place.place_association_types import (
    fetch_association_types,
    find_or_create_association_type,
)

SONG_ABOUT_TYPE_NAME = "Song About"


@dataclass
class AutotagContext:
    mood_id_by_name: dict
    place_id_by_name: dict
    song_about_type_id: int | None
    existing_place_pairs: set = field(default_factory=set)


def build_autotag_context(controller) -> AutotagContext:
    """Load everything auto_tag_track() needs once, so a library-wide scan
    doesn't re-query Mood/Place/PlaceAssociationType per track."""
    moods = controller.get.get_all_entities("Mood") or []
    mood_id_by_name = {m.mood_name: m.mood_id for m in moods}

    # assets/mood_keywords.json is the live source of truth for what the
    # tagger can match (see mood_scoring.known_mood_names) -- a name can
    # end up there without a matching `Mood` DB row (hand-edited keyword
    # file, taxonomy drift from db_defaults.py's seed list, etc). Without
    # this, auto_tag_track() below silently drops that mood's matches
    # forever since it only writes rows for names already in
    # mood_id_by_name. Create the missing rows here so both auto-tag call
    # sites self-heal without needing an app restart.
    missing_mood_names = known_mood_names() - mood_id_by_name.keys()
    if missing_mood_names:
        try:
            created = controller.add.add_entities(
                "Mood", [{"mood_name": name} for name in missing_mood_names]
            )
            mood_id_by_name.update({m.mood_name: m.mood_id for m in created})
            logger.info(
                f"Created {len(created)} Mood row(s) missing for keyword-listed "
                f"moods: {', '.join(m.mood_name for m in created)}"
            )
        except SQLAlchemyError as e:
            logger.error(f"Failed to create missing Mood row(s): {e}")

    places = controller.get.get_all_entities("Place") or []
    place_id_by_name = {p.place_name: p.place_id for p in places}

    known_types = fetch_association_types(controller)
    song_about = find_or_create_association_type(
        controller, SONG_ABOUT_TYPE_NAME, known_types
    )
    song_about_type_id = song_about.association_type_id if song_about else None

    existing_place_pairs = set()
    if song_about_type_id is not None:
        existing = (
            controller.get.get_all_entities(
                "PlaceAssociation",
                entity_type="Track",
                association_type_id=song_about_type_id,
            )
            or []
        )
        existing_place_pairs = {(a.place_id, a.entity_id) for a in existing}

    return AutotagContext(
        mood_id_by_name=mood_id_by_name,
        place_id_by_name=place_id_by_name,
        song_about_type_id=song_about_type_id,
        existing_place_pairs=existing_place_pairs,
    )


def auto_tag_track(controller, track_id, lyrics, context: AutotagContext):
    """Score `lyrics` and write any newly-matching mood/place associations
    for `track_id`. Returns (moods_added, places_added) -- only the names
    that were newly written by this call, NOT the full matched set (a
    keyword can keep matching every run; already-existing associations,
    manual or previously auto-added, are never touched or recounted)."""
    if not lyrics or not lyrics.strip():
        return [], []

    moods_matched = score_moods_detailed(lyrics)
    mood_name_by_id = {v: k for k, v in context.mood_id_by_name.items()}
    mood_rows = [
        {
            "mood_id": context.mood_id_by_name[name],
            "track_id": track_id,
            "score": match.density,
        }
        for name, match in moods_matched.items()
        if name in context.mood_id_by_name
    ]
    moods_added = []
    if mood_rows:
        try:
            added_entities, _failed = controller.add.add_entities_with_fallback(
                "MoodTrackAssociation", mood_rows
            )
            moods_added = [
                mood_name_by_id[e.mood_id]
                for e in added_entities
                if e.mood_id in mood_name_by_id
            ]
        except SQLAlchemyError as e:
            logger.error(
                f"Failed to write mood associations for track {track_id}: {e}"
            )

    places_matched = detect_known_places(
        lyrics, list(context.place_id_by_name.keys())
    )
    place_rows = []
    places_added = []
    if context.song_about_type_id is not None:
        for name in places_matched:
            place_id = context.place_id_by_name[name]
            pair = (place_id, track_id)
            if pair in context.existing_place_pairs:
                continue
            place_rows.append(
                {
                    "place_id": place_id,
                    "entity_id": track_id,
                    "entity_type": "Track",
                    "association_type_id": context.song_about_type_id,
                }
            )
            context.existing_place_pairs.add(pair)
            places_added.append(name)
    if place_rows:
        try:
            controller.add.add_entities("PlaceAssociation", place_rows)
        except SQLAlchemyError as e:
            logger.error(
                f"Failed to write place associations for track {track_id}: {e}"
            )
            places_added = []
            for pair in [(r["place_id"], r["entity_id"]) for r in place_rows]:
                context.existing_place_pairs.discard(pair)

    return moods_added, places_added


def auto_tag_lyrics_safe(controller, track_id, lyrics) -> tuple[list, list]:
    """Convenience wrapper around build_autotag_context()+auto_tag_track()
    for one-off (non-batch) call sites -- lyrics search/save on the player
    dock and the track edit dialog's Lyrics tab. Never raises: a
    mood-matching or DB-write failure here must not block the lyrics save
    or search result it's attached to. Returns ([], []) on failure."""
    try:
        context = build_autotag_context(controller)
        return auto_tag_track(controller, track_id, lyrics, context)
    except Exception as e:  # ruff: ignore[blind-except]
        logger.error(f"Mood auto-tag failed for track {track_id}: {e}")
        return [], []
