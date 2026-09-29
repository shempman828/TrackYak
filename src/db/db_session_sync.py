"""Keep the main thread's session in step with writes committed on worker threads.

The app's `Session` is a `scoped_session`, so every worker thread gets its own
Session. `BaseDBHelper._commit()` calls `expire_all()` after a write, but that
only reaches the writing thread's own session. The main thread's session keeps
every object it already loaded (views hold strong references to them) with the
pre-write attribute values, and re-querying doesn't help: the identity map
hands back the same stale instances. That is how artwork reconcile, run after
File Organization, tried to write tracks at their old, moved-away paths.

This module watches every commit on the engine. When a transaction committed
on a non-main thread actually changed rows, it marks the main session stale and
calls the registered notifier (still on the worker thread). The notifier's job
is to get `expire_main_session_if_stale()` run on the main thread -- see
src/common/main_session_sync.py, which queues it through a Qt signal so it runs
before the worker's own `finished` handlers.

"Actually changed rows" is judged by SQLite's per-connection `total_changes`
counter, not by the commit itself: get.py's read helpers commit after every
fetch, and expiring the main session on each of those would turn every
background read into a full reload of whatever the UI is showing. Writes made
outside `_commit()` (raw `session.commit()` in chart/import/playlist code) are
caught the same way, since the hook sits on the engine, not the helpers.
"""

from collections.abc import Callable
import threading

from sqlalchemy import event

_BASELINE_KEY = "_yaktrack_total_changes"

_lock = threading.Lock()
_stale = False
_notifier: Callable[[], None] | None = None
_session_registry = None
_thread_state = threading.local()


def install(engine, session_registry) -> None:
    """Start tracking worker-thread writes on `engine` for the main thread of `session_registry` (a scoped_session)."""
    global _session_registry
    _session_registry = session_registry
    event.listen(engine, "begin", _on_engine_begin)
    event.listen(engine, "commit", _on_engine_commit)
    event.listen(engine, "rollback", _on_engine_rollback)
    # after_commit fires once the DBAPI COMMIT has landed. The engine-level
    # "commit" event fires just *before* it, so flagging there would let the
    # main thread reload (and clear the flag) ahead of the new rows.
    event.listen(session_registry.session_factory, "after_commit", _on_session_after_commit)


def set_notifier(notifier: Callable[[], None] | None) -> None:
    """Register the callable to run (on the worker thread) when the main session goes stale."""
    global _notifier
    _notifier = notifier


def is_main_session_stale() -> bool:
    with _lock:
        return _stale


def expire_main_session_if_stale() -> bool:
    """Expire the main thread's session if a worker committed a write since the last call. Main thread only."""
    global _stale
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("expire_main_session_if_stale() must run on the main thread")
    with _lock:
        if not _stale:
            return False
        _stale = False

    session = _session_registry()
    pending = set(session.new) | set(session.dirty) | set(session.deleted)
    if not pending:
        session.expire_all()
        return True

    # expire_all() would silently throw away uncommitted edits the main
    # thread is still holding -- expire everything else instead.
    for obj in list(session.identity_map.values()):
        if obj not in pending:
            session.expire(obj)
    return True


def _total_changes(conn) -> int:
    return conn.connection.dbapi_connection.total_changes


def _on_engine_begin(conn) -> None:
    # Baseline per transaction, not per connection lifetime: a pooled
    # connection may carry changes made outside these events (e.g.
    # MusicDatabase's raw_connection() startup work), which must not be
    # blamed on the next worker that happens to check it out.
    conn.connection.info[_BASELINE_KEY] = _total_changes(conn)


def _on_engine_commit(conn) -> None:
    baseline = conn.connection.info.get(_BASELINE_KEY)
    wrote = baseline is None or _total_changes(conn) > baseline
    if wrote and threading.current_thread() is not threading.main_thread():
        _thread_state.pending_write = True


def _on_engine_rollback(_conn) -> None:
    _thread_state.pending_write = False


def _on_session_after_commit(_session) -> None:
    global _stale
    if not getattr(_thread_state, "pending_write", False):
        return
    _thread_state.pending_write = False

    with _lock:
        newly_stale = not _stale
        _stale = True
    # Only notify on the transition: a worker committing in batches would
    # otherwise queue one main-thread expire per batch.
    if newly_stale and _notifier is not None:
        _notifier()
