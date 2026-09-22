# track_shuffle.py

"""Shuffle a track list into the queue and start playback.

Shared by BaseTrackView's "Shuffle All" action and by the playlist/mood tree
context menus, which shuffle a playlist or mood without opening its tracks
dialog first.
"""

from pathlib import Path
import random

from src.foundation.logger_config import logger
from src.foundation.status_utility import show_status_message


def shuffle_and_play(parent, controller, tracks):
    """Shuffle `tracks`, queue them, and start playback of the first one."""
    if not tracks:
        show_status_message(parent, "No tracks available to shuffle.")
        return

    shuffled = list(tracks)
    random.shuffle(shuffled)

    queue_manager = getattr(controller, "queue_manager", None)
    if not queue_manager and hasattr(controller, "mediaplayer"):
        queue_manager = getattr(controller.mediaplayer, "queue_manager", None)
    if not queue_manager:
        show_status_message(parent, "Could not access the playback queue.")
        return

    if hasattr(queue_manager, "clear_queue"):
        queue_manager.clear_queue()
    queue_manager.add_tracks_to_queue(shuffled)

    if hasattr(controller, "mediaplayer"):
        try:
            track_path = Path(shuffled[0].track_file_path)
            if controller.mediaplayer.load_track(track_path):
                controller.mediaplayer.play()
                logger.info(f"Started shuffled playback: {len(shuffled)} tracks")
        except (OSError, RuntimeError, TypeError) as e:
            logger.error(f"Error starting playback: {e}")
