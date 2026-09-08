"""Shared DB-snapshot helper for the one-time repair / migration scripts.

Every script that mutates ``music_library.db`` copies it first. This puts
that copy in ``backups/`` -- never the repo working tree / cwd, where a
~400 MB snapshot gets dragged into every rsync, IDE index and backup pass
-- and prunes its own lineage to the last N so they don't pile up
unbounded (``backups/`` has no other retention policy).
"""

from datetime import datetime
from pathlib import Path
import shutil

BACKUP_DIR = Path("backups")
KEEP_LAST = 3


def backup_db(db_path: str | Path, *, tag: str, keep: int = KEEP_LAST) -> Path:
    """Copy ``db_path`` into ``backups/`` as ``<name>.<tag>-<stamp>.bak``.

    Returns the new snapshot's path. After the copy, all but the ``keep``
    newest snapshots sharing this ``<name>.<tag>-`` prefix are unlinked
    (``keep <= 0`` disables pruning).
    """
    src = Path(db_path)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    prefix = f"{src.name}.{tag}-"
    dest = BACKUP_DIR / f"{prefix}{stamp}.bak"
    shutil.copy2(src, dest)
    prune_snapshots(prefix, keep=keep)
    return dest


def prune_snapshots(prefix: str, *, keep: int = KEEP_LAST) -> list[Path]:
    """Unlink all but the ``keep`` newest ``backups/<prefix>*.bak`` files.

    Ordering is by filename, which sorts chronologically because the stamp
    is ``%Y%m%d-%H%M%S``. Returns the paths that were removed.
    """
    if keep <= 0:
        return []
    snapshots = sorted(BACKUP_DIR.glob(f"{prefix}*.bak"))
    stale = snapshots[:-keep]
    for path in stale:
        path.unlink()
    return stale
