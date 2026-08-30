"""
installation_check.py

Fail-fast environment validation, run *before* any third-party import
(PySide6 included) so a broken install prints a clear, actionable message
instead of a raw ImportError traceback. Deliberately stdlib-only.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import re
import shutil
import sys

MIN_PYTHON_VERSION = (3, 10)

_REQUIREMENTS_FILE = Path(__file__).resolve().parents[2] / "requirements.txt"

_NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def check_python_version() -> str | None:
    """Return an error message if the running interpreter is too old, else None."""
    if sys.version_info[:2] < MIN_PYTHON_VERSION:
        required = ".".join(str(part) for part in MIN_PYTHON_VERSION)
        current = ".".join(str(part) for part in sys.version_info[:3])
        return f"TrackYak requires Python {required}+, but this interpreter is {current}."
    return None


def _iter_requirement_names(requirements_file: Path):
    with requirements_file.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            match = _NAME_PATTERN.match(line)
            if match:
                yield match.group(0)


def check_required_packages() -> list[str]:
    """Return the distribution names from requirements.txt that aren't installed."""
    if not _REQUIREMENTS_FILE.exists():
        return []

    missing = []
    for name in _iter_requirement_names(_REQUIREMENTS_FILE):
        try:
            version(name)
        except PackageNotFoundError:
            missing.append(name)
    return missing


def check_audio_fingerprint_backend() -> str | None:
    """Return a warning if neither chromaprint bindings nor fpcalc are usable.

    Non-fatal: fingerprinting/duplicate-matching features degrade gracefully
    without this, unlike the hard package requirements above.
    """
    try:
        import acoustid
    except ImportError:
        # pyacoustid itself missing is already reported by check_required_packages.
        return None

    has_python_backend = getattr(acoustid, "have_chromaprint", False) and getattr(
        acoustid, "have_audioread", False
    )
    has_fpcalc = shutil.which("fpcalc") is not None
    if not has_python_backend and not has_fpcalc:
        return (
            "Audio fingerprinting backend not found (no libchromaprint bindings and "
            "no 'fpcalc' on PATH). Fingerprinting/duplicate-matching features will be "
            "unavailable. See README.md 'System dependencies' to install it."
        )
    return None


def verify_installation() -> None:
    """Validate the runtime environment, exiting with a clear message on hard failures."""
    version_error = check_python_version()
    if version_error:
        print(f"FATAL: {version_error}", file=sys.stderr)
        sys.exit(1)

    missing_packages = check_required_packages()
    if missing_packages:
        print(
            "FATAL: TrackYak is missing required Python packages:\n  "
            + "\n  ".join(missing_packages)
            + "\n\nInstall them with:\n  pip install -r requirements.txt",
            file=sys.stderr,
        )
        sys.exit(1)

    fingerprint_warning = check_audio_fingerprint_backend()
    if fingerprint_warning:
        print(f"WARNING: {fingerprint_warning}", file=sys.stderr)
