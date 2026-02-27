"""
queue_utility.py — QueueManager

Queue model
───────────
  self.queue    : List[Track]  — index 0 is always the currently playing track.
                                 index 1 is "up next", and so on.
  self.history  : deque[Track] — tracks that have already been played,
                                 most-recent last.  history[-1] is the
                                 track played just before the current one.

Advancing the queue
───────────────────
  advance_queue() pops queue[0] into history and plays the new queue[0].

Going back
──────────
  go_to_previous() pops history[-1] back onto the front of the queue so
  the caller can play queue[0] again.

Config persistence (save-on-close only)
────────────────────────────────────────
  save_queue_to_config()  — called once from main_window.closeEvent().
  load_queue_from_config() — called once at startup.
  Saves up to SAVE_HISTORY_LIMIT history + current + SAVE_UPCOMING_LIMIT
  upcoming track IDs.  That is ≤ 1 001 IDs, well under 200 KB.

Threading
─────────
  add_tracks_to_queue() and shuffle_queue() are fast pure-Python list ops
  and can run on the main thread without issue.

  For the "Shuffle Library" use-case (50 000+ tracks) the caller should use
  add_tracks_async() which offloads the list extension to a QThread and emits
  queue_changed on the main thread when done, keeping the UI responsive.
"""

import random
from collections import deque
from typing import List, Optional

from PySide6.QtCore import QObject, QThread, Signal, Slot

from src.db_tables import Track
from src.logger_config import logger

# ── Persistence limits ────────────────────────────────────────────────────────
SAVE_HISTORY_LIMIT = 500  # most-recent N played tracks saved to config
SAVE_UPCOMING_LIMIT = 500  # next N upcoming tracks saved to config


# ── Background worker for bulk queue additions ────────────────────────────────


class _BulkAddWorker(QObject):
    """Runs on a QThread.  Builds the extended list then signals back."""

    finished = Signal(list)  # emits the new list of tracks to append
    error = Signal(str)

    def __init__(self, tracks: List[Track], shuffle: bool):
        super().__init__()
        self._tracks = tracks
        self._shuffle = shuffle

    @Slot()
    def run(self):
        try:
            tracks = list(self._tracks)
            if self._shuffle:
                random.shuffle(tracks)
            self.finished.emit(tracks)
        except Exception as exc:
            self.error.emit(str(exc))


# ── QueueManager ──────────────────────────────────────────────────────────────


