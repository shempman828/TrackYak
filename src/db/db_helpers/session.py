"""Database engine/session initialization, shared across db_helpers modules."""

from sqlalchemy import create_engine
from sqlalchemy.orm import scoped_session, sessionmaker

engine = create_engine(
    "sqlite:///music_library.db", connect_args={"check_same_thread": False}
)
Session = scoped_session(sessionmaker(bind=engine))
