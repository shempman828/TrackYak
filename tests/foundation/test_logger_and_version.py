"""Tests for src/foundation/logger_config.py and src/foundation/version.py."""

import logging

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


def test_version_with_tag_and_git(monkeypatch):
    version.get_version.cache_clear()
    monkeypatch.setattr(version, "_git", lambda *a: "7" if a[1].startswith("v") else "300")
    assert version.get_version() == f"{version.BASE_VERSION}.7 (build 300)"
    version.get_version.cache_clear()


def test_version_without_tag_keeps_build_count(monkeypatch):
    version.get_version.cache_clear()
    monkeypatch.setattr(version, "_git", lambda *a: None if a[1].startswith("v") else "300")
    assert version.get_version() == f"{version.BASE_VERSION} (build 300)"
    version.get_version.cache_clear()


def test_version_without_git_is_dev(monkeypatch):
    version.get_version.cache_clear()
    monkeypatch.setattr(version, "_git", lambda *a: None)
    assert version.get_version() == f"{version.BASE_VERSION} (dev)"
    version.get_version.cache_clear()
