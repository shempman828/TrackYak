"""Regression test: ImportWorker must reuse one psutil.Process() instance
across resource checks. psutil's cpu_percent() reports usage since its
*previous* call on the same instance -- a fresh Process() per check (the
old behavior) always reported 0.0%, making CPU monitoring silently inert.
"""

from types import SimpleNamespace

import pytest

from src.importing import import_worker
from src.importing.import_worker import ImportWorker


class _FakeController:
    pass


def test_check_resources_reuses_one_process_instance(monkeypatch):
    created = []

    class _FakeProcess:
        def __init__(self):
            created.append(self)

        def memory_info(self):
            return SimpleNamespace(rss=0)

        def cpu_percent(self):
            return 0.0

    monkeypatch.setattr(import_worker.psutil, "Process", _FakeProcess)

    worker = ImportWorker(_FakeController(), [])
    assert len(created) == 1

    worker._check_resources()
    worker._check_resources()

    assert len(created) == 1
    assert worker._process is created[0]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
