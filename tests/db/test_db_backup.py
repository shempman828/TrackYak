"""Tests for scripts/_db_backup.py -- the shared DB-snapshot helper the
one-time repair scripts use so their ~400 MB copies land in backups/
(not the repo working tree) and stay pruned to the last N.
"""

import os

import pytest

from scripts import _db_backup
from scripts._db_backup import backup_db, prune_snapshots


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    """Run each test in a throwaway cwd with a stand-in DB file."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "music_library.db").write_bytes(b"fake db")
    return tmp_path


def test_snapshot_goes_into_backups_not_cwd(workdir):
    dest = backup_db("music_library.db", tag="pre-orphan-disc")

    assert dest.parent == _db_backup.BACKUP_DIR
    assert dest.exists()
    assert dest.read_bytes() == b"fake db"
    # nothing dropped next to the DB in the working tree
    assert not list(workdir.glob("*.bak"))


def test_snapshot_name_carries_db_name_and_tag(workdir):
    dest = backup_db("music_library.db", tag="pre-fake-lyrics")

    assert dest.name.startswith("music_library.db.pre-fake-lyrics-")
    assert dest.name.endswith(".bak")


def test_accepts_a_db_path_outside_cwd(workdir, tmp_path):
    other = tmp_path / "sub" / "library.db"
    other.parent.mkdir()
    other.write_bytes(b"elsewhere")

    dest = backup_db(other, tag="pre-orphan-disc")

    assert dest.parent == _db_backup.BACKUP_DIR
    assert dest.name.startswith("library.db.pre-orphan-disc-")


def _make_snapshots(prefix, stamps):
    _db_backup.BACKUP_DIR.mkdir(exist_ok=True)
    paths = []
    for stamp in stamps:
        p = _db_backup.BACKUP_DIR / f"{prefix}{stamp}.bak"
        p.write_bytes(b"x")
        paths.append(p)
    return paths


def test_prune_keeps_only_the_newest_n(workdir):
    prefix = "music_library.db.pre-orphan-disc-"
    made = _make_snapshots(
        prefix, ["20260101-000000", "20260102-000000", "20260103-000000", "20260104-000000"]
    )

    removed = prune_snapshots(prefix, keep=2)

    assert set(removed) == {made[0], made[1]}
    survivors = sorted(p.name for p in _db_backup.BACKUP_DIR.glob(f"{prefix}*.bak"))
    assert survivors == [
        "music_library.db.pre-orphan-disc-20260103-000000.bak",
        "music_library.db.pre-orphan-disc-20260104-000000.bak",
    ]


def test_prune_leaves_other_tags_alone(workdir):
    keep_prefix = "music_library.db.pre-fake-lyrics-"
    other = _make_snapshots("music_library.db.pre-orphan-disc-", ["20260101-000000"])[0]
    _make_snapshots(keep_prefix, ["20260101-000000", "20260102-000000", "20260103-000000"])

    prune_snapshots(keep_prefix, keep=1)

    assert other.exists()
    assert len(list(_db_backup.BACKUP_DIR.glob(f"{keep_prefix}*.bak"))) == 1


def test_prune_is_a_noop_when_keep_is_zero_or_negative(workdir):
    prefix = "music_library.db.pre-orphan-disc-"
    _make_snapshots(prefix, ["20260101-000000", "20260102-000000"])

    assert prune_snapshots(prefix, keep=0) == []
    assert prune_snapshots(prefix, keep=-1) == []
    assert len(list(_db_backup.BACKUP_DIR.glob(f"{prefix}*.bak"))) == 2


def test_backup_db_prunes_its_own_lineage(workdir):
    prefix = "music_library.db.pre-orphan-disc-"
    _make_snapshots(prefix, ["20260101-000000", "20260102-000000", "20260103-000000"])

    backup_db("music_library.db", tag="pre-orphan-disc", keep=2)

    names = sorted(p.name for p in _db_backup.BACKUP_DIR.glob(f"{prefix}*.bak"))
    assert len(names) == 2
    # the just-written snapshot is the newest and survives
    assert names[-1] not in {
        f"{prefix}20260101-000000.bak",
        f"{prefix}20260102-000000.bak",
        f"{prefix}20260103-000000.bak",
    }


def test_default_keep_is_three(workdir):
    prefix = "music_library.db.pre-fake-lyrics-"
    _make_snapshots(
        prefix, ["20260101-000000", "20260102-000000", "20260103-000000", "20260104-000000"]
    )

    backup_db("music_library.db", tag="pre-fake-lyrics")

    assert len(list(_db_backup.BACKUP_DIR.glob(f"{prefix}*.bak"))) == 3
    assert _db_backup.KEEP_LAST == 3


def test_creates_backups_dir_when_missing(workdir):
    assert not _db_backup.BACKUP_DIR.exists()

    backup_db("music_library.db", tag="pre-orphan-disc")

    assert _db_backup.BACKUP_DIR.is_dir()


def test_copy_preserves_mtime(workdir):
    src = workdir / "music_library.db"
    old = 1_600_000_000
    os.utime(src, (old, old))

    dest = backup_db("music_library.db", tag="pre-orphan-disc")

    assert dest.stat().st_mtime == pytest.approx(old, abs=1)
