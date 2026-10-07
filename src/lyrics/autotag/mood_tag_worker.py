"""MoodAutoTagWorker: library-wide backfill for lyrics-based mood/place tagging."""

# Scans every track with non-empty lyrics and writes any newly-matching
# Mood/Place associations via mood_autotag.auto_tag_track(), the same write
# path the per-track auto-fill in track_edit_lyrics.py uses. Additive only
# -- never removes or overwrites an existing association. Remembers what it
# already scanned (mood_autotag_state), so a re-run only scans tracks whose
# lyrics changed, and tests other tracks only against newly-added keywords
# and place names.

from PySide6.QtCore import Signal

from src.common.cancellable_worker import CancellableWorker
from src.foundation.logger_config import logger
from src.lyrics.autotag import mood_autotag_state
from src.lyrics.autotag.mood_autotag import auto_tag_track, build_autotag_context, flush_pending_queue
from src.lyrics.autotag.mood_scoring import mood_keyword_fingerprints, opposite_pairs

# How often to emit progress while scanning.
PROGRESS_INTERVAL = 25


class MoodAutoTagWorker(CancellableWorker):
    """
    Signals:
        progress(done, total, mood_tags_added, place_tags_added, place_tags_queued)
        finished(scanned, mood_tags_added, place_tags_added, place_tags_queued)
        error(message)
    """

    progress = Signal(int, int, int, int, int)
    finished = Signal(int, int, int, int)
    error = Signal(str)

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller

    def run(self):
        try:
            context = build_autotag_context(self.controller)

            tracks = self.controller.get.query_entities("Track", lyrics__notnull=True)
            candidates = [t for t in tracks if t.lyrics and t.lyrics.strip()]

            state = mood_autotag_state.load_state()
            # Only moods with a DB row can be written, so only those count
            # as covered by this scan.
            mood_fingerprints = {name: fp for name, fp in mood_keyword_fingerprints().items() if name in context.mood_id_by_name}
            pairs = opposite_pairs()
            changed_moods = mood_autotag_state.changed_moods(state, mood_fingerprints, pairs)
            # Place matches are only written with a "Song About" type, so
            # without one no place name counts as covered either.
            places_covered = set(context.place_id_by_name) if context.song_about_type_id is not None else set(state["places"])
            new_places = places_covered - set(state["places"])

            scanned_lyrics = state["tracks"]
            lyrics_fps = {}
            work = []
            for track in candidates:
                fp = mood_autotag_state.lyrics_fingerprint(track.lyrics)
                lyrics_fps[str(track.track_id)] = fp
                if scanned_lyrics.get(str(track.track_id)) != fp:
                    work.append((track, None, None))
                elif changed_moods or new_places:
                    work.append((track, changed_moods, new_places))

            total = len(work)
            scanned = 0
            mood_tags_added = 0
            place_tags_added = 0
            place_tags_queued = 0

            for track, moods, place_names in work:
                if self.is_cancelled:
                    break

                moods_added, places_added, places_queued = auto_tag_track(self.controller, track.track_id, track.lyrics, context, moods=moods, place_names=place_names)
                key = str(track.track_id)
                if track.track_id in context.failed_track_ids:
                    # Forget it, so the next run does a full scan of it.
                    scanned_lyrics.pop(key, None)
                else:
                    scanned_lyrics[key] = lyrics_fps[key]
                mood_tags_added += len(moods_added)
                place_tags_added += len(places_added)
                place_tags_queued += len(places_queued)

                scanned += 1
                if scanned % PROGRESS_INTERVAL == 0:
                    self.progress.emit(scanned, total, mood_tags_added, place_tags_added, place_tags_queued)

            flush_pending_queue(context)
            if not self.is_cancelled:
                # The vocabulary only counts as covered once every track
                # was scanned against it; a cancelled run keeps the old
                # vocabulary, so the rest of the tracks still get the delta.
                state["moods"] = mood_fingerprints
                state["opposites"] = [list(p) for p in pairs]
                state["places"] = sorted(places_covered)
                state["tracks"] = {k: v for k, v in scanned_lyrics.items() if k in lyrics_fps}
            mood_autotag_state.save_state(state)
            self.progress.emit(scanned, total, mood_tags_added, place_tags_added, place_tags_queued)
            self.finished.emit(scanned, mood_tags_added, place_tags_added, place_tags_queued)
        except Exception as e:
            logger.error(f"MoodAutoTagWorker failed: {e}", exc_info=True)
            self.error.emit(str(e))
        finally:
            self._release_db_session()
