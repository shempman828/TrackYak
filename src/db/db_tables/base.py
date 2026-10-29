"""Declarative base, engine configuration, and helpers shared by all ORM models."""

from sqlalchemy import Engine, event
from sqlalchemy.orm import declarative_base

Base = declarative_base()

# Polymorphic association entity_type -> name of the relationship that loads it.
ENTITY_RELATIONSHIP_ATTRS = {"Artist": "artist", "Album": "album", "Track": "track", "Publisher": "publisher", "Playlist": "playlist"}


@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection, _):
    """Turn on SQLite foreign-key enforcement for every new connection."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def oxford_join(names: list) -> str:
    """Join names as 'A' / 'A & B' / 'A, B, & C' (Oxford comma for 3+)."""
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} & {names[1]}"
    return f"{', '.join(names[:-1])}, & {names[-1]}"


def walk_parents(node):
    """Yield node and each ancestor via .parent, stopping at a parent cycle."""
    seen = set()
    while node is not None and id(node) not in seen:
        seen.add(id(node))
        yield node
        node = node.parent
