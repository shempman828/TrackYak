"""Regression test for the "opening Places/Influences minimizes and resizes
the main window" bug (first-open-only).

Root cause: the first time a QWebEngineView is added anywhere inside the
main window's widget hierarchy while that window is already visible, Qt
has to recreate the window's native (X11) handle to back the new
GPU-compositing child -- visible as a brief hide/resize. Confirmed
empirically under Xvfb: a standalone prewarmed QWebEngineView (not parented
into the window) or a bare winId() call does NOT prevent it -- only a
QWebEngineView parented into the window's own hierarchy, realized before
the window is shown, does.

run._prewarm_webengine() pays that cost once, before window.show(), with a
throwaway probe. This test verifies the one property the fix actually
depends on (the probe is parented into the target window) and that
initialize_application() invokes it before returning the window to be
shown.
"""

import inspect

import run
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QMainWindow


def test_prewarm_webengine_parents_probe_into_window(qapp):
    window = QMainWindow()

    run._prewarm_webengine(window)

    probes = window.findChildren(QWebEngineView)
    assert len(probes) == 1
    assert probes[0].parent() is window

    window.close()


def test_initialize_application_prewarms_before_returning():
    source = inspect.getsource(run.initialize_application)

    prewarm_pos = source.index("_prewarm_webengine(")
    return_pos = source.rindex("return window")

    assert prewarm_pos < return_pos, (
        "_prewarm_webengine(window) must run before initialize_application "
        "returns, so the native-window-recreation glitch is absorbed while "
        "the window is still hidden (window.show() happens later, in main())"
    )
