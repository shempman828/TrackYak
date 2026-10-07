"""Builds and refreshes smart playlists by evaluating stored criteria."""

import datetime
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from src.foundation.logger_config import logger
from src.playlist.playlist_smart_criteria_fields import is_queryable_track_field
from src.playlist.playlist_track_sync import sync_playlist_tracks

# Separator between the two bounds of a stored "between" value (matches the
# criteria widget's RANGE_SEPARATOR).
_RANGE_SEPARATOR = "|"


def row_to_condition(row) -> dict[str, Any]:
    """Convert a SmartPlaylistCriteria ORM row into a field/comparison/value/type dict."""
    return {"field": getattr(row, "field_name", ""), "comparison": getattr(row, "comparison", "eq"), "value": getattr(row, "value", None), "type": getattr(row, "type", "String")}


def condition_to_row_fields(condition: dict[str, Any]) -> dict[str, Any]:
    """Convert a criteria dict into SmartPlaylistCriteria column values."""
    value = condition.get("value", "")
    # The value column is text: a List value is stored comma-joined (the
    # builder splits it again), and SQLite can't bind a Python list at all.
    if isinstance(value, (list, tuple, set)):
        value = ", ".join(str(v) for v in value)
    return {"field_name": condition.get("field", ""), "comparison": condition.get("comparison", ""), "value": value, "type": condition.get("type", "String")}


