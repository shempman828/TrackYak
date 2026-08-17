"""Resolves which Track rows need their tags rewritten after a DB mutation,
and marks them via Track.needs_tag_write, so tag-writing can query "what
changed" instead of rescanning the whole library every time.

Scope is driven by what TrackDataAssembler (src/metadata/metadata_track_data.py)
actually reads for a tag write: Track, Album, Disc, TrackArtistRole,
AlbumRoleAssociation, TrackGenre, MoodTrackAssociation, AlbumPublisher,
PlaceAssociation (Track/Album only), and non-smart Playlist/PlaylistTracks
membership. Award/AwardAssociation, TrackUsage, and Samples are deliberately
not wired up here -- they're not read by TrackDataAssembler, so marking
tracks dirty for them would be pure overhead with no effect on what gets
written.
"""

from sqlalchemy import select, update

from src.db.db_tables.associations import (
    AlbumPublisher,
    AlbumRoleAssociation,
    TrackArtistRole,
    TrackGenre,
)
from src.db.db_tables.mood import MoodTrackAssociation
from src.db.db_tables.place import PlaceAssociation
from src.db.db_tables.playlist import PlaylistTracks
from src.db.db_tables.track import Track
from src.metadata.metadata_mapping import (
    ID3_DATE_MAPPINGS,
    ID3_TRACK_MAPPINGS,
    MP4_DATE_MAPPINGS,
    MP4_TRACK_MAPPINGS,
    VORBIS_DATE_MAPPINGS,
    VORBIS_TRACK_MAPPINGS,
    WAV_DATE_MAPPINGS,
    WAV_TRACK_MAPPINGS,
)


def _date_fields_for_track(*mapping_dicts) -> set:
    fields = set()
    for mapping in mapping_dicts:
        for spec in mapping.values():
            if spec.get("target") == "track":
                fields.update(spec.get("fields", []))
    return fields


# Track's own scalar fields that are actually written into a file's tags,
# derived from the mapping tables (the single source of truth for what gets
# written) instead of a hand-maintained list that could drift from them.
# disc_id/album_id aren't literal tag fields, but changing them changes which
# Album/Disc row feeds the tags, so they count too.
TRACK_TAG_FIELDS = (
    {m["field"] for m in ID3_TRACK_MAPPINGS.values()}
    | {m["field"] for m in VORBIS_TRACK_MAPPINGS.values()}
    | {m["field"] for m in MP4_TRACK_MAPPINGS.values()}
    | {m["field"] for m in WAV_TRACK_MAPPINGS.values()}
    | _date_fields_for_track(
        ID3_DATE_MAPPINGS, VORBIS_DATE_MAPPINGS, MP4_DATE_MAPPINGS, WAV_DATE_MAPPINGS
    )
    | {"disc_id", "album_id"}
)

# Association-row models that carry a track_id column directly -- the row's
# own attributes/kwargs already have the answer, no query needed.
DIRECT_TRACK_FK = {
    "TrackArtistRole": "track_id",
    "TrackGenre": "track_id",
    "MoodTrackAssociation": "track_id",
    "PlaylistTracks": "track_id",
}

# Association-row models that carry an album_id -- cascades to every track
# on that album (album-level artist credits / publishers).
ALBUM_FK = {
    "AlbumRoleAssociation": "album_id",
    "AlbumPublisher": "album_id",
}


def mark_tracks_dirty(session, track_ids) -> None:
    """Bulk-flag the given tracks as needing a tag rewrite."""
    ids = {tid for tid in track_ids if tid is not None}
    if not ids:
        return
    session.execute(
        update(Track).where(Track.track_id.in_(ids)).values(needs_tag_write=1)
    )


def tracks_for_album(session, album_ids) -> set:
    """Every track belonging to any of the given albums."""
    ids = {i for i in album_ids if i is not None}
    if not ids:
        return set()
    return set(
        session.scalars(
            select(Track.track_id).where(Track.album_id.in_(ids))
        ).all()
    )


