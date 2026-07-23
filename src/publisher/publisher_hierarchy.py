from collections import defaultdict


def get_descendant_publisher_ids(controller, publisher_id):
    """Return publisher_id plus every descendant publisher_id (children, grandchildren, ...)."""
    publishers = controller.get.get_all_entities("Publisher")
    children_map = defaultdict(list)
    for publisher in publishers:
        children_map[publisher.parent_id].append(publisher.publisher_id)

    ids = []
    stack = [publisher_id]
    while stack:
        current = stack.pop()
        ids.append(current)
        stack.extend(children_map.get(current, []))
    return ids


def get_publisher_albums(controller, publisher_id):
    """Return every Album linked to this publisher or any of its descendant publishers."""
    publisher_ids = get_descendant_publisher_ids(controller, publisher_id)

    albums = []
    seen_album_ids = set()
    for pid in publisher_ids:
        for link in controller.get.get_entity_links("AlbumPublisher", publisher_id=pid):
            if link.album_id in seen_album_ids:
                continue
            album = controller.get.get_entity_object("Album", album_id=link.album_id)
            if album:
                seen_album_ids.add(link.album_id)
                albums.append(album)
    return albums
