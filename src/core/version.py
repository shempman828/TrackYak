import subprocess
from functools import lru_cache
from pathlib import Path

BASE_VERSION = "0.4"

_REPO_DIR = Path(__file__).resolve().parent


def _git(*args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(_REPO_DIR), *args],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


@lru_cache(maxsize=1)
def get_version() -> str:
    """Return 'BASE_VERSION.N (build M)', where N is commits since the vBASE_VERSION
    tag and M is the total commit count. Falls back to BASE_VERSION alone if git
    metadata isn't available (e.g. no .git directory)."""
    since_base = _git("rev-list", f"v{BASE_VERSION}..HEAD", "--count")
    total = _git("rev-list", "--count", "HEAD")

    if since_base is None or total is None:
        return f"{BASE_VERSION} (dev)"

    return f"{BASE_VERSION}.{since_base} (build {total})"
