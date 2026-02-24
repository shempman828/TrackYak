import random
from typing import List, Optional

from PySide6.QtCore import QObject, Signal  # Add these

from db_tables import Track
from logger_config import logger


class QueueManager(QObject):
    """Manages the playback queue with straightforward list manipulation."""

    queue_changed = Signal()  # Signal to notify UI when the queue changes

    def __init__(self, config=None):
        super().__init__()
        self.queue: List[Track] = []
        self.history_exists = False
        self._queue_track_ids: List[int] = []
        self.config = config
        self._track_ids: List[int] = []

    def get_current_track(self) -> Optional[Track]:
        if not self.queue:
            return None

        # If we have history (we've advanced at least once), Current is Index 1
        if self.history_exists and len(self.queue) >= 2:
            return self.queue[1]

        # If no history (fresh queue), or only 1 track left, Current is Index 0
        return self.queue[0]

    def add_track_to_queue(self, track: Track):
        self.queue.append(track)
        self._track_ids.append(track.track_id)
        self._save_queue_to_config()
        self.queue_changed.emit()

    def add_tracks_to_queue(self, tracks: List[Track]):
        self.queue.extend(tracks)
        self._track_ids.extend([track.track_id for track in tracks])
        self._save_queue_to_config()
        self.queue_changed.emit()

    def insert_tracks_next(self, tracks: List[Track]):
        """Insert tracks right after the current track (index 1)."""
        insert_index = 1 if len(self.queue) >= 2 else len(self.queue)
        for i, track in enumerate(tracks):
            self.queue.insert(insert_index + i, track)
            self._track_ids.insert(insert_index + i, track.track_id)
        self._save_queue_to_config()
        self.queue_changed.emit()

    def shuffle_queue(self):
        """One-time shuffle of upcoming tracks only (index 2 onwards)."""
        if len(self.queue) <= 3:
            return

        # Slice the list to protect the window's 'Past' and 'Now'
        protected = self.queue[:2]
        protected_ids = self._track_ids[:2]
        upcoming = self.queue[2:]
        upcoming_ids = self._track_ids[2:]

        # Shuffle both lists in the same order
        combined = list(zip(upcoming, upcoming_ids))
        random.shuffle(combined)
        upcoming, upcoming_ids = zip(*combined)

        self.queue = protected + list(upcoming)
        self._track_ids = protected_ids + list(upcoming_ids)
        self._save_queue_to_config()
        self.queue_changed.emit()

    def remove_from_queue(self, index: int):
        """Simply remove the item; the window shifts naturally."""
        if 0 <= index < len(self.queue):
            self.queue.pop(index)
            self._track_ids.pop(index)
            self._save_queue_to_config()
            self.queue_changed.emit()

    def clear_queue(self):
        self.queue.clear()
        self._track_ids.clear()
        self.history_exists = False  # Reset flag on clear
        self._save_queue_to_config()
        self.queue_changed.emit()

    def get_queue_length(self) -> int:
        """Returns the number of tracks in the queue."""
        return len(self.queue)

    def advance_queue(self):
        """
        Called when a track finishes.
        We don't necessarily pop the item; we just shift our 'History' state.
        """
        if not self.queue:
            return

        if not self.history_exists:
            # First advance: We don't pop anything.
            # We just mark that Index 0 is now 'History', so get_current_track() will look at Index 1.
            if len(self.queue) > 1:
                self.history_exists = True
                self._save_queue_to_config()
                self.queue_changed.emit()
            else:
                # If only 1 track, we pop it because it's done
                self.queue.pop(0)
                self._track_ids.pop(0)
                self._save_queue_to_config()
                self.queue_changed.emit()
        else:
            # Subsequent advances: Pop the OLD history (Index 0).
            # The Old Current (Index 1) becomes New History (Index 0).
            self.queue.pop(0)
            self._track_ids.pop(0)
            self._save_queue_to_config()
            self.queue_changed.emit()

    def previous_track_in_queue(self) -> Optional[Track]:
        """
        Returns the track at the 'Previous' position.
        Note: In a sliding window, we don't 'move an index',
        we just return the item at index 0.
        """
        if not self.queue:
            return None

        # If length is 1 or 2, there is no 'History' yet,
        # so index 0 is actually the current/only track.
        if len(self.queue) < 3:
            return self.queue[0]

        # In a full buffer [Prev, Current, Next], index 0 is the previous track.
        return self.queue[0]

    def _save_queue_to_config(self):
        """Save queue state to config."""
        if not self.config:
            return

        # Save track IDs as comma-separated string
        track_ids_str = ",".join(str(track_id) for track_id in self._track_ids)
        self.config.set("queue", "track_ids", track_ids_str)

        # Save history state
        self.config.set("queue", "history_exists", str(self.history_exists).lower())

        # Save immediately if config supports it
        if hasattr(self.config, "save"):
            try:
                self.config.save()
            except Exception as e:
                logger.error(f"Error saving queue to config: {e}")

    def load_queue_from_config(self, db_session):
        """Load queue state from config."""
        if not self.config:
            return False

        try:
            # Get track IDs from config
            track_ids_str = self.config.get("queue", "track_ids", fallback="")
            if not track_ids_str:
                return False

            # Convert string to list of integers
            track_ids = [
                int(tid.strip()) for tid in track_ids_str.split(",") if tid.strip()
            ]
            if not track_ids:
                return False

            # Clear current queue
            self.queue.clear()
            self._track_ids.clear()

            # Fetch tracks from database
            for track_id in track_ids:
                track = (
                    db_session.query(Track).filter(track.track_id == track_id).first()
                )
                if track:
                    self.queue.append(track)
                    self._track_ids.append(track_id)
                else:
                    logger.warning(f"Track with ID {track_id} not found in database")

            # Restore history state
            self.history_exists = self.config.getboolean(
                "queue", "history_exists", fallback=False
            )

            logger.info(f"Loaded {len(self.queue)} tracks from saved queue")
            self.queue_changed.emit()
            return True

        except Exception as e:
            logger.error(f"Error loading queue from config: {e}")
            return False

    def get_queue_state_for_ui(self):
        """Get queue state for UI display."""
        return {
            "tracks": self.queue,
            "current_track": self.get_current_track(),
            "has_history": self.history_exists,
            "queue_length": len(self.queue),
        }