def tracks_for_disc(session, disc_ids) -> set:
    """Every track belonging to any of the given discs."""
    ids = {i for i in disc_ids if i is not None}
    if not ids:
        return set()
    return set(
        session.scalars(select(Track.track_id).where(Track.disc_id.in_(ids))).all()
    )


def sibling_track_ids(session, disc_id=None, album_id=None) -> set:
    """Tracks sharing a disc_id/album_id with a track that just moved --
    disc_track_count/album_disc_count depend on sibling counts, so every
    sibling's tags need rewriting too, not just the moved track's.
    """
    ids = set()
    if disc_id is not None:
        ids |= tracks_for_disc(session, [disc_id])
    if album_id is not None:
        ids |= tracks_for_album(session, [album_id])
    return ids


def tracks_for_artist(session, artist_ids) -> set:
    ids = {i for i in artist_ids if i is not None}
    if not ids:
        return set()
    direct = session.scalars(
        select(TrackArtistRole.track_id).where(TrackArtistRole.artist_id.in_(ids))
    ).all()
    album_ids = session.scalars(
        select(AlbumRoleAssociation.album_id).where(
            AlbumRoleAssociation.artist_id.in_(ids)
        )
    ).all()
    return set(direct) | tracks_for_album(session, album_ids)


def tracks_for_role(session, role_ids) -> set:
    ids = {i for i in role_ids if i is not None}
    if not ids:
        return set()
    direct = session.scalars(
        select(TrackArtistRole.track_id).where(TrackArtistRole.role_id.in_(ids))
    ).all()
    album_ids = session.scalars(
        select(AlbumRoleAssociation.album_id).where(
            AlbumRoleAssociation.role_id.in_(ids)
        )
    ).all()
    return set(direct) | tracks_for_album(session, album_ids)


def tracks_for_publisher(session, publisher_ids) -> set:
    ids = {i for i in publisher_ids if i is not None}
    if not ids:
        return set()
    album_ids = session.scalars(
        select(AlbumPublisher.album_id).where(AlbumPublisher.publisher_id.in_(ids))
    ).all()
    return tracks_for_album(session, album_ids)


def tracks_for_genre(session, genre_ids) -> set:
    ids = {i for i in genre_ids if i is not None}
    if not ids:
        return set()
    return set(
        session.scalars(
            select(TrackGenre.track_id).where(TrackGenre.genre_id.in_(ids))
        ).all()
    )


def tracks_for_mood(session, mood_ids) -> set:
    ids = {i for i in mood_ids if i is not None}
    if not ids:
        return set()
    return set(
        session.scalars(
            select(MoodTrackAssociation.track_id).where(
                MoodTrackAssociation.mood_id.in_(ids)
            )
        ).all()
    )


def tracks_for_place(session, place_ids) -> set:
    ids = {i for i in place_ids if i is not None}
    if not ids:
        return set()
    direct = session.scalars(
        select(PlaceAssociation.entity_id).where(
            PlaceAssociation.place_id.in_(ids),
            PlaceAssociation.entity_type == "Track",
        )
    ).all()
    album_ids = session.scalars(
        select(PlaceAssociation.entity_id).where(
            PlaceAssociation.place_id.in_(ids),
            PlaceAssociation.entity_type == "Album",
        )
    ).all()
    return set(direct) | tracks_for_album(session, album_ids)


def tracks_for_playlist(session, playlist_ids) -> set:
    ids = {i for i in playlist_ids if i is not None}
    if not ids:
        return set()
    return set(
        session.scalars(
            select(PlaylistTracks.track_id).where(
                PlaylistTracks.playlist_id.in_(ids)
            )
        ).all()
    )


