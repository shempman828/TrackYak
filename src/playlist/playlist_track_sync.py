"""
playlist_track_sync.py

Shared bulk diff/delete/insert helper for syncing a playlist's
PlaylistTracks rows to a desired track_id set, instead of clearing and
reinserting. Extracted from SmartPlaylistBuilder._update_playlist_tracks
so ChartPlaylistBuilder (src/charts/chart_playlist_builder.py) can reuse
the same tested bulk-diff logic rather than duplicating it.
"""

from collections.abc import Iterable
import datetime

from sqlalchemy.exc import SQLAlchemyError

from src.core.logger_config import logger


class PlaylistTrackSyncResult:
    def __init__(self, added: int, removed: int, kept: int):
        self.added = added
        self.removed = removed
        self.kept = kept


def sync_playlist_tracks(
    controller, playlist_id: int, track_ids: Iterable[int]
) -> PlaylistTrackSyncResult | None:
    """
    Bulk-diff a playlist's PlaylistTracks against `track_ids` and apply
    only the delta (delete removed, insert added), then touch the
    playlist's last_modified timestamp.

    Returns a PlaylistTrackSyncResult, or None if a DB error occurred
    (logged and rolled back).
    """
    try:
        from src.db.db_tables import PlaylistTracks

        existing_tracks = controller.get.get_all_entities(
            "PlaylistTracks", playlist_id__eq=playlist_id
        )

        # Capture positions as plain values now -- the ORM objects get
        # expired by the commit() below, and any bulk-deleted row
        # (synchronize_session=False, so the session doesn't know) would
        # raise ObjectDeletedError if touched again afterward.
        existing_track_ids = set(pt.track_id for pt in existing_tracks)
        existing_positions = [getattr(pt, "position", 0) for pt in existing_tracks]
        new_track_ids = set(track_ids)

        tracks_to_remove = existing_track_ids - new_track_ids
        tracks_to_add = new_track_ids - existing_track_ids
        kept = len(existing_track_ids & new_track_ids)

        now = datetime.datetime.now()

        if tracks_to_remove:
            session = controller.get.session
            session.query(PlaylistTracks).filter(
                PlaylistTracks.playlist_id == playlist_id,
                PlaylistTracks.track_id.in_(tracks_to_remove),
            ).delete(synchronize_session=False)
            session.commit()

        if tracks_to_add:
            next_position = max(existing_positions, default=0) + 1
            new_entries = []
            for track_id in tracks_to_add:
                new_entries.append(
                    PlaylistTracks(
                        playlist_id=playlist_id,
                        track_id=track_id,
                        position=next_position,
                        date_added=now,
                    )
                )
                next_position += 1

            session = controller.get.session
            session.bulk_save_objects(new_entries)
            session.commit()

        controller.update.update_entity("Playlist", playlist_id, last_modified=now)

        return PlaylistTrackSyncResult(
            added=len(tracks_to_add), removed=len(tracks_to_remove), kept=kept
        )

    except SQLAlchemyError as e:
        logger.error(f"Database error syncing playlist {playlist_id} tracks: {e}")
        try:
            controller.get.session.rollback()
        except SQLAlchemyError as rollback_exc:
            logger.error(f"Rollback failed after playlist sync error: {rollback_exc}")
        return None
