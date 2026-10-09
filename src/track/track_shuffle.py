# track_shuffle.py

"""Play or shuffle a track list into the queue and start playback.

Shared by BaseTrackView's "Shuffle All" action and by the playlist/mood tree
context menus, which shuffle a playlist or mood without opening its tracks
dialog first.
"""

from pathlib import Path
import random

from src.foundation.logger_config import logger
from src.foundation.status_utility import show_status_message

# Above this many tracks the queue is filled on a background thread (the
# same threshold TrackView's "Add/Shuffle to Queue" actions use), so
# playing a whole library doesn't freeze the UI.
_ASYNC_QUEUE_THRESHOLD = 500


def shuffle_and_play(parent, controller, tracks):
    """Shuffle `tracks`, queue them, and start playback of the first one."""
    play_tracks(parent, controller, tracks, shuffle=True)


def play_tracks(parent, controller, tracks, shuffle: bool = False):
    """Replace the queue with `tracks` (in order, or shuffled) and start
    playback of the first one."""
    if not tracks:
        show_status_message(parent, f"No tracks available to {'shuffle' if shuffle else 'play'}.")
        return

    ordered = list(tracks)
    if shuffle:
        random.shuffle(ordered)

    queue_manager = getattr(controller, "queue_manager", None)
    if not queue_manager and hasattr(controller, "mediaplayer"):
        queue_manager = getattr(controller.mediaplayer, "queue_manager", None)
    if not queue_manager:
        show_status_message(parent, "Could not access the playback queue.")
        return

    if hasattr(queue_manager, "clear_queue"):
        queue_manager.clear_queue()
    if len(ordered) > _ASYNC_QUEUE_THRESHOLD and hasattr(queue_manager, "add_tracks_async"):
        queue_manager.add_tracks_async(ordered, shuffle=False)
    else:
        queue_manager.add_tracks_to_queue(ordered)

    if hasattr(controller, "mediaplayer"):
        try:
            track_path = Path(ordered[0].track_file_path)
            if controller.mediaplayer.load_track(track_path):
                controller.mediaplayer.play()
                logger.info(f"Started {'shuffled ' if shuffle else ''}playback: {len(ordered)} tracks")
        except (OSError, RuntimeError, TypeError) as e:
            logger.error(f"Error starting playback: {e}")