def place_association_tracks(session, place_association_rows) -> set:
    """Resolve affected tracks for a batch of PlaceAssociation rows/dicts
    (each with entity_type/entity_id), branching on the polymorphic type.
    """
    track_ids = set()
    album_ids = set()
    for row in place_association_rows:
        entity_type = row.get("entity_type") if isinstance(row, dict) else row.entity_type
        entity_id = row.get("entity_id") if isinstance(row, dict) else row.entity_id
        if entity_type == "Track":
            track_ids.add(entity_id)
        elif entity_type == "Album":
            album_ids.add(entity_id)
    return track_ids | tracks_for_album(session, album_ids)


def mark_dirty_for_new_rows(session, model_name: str, rows: list) -> None:
    """Mark tracks dirty for newly-inserted association rows (add.py path).
    `rows` is a list of kwargs dicts, one per row about to be inserted --
    cheap to resolve since the row's own attributes carry the FK, no query
    needed (except for PlaceAssociation's Album-side, and Album/AlbumPublisher
    style cascades to every track on the album).
    """
    if not rows:
        return

    if model_name in DIRECT_TRACK_FK:
        fk = DIRECT_TRACK_FK[model_name]
        mark_tracks_dirty(session, (row.get(fk) for row in rows))
    elif model_name in ALBUM_FK:
        fk = ALBUM_FK[model_name]
        album_ids = {row.get(fk) for row in rows}
        mark_tracks_dirty(session, tracks_for_album(session, album_ids))
    elif model_name == "PlaceAssociation":
        mark_tracks_dirty(session, place_association_tracks(session, rows))


# Entity-rename/merge/delete models -- resolving these requires a query
# against the association tables (unlike DIRECT_TRACK_FK/ALBUM_FK, where the
# mutated row's own attributes already carry the answer).
CASCADE_RESOLVERS = {
    "Artist": tracks_for_artist,
    "Publisher": tracks_for_publisher,
    "Genre": tracks_for_genre,
    "Mood": tracks_for_mood,
    "Place": tracks_for_place,
    "Role": tracks_for_role,
    "Album": tracks_for_album,
    "Disc": tracks_for_disc,
    "Playlist": tracks_for_playlist,
}


def _track_siblings_for_move(session, old_disc_ids, old_album_ids, new_disc_ids, new_album_ids) -> set:
    return (
        tracks_for_disc(session, set(old_disc_ids) | set(new_disc_ids))
        | tracks_for_album(session, set(old_album_ids) | set(new_album_ids))
    )


def mark_dirty_for_entity_update(session, model_name: str, entity_ids, kwargs: dict) -> None:
    """Call BEFORE executing an update_entity/update_entities UPDATE, so any
    Track disc_id/album_id sibling lookup below still sees pre-mutation state.
    """
    entity_ids = list(entity_ids)
    if not entity_ids or not kwargs:
        return

    if model_name == "Track":
        if TRACK_TAG_FIELDS & kwargs.keys():
            mark_tracks_dirty(session, entity_ids)
        if "disc_id" in kwargs or "album_id" in kwargs:
            rows = session.execute(
                select(Track.disc_id, Track.album_id).where(
                    Track.track_id.in_(entity_ids)
                )
            ).all()
            old_disc_ids = {r[0] for r in rows if r[0] is not None}
            old_album_ids = {r[1] for r in rows if r[1] is not None}
            new_disc_ids = {kwargs["disc_id"]} if kwargs.get("disc_id") is not None else set()
            new_album_ids = {kwargs["album_id"]} if kwargs.get("album_id") is not None else set()
            mark_tracks_dirty(
                session,
                _track_siblings_for_move(
                    session, old_disc_ids, old_album_ids, new_disc_ids, new_album_ids
                ),
            )
    elif model_name in CASCADE_RESOLVERS:
        mark_tracks_dirty(session, CASCADE_RESOLVERS[model_name](session, entity_ids))
    elif model_name == "AlbumPublisher":
        # AlbumPublisher's PK is the composite (album_id, publisher_id); the
        # first PK column update_entity/update_entities key off of is
        # album_id itself, so entity_ids already *are* album ids.
        mark_tracks_dirty(session, tracks_for_album(session, entity_ids))
    elif model_name == "AlbumRoleAssociation":
        album_ids = session.scalars(
            select(AlbumRoleAssociation.album_id).where(
                AlbumRoleAssociation.association_id.in_(entity_ids)
            )
        ).all()
        mark_tracks_dirty(session, tracks_for_album(session, album_ids))


