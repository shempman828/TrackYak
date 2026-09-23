"""Developer-mode feature: write a track's audio file the moment a DB change
marks it dirty, instead of waiting for the "Update Audio File Metadata"
dialog's manual batch run.

``patch()`` wraps two things:

* ``track_dirty.mark_tracks_dirty`` -- records the track ids it's about to
  mark dirty into a thread-local pending set before calling the original.
* ``BaseDBHelper._commit`` -- after the original commit (+ ``expire_all()``)
  succeeds, if both the master developer-mode flag and this feature's own
  flag are on, drains the pending set and writes each track's file via the
  same ``MetadataWriter`` call the batch dialog uses.

Nothing in ``src/db`` or ``src/metadata`` imports this module.
"""

from __future__ import annotations

import functools
import threading
from types import SimpleNamespace

from src.db.db_helpers import merge as merge_module, track_dirty
from src.db.db_helpers.get import GetFromDB
from src.db.db_helpers.registry import BaseDBHelper
from src.db.db_helpers.update import UpdateDB
from src.dev import dev_mode
from src.foundation.config_setup import Config
from src.foundation.logger_config import logger
from src.metadata.metadata_writer import MetadataWriter, WriteMode

SECTION = "developer"
KEY = "immediate_tag_write"


def is_enabled(config: Config | None = None) -> bool:
    """True when the immediate-write feature is switched on. Only meaningful
    together with ``dev_mode.is_enabled()`` -- callers should check both."""
    cfg = config if config is not None else Config()
    return cfg.config.getboolean(SECTION, KEY, fallback=False)


def set_enabled(config: Config, value: bool) -> None:
    """Write the flag. The caller owns persistence (``config.save()``)."""
    if not config.config.has_section(SECTION):
        config.config.add_section(SECTION)
    config.config.set(SECTION, KEY, str(bool(value)).lower())


# Thread-local set of track ids dirtied by the mutation currently in flight
# on this thread, drained the moment the wrapped _commit() sees it.
_local = threading.local()


def _pending() -> set:
    pending = getattr(_local, "pending", None)
    if pending is None:
        pending = set()
        _local.pending = pending
    return pending


def _write_pending(session, track_ids) -> None:
    # Built from the *same* session the mutation that just committed used --
    # not the app-wide scoped-session singleton -- so this reads the change
    # that was just made (and, in tests, the isolated session a test builds
    # its own BaseDBHelper instances around, instead of reaching past it into
    # a real db_engine.Session backed by music_library.db).
    shim = SimpleNamespace(get=GetFromDB(session), update=UpdateDB(session))
    writer = MetadataWriter(shim)
    for track_id in track_ids:
        try:
            writer.write_metadata_to_track(track_id, WriteMode.UPDATE_EXISTING)
        except Exception:
            # Must never propagate into the caller's original DB operation --
            # that operation already committed successfully.
            logger.exception(f"Developer mode: immediate write failed for track {track_id}")


_orig_mark_tracks_dirty = None
_orig_commit = None


def patch() -> None:
    global _orig_mark_tracks_dirty, _orig_commit

    if _orig_mark_tracks_dirty is None:
        _orig_mark_tracks_dirty = track_dirty.mark_tracks_dirty
        original = _orig_mark_tracks_dirty

        @functools.wraps(original)
        def _mark_tracks_dirty(session, track_ids):
            ids = list(track_ids)
            newly = {tid for tid in ids if tid is not None}
            if newly:
                _pending().update(newly)
            return original(session, ids)

        track_dirty.mark_tracks_dirty = _mark_tracks_dirty
        # merge.py did `from ...track_dirty import mark_tracks_dirty`, so it
        # holds its own binding to the original function object -- patching
        # track_dirty's module attribute above never reaches that binding.
        # Every other db_helpers caller only calls the mark_dirty_for_*
        # wrapper functions defined *in* track_dirty.py, which resolve
        # `mark_tracks_dirty` through that module's own globals at call time
        # and so pick up the patch automatically; merge.py needs its own
        # rebind.
        merge_module.mark_tracks_dirty = _mark_tracks_dirty

    if _orig_commit is None:
        _orig_commit = BaseDBHelper._commit
        original_commit = _orig_commit

        @functools.wraps(original_commit)
        def _commit(self):
            original_commit(self)
            if dev_mode.is_enabled() and is_enabled():
                pending = _pending()
                if pending:
                    ids = set(pending)
                    pending.clear()
                    _write_pending(self.session, ids)

        BaseDBHelper._commit = _commit


def unpatch() -> None:
    """Restore the originals. Used by tests; harmless if never patched."""
    global _orig_mark_tracks_dirty, _orig_commit

    if _orig_mark_tracks_dirty is not None:
        track_dirty.mark_tracks_dirty = _orig_mark_tracks_dirty
        merge_module.mark_tracks_dirty = _orig_mark_tracks_dirty
        _orig_mark_tracks_dirty = None
    if _orig_commit is not None:
        BaseDBHelper._commit = _orig_commit
        _orig_commit = None
    _pending().clear()
