import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


_exit_status = 0


def pytest_sessionfinish(session, exitstatus):
    global _exit_status
    _exit_status = int(exitstatus)


@pytest.hookimpl(trylast=True)
def pytest_unconfigure(config):
    # Some tests leave widgets whose C++ side is already gone; PySide's atexit
    # cleanup then double-deletes them and segfaults. Skip interpreter teardown.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(_exit_status)
