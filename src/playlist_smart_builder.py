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
        Replace all tracks in the playlist with the given track_ids.

        Preserves the original date_added for tracks that were already
        in the playlist — only new tracks get today's date.
        """
        try:
            # Load existing tracks so we can preserve date_added
            existing_tracks = self.controller.get.get_all_entities(
                "PlaylistTracks", playlist_id__eq=playlist_id
            )
            existing_date_map = {pt.track_id: pt.date_added for pt in existing_tracks}

            # Delete all current tracks
            self.controller.delete.delete_entity(
                "PlaylistTracks", playlist_id__eq=playlist_id
            )

            # Re-add in new order
            now = datetime.datetime.now()
            for position, track_id in enumerate(track_ids, start=1):
                date_added = existing_date_map.get(track_id, now)
                self.controller.add.add_entity(
                    "PlaylistTracks",
                    playlist_id=playlist_id,
                    track_id=track_id,
                    position=position,
                    date_added=date_added,
                )

            # Bump last_modified on the Playlist itself
            self.controller.update.update_entity(
                "Playlist", playlist_id, last_modified=now
            )

            return True

        except SQLAlchemyError as e:
            logger.error(f"Database error updating playlist tracks: {e}")
            return False
        except Exception as e:
            logger.error(f"Error updating playlist tracks: {e}")
            return False

    def _touch_last_refreshed(self, playlist_id: int):
        """Update the last_refreshed timestamp on the SmartPlaylist record."""
        try:
            self.controller.update.update_entity(
                "SmartPlaylist", playlist_id, last_refreshed=datetime.datetime.now()
            )
        except Exception as e:
            logger.warning(
                f"Could not update last_refreshed for playlist {playlist_id}: {e}"
            )
