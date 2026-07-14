"""
Declarative base and engine-level configuration shared by all ORM models.
"""

from sqlalchemy import Engine, event
from sqlalchemy.orm import declarative_base

Base = declarative_base()


# Enable SQLite foreign key support
@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection, _):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()