class QueueManager(QObject):
    """
    Manages the playback queue.

    Signals
    -------
    queue_changed   — emitted after any structural change to queue or history.
    bulk_add_started  — emitted when a large async add begins (for status bar).
    bulk_add_finished — emitted when it completes.
    """

    queue_changed = Signal()
    bulk_add_started = Signal(int)  # track count being added
    bulk_add_finished = Signal(int)  # track count that was added

    def __init__(self, config=None):
        super().__init__()
        self.queue: List[Track] = []
        self.history: deque = deque()  # history[-1] == most recently played
        self.config = config

        # Kept for any legacy callers that check .history_exists — always False now.
        self.history_exists: bool = False

        # Thread bookkeeping
        self._bulk_thread: Optional[QThread] = None
        self._bulk_worker: Optional[_BulkAddWorker] = None

    # ── Current / next / previous ─────────────────────────────────────────────

    def get_current_track(self) -> Optional[Track]:
        """Index 0 is always current.  Returns None if queue is empty."""
        return self.queue[0] if self.queue else None

    def get_next_track(self) -> Optional[Track]:
        """Peek at what will play after the current track."""
        return self.queue[1] if len(self.queue) > 1 else None

    def get_previous_track(self) -> Optional[Track]:
        """Peek at the most recently played track without changing state."""
        return self.history[-1] if self.history else None

    # ── Playback flow ─────────────────────────────────────────────────────────

    def advance_queue(self):
        """
        Move to the next track.
        Current track (queue[0]) is moved to history.
        New queue[0] becomes the current track.
        """
        if not self.queue:
            return

        finished = self.queue.pop(0)
        self.history.append(finished)
        logger.debug(
            f"advance_queue: '{getattr(finished, 'track_name', '?')}' → history "
            f"(history depth: {len(self.history)}, remaining: {len(self.queue)})"
        )
        self.queue_changed.emit()

    def go_to_previous(self) -> bool:
        """
        Move the most recently played track back to the front of the queue.
        Returns True if there was a previous track, False otherwise.
        The caller should then play queue[0].
        """
        if not self.history:
            return False

        prev = self.history.pop()
        self.queue.insert(0, prev)
        logger.debug(f"go_to_previous: '{getattr(prev, 'track_name', '?')}' ← history")
        self.queue_changed.emit()
        return True

    # ── Queue mutation ────────────────────────────────────────────────────────

    def add_track_to_queue(self, track: Track):
        """Append a single track to the end of the queue."""
        self.queue.append(track)
        self.queue_changed.emit()

    def add_tracks_to_queue(self, tracks: List[Track]):
        """
        Append multiple tracks synchronously.
        Fine for small-to-medium lists (< ~5 000 tracks).
        For library-scale additions use add_tracks_async().
        """
        self.queue.extend(tracks)
        logger.info(
            f"add_tracks_to_queue: +{len(tracks)} tracks (total: {len(self.queue)})"
        )
        self.queue_changed.emit()

    def add_tracks_async(self, tracks: List[Track], shuffle: bool = False):
        """
        Add (and optionally shuffle) a large batch of tracks on a background
        thread so the UI stays responsive.

        Emits bulk_add_started(count) immediately, then bulk_add_finished(count)
        and queue_changed once the work is done.
        """
        count = len(tracks)
        if count == 0:
            return

        # If a previous bulk add is still running, wait for it to finish first.
        if self._bulk_thread and self._bulk_thread.isRunning():
            logger.warning(
                "add_tracks_async: previous bulk add still running — queuing after"
            )
            # Simple approach: just do it synchronously to avoid complexity.
            lst = list(tracks)
            if shuffle:
                random.shuffle(lst)
            self.queue.extend(lst)
            self.queue_changed.emit()
            return

        self.bulk_add_started.emit(count)

        self._bulk_worker = _BulkAddWorker(tracks, shuffle)
        self._bulk_thread = QThread(self)
        self._bulk_worker.moveToThread(self._bulk_thread)

        self._bulk_thread.started.connect(self._bulk_worker.run)
        self._bulk_worker.finished.connect(self._on_bulk_add_finished)
        self._bulk_worker.error.connect(self._on_bulk_add_error)
        self._bulk_worker.finished.connect(self._bulk_thread.quit)
        self._bulk_worker.error.connect(self._bulk_thread.quit)
        self._bulk_thread.finished.connect(self._bulk_thread.deleteLater)

        self._bulk_thread.start()

    @Slot(list)
    def _on_bulk_add_finished(self, tracks: List[Track]):
        count = len(tracks)
        self.queue.extend(tracks)
        logger.info(
            f"bulk add complete: +{count} tracks (queue total: {len(self.queue)})"
        )
        self.bulk_add_finished.emit(count)
        self.queue_changed.emit()
        self._bulk_worker = None

    @Slot(str)
    def _on_bulk_add_error(self, msg: str):
        logger.error(f"bulk add error: {msg}")
        self._bulk_worker = None

    def insert_tracks_next(self, tracks: List[Track]):
        """
        Insert tracks immediately after the current track (index 1).
        If the queue is empty the tracks become the queue.
        """
        insert_at = 1 if self.queue else 0
        for i, track in enumerate(tracks):
            self.queue.insert(insert_at + i, track)
        logger.debug(f"insert_tracks_next: {len(tracks)} track(s) at index {insert_at}")
        self.queue_changed.emit()

    def remove_from_queue(self, index: int):
        """Remove the track at queue[index].  Index 0 (current) can be removed."""
        if 0 <= index < len(self.queue):
            removed = self.queue.pop(index)
            logger.debug(
                f"remove_from_queue[{index}]: '{getattr(removed, 'track_name', '?')}'"
            )
            self.queue_changed.emit()

    def move_track(self, from_index: int, to_index: int):
        """
        Reorder the queue by moving a track from one position to another.
        Both indices are into self.queue.
        """
        n = len(self.queue)
        if not (0 <= from_index < n and 0 <= to_index < n):
            return
        track = self.queue.pop(from_index)
        self.queue.insert(to_index, track)
        self.queue_changed.emit()

    def shuffle_queue(self):
        """
        Shuffle all upcoming tracks (index 1 onwards).
        The currently playing track (index 0) is never moved.
        """
        if len(self.queue) < 2:
            return
        upcoming = self.queue[1:]
        random.shuffle(upcoming)
        self.queue[1:] = upcoming
        logger.info(f"shuffle_queue: {len(upcoming)} upcoming tracks shuffled")
        self.queue_changed.emit()

    def clear_queue(self):
        """Remove all tracks from the queue (history is preserved)."""
        self.queue.clear()
        logger.info("clear_queue: queue cleared")
        self.queue_changed.emit()

    def clear_history(self):
        """Wipe play history."""
        self.history.clear()
        self.queue_changed.emit()

    # ── Info ──────────────────────────────────────────────────────────────────

    def get_queue_length(self) -> int:
        return len(self.queue)

    def get_history_length(self) -> int:
        return len(self.history)

    # Kept for any legacy callers
    def previous_track_in_queue(self) -> Optional[Track]:
        """Legacy shim — returns the previous track without mutating state."""
        return self.get_previous_track()

    def get_queue_state_for_ui(self) -> dict:
        return {
            "tracks": self.queue,
            "current_track": self.get_current_track(),
            "history_length": len(self.history),
            "queue_length": len(self.queue),
        }

    # ── Persistence ───────────────────────────────────────────────────────────

    def save_queue_to_config(self):
        """
        Persist queue state to config.  Call once from closeEvent().

        Saves:
          • Up to SAVE_HISTORY_LIMIT most-recent history entries
          • The current track (queue[0]) + up to SAVE_UPCOMING_LIMIT upcoming

        Format in config:
          history_ids  = comma-separated track IDs, oldest-first
          queue_ids    = comma-separated track IDs, current-first
        """
        if not self.config:
            return

        try:
            # History — keep the most-recent N
            history_list = list(self.history)
            if len(history_list) > SAVE_HISTORY_LIMIT:
                history_list = history_list[-SAVE_HISTORY_LIMIT:]
            history_ids = ",".join(str(t.track_id) for t in history_list)

            # Queue — current + next N
            save_queue = self.queue[: SAVE_UPCOMING_LIMIT + 1]
            queue_ids = ",".join(str(t.track_id) for t in save_queue)

            self.config.set("queue", "history_ids", history_ids)
            self.config.set("queue", "queue_ids", queue_ids)
            # Legacy key — clear it so old load code doesn't confuse things
            self.config.set("queue", "track_ids", "")
            self.config.set("queue", "history_exists", "false")

            logger.info(
                f"save_queue_to_config: {len(history_list)} history + "
                f"{len(save_queue)} upcoming saved"
            )
        except Exception as exc:
            logger.error(f"save_queue_to_config failed: {exc}")

    def load_queue_from_config(self, db_session):
        """
        Restore queue state from config at startup.
        Uses a single IN-clause query per batch instead of N individual queries.
        Returns True if anything was loaded.
        """
        if not self.config:
            return False

        try:
            history_ids_str = self.config.get("queue", "history_ids", fallback="")
            queue_ids_str = self.config.get("queue", "queue_ids", fallback="")

            # Fall back to legacy key if new keys are empty
            if not queue_ids_str:
                queue_ids_str = self.config.get("queue", "track_ids", fallback="")

            if not queue_ids_str and not history_ids_str:
                return False

            def _parse_ids(s: str) -> List[int]:
                return [int(x.strip()) for x in s.split(",") if x.strip()]

            def _fetch_tracks(ids: List[int]) -> List[Track]:
                if not ids:
                    return []
                # Single batch query — fast regardless of list length
                rows = db_session.query(Track).filter(Track.track_id.in_(ids)).all()
                # Preserve the original order
                id_to_track = {t.track_id: t for t in rows}
                return [id_to_track[i] for i in ids if i in id_to_track]

            history_ids = _parse_ids(history_ids_str)
            queue_ids = _parse_ids(queue_ids_str)

            loaded_history = _fetch_tracks(history_ids)
            loaded_queue = _fetch_tracks(queue_ids)

            self.history = deque(loaded_history)
            self.queue = loaded_queue

            logger.info(
                f"load_queue_from_config: {len(loaded_history)} history + "
                f"{len(loaded_queue)} upcoming restored"
            )
            self.queue_changed.emit()
            return bool(loaded_queue)

        except Exception as exc:
            logger.error(f"load_queue_from_config failed: {exc}")
            return False
