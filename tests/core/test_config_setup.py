"""Unit tests for src.core.config_setup.Config accessors.

Covers the CSV-list config fields (excluded_genres / excluded_roles) that
back the "Skipped …" parse-ignore lists — see
docs/specs/role_parse_ignore_list.md.
"""

import configparser

import pytest

from src.core import config_setup


@pytest.fixture
def fresh_config(tmp_path, monkeypatch):
    """A Config bound to a scratch config.ini, with the singleton reset so
    each test gets its own instance and file."""
    scratch_ini = tmp_path / "config.ini"
    monkeypatch.setattr(config_setup, "config", lambda name: str(scratch_ini))
    config_setup.Config._instance = None
    config_setup.Config._initialized = False
    cfg = config_setup.Config()
    yield cfg, scratch_ini
    config_setup.Config._instance = None
    config_setup.Config._initialized = False


def _reload(scratch_ini):
    config_setup.Config._instance = None
    config_setup.Config._initialized = False
    return config_setup.Config()


def test_excluded_roles_round_trips_through_config_file(fresh_config):
    cfg, scratch_ini = fresh_config

    cfg.set_excluded_roles(["Composer", "Remixer"])
    cfg.save()

    reloaded = _reload(scratch_ini)
    assert reloaded.get_excluded_roles() == ["Composer", "Remixer"]

    raw = configparser.ConfigParser()
    raw.read(scratch_ini)
    assert raw["library"]["excluded_roles"] == "Composer,Remixer"


def test_excluded_roles_defaults_to_empty_list_when_unset(fresh_config):
    cfg, _ = fresh_config
    assert cfg.get_excluded_roles() == []


def test_excluded_roles_and_genres_are_independent(fresh_config):
    cfg, scratch_ini = fresh_config

    cfg.set_excluded_genres(["Noise"])
    cfg.set_excluded_roles(["Engineer"])
    cfg.save()

    reloaded = _reload(scratch_ini)
    assert reloaded.get_excluded_genres() == ["Noise"]
    assert reloaded.get_excluded_roles() == ["Engineer"]
