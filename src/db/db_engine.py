"""App-wide SQLAlchemy engine/session singleton.

Lives directly under src.db (not inside db_helpers or db_tables) so both
packages can import it without a circular import: db_helpers depends on
db_tables via the model registry, and db_tables.MusicDatabase needs to reuse
this same engine instead of opening a second, uncoordinated connection pool
against the same SQLite file.
"""

from sqlalchemy import create_engine, event
from sqlalchemy.orm import scoped_session, sessionmaker

engine = create_engine(
    "sqlite:///music_library.db", connect_args={"check_same_thread": False, "timeout": 30}
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    """Put every new connection in this pool into WAL mode.

    Default SQLite (rollback-journal) mode requires a writer's COMMIT to
    wait for every other connection's read transaction to end, and this one
    engine is shared by every background worker thread/process in the app
    (analysis scheduler, chart search/matching, etc). WAL mode lets readers
    and a single writer proceed without blocking each other, which is what
    "database is locked" during batch operations like audio analysis
    actually indicates.
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.close()


Session = scoped_session(sessionmaker(bind=engine, expire_on_commit=False))
# expire_on_commit=False: get.py's read helpers now commit right after
# fetching (so a query doesn't leave its transaction open indefinitely --
# see project memory on the "database is locked" investigation). Default
# expire-on-commit would invalidate every attribute on every object that
# commit touches, turning the next attribute access anywhere in the app
# into an implicit reload -- catastrophic for a query like
# get_all_entities("Track") returning 50k+ rows the UI reads repeatedly.
