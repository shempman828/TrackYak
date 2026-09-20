"""role_loader_worker.py"""

from collections import defaultdict

from PySide6.QtCore import QObject, Signal

from src.foundation.logger_config import logger


class RoleLoaderWorker(QObject):
    """Background-thread worker that fetches all roles and their album/track association counts."""

    # Emitted when loading succeeds.
    # Payload: (all_roles, album_counts_by_role_id, track_counts_by_role_id,
    #           recursive_counts_by_role_id)
    # Uses `object` rather than `list`/`dict` because PySide6's queued
    # cross-thread delivery can fail to copy-convert plain dict/list
    # signal args, logging "_pythonToCppCopy" errors (or worse, dropping
    # the payload). `object` passes the Python object through untouched.
    finished = Signal(object, object, object, object)

    # Emitted if something goes wrong.
    error = Signal(str)

    def __init__(self, controller):
        super().__init__()
        self.controller = controller

    def run(self):
        """Fetch everything we need in as few queries as possible."""
        try:
            # --- Query 1: all roles ---
            all_roles = self.controller.get.get_all_entities("Role") or []

            # --- Query 2: ALL album associations at once ---
            all_album_links = self.controller.get.get_all_entities("AlbumRoleAssociation") or []

            # --- Query 3: ALL track associations at once ---
            all_track_links = self.controller.get.get_all_entities("TrackArtistRole") or []

            # Direct (own-role-only) counts, kept separate for the detail
            # tooltip's album/track breakdown.
            album_counts: dict[int, int] = defaultdict(int)
            direct_album_ids = defaultdict(set)
            for link in all_album_links:
                album_counts[link.role_id] += 1
                direct_album_ids[link.role_id].add(link.association_id)

            track_counts: dict[int, int] = defaultdict(int)
            direct_track_ids = defaultdict(set)
            for link in all_track_links:
                track_counts[link.role_id] += 1
                # role_id is part of the key (not just track_id/artist_id):
                # the same artist can legitimately hold two different roles
                # on the same track (e.g. Guitar and Producer), and those
                # are two distinct credits that must both still count.
                direct_track_ids[link.role_id].add((link.track_id, link.artist_id, link.role_id))

            # Recursive (own + descendants) counts, unioned as sets per
            # dimension. AlbumRoleAssociation has a real per-row surrogate
            # key (association_id) so a credit can never collide across
            # roles; TrackArtistRole's key above is similarly row-unique.
            # The set union itself is a no-op given that (each row lives
            # under exactly one role_id, so no id is ever reachable from
            # two branches) -- kept for structural parity with
            # GenreLoaderWorker.track_ids_for, where a genuine track_id can
            # be reachable from multiple branches and must dedupe.
            children_map = defaultdict(list)
            for role in all_roles:
                children_map[role.parent_id].append(role.role_id)

            recursive_album_ids: dict = {}
            recursive_track_ids: dict = {}

            def album_ids_for(role_id):
                if role_id not in recursive_album_ids:
                    ids = set(direct_album_ids.get(role_id, ()))
                    for child_id in children_map.get(role_id, []):
                        ids |= album_ids_for(child_id)
                    recursive_album_ids[role_id] = ids
                return recursive_album_ids[role_id]

            def track_ids_for(role_id):
                if role_id not in recursive_track_ids:
                    ids = set(direct_track_ids.get(role_id, ()))
                    for child_id in children_map.get(role_id, []):
                        ids |= track_ids_for(child_id)
                    recursive_track_ids[role_id] = ids
                return recursive_track_ids[role_id]

            recursive_counts: dict[int, int] = {}
            for role in all_roles:
                recursive_counts[role.role_id] = len(album_ids_for(role.role_id)) + len(
                    track_ids_for(role.role_id)
                )

            self.finished.emit(all_roles, dict(album_counts), dict(track_counts), recursive_counts)

        except Exception as e:
            # Intentional broad boundary catch: this runs on a QThread and must
            # not let an exception kill the thread silently.
            logger.exception("Role count scan failed")
            self.error.emit(str(e))
        finally:
            # load_roles() starts a fresh QThread on every Roles-nav revisit,
            # and the scoped_session registry hands each new OS thread its own
            # Session the first time controller.get.session is touched above.
            # Without this remove(), that Session's pooled connection is never
            # returned and its read transaction stays open for the life of the
            # process, leaking a connection per revisit. Mirrors
            # GenreLoaderWorker._release_db_session / _RolesLoaderWorker in
            # src/track/track_edit_roles.py.
            from src.db.db_engine import Session

            Session.remove()
