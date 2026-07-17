"""App-wide SQLAlchemy engine/session singleton.

Lives directly under src.db (not inside db_helpers or db_tables) so both
packages can import it without a circular import: db_helpers depends on
db_tables via the model registry, and db_tables.MusicDatabase needs to reuse
this same engine instead of opening a second, uncoordinated connection pool
against the same SQLite file.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import scoped_session, sessionmaker

engine = create_engine(
    "sqlite:///music_library.db", connect_args={"check_same_thread": False}
)
Session = scoped_session(sessionmaker(bind=engine))
