"""Self-contained developer-mode package.

Activated by a single guarded ``install()`` call in ``run.py``. No tracked file
under ``src/`` outside this package references ``src.dev`` — the feature wires
itself into the running app by monkey-patching existing classes.

Currently behind the flag: the "Primary Artist Count" album sort option.
"""

from __future__ import annotations

from src.core.logger_config import logger

_installed = False


def install() -> None:
    """Apply the developer-mode patches. Idempotent; cheap when the flag is off
    (the patches are still applied, but expose nothing)."""
    global _installed
    if _installed:
        return

    from src.dev import dev_album_sort, dev_mode, dev_settings_tab

    dev_settings_tab.patch()
    dev_album_sort.patch()
    _installed = True
    logger.info("developer mode: patches installed (enabled=%s)", dev_mode.is_enabled())


def uninstall() -> None:
    """Reverse :func:`install`. Used by tests."""
    global _installed

    from src.dev import dev_album_sort, dev_settings_tab

    dev_album_sort.unpatch()
    dev_settings_tab.unpatch()
    _installed = False