def mark_dirty_for_bulk_track_update(session, updates: list) -> None:
    """update_entities_bulk's per-row-differing-values path, Track only (the
    only model this bulk form is used for today -- see MB import review's
    _batch_update_tracks). Each dict in `updates` has track_id plus whatever
    fields are being set on that row.
    """
    if not updates:
        return

    tag_ids = [u["track_id"] for u in updates if TRACK_TAG_FIELDS & u.keys()]
    mark_tracks_dirty(session, tag_ids)

    moving = [u for u in updates if "disc_id" in u or "album_id" in u]
    if not moving:
        return

    ids = [u["track_id"] for u in moving]
    rows = session.execute(
        select(Track.disc_id, Track.album_id).where(Track.track_id.in_(ids))
    ).all()
    old_disc_ids = {r[0] for r in rows if r[0] is not None}
    old_album_ids = {r[1] for r in rows if r[1] is not None}
    new_disc_ids = {u["disc_id"] for u in moving if u.get("disc_id") is not None}
    new_album_ids = {u["album_id"] for u in moving if u.get("album_id") is not None}
    mark_tracks_dirty(
        session,
        _track_siblings_for_move(
            session, old_disc_ids, old_album_ids, new_disc_ids, new_album_ids
        ),
    )


def _attr(row, name):
    return row.get(name) if isinstance(row, dict) else getattr(row, name, None)


# PK attribute name for each CASCADE_RESOLVERS model, for the row-based path
# below (update_entity_by_filter / delete_entity), where a loaded ORM object
# (not just a bare id) is already in hand.
_CASCADE_PK_ATTR = {
    "Artist": "artist_id",
    "Publisher": "publisher_id",
    "Genre": "genre_id",
    "Mood": "mood_id",
    "Place": "place_id",
    "Role": "role_id",
    "Album": "album_id",
    "Disc": "disc_id",
    "Playlist": "playlist_id",
}


def mark_dirty_for_rows(session, model_name: str, rows) -> None:
    """Mark tracks dirty for a batch of already-loaded existing rows of
    `model_name` that are about to be updated (update_entity_by_filter) or
    deleted (delete_entity). `rows` are ORM instances, so old disc_id/album_id
    values are read straight off them -- no extra SELECT needed.
    """
    rows = list(rows)
    if not rows:
        return

    if model_name == "Track":
        mark_tracks_dirty(session, (_attr(r, "track_id") for r in rows))
        disc_ids = {_attr(r, "disc_id") for r in rows}
        album_ids = {_attr(r, "album_id") for r in rows}
        for disc_id in disc_ids:
            mark_tracks_dirty(session, sibling_track_ids(session, disc_id=disc_id))
        for album_id in album_ids:
            mark_tracks_dirty(session, sibling_track_ids(session, album_id=album_id))
    elif model_name in DIRECT_TRACK_FK:
        fk = DIRECT_TRACK_FK[model_name]
        mark_tracks_dirty(session, (_attr(r, fk) for r in rows))
    elif model_name in ALBUM_FK:
        fk = ALBUM_FK[model_name]
        album_ids = {_attr(r, fk) for r in rows}
        mark_tracks_dirty(session, tracks_for_album(session, album_ids))
    elif model_name == "PlaceAssociation":
        mark_tracks_dirty(session, place_association_tracks(session, rows))
    elif model_name in CASCADE_RESOLVERS:
        pk_attr = _CASCADE_PK_ATTR[model_name]
        ids = [_attr(r, pk_attr) for r in rows]
        mark_tracks_dirty(session, CASCADE_RESOLVERS[model_name](session, ids))
