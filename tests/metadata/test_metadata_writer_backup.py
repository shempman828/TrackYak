"""Regression: backup_file must not clobber a leftover crash backup.

Bug: backup_file did `shutil.copy2(file_path, file_path + ".bak")`
unconditionally. If an earlier tag/artwork write died between backup_file
and restore_backup/discard_backup (SIGKILL, power loss, an exception the
caller's except clause doesn't cover), the stale <file>.bak is the only
untouched copy of the original -- and the next write attempt overwrote it
with the already-modified file, destroying the last good copy.

  AC1  backup_file copies to <file>.bak and returns that path.
  AC2  backup_file raises FileExistsError, and leaves the existing backup
       byte-for-byte intact, when <file>.bak already exists.
  AC3  crash-then-retry through write_artwork_with_backup: after a mutate
       that half-writes the file and then dies, the retry aborts (returns
       False) and the pristine backup still holds the ORIGINAL bytes.
"""

import os
import time

import pytest

from src.metadata import metadata_writer_backup
from src.metadata.metadata_writer_backup import (
    atomic_write,
    backup_file,
    sweep_stale_temp_files,
    write_artwork_with_backup,
)


def test_backup_file_creates_sibling_bak(tmp_path):
    src = tmp_path / "song.flac"
    src.write_bytes(b"ORIGINAL")

    backup_path = backup_file(str(src))

    assert backup_path == str(src) + ".bak"
    assert (tmp_path / "song.flac.bak").read_bytes() == b"ORIGINAL"


def test_backup_file_refuses_to_overwrite_existing_backup(tmp_path):
    src = tmp_path / "song.flac"
    src.write_bytes(b"MODIFIED")  # file already got mutated by the failed write
    bak = tmp_path / "song.flac.bak"
    bak.write_bytes(b"ORIGINAL")  # leftover pristine backup from the crash

    with pytest.raises(FileExistsError):
        backup_file(str(src))

    # The pristine backup is untouched - not overwritten with b"MODIFIED".
    assert bak.read_bytes() == b"ORIGINAL"


def test_retry_after_crash_preserves_original_backup(tmp_path):
    src = tmp_path / "song.flac"
    src.write_bytes(b"ORIGINAL")
    bak = tmp_path / "song.flac.bak"
    role_to_type = {"front": 3}

    def crashing_mutate():
        src.write_bytes(b"HALF-WRITTEN")  # partial write already on disk
        raise RuntimeError("process killed mid-write")

    # First attempt dies with an exception the caller doesn't catch, leaving
    # the pristine backup and a half-written file behind.
    with pytest.raises(RuntimeError):
        write_artwork_with_backup(
            str(src), "front", b"img-bytes", role_to_type, crashing_mutate, "artwork"
        )
    assert bak.read_bytes() == b"ORIGINAL"

    def second_mutate():
        src.write_bytes(b"SECOND-ATTEMPT")
        return True

    # Retry must bail rather than overwrite the only good copy.
    result = write_artwork_with_backup(
        str(src), "front", b"img-bytes", role_to_type, second_mutate, "artwork"
    )

    assert result is False
    assert bak.read_bytes() == b"ORIGINAL"


# --- atomic_write: stranded temp sweep + fs-metadata preservation -------------
#
# Bug: a SIGKILL between mkstemp and os.replace strands a `.tmp-XXXXXX` file
# next to the target with no cleanup path, and shutil.copymode restored only
# permission bits -- owner/group and xattrs (e.g. `user.*` rating tags) were
# dropped on the swapped-in inode. Timestamps must still NOT be carried over,
# or mtime-keyed caches downstream stop invalidating.


@pytest.fixture(autouse=True)
def _reset_swept_dirs():
    metadata_writer_backup._swept_dirs.clear()
    yield
    metadata_writer_backup._swept_dirs.clear()


def _can_set_user_xattr(path: str) -> bool:
    try:
        os.setxattr(path, "user.__probe__", b"1")
        os.removexattr(path, "user.__probe__")
        return True
    except OSError:
        return False


def test_sweep_removes_only_aged_temp_files(tmp_path):
    stale = tmp_path / ".tmp-abc123.flac"
    stale.write_bytes(b"orphan")
    old = time.time() - 3600
    os.utime(stale, (old, old))

    fresh = tmp_path / ".tmp-def456.flac"
    fresh.write_bytes(b"in-flight")

    real = tmp_path / "song.flac"
    real.write_bytes(b"AUDIO")

    sweep_stale_temp_files(str(tmp_path))

    assert not stale.exists()  # aged orphan reaped
    assert fresh.exists()  # concurrent in-flight write left alone
    assert real.exists()  # non-temp file untouched


def test_atomic_write_sweeps_stale_temp_on_first_write_to_dir(tmp_path):
    stale = tmp_path / ".tmp-deadbeef.flac"
    stale.write_bytes(b"orphan")
    old = time.time() - 3600
    os.utime(stale, (old, old))

    target = tmp_path / "song.flac"
    target.write_bytes(b"OLD")

    atomic_write(str(target), b"NEW")

    assert target.read_bytes() == b"NEW"
    assert not stale.exists()


def test_atomic_write_preserves_xattrs(tmp_path):
    target = tmp_path / "song.flac"
    target.write_bytes(b"OLD")
    if not _can_set_user_xattr(str(target)):
        pytest.skip("filesystem does not support user xattrs")
    os.setxattr(str(target), "user.rating", b"5")

    atomic_write(str(target), b"NEW")

    assert target.read_bytes() == b"NEW"
    assert os.getxattr(str(target), "user.rating") == b"5"


def test_atomic_write_does_not_freeze_mtime(tmp_path):
    target = tmp_path / "song.flac"
    target.write_bytes(b"OLD")
    old = time.time() - 86400
    os.utime(target, (old, old))

    atomic_write(str(target), b"NEW")

    # The replacement inode keeps its own fresh mtime; carrying the old one
    # over would stop artwork_cache / transcode caches from invalidating.
    assert target.stat().st_mtime > old + 60


def test_atomic_write_preserves_mode(tmp_path):
    target = tmp_path / "song.flac"
    target.write_bytes(b"OLD")
    target.chmod(0o640)

    atomic_write(str(target), b"NEW")

    assert oct(target.stat().st_mode & 0o777) == oct(0o640)
