"""
queue_utility.py — QueueManager
"""

import json
import random
from collections import deque
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import QObject, QThread, Signal, Slot
from sqlalchemy.exc import SQLAlchemyError

from src.db.db_tables import Track
from src.core.asset_paths import config as config_path
from src.core.logger_config import logger
from src.metadata.metadata_writer_backup import atomic_write

# ── Persistence limits ────────────────────────────────────────────────────────
SAVE_HISTORY_LIMIT = 500  # most-recent N played tracks kept (recency buffer, not the full queue)

# Weight given to tracks with no user_rating in weighted_shuffle_queue(), so they
# still turn up at a reasonable rate instead of always sinking to the end.
# Rating scale is 0.5–10 (see statistics_utility.RATING_MIN/RATING_MAX); this
# sits below the midpoint so rated-and-liked tracks still skew earlier on average.
WEIGHTED_SHUFFLE_UNRATED_WEIGHT = 3.0


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
            # Intentional broad boundary catch: this runs on a QThread and must
            # not let an exception kill the thread silently — surface it to the UI.
            logger.exception("Bulk queue add failed")
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
        # maxlen keeps memory bounded; oldest entry is auto-dropped when full.
        self.history: deque = deque(
            maxlen=SAVE_HISTORY_LIMIT
        )  # history[-1] == most recently played
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
        self._bulk_thread.finished.connect(self._on_bulk_thread_finished)

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

    @Slot()
    def _on_bulk_thread_finished(self):
        """Null out the thread reference after Qt has deleted the C++ object."""
        self._bulk_thread = None

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

    def weighted_shuffle_queue(self):
        """
        Shuffle upcoming tracks (index 1 onwards) with a bias toward
        higher-rated tracks landing earlier. The currently playing track
        (index 0) is never moved.

        Uses the Efraimidis-Spirakis method: each track gets a random key
        of random()**(1/weight), and sorting by that key descending yields
        an unbiased weighted random permutation — higher-weight tracks tend
        to sort earlier, but nothing is guaranteed or deterministic.
        """
        if len(self.queue) < 2:
            return
        upcoming = self.queue[1:]

        def _key(track: Track) -> float:
            weight = getattr(track, "user_rating", None) or WEIGHTED_SHUFFLE_UNRATED_WEIGHT
            return random.random() ** (1.0 / weight)

        upcoming.sort(key=_key, reverse=True)
        self.queue[1:] = upcoming
        logger.info(f"weighted_shuffle_queue: {len(upcoming)} upcoming tracks weighted-shuffled")
        self.queue_changed.emit()

    def clear_queue(self):
        """Remove all tracks from the queue (history is preserved)."""
        self.queue.clear()
        logger.info("clear_queue: queue cleared")
        self.queue_changed.emit()

    # ── Persistence ───────────────────────────────────────────────────────────
    #
    # Queue/history track IDs are stored in queue_state.json (next to
    # config.ini) rather than in the config file itself. A shuffled
    # full-library queue can be tens of thousands of track IDs — fine as a
    # small JSON blob, but not something that belongs mixed into a
    # human-editable settings file that gets rewritten on every save.

    def _queue_state_path(self) -> str:
        return config_path("queue_state.json")

    def save_queue_to_config(self):
        """
        Persist queue state to queue_state.json.  Call once from closeEvent().

        Saves:
          • Up to SAVE_HISTORY_LIMIT most-recent history entries
          • The full upcoming queue (current track + everything after it),
            however many tracks that is — no cap.
        """
        if not self.config:
            return

        # Respect the user's "persist queue across sessions" preference.
        if not self.config.get_persist_queue():
            logger.debug("save_queue_to_config: persist_queue is off — skipping")
            return

        try:
            # History — deque.maxlen already caps at SAVE_HISTORY_LIMIT, so no trimming needed
            history_ids = [t.track_id for t in self.history]
            queue_ids = [t.track_id for t in self.queue]

            state = {"history": history_ids, "queue": queue_ids}
            data = json.dumps(state).encode("utf-8")
            atomic_write(self._queue_state_path(), data)

            logger.info(
                f"save_queue_to_config: {len(history_ids)} history + "
                f"{len(queue_ids)} queue saved"
            )
        except (OSError, TypeError, AttributeError) as exc:
            logger.error(f"save_queue_to_config failed: {exc}")

    def load_queue_from_config(self, db_session):
        """
        Restore queue state from queue_state.json at startup.
        Uses a single IN-clause query per batch instead of N individual queries.
        Returns True if anything was loaded.
        """
        if not self.config:
            return False

        # Respect the user's "persist queue across sessions" preference.
        if not self.config.get_persist_queue():
            logger.debug("load_queue_from_config: persist_queue is off — skipping")
            return False

        try:
            state_path = Path(self._queue_state_path())
            if not state_path.exists():
                return False

            with open(state_path, "r", encoding="utf-8") as f:
                state = json.load(f)

            history_ids = state.get("history", [])
            queue_ids = state.get("queue", [])

            if not queue_ids and not history_ids:
                return False

            def _fetch_tracks(ids: List[int]) -> List[Track]:
                if not ids:
                    return []
                # Single batch query — fast regardless of list length
                rows = db_session.query(Track).filter(Track.track_id.in_(ids)).all()
                # Preserve the original order
                id_to_track = {t.track_id: t for t in rows}
                return [id_to_track[i] for i in ids if i in id_to_track]

            loaded_history = _fetch_tracks(history_ids)
            loaded_queue = _fetch_tracks(queue_ids)

            # These reads leave db_session's transaction open (a read-only
            # query never calls commit/rollback on its own -- see
            # CancellableWorker's docstring for the same pattern elsewhere).
            # This runs once on the main thread at startup, so left
            # unclosed it pins a pooled connection for the rest of the
            # app's life instead of just for this one call.
            db_session.commit()

            self.history = deque(loaded_history, maxlen=SAVE_HISTORY_LIMIT)
            self.queue = loaded_queue

            logger.info(
                f"load_queue_from_config: {len(loaded_history)} history + "
                f"{len(loaded_queue)} queue restored"
            )
            self.queue_changed.emit()
            return bool(loaded_queue)

        except (OSError, json.JSONDecodeError, SQLAlchemyError, AttributeError, KeyError) as exc:
            logger.error(f"load_queue_from_config failed: {exc}")
            return False
