"""Developer-mode flag: a single persisted boolean in config.ini.

Self-contained: nothing under ``src/`` (outside ``src/dev/``) imports this.
The flag is read/written straight through the raw configparser handle that
``Config`` already exposes, so no ``get_*``/``set_*`` accessor has to be added
to ``config_setup.py``.
"""

from __future__ import annotations

from src.foundation.config_setup import Config

SECTION = "developer"
KEY = "enabled"


def is_enabled(config: Config | None = None) -> bool:
    """True when developer mode is switched on. Defaults to the ``Config``
    singleton so callers deep in the widget tree need not thread it through."""
    cfg = config if config is not None else Config()
    return cfg.config.getboolean(SECTION, KEY, fallback=False)


def set_enabled(config: Config, value: bool) -> None:
    """Write the flag. The caller owns persistence (``config.save()``) so this
    can ride along with the Settings dialog's existing save on Apply/OK."""
    if not config.config.has_section(SECTION):
        config.config.add_section(SECTION)
    config.config.set(SECTION, KEY, str(bool(value)).lower())
