"""Tests for src/foundation/logger_config.py and src/foundation/version.py."""

import logging

import pytest

from src.foundation import logger_config, version


def test_reconfigure_closes_old_handlers_and_disables_propagation(monkeypatch, tmp_path):
    monkeypatch.setattr(logger_config, "LOGS_DIR", tmp_path)
    log = logger_config.setup_logging()
    old_handlers = list(log.handlers)
    file_handlers = [h for h in old_handlers if isinstance(h, logging.FileHandler)]
    assert file_handlers

    logger_config.setup_logging()

    assert log.propagate is False
    for handler in file_handlers:
        assert handler.stream is None  # closed
    assert all(h not in log.handlers for h in old_handlers)


@pytest.mark.parametrize(
    ("tag_count", "build_count", "suffix"),
    [("7", "300", ".7 (build 300)"), (None, "300", " (build 300)"), (None, None, " (dev)")],
    ids=["tag-and-git", "no-tag", "no-git"],
)
def test_version_string(monkeypatch, tag_count, build_count, suffix):
    version.get_version.cache_clear()
    monkeypatch.setattr(version, "_git", lambda *a: tag_count if a[1].startswith("v") else build_count)
    assert version.get_version() == f"{version.BASE_VERSION}{suffix}"
    version.get_version.cache_clear()
