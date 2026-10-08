"""App version string derived from git metadata."""

from functools import lru_cache
from pathlib import Path
import subprocess

BASE_VERSION = "0.5"

_REPO_DIR = Path(__file__).resolve().parent


def _git(*args: str) -> str | None:
    """Run a git command in this repo and return its stripped stdout, or None on failure."""
    try:
        result = subprocess.run(["git", "-C", str(_REPO_DIR), *args], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


@lru_cache(maxsize=1)
def get_version() -> str:
    """Return 'BASE.N (build M)', 'BASE (build M)' without the vBASE tag, or 'BASE (dev)' without git."""
    # N = commits since the vBASE_VERSION tag, M = total commit count.
    since_base = _git("rev-list", f"v{BASE_VERSION}..HEAD", "--count")
    total = _git("rev-list", "--count", "HEAD")

    if total is None:
        return f"{BASE_VERSION} (dev)"
    if since_base is None:
        return f"{BASE_VERSION} (build {total})"

    return f"{BASE_VERSION}.{since_base} (build {total})"
