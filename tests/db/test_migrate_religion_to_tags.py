"""Tests for scripts/migrate_religion_to_tags.py.

Builds a throwaway SQLite file with the pre-migration shape (a religions
table plus an artists.religion_id column bolted onto the current schema,
mirroring how MusicDatabase._try_add_column would have added it to an
existing install) and runs the real migration against it -- same
assertions as the scratch-copy rehearsal this script was manually verified
against before being run on the real database.
"""

import sqlite3

import pytest
from sqlalchemy import create_engine

from scripts.migrate_religion_to_tags import _table_exists, main
from src.db.db_tables.base import Base


def _make_pre_migration_db(path) -> None:
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    engine.dispose()

    conn = sqlite3.connect(path)
    try:
        conn.execute("ALTER TABLE artists ADD COLUMN religion_id INTEGER")
        conn.execute(
            "CREATE TABLE religions ("
            "religion_id INTEGER PRIMARY KEY, "
            "religion_name VARCHAR NOT NULL UNIQUE, "
            "description VARCHAR, "
            "parent_id INTEGER REFERENCES religions(religion_id))"
        )
        conn.execute(
            "INSERT INTO religions (religion_id, religion_name, parent_id, description) "
            "VALUES (1, 'Christian', NULL, NULL), "
            "(2, 'Catholic', 1, 'A Christian denomination'), "
            "(3, 'Jewish', NULL, NULL)"
        )
        conn.execute("INSERT INTO artists (artist_id, artist_name) VALUES (1, 'Miles Davis')")
        conn.execute("INSERT INTO artists (artist_id, artist_name) VALUES (2, 'Bing Crosby')")
        conn.execute("UPDATE artists SET religion_id = 2 WHERE artist_id = 1")
        conn.execute("UPDATE artists SET religion_id = 3 WHERE artist_id = 2")
        conn.commit()
    finally:
        conn.close()


def _run_main(monkeypatch, args) -> int:
    monkeypatch.setattr("sys.argv", ["migrate_religion_to_tags.py", *args])
    return main()


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    """Run each test in a throwaway cwd so backup_db's snapshot lands under
    a scratch backups/ dir, not the real repo's."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_dry_run_reports_and_writes_nothing(workdir, monkeypatch, capsys):
    db_path = workdir / "library.db"
    _make_pre_migration_db(db_path)

    exit_code = _run_main(monkeypatch, ["--db", str(db_path)])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "3 religion(s), 2 artist assignment(s)" in out
    assert "Dry run" in out

    conn = sqlite3.connect(db_path)
    try:
        assert _table_exists(conn.cursor(), "religions")
        assert conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0] == 0
    finally:
        conn.close()


def test_apply_migrates_hierarchy_and_assignments_then_drops_religion(workdir, monkeypatch, capsys):
    db_path = workdir / "library.db"
    _make_pre_migration_db(db_path)

    exit_code = _run_main(monkeypatch, ["--db", str(db_path), "--apply"])
    assert exit_code == 0
    assert "Migrated 3 religion(s)" in capsys.readouterr().out

    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()

        assert not _table_exists(cursor, "religions")

        cursor.execute("PRAGMA table_info(artists)")
        assert "religion_id" not in {row[1] for row in cursor.fetchall()}

        cursor.execute("SELECT tag_type_id, type_name FROM tag_types")
        (tag_type_id, type_name) = cursor.fetchone()
        assert type_name == "Religion"

        cursor.execute(
            "SELECT tag_name, parent_id FROM tags WHERE tag_type_id = ? ORDER BY tag_name",
            (tag_type_id,),
        )
        rows = dict(cursor.fetchall())
        assert set(rows) == {"Christian", "Catholic", "Jewish"}
        christian_id = cursor.execute(
            "SELECT tag_id FROM tags WHERE tag_name = 'Christian'"
        ).fetchone()[0]
        assert rows["Catholic"] == christian_id
        assert rows["Jewish"] is None

        cursor.execute(
            "SELECT ar.artist_name, t.tag_name FROM artists ar "
            "JOIN artist_tag_associations ata ON ata.artist_id = ar.artist_id "
            "JOIN tags t ON t.tag_id = ata.tag_id "
            "ORDER BY ar.artist_name"
        )
        assert cursor.fetchall() == [("Bing Crosby", "Jewish"), ("Miles Davis", "Catholic")]

        cursor.execute("PRAGMA foreign_key_check")
        assert cursor.fetchall() == []
    finally:
        conn.close()


def test_apply_is_a_no_op_the_second_time(workdir, monkeypatch, capsys):
    db_path = workdir / "library.db"
    _make_pre_migration_db(db_path)

    _run_main(monkeypatch, ["--db", str(db_path), "--apply"])
    capsys.readouterr()

    exit_code = _run_main(monkeypatch, ["--db", str(db_path), "--apply"])

    assert exit_code == 0
    assert "already migrated" in capsys.readouterr().out
