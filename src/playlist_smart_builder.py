"""
playlist_smart_builder.py

Builds smart playlists by evaluating criteria and adding matching tracks.
"""

import datetime
from typing import Any, Dict, List, Set

from sqlalchemy.exc import SQLAlchemyError

from src.logger_config import logger


class SmartPlaylistBuilder:
    """Builds and updates smart playlists based on criteria."""

    def __init__(self, controller):
        self.controller = controller

    def refresh_playlist(self, playlist_id: int) -> bool:
        """
        Refresh a smart playlist by evaluating criteria and updating tracks.

        Args:
            playlist_id: ID of smart playlist to refresh

        Returns:
            bool: True if successful, False otherwise
        """
        try:
            # Get the smart playlist criteria
            smart_playlist = self.controller.get.get_entity_object(
                "SmartPlaylist", playlist_id=playlist_id
            )

            if not smart_playlist:
                logger.error(f"Smart playlist {playlist_id} not found")
                return False

            # Convert criteria string to dictionary
            criteria_dict = self._string_to_dict(smart_playlist.criteria)
            if not criteria_dict:
                logger.warning(f"No valid criteria for playlist {playlist_id}")
                return False

            # Get matching track IDs based on criteria
            matching_track_ids = self._get_matching_track_ids(criteria_dict)

            # Update playlist with matching tracks
            success = self._update_playlist_tracks(playlist_id, matching_track_ids)

            if success:
                # Update last_refreshed timestamp
                self.controller.update.update_entity(
                    "SmartPlaylist", playlist_id, last_refreshed=datetime.datetime.now()
                )
                logger.info(
                    f"Refreshed smart playlist {playlist_id} with {len(matching_track_ids)} tracks"
                )

            return success

        except Exception as e:
            logger.error(f"Error refreshing smart playlist {playlist_id}: {str(e)}")
            return False

    def _string_to_dict(self, criteria_string: str) -> Dict[str, Any]:
        """
        Convert criteria string back to dictionary.

        Args:
            criteria_string: String representation of criteria dictionary

        Returns:
            Criteria dictionary
        """
        try:
            if not criteria_string or criteria_string.strip() == "":
                return {}

            # Use eval to convert string back to dict (from repr())
            # Note: In production, consider safer alternatives for untrusted input
            criteria_dict = eval(criteria_string)

            if isinstance(criteria_dict, dict):
                return criteria_dict
            else:
                logger.warning(f"Criteria is not a dictionary: {type(criteria_dict)}")
                return {}

        except Exception as e:
            logger.error(f"Error parsing criteria string: {str(e)}")
            return {}

    def _get_matching_track_ids(self, criteria_dict: Dict[str, Any]) -> List[int]:
        """
        Get track IDs matching the criteria.

        Args:
            criteria_dict: Criteria dictionary with logic and conditions

        Returns:
            List of matching track IDs
        """
        logic = criteria_dict.get("logic", "AND").upper()
        conditions = criteria_dict.get("conditions", [])

        if not conditions:
            # If no conditions, get all tracks
            all_tracks = self.controller.get.get_all_entities("Track")
            return [track.track_id for track in all_tracks]

        if logic == "AND":
            # Build combined kwargs for AND logic
            combined_kwargs = {}
            for condition in conditions:
                kwargs = self._condition_to_kwargs(condition)
                combined_kwargs.update(kwargs)

            # Query with all conditions combined
            matching_tracks = self.controller.get.get_all_entities(
                "Track", **combined_kwargs
            )
            return [track.track_id for track in matching_tracks]

        else:  # OR logic
            track_ids_set: Set[int] = set()

            # Query each condition separately and combine results
            for condition in conditions:
                kwargs = self._condition_to_kwargs(condition)
                matching_tracks = self.controller.get.get_all_entities(
                    "Track", **kwargs
                )
                track_ids_set.update(track.track_id for track in matching_tracks)

            return list(track_ids_set)

    def _condition_to_kwargs(self, condition: Dict[str, Any]) -> Dict[str, Any]:
        """
        Convert a single condition to controller kwargs format.

        Args:
            condition: Dictionary with field, comparison, value, type

        Returns:
            kwargs for controller.get_all_entities()
        """
        field = condition.get("field")
        comparison = condition.get("comparison")
        value = condition.get("value")

        if not field or comparison is None:
            return {}

        # Map comparison to controller operator
        # Note: Check if any mapping needed based on OPERATOR_MAPPINGS
        operator_map = {
            "eq": "eq",
            "not": "not",
            "in": "in",
            "not_in": "not_in",
            "contains": "contains",
            "startswith": "startswith",
            "endswith": "endswith",
            "gt": "gt",
            "lt": "lt",
            "gte": "gte",
            "lte": "lte",
            "range": "range",
            "isnull": "isnull",
            "notnull": "notnull",
        }

        controller_op = operator_map.get(comparison)
        if not controller_op:
            logger.warning(f"Unknown comparison operator: {comparison}")
            return {}

        # Build kwargs key in format: field__operator
        kwargs_key = f"{field}__{controller_op}"

        # Handle special cases for null operators
        if controller_op in ["isnull", "notnull"]:
            # These operators use boolean values in the controller
            value = True if controller_op == "isnull" else False

        return {kwargs_key: value}

    def _update_playlist_tracks(self, playlist_id: int, track_ids: List[int]) -> bool:
        """
        Update playlist with new track list, preserving existing date_added when possible.

        Args:
            playlist_id: Playlist ID
            track_ids: List of track IDs to add

        Returns:
            bool: True if successful
        """
        try:
            # Get existing playlist tracks to preserve date_added
            existing_tracks = self.controller.get.get_all_entities(
                "PlaylistTracks", playlist_id__eq=playlist_id
            )

            # Create map of existing track_id -> PlaylistTracks object
            existing_map = {pt.track_id: pt for pt in existing_tracks}

            # Clear all existing tracks from playlist
            self.controller.delete.delete_entity_by_filter(
                "PlaylistTracks", playlist_id__eq=playlist_id
            )

            # Add tracks back with proper positions
            for position, track_id in enumerate(track_ids, start=1):
                # Check if this track was previously in playlist
                existing_pt = existing_map.get(track_id)

                if existing_pt:
                    # Preserve original date_added
                    date_added = existing_pt.date_added
                else:
                    # Use current datetime
                    date_added = datetime.datetime.now()

                # Add track to playlist
                self.controller.add.add_entity(
                    "PlaylistTracks",
                    playlist_id=playlist_id,
                    track_id=track_id,
                    position=position,
                    date_added=date_added,
                )

            # Update playlist's last_modified timestamp
            self.controller.update.update_entity(
                "Playlist", playlist_id, last_modified=datetime.datetime.now()
            )

            return True

        except SQLAlchemyError as e:
            logger.error(f"Database error updating playlist tracks: {e}")
            return False
        except Exception as e:
            logger.error(f"Error updating playlist tracks: {str(e)}")
            return False
