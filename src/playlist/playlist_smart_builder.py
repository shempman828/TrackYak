"""
playlist_smart_builder.py

Builds and refreshes smart playlists by evaluating stored criteria and
updating which tracks belong in the playlist.
"""

import datetime
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from src.core.logger_config import logger
from src.playlist.playlist_track_sync import sync_playlist_tracks


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
                logger.error(f"SmartPlaylist record not found for playlist_id={playlist_id}")
                return False

            # 2. Load criteria rows for this smart playlist
            criteria_rows = self.controller.get.get_all_entities(
                "SmartPlaylistCriteria", smart_playlist_id=smart_playlist.playlist_id
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

        except SQLAlchemyError as e:
            logger.error(f"Error refreshing smart playlist {playlist_id}: {e}")
            return False

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _row_to_condition(self, row) -> dict[str, Any]:
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

    def _get_matching_track_ids(self, conditions: list[dict], logic: str) -> list[int]:
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
                if kwargs is None:
                    # Condition couldn't be validated (bad/malformed value).
                    # It must not silently drop out of an AND — an empty
                    # kwargs dict would query with no filter at all and
                    # match every track. Treat it as "matches nothing".
                    logger.warning(
                        f"Invalid condition excluded all tracks from AND match: {condition}"
                    )
                    return []
                combined_kwargs.update(kwargs)

            tracks = self.controller.get.get_all_entities("Track", **combined_kwargs)
            return [t.track_id for t in tracks]

        # OR
        seen: set[int] = set()
        for condition in conditions:
            kwargs = self._condition_to_kwargs(condition)
            if kwargs is None:
                # Invalid condition contributes no matches — must not
                # be queried with empty kwargs, which would match
                # every track.
                continue
            tracks = self.controller.get.get_all_entities("Track", **kwargs)
            seen.update(t.track_id for t in tracks)
        return list(seen)

    def _condition_to_kwargs(self, condition: dict[str, Any]) -> dict[str, Any] | None:
        """
        Turn one condition dict into a **kwargs dict for get_all_entities.

        Returns None if the condition is invalid/unusable — callers must
        NOT treat that the same as an empty-but-valid kwargs dict, since
        an empty dict passed to get_all_entities means "no filter" and
        would match every track.

        Example:
            {"field": "user_rating", "comparison": "gt", "value": "5.5"}
            → {"user_rating__gt": 5.5}
        """
        field = condition.get("field", "")
        comparison = condition.get("comparison", "eq")
        value = condition.get("value")
        data_type = condition.get("type", "String")

        if not field or not comparison:
            return None

        # Operators that use a boolean flag instead of a real value
        if comparison == "isnull":
            return {f"{field}__isnull": True}
        if comparison == "notnull":
            return {f"{field}__isnull": False}

        # Datetime comparisons ("on this day", "between", "last N days") don't
        # map to a single cast value — they expand into one or more filters
        # computed relative to the stored string(s) or the current time.
        if data_type == "Datetime":
            return self._datetime_condition_to_kwargs(field, comparison, value)

        # Cast the stored string value to the correct Python type
        cast_value = self._cast_value(value, data_type, comparison)

        if cast_value is None:
            # Don't add a condition with a None value (would match everything)
            logger.warning(f"Skipping condition with None value: {condition}")
            return None

        return {f"{field}__{comparison}": cast_value}

    # Matches the space-separated format SQLAlchemy/SQLite store DATETIME
    # columns in, and the format CriteriaWidget now writes (see
    # playlist_smart_criteria_widget.DATETIME_DISPLAY_FORMAT).
    _DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"

    def _datetime_condition_to_kwargs(
        self, field: str, comparison: str, value: Any
    ) -> dict[str, Any] | None:
        """
        Translate a Datetime condition into query kwargs.

        - "range": value is "start|end" (already whole-day-widened by the
          widget) → a single between() filter.
        - "last_n_days": value is an integer day count → a rolling "on or
          after (now - N days)" filter, recomputed each refresh.
        - "eq" ("on this day"): value is a "yyyy-MM-dd" date → widened to a
          [start of day, start of next day) range, since comparing against
          an exact stored timestamp would almost never match.
        - everything else (gt/lt/gte/lte): plain string comparison against
          the stored value.

        Returns None (not {}) when the value fails validation — an empty
        dict would be interpreted by the caller as "no filter", which
        matches every track instead of rejecting the bad criterion.
        """
        if value is None or value == "":
            logger.warning(f"Skipping datetime condition with no value: {field}")
            return None

        if comparison == "range":
            parts = str(value).split("|", 1)
            if len(parts) != 2:
                logger.warning(f"Malformed datetime range value for {field}: {value}")
                return None
            return {f"{field}__range": (parts[0], parts[1])}

        if comparison == "last_n_days":
            try:
                days = int(float(value))
            except (TypeError, ValueError):
                logger.warning(f"Invalid 'last N days' value for {field}: {value}")
                return None
            cutoff = datetime.datetime.now() - datetime.timedelta(days=days)
            return {f"{field}__gte": cutoff.strftime(self._DATETIME_FORMAT)}

        if comparison == "eq":
            day_text = str(value).strip().split(" ")[0].split("T")[0]
            try:
                day_start = datetime.datetime.strptime(day_text, "%Y-%m-%d")
            except ValueError:
                logger.warning(f"Invalid 'on this day' value for {field}: {value}")
                return None
            day_end = day_start + datetime.timedelta(days=1)
            return {
                f"{field}__gte": day_start.strftime(self._DATETIME_FORMAT),
                f"{field}__lt": day_end.strftime(self._DATETIME_FORMAT),
            }

        return {f"{field}__{comparison}": str(value)}

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
            if data_type == "Float":
                return float(value)
            if data_type == "List":
                # Could be a Python list already, or a comma-separated string
                if isinstance(value, list):
                    return value
                return [v.strip() for v in str(value).split(",") if v.strip()]
            # String, Text — keep as string (Datetime is handled separately
            # by _datetime_condition_to_kwargs before this is ever called)
            return str(value) if value != "" else None
        except (ValueError, TypeError) as e:
            logger.warning(f"Could not cast value '{value}' as {data_type}: {e}")
            return None

    def _update_playlist_tracks(self, playlist_id: int, track_ids: list[int]) -> bool:
        """
        Bulk-diff the playlist's tracks against `track_ids` via the shared
        sync_playlist_tracks() helper (src/playlist/playlist_track_sync.py),
        which also underlies ChartPlaylistBuilder.

        Returns:
            True if successful, False if an error occurred
        """
        try:
            result = sync_playlist_tracks(self.controller, playlist_id, track_ids)
        except (ImportError, TypeError) as e:
            logger.error(f"Error updating playlist tracks: {e}")
            return False

        if result is None:
            return False

        logger.info(
            f"Playlist {playlist_id}: {result.added} to add, "
            f"{result.removed} to remove, {result.kept} to keep"
        )
        return True

    def _touch_last_refreshed(self, playlist_id: int):
        """Update the last_refreshed timestamp on the SmartPlaylist record."""
        try:
            self.controller.update.update_entity(
                "SmartPlaylist", entity_id=playlist_id, last_refreshed=datetime.datetime.now()
            )
        except SQLAlchemyError as e:
            logger.warning(f"Could not update last_refreshed for playlist {playlist_id}: {e}")
