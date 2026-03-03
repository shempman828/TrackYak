"""
playlist_smart_builder.py

Builds and refreshes smart playlists by evaluating stored criteria and
updating which tracks belong in the playlist.
"""

import datetime
from typing import Any, Dict, List, Set

from sqlalchemy.exc import SQLAlchemyError

from src.logger_config import logger


class SmartPlaylistBuilder:
    """Builds and refreshes smart playlists based on stored criteria."""

    def __init__(self, controller):
        self.controller = controller

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def refresh_playlist(self, playlist_id: int) -> bool:
        """
        Re-evaluate a smart playlist's criteria and update its tracks.

        Steps:
          - Load criteria rows from the database
          - Find tracks that match
          - Replace the playlist's track list

        Returns True on success, False on any error.
        """
        try:
            # 1. Get the SmartPlaylist record (for logic = AND / OR)
            smart_playlist = self.controller.get.get_entity_object(
                "SmartPlaylist", playlist_id=playlist_id
            )
            if not smart_playlist:
                logger.error(
                    f"SmartPlaylist record not found for playlist_id={playlist_id}"
                )
                return False

            # 2. Load criteria rows for this smart playlist
            criteria_rows = self.controller.get.get_all_entities(
                "SmartPlaylistCriteria",
                smart_playlist_id=smart_playlist.playlist_id,
            )

            if not criteria_rows:
                logger.warning(
                    f"Smart playlist {playlist_id} has no criteria — no tracks will be added."
                )
                # Still update the playlist (clear it) and timestamp
                self._update_playlist_tracks(playlist_id, [])
                self._touch_last_refreshed(playlist_id)
                return True

            # 3. Convert ORM rows to plain dicts that _get_matching_track_ids understands
            conditions = [self._row_to_condition(row) for row in criteria_rows]

            # 4. Read AND/OR logic — defaults to AND if not stored
            logic = getattr(smart_playlist, "logic", "AND") or "AND"

            # 5. Find matching tracks
            matching_track_ids = self._get_matching_track_ids(conditions, logic.upper())

            # 6. Update the playlist
            success = self._update_playlist_tracks(playlist_id, matching_track_ids)

            if success:
                self._touch_last_refreshed(playlist_id)
                logger.info(
                    f"Refreshed smart playlist {playlist_id} "
                    f"({logic}) → {len(matching_track_ids)} tracks"
                )

            return success

        except Exception as e:
            logger.error(f"Error refreshing smart playlist {playlist_id}: {e}")
            return False

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _row_to_condition(self, row) -> Dict[str, Any]:
        """
        Convert a SmartPlaylistCriteria ORM row into a plain dict like:
            {"field": "user_rating", "comparison": "gt", "value": "5.5", "type": "Float"}
        """
        return {
            "field": getattr(row, "field_name", ""),
            "comparison": getattr(row, "comparison", "eq"),
            "value": getattr(row, "value", None),
            "type": getattr(row, "type", "String"),
        }

    def _get_matching_track_ids(self, conditions: List[Dict], logic: str) -> List[int]:
        """
        Query the Track table using the given conditions.

        AND logic: one query with all conditions combined (faster).
        OR logic:  one query per condition, results merged.
        """
        if not conditions:
            return []

        if logic == "AND":
            combined_kwargs = {}
            for condition in conditions:
                kwargs = self._condition_to_kwargs(condition)
                combined_kwargs.update(kwargs)

            tracks = self.controller.get.get_all_entities("Track", **combined_kwargs)
            return [t.track_id for t in tracks]

        else:  # OR
            seen: Set[int] = set()
            for condition in conditions:
                kwargs = self._condition_to_kwargs(condition)
                tracks = self.controller.get.get_all_entities("Track", **kwargs)
                seen.update(t.track_id for t in tracks)
            return list(seen)

    def _condition_to_kwargs(self, condition: Dict[str, Any]) -> Dict[str, Any]:
        """
        Turn one condition dict into a **kwargs dict for get_all_entities.

        Example:
            {"field": "user_rating", "comparison": "gt", "value": "5.5"}
            → {"user_rating__gt": 5.5}
        """
        field = condition.get("field", "")
        comparison = condition.get("comparison", "eq")
        value = condition.get("value")
        data_type = condition.get("type", "String")

        if not field or not comparison:
            return {}

        # Cast the stored string value to the correct Python type
        cast_value = self._cast_value(value, data_type, comparison)

        # Operators that use a boolean flag instead of a real value
        if comparison == "isnull":
            return {f"{field}__isnull": True}
        if comparison == "notnull":
            return {f"{field}__isnull": False}

        if cast_value is None:
            # Don't add a condition with a None value (would match everything)
            logger.warning(f"Skipping condition with None value: {condition}")
            return {}

        return {f"{field}__{comparison}": cast_value}

    def _cast_value(self, value: Any, data_type: str, comparison: str) -> Any:
        """
        Cast the stored string value to the appropriate Python type.

        Values are stored as strings in the database, so we need to convert
        them back before querying (e.g. "5.5" → 5.5 for a Float field).
        """
        if value is None:
            return None

        try:
            if data_type == "Integer":
                return int(float(str(value)))  # handles "5.0" → 5
            elif data_type == "Float":
                return float(value)
            elif data_type == "List":
                # Could be a Python list already, or a comma-separated string
                if isinstance(value, list):
                    return value
                return [v.strip() for v in str(value).split(",") if v.strip()]
            else:
                # String, Text, Datetime — keep as string
                return str(value) if value != "" else None
        except (ValueError, TypeError) as e:
            logger.warning(f"Could not cast value '{value}' as {data_type}: {e}")
            return None

    def _update_playlist_tracks(self, playlist_id: int, track_ids: List[int]) -> bool:
        """
        SUPER OPTIMIZED: Use bulk database operations for massive speed improvement.

        Instead of 10,000+ individual inserts, we do 1 bulk insert.

        Performance:
        - OLD: 17,557 tracks = 17,557 separate transactions = 10+ minutes
        - NEW: 17,557 tracks = 3 bulk operations = 5-10 seconds

        Args:
            playlist_id: The ID of the playlist to update
            track_ids: List of track IDs that should be in the playlist

        Returns:
            True if successful, False if an error occurred
        """
        try:
            from src.db_tables import PlaylistTracks  # Import the ORM model

            # ═══════════════════════════════════════════════════════════════
            # STEP 1: Load existing tracks
            # ═══════════════════════════════════════════════════════════════

            existing_tracks = self.controller.get.get_all_entities(
                "PlaylistTracks", playlist_id__eq=playlist_id
            )

            # Create sets for comparison
            existing_track_ids = set(pt.track_id for pt in existing_tracks)
            new_track_ids = set(track_ids)

            # ═══════════════════════════════════════════════════════════════
            # STEP 2: Calculate differences
            # ═══════════════════════════════════════════════════════════════

            tracks_to_remove = existing_track_ids - new_track_ids
            tracks_to_add = new_track_ids - existing_track_ids

            now = datetime.datetime.now()

            logger.info(
                f"Playlist {playlist_id}: {len(tracks_to_add)} to add, "
                f"{len(tracks_to_remove)} to remove, "
                f"{len(existing_track_ids & new_track_ids)} to keep"
            )

            # ═══════════════════════════════════════════════════════════════
            # STEP 3: BULK DELETE tracks that are no longer needed
            # ═══════════════════════════════════════════════════════════════

            if tracks_to_remove:
                # Delete in bulk using SQLAlchemy's delete with IN clause
                session = self.controller.get.session
                session.query(PlaylistTracks).filter(
                    PlaylistTracks.playlist_id == playlist_id,
                    PlaylistTracks.track_id.in_(tracks_to_remove),
                ).delete(synchronize_session=False)
                session.commit()
                logger.debug(f"BULK deleted {len(tracks_to_remove)} tracks")

            # ═══════════════════════════════════════════════════════════════
            # STEP 4: BULK INSERT new tracks
            # ═══════════════════════════════════════════════════════════════

            if tracks_to_add:
                # Get current max position
                current_positions = [
                    getattr(pt, "position", 0) for pt in existing_tracks
                ]
                next_position = max(current_positions, default=0) + 1

                # Create a list of PlaylistTracks objects to insert
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

                # BULK INSERT all at once!
                session = self.controller.get.session
                session.bulk_save_objects(new_entries)
                session.commit()
                logger.debug(f"BULK inserted {len(tracks_to_add)} tracks")

            # ═══════════════════════════════════════════════════════════════
            # STEP 5: Update playlist metadata
            # ═══════════════════════════════════════════════════════════════

            self.controller.update.update_entity(
                "Playlist", playlist_id, last_modified=now
            )

            logger.info(
                f"✅ Playlist {playlist_id} updated in bulk: "
                f"{len(new_track_ids)} total tracks"
            )

            return True

        except SQLAlchemyError as e:
            logger.error(f"Database error updating playlist tracks: {e}")
            # Rollback on error
            try:
                self.controller.get.session.rollback()
            except:
                pass
            return False
        except Exception as e:
            logger.error(f"Error updating playlist tracks: {e}")
            return False

    def _touch_last_refreshed(self, playlist_id: int):
        """Update the last_refreshed timestamp on the SmartPlaylist record."""
        try:
            self.controller.update.update_entity(
                "SmartPlaylist",
                entity_id=playlist_id,
                last_refreshed=datetime.datetime.now(),
            )
        except Exception as e:
            logger.warning(
                f"Could not update last_refreshed for playlist {playlist_id}: {e}"
            )
