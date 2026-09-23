"""Fixtures for the self-contained developer-mode package.

Both fixtures fully reverse their side effects so dev tests never leak the
monkey-patches or a flipped config flag into the rest of the suite.
"""

import pytest

import src.dev as dev_pkg
from src.dev import dev_album_sort, dev_immediate_write, dev_mode, dev_settings_tab
from src.foundation.config_setup import Config


@pytest.fixture
def dev_config():
    """The process-wide ``Config`` with its ``[developer]`` section (both the
    master flag and the immediate-write flag, which share it) restored after
    the test. Mutations stay in memory — this fixture never calls
    ``config.save()``, so ``config.ini`` on disk is untouched."""
    cfg = Config()
    had_section = cfg.config.has_section(dev_mode.SECTION)
    prev_keys = {
        dev_mode.KEY: cfg.config.get(dev_mode.SECTION, dev_mode.KEY, fallback=None) if had_section else None,
        dev_immediate_write.KEY: cfg.config.get(dev_immediate_write.SECTION, dev_immediate_write.KEY, fallback=None) if had_section else None,
    }
    try:
        yield cfg
    finally:
        if had_section:
            for key, prev in prev_keys.items():
                if prev is None:
                    if cfg.config.has_option(dev_mode.SECTION, key):
                        cfg.config.remove_option(dev_mode.SECTION, key)
                else:
                    cfg.config.set(dev_mode.SECTION, key, prev)
        elif cfg.config.has_section(dev_mode.SECTION):
            cfg.config.remove_section(dev_mode.SECTION)


@pytest.fixture
def dev_patches():
    """Guarantee a clean patch slate before the test and a full teardown after.

    Set the desired ``dev_config`` flag value first, then call
    ``src.dev.install()`` inside the test (``patch()`` reads the flag at install
    time — that's the "restart to apply" contract)."""
    _reset()
    try:
        yield dev_pkg
    finally:
        _reset()


def _reset():
    dev_album_sort.unpatch()
    dev_immediate_write.unpatch()
    dev_settings_tab.unpatch()
    dev_pkg._installed = False