class SmartPlaylistBuilder:
    """Builds and refreshes smart playlists based on stored criteria."""

    def __init__(self, controller):
        self.controller = controller

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def refresh_playlist(self, playlist_id: int) -> bool:
        """Re-evaluate a smart playlist's criteria and update its tracks; True on success."""
        try:
            # 1. Get the SmartPlaylist record (for logic = AND / OR)
            smart_playlist = self.controller.get.get_entity_object("SmartPlaylist", playlist_id=playlist_id)
            if not smart_playlist:
                logger.error(f"SmartPlaylist record not found for playlist_id={playlist_id}")
                return False

            # 2. Load criteria rows for this smart playlist
            criteria_rows = self.controller.get.get_all_entities("SmartPlaylistCriteria", smart_playlist_id=smart_playlist.playlist_id)

            if not criteria_rows:
                logger.warning(f"Smart playlist {playlist_id} has no criteria — no tracks will be added.")
                # Still update the playlist (clear it) and timestamp
                success = self._update_playlist_tracks(playlist_id, [])
                if success:
                    self._touch_last_refreshed(playlist_id)
                return success

            # 3. Convert ORM rows to plain dicts that _get_matching_track_ids understands
            conditions = [row_to_condition(row) for row in criteria_rows]

            # 4. Read AND/OR logic — defaults to AND if not stored
            logic = getattr(smart_playlist, "logic", "AND") or "AND"

            # 5. Find matching tracks
            matching_track_ids = self._get_matching_track_ids(conditions, logic.upper())

            # 6. Update the playlist
            success = self._update_playlist_tracks(playlist_id, matching_track_ids)

            if success:
                self._touch_last_refreshed(playlist_id)
                logger.info(f"Refreshed smart playlist {playlist_id} ({logic}) → {len(matching_track_ids)} tracks")

            return success

        except SQLAlchemyError as e:
            logger.error(f"Error refreshing smart playlist {playlist_id}: {e}")
            return False

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get_matching_track_ids(self, conditions: list[dict], logic: str) -> list[int]:
        """Query the Track table using the given conditions, combined by AND/OR logic."""
        if not conditions:
            return []

        if logic == "AND":
            # Merge conditions into as few queries as possible, but start a
            # new query whenever a key repeats -- dict.update() would let the
            # second "genre contains X" silently overwrite the first.
            query_groups: list[dict[str, Any]] = [{}]
            for condition in conditions:
                kwargs = self._condition_to_kwargs(condition)
                if kwargs is None:
                    # An invalid condition must not drop out of an AND (empty
                    # kwargs would match every track) -- it matches nothing.
                    logger.warning(f"Invalid condition excluded all tracks from AND match: {condition}")
                    return []
                group = next((g for g in query_groups if not g.keys() & kwargs.keys()), None)
                if group is None:
                    group = {}
                    query_groups.append(group)
                group.update(kwargs)

            matched: set[int] | None = None
            for group in query_groups:
                ids = {t.track_id for t in self.controller.get.get_all_entities("Track", **group)}
                matched = ids if matched is None else matched & ids
                if not matched:
                    return []
            return list(matched or [])

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
        """Turn one condition dict into kwargs for get_all_entities, or None if unusable."""
        field = condition.get("field", "")
        comparison = condition.get("comparison", "eq")
        value = condition.get("value")
        data_type = condition.get("type", "String")

        if not field or not comparison:
            return None
        if not is_queryable_track_field(field):
            # The query layer skips unknown fields, so this would match everything.
            logger.warning(f"Skipping condition on non-queryable field: {condition}")
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

        if comparison == "range":
            return self._range_condition_to_kwargs(field, value, data_type)

        # Cast the stored string value to the correct Python type
        cast_value = self._cast_value(value, data_type, comparison)

        if cast_value is None:
            # Don't add a condition with a None value (would match everything)
            logger.warning(f"Skipping condition with None value: {condition}")
            return None

        return {f"{field}__{comparison}": cast_value}

    def _range_condition_to_kwargs(self, field: str, value: Any, data_type: str) -> dict[str, Any] | None:
        """Translate a numeric 'low|high' between condition into query kwargs, or None if invalid."""
        parts = str(value).split(_RANGE_SEPARATOR, 1) if value is not None else []
        if len(parts) != 2:
            logger.warning(f"Malformed range value for {field}: {value!r}")
            return None
        low = self._cast_value(parts[0].strip(), data_type, "range")
        high = self._cast_value(parts[1].strip(), data_type, "range")
        if low is None or high is None:
            return None
        return {f"{field}__range": (min(low, high), max(low, high))}

    # Matches the space-separated format SQLAlchemy/SQLite store DATETIME
    # columns in, and the format CriteriaWidget now writes (see
    # playlist_smart_criteria_widget.DATETIME_DISPLAY_FORMAT).
    _DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"

    def _datetime_condition_to_kwargs(self, field: str, comparison: str, value: Any) -> dict[str, Any] | None:
        """Translate a Datetime condition into query kwargs, or None if invalid."""
        if value is None or value == "":
            logger.warning(f"Skipping datetime condition with no value: {field}")
            return None

        if comparison == "range":
            parts = str(value).split(_RANGE_SEPARATOR, 1)
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
            # "On this day" — widen to [start of day, start of next day),
            # since comparing against an exact stored timestamp would
            # almost never match.
            day_text = str(value).strip().split(" ")[0].split("T")[0]
            try:
                day_start = datetime.datetime.strptime(day_text, "%Y-%m-%d")
            except ValueError:
                logger.warning(f"Invalid 'on this day' value for {field}: {value}")
                return None
            day_end = day_start + datetime.timedelta(days=1)
            return {f"{field}__gte": day_start.strftime(self._DATETIME_FORMAT), f"{field}__lt": day_end.strftime(self._DATETIME_FORMAT)}

        return {f"{field}__{comparison}": str(value)}

    def _cast_value(self, value: Any, data_type: str, comparison: str) -> Any:
        """Cast the stored string value to the type its field needs for querying."""
        if value is None:
            return None

        try:
            if data_type == "Integer":
                return int(float(str(value)))  # handles "5.0" → 5
            if data_type == "Float":
                return float(value)
            if data_type == "Bool":
                # Stored as a Python bool, or as "true"/"1"/"false"/"0" text --
                # a bare str(value) cast here would compare text against the
                # track's actual boolean/int column and never match.
                if isinstance(value, bool):
                    return value
                text = str(value).strip().lower()
                if text in ("true", "1", "yes"):
                    return True
                if text in ("false", "0", "no"):
                    return False
                return None
            if data_type == "List":
                if comparison == "contains":
                    # A LIKE match needs one text value, not a list.
                    text = ", ".join(value) if isinstance(value, list) else str(value).strip()
                    return text or None
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
        """Bulk-diff the playlist's tracks against `track_ids` via sync_playlist_tracks()."""
        try:
            result = sync_playlist_tracks(self.controller, playlist_id, track_ids)
        except (ImportError, TypeError) as e:
            logger.error(f"Error updating playlist tracks: {e}")
            return False

        if result is None:
            return False

        logger.info(f"Playlist {playlist_id}: {result.added} to add, {result.removed} to remove, {result.kept} to keep")
        return True

    def _touch_last_refreshed(self, playlist_id: int):
        """Update the last_refreshed timestamp on the SmartPlaylist record."""
        try:
            self.controller.update.update_entity("SmartPlaylist", entity_id=playlist_id, last_refreshed=datetime.datetime.now())
        except SQLAlchemyError as e:
            logger.warning(f"Could not update last_refreshed for playlist {playlist_id}: {e}")
