"""
artist_view_dedup.py

Data-quality scanning for ArtistView: finding artists with no links to
anything else (orphans, safe to delete) and finding likely-duplicate
artist names via fuzzy matching.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QMessageBox, QProgressDialog
from sqlalchemy.exc import SQLAlchemyError

from src.artist.artist_delete_orphans import OrphanArtistDialog
from src.artist.artist_fuzzy_match import ArtistFuzzyMatchWorker, FuzzyMatchDialog
from src.core.status_utility import show_status_message


class ArtistDedupMixin:
    """
    Expects the host class to provide: self.controller, self.load_artists(),
    and to be a QWidget subclass.
    """

    def _get_linked_artist_ids(self) -> set:
        """Return the artist_ids of every artist referenced by a track role,
        album role, group membership, influence, place, or award — i.e.
        everything that isn't an alias, since aliases belong to the artist
        rather than linking it to something else."""
        linked_ids = set()

        track_roles = self.controller.get.get_all_entities("TrackArtistRole")
        linked_ids.update(r.artist_id for r in track_roles)

        album_roles = self.controller.get.get_all_entities("AlbumRoleAssociation")
        linked_ids.update(r.artist_id for r in album_roles)

        memberships = self.controller.get.get_all_entities("GroupMembership")
        for m in memberships:
            linked_ids.add(m.group_id)
            linked_ids.add(m.member_id)

        influences = self.controller.get.get_all_entities("ArtistInfluence")
        for inf in influences:
            linked_ids.add(inf.influencer_id)
            linked_ids.add(inf.influenced_id)

        places = self.controller.get.get_all_entities(
            "PlaceAssociation", entity_type="Artist"
        )
        linked_ids.update(p.entity_id for p in places)

        awards = self.controller.get.get_all_entities(
            "AwardAssociation", entity_type="Artist"
        )
        linked_ids.update(a.entity_id for a in awards)

        return linked_ids

    def find_orphan_artists(self):
        """Scan for artists with no roles, influences, memberships, places,
        or awards, then open a review dialog so the user can deselect any
        they'd rather keep before the rest are permanently deleted."""
        try:
            all_artists = self.controller.get.get_all_entities("Artist")
            linked_ids = self._get_linked_artist_ids()
            orphans = [a for a in all_artists if a.artist_id not in linked_ids]
        except SQLAlchemyError as e:
            QMessageBox.critical(
                self, "Error", f"Failed to scan for unused artists: {e}"
            )
            return

        if not orphans:
            show_status_message(
                self,
                "No artists were found with zero roles, influences, "
                "memberships, places, or awards.",
            )
            return

        dialog = OrphanArtistDialog(orphans, self)
        if dialog.exec_() != QDialog.Accepted:
            return

        selected_ids = dialog.selected_artist_ids()
        if not selected_ids:
            return

        deleted = 0
        for artist_id in selected_ids:
            if self.controller.delete.delete_entity("Artist", entity_id=artist_id):
                deleted += 1

        show_status_message(
            self, f"Deleted {deleted} of {len(selected_ids)} selected artist(s)."
        )
        self.load_artists()

    def find_fuzzy_matches(self):
        """Generate fuzzy duplicate candidates and open the review dialog.

        Uses a blocking strategy (first 3 chars of normalised name) to avoid
        comparing all 23k artists against each other (O(n²) = ~265M pairs).
        Blocking reduces this to only comparing artists that share the same
        name prefix, cutting comparisons by ~99%.

        The scan runs in a background thread (ArtistFuzzyMatchWorker) so the
        UI stays responsive, with progress and cancellation checked every
        500 pairs — a common prefix like "the" can still put thousands of
        artists in one block, and without frequent checkpoints inside that
        block the dialog would sit unresponsive (Cancel included) until the
        whole block finished.
        """
        THRESHOLD = 0.85  # 85% similarity required to flag as a duplicate

        # --- Load artists up front (fast DB call) ---
        try:
            artists = self.controller.get.get_all_entities("Artist")
        except SQLAlchemyError as e:
            QMessageBox.critical(self, "Error", f"Failed to load artists: {e}")
            return

        if not artists:
            show_status_message(self, "No artists found in database.")
            return

        # --- Show a determinate progress dialog so the user can see it's
        # actually moving, and Cancel responds promptly ---
        progress = QProgressDialog(
            "Scanning for duplicate artists…", "Cancel", 0, 1, self
        )
        progress.setWindowTitle("Duplicate Scan")
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)
        progress.show()

        worker = ArtistFuzzyMatchWorker(artists, THRESHOLD)

        def _on_progress(current, total):
            progress.setRange(0, total)
            progress.setValue(current)
            progress.setLabelText(
                f"Scanning for duplicate artists… ({current:,} / {total:,})"
            )

        def _on_finished(matches):
            progress.close()
            if not matches:
                show_status_message(
                    self,
                    f"No similar artist names found (threshold: {int(THRESHOLD * 100)}% similarity).",
                )
                return
            dialog = FuzzyMatchDialog(matches, self.controller, self)
            if dialog.exec_() == QDialog.Accepted:
                self.load_artists()

        def _on_error(msg):
            progress.close()
            QMessageBox.critical(self, "Scan Error", f"Duplicate scan failed:\n{msg}")

        def _on_cancelled():
            worker.request_cancel()

        worker.progress.connect(_on_progress)
        worker.finished.connect(_on_finished)
        worker.error.connect(_on_error)
        progress.canceled.connect(_on_cancelled)

        # Keep a reference so the worker isn't garbage collected
        self._fuzzy_worker = worker
        worker.start()
