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

import pytest

from src.metadata.metadata_writer_backup import backup_file, write_artwork_with_backup


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
