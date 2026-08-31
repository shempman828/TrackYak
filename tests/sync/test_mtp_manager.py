"""
Regression tests for MtpManager._run_bounded.

Motivating bug: opening SyncView could hang the GUI thread. list_devices()
shells out to `gio`, and subprocess.run(timeout=...) — after a timeout —
kills the child and then does an UNBOUNDED wait() (Popen.__exit__ waits
again). A `gio` call stuck on a wedged MTP backend never dies, so that wait
never returns. _run_bounded kills + waits with a hard cap and abandons the
child rather than blocking forever.
"""

import time

from src.sync.mtp_manager import _run_bounded


def test_run_bounded_returns_completed_process_for_quick_command():
    result = _run_bounded(["printf", "hello"], timeout=5)
    assert result is not None
    assert result.returncode == 0
    assert result.stdout == "hello"


def test_run_bounded_returns_none_on_timeout_without_blocking():
    start = time.monotonic()
    result = _run_bounded(["sleep", "30"], timeout=1)
    elapsed = time.monotonic() - start

    assert result is None
    # timeout (1s) + kill grace (2s) + slack — nowhere near `sleep`'s 30s.
    assert elapsed < 10, f"_run_bounded blocked for {elapsed:.1f}s"


def test_run_bounded_returns_none_when_binary_missing():
    assert _run_bounded(["this-binary-does-not-exist-xyz"], timeout=5) is None
