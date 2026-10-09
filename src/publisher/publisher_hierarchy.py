from collections import defaultdict

from sqlalchemy import select

from src.db.db_tables import AlbumPublisher
from src.db.db_tables.album import Album


def get_descendant_publisher_ids(controller, publisher_id):
    """Return publisher_id plus every descendant publisher_id (children, grandchildren, ...)."""
    publishers = controller.get.get_all_entities("Publisher")
    children_map = defaultdict(list)
    for publisher in publishers:
        children_map[publisher.parent_id].append(publisher.publisher_id)

    ids = []
    visited = set()  # guards against a parent_id cycle in the data
    stack = [publisher_id]
    while stack:
        current = stack.pop()
        if current in visited:
            continue
        visited.add(current)
        ids.append(current)
        stack.extend(children_map.get(current, []))
    return ids


def _album_chronological_key(album):
    """Sort key ordering albums oldest-first, with unknown dates last."""
    year = album.release_year
    return (year is None, year or 0, album.release_month or 0, album.release_day or 0)


def get_publisher_albums(controller, publisher_id):
    """Return every Album of this publisher and its descendants, oldest first."""
    publisher_ids = get_descendant_publisher_ids(controller, publisher_id)
    stmt = select(Album).join(AlbumPublisher, AlbumPublisher.album_id == Album.album_id).where(AlbumPublisher.publisher_id.in_(publisher_ids)).distinct()
    albums = list(controller.get.session.execute(stmt).scalars().all())
    albums.sort(key=_album_chronological_key)
    return albums
