"""
artwork_consistency_dialog.py

Tools-menu singleton dialog that scans the library for albums whose
embeddable tracks disagree on their embedded art (see
src/library/library_artwork_consistency.py for why that matters to the
thumbnail cache) and lets the user pick one variant to re-embed into every
track of the album.

Layout mirrors duplicate_finder.py: the background scan worker and the
dialog live in one file. Resolution reuses CoverEmbedWorker - the same
path the album editor takes when you pick a cover - so the embed happens
off the UI thread and the cache row is warmed the same way.

Singleton behavior lives in menu_bar.py's show_artwork_consistency_dialog(),
mirroring show_alias_management_dialog().
"""

from collections import OrderedDict
from pathlib import Path
import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.album.album_art_worker import CoverEmbedWorker
from src.common.cancellable_worker import CancellableWorker
from src.foundation.logger_config import logger
from src.foundation.status_utility import show_status_message
from src.image.artwork_cache import get_artwork_cache
from src.library.library_artwork_consistency import ArtworkConsistencyChecker
from src.metadata.metadata_artwork import ArtworkExtractor
from src.metadata.metadata_writer import MetadataWriter

_THUMB_PX = 72


class ArtworkConsistencyScanWorker(CancellableWorker):
    """Runs ArtworkConsistencyChecker.run() off the UI thread.

    Signals:
        progress(scanned, total)
        finished(conflicts)   - list of conflict dicts (may be partial on cancel)
        error(message)
    """

    progress = Signal(int, int)
    finished = Signal(list)
    error = Signal(str)

    def __init__(self, controller):
        super().__init__()
        self._controller = controller

    def run(self):
        try:
            checker = ArtworkConsistencyChecker(self._controller)
            checker.run(
                progress_callback=lambda scanned, total: self.progress.emit(scanned, total),
                is_cancelled=lambda: self.is_cancelled,
            )
            if not self.is_cancelled:
                self.finished.emit(checker.conflicts)
        except Exception as e:
            # Broad boundary catch: this is a QThread body; an escaping
            # exception would be lost and leave the dialog's Scan button
            # disabled forever.
            logger.exception("ArtworkConsistencyScanWorker failed")
            self.error.emit(str(e))
        finally:
            self._release_db_session()


def _group_tracks_by_variant(tracks: list) -> "OrderedDict[str | None, list]":
    """Group a conflict's per-track entries by their picture hash, preserving
    first-seen order. A key of None is the "no artwork" group."""
    groups: OrderedDict = OrderedDict()
    for entry in tracks:
        groups.setdefault(entry["hash"], []).append(entry)
    return groups


class ArtworkConsistencyDialog(QDialog):
    def __init__(self, controller, parent=None, initial_conflicts: list | None = None):
        super().__init__(parent)
        self.controller = controller
        self._scan_worker: ArtworkConsistencyScanWorker | None = None
        self._embed_worker: CoverEmbedWorker | None = None
        self._scan_start_time: float | None = None
        self._conflicts: list = []
        # Import-reconciliation mode: the caller (ImportDialog) has already
        # computed the conflicts for the albums an import just touched, so
        # the library-scan controls are hidden and the tree is populated
        # directly. `None` => the normal Tools-menu full-library scan.
        self._import_mode = initial_conflicts is not None
        self.setWindowTitle(
            "Reconcile Imported Artwork" if self._import_mode else "Artwork Conflicts"
        )
        self.setMinimumSize(720, 520)
        self._build_ui()
        if self._import_mode:
            self._conflicts = list(initial_conflicts)
            self._populate_tree(self._conflicts)

    # ------------------------------------------------------------------ UI

    def _build_ui(self):
        layout = QVBoxLayout(self)

        if self._import_mode:
            intro_text = (
                "The import just added tracks to these albums, and their "
                "embedded artwork disagrees - some tracks carry a different "
                "picture than others, or have one where others have none. "
                "Album art is read from a single track per album, so a "
                "disagreement can show the wrong image for the whole album. "
                "Pick a version below to re-embed it into every track of "
                "that album."
            )
        else:
            intro_text = (
                "Scan every album for tracks that disagree on their embedded "
                "artwork - some tracks carrying a different picture than "
                "others, or having one where others have none. The thumbnail "
                "cache trusts a single track per album, so a disagreement can "
                "cache the wrong image for the whole album. Pick a version "
                "below to re-embed it into every track of that album."
            )
        intro = QLabel(intro_text)
        intro.setWordWrap(True)
        layout.addWidget(intro)

        scan_row = QHBoxLayout()
        self._scan_btn = QPushButton("Scan Library")
        self._scan_btn.clicked.connect(self._start_scan)
        scan_row.addWidget(self._scan_btn)
        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.clicked.connect(self._cancel_scan)
        self._cancel_btn.setVisible(False)
        scan_row.addWidget(self._cancel_btn)
        self._status_label = QLabel("")
        scan_row.addWidget(self._status_label)
        scan_row.addStretch()
        layout.addLayout(scan_row)

        self._progress_bar = QProgressBar()
        self._progress_bar.setVisible(False)
        layout.addWidget(self._progress_bar)

        if self._import_mode:
            # Nothing to scan - the conflicts were handed in by the caller.
            for widget in (self._scan_btn, self._cancel_btn, self._status_label):
                widget.setVisible(False)

        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["Album / artwork version", "Tracks"])
        self._tree.setColumnWidth(0, 480)
        self._tree.setUniformRowHeights(False)
        layout.addWidget(self._tree)

        button_box = QDialogButtonBox(QDialogButtonBox.Close)
        button_box.rejected.connect(self.reject)
        button_box.accepted.connect(self.accept)
        layout.addWidget(button_box)

    # ------------------------------------------------------------------ scan

    def _start_scan(self):
        if self._scan_worker is not None:
            return
        self._tree.clear()
        self._conflicts = []
        self._scan_btn.setEnabled(False)
        self._cancel_btn.setVisible(True)
        self._cancel_btn.setEnabled(True)
        self._progress_bar.setVisible(True)
        self._progress_bar.setRange(0, 0)
        self._progress_bar.setFormat("Scanning library…")
        self._status_label.setText("")
        self._scan_start_time = time.monotonic()
        show_status_message(self, "Scanning library for artwork conflicts…", duration=0)

        self._scan_worker = ArtworkConsistencyScanWorker(self.controller)
        self._scan_worker.progress.connect(self._on_progress)
        self._scan_worker.finished.connect(self._on_scan_finished)
        self._scan_worker.error.connect(self._on_scan_error)
        self._scan_worker.start()

    def _cancel_scan(self):
        if self._scan_worker is not None:
            self._scan_worker.request_cancel()
            self._cancel_btn.setEnabled(False)

    def _on_progress(self, scanned: int, total: int):
        self._progress_bar.setRange(0, max(total, 1))
        self._progress_bar.setValue(scanned)
        eta = self._estimate_remaining(scanned, total)
        if eta:
            self._progress_bar.setFormat(f"%p%  ({scanned:,}/{total:,} albums, ETA: {eta})")
        else:
            self._progress_bar.setFormat(f"%p%  ({scanned:,}/{total:,} albums)")

    def _estimate_remaining(self, current: int, total: int) -> str | None:
        """Human-readable ETA for the scan, or None if there isn't enough
        data yet. Same elapsed/rate estimate as DuplicateFinderDialog."""
        if not self._scan_start_time or current <= 0 or current >= total:
            return None
        elapsed = time.monotonic() - self._scan_start_time
        if elapsed < 1.0:
            return None
        rate = current / elapsed
        if rate <= 0:
            return None
        remaining = int((total - current) / rate)
        if remaining < 60:
            return f"{remaining}s"
        minutes, seconds = divmod(remaining, 60)
        return f"{minutes}m {seconds:02d}s"

    def _on_scan_finished(self, conflicts: list):
        self._scan_worker.wait()
        self._scan_worker = None
        self._reset_scan_controls()
        self._conflicts = conflicts
        self._populate_tree(conflicts)
        n_albums = len({(c["album_id"], c["role"]) for c in conflicts})
        if conflicts:
            msg = f"{n_albums} album/role conflict(s) found."
        else:
            msg = "No artwork conflicts found."
        self._status_label.setText(msg)
        show_status_message(self, msg)

    def _on_scan_error(self, message: str):
        if self._scan_worker is not None:
            self._scan_worker.wait()
        self._scan_worker = None
        self._reset_scan_controls()
        logger.error(f"Artwork consistency scan failed: {message}")
        self._status_label.setText("Scan failed.")
        show_status_message(self, f"Artwork consistency scan failed: {message}")

    def _reset_scan_controls(self):
        self._scan_btn.setEnabled(True)
        self._cancel_btn.setVisible(False)
        self._cancel_btn.setEnabled(True)
        self._progress_bar.setVisible(False)

    # ------------------------------------------------------------------ tree

    def _populate_tree(self, conflicts: list):
        self._tree.clear()
        for conflict in conflicts:
            groups = _group_tracks_by_variant(conflict["tracks"])
            n_tracks = len(conflict["tracks"])
            album_label = conflict["album_name"] or f"Album {conflict['album_id']}"
            top = QTreeWidgetItem(
                self._tree,
                [
                    f"{album_label}  ·  {conflict['role']}",
                    f"{n_tracks} tracks, {len(groups)} version(s)",
                ],
            )
            top.setData(0, Qt.UserRole, conflict)
            top.setExpanded(True)
            for variant_hash, entries in groups.items():
                self._add_variant_row(top, conflict, variant_hash, entries)
        self._tree.resizeColumnToContents(1)

    def _add_variant_row(self, parent_item, conflict, variant_hash, entries):
        role = conflict["role"]
        count = len(entries)
        row = QTreeWidgetItem(parent_item, ["", f"{count} track(s)"])

        cell = QWidget()
        cell_layout = QHBoxLayout(cell)
        cell_layout.setContentsMargins(4, 4, 4, 4)
        cell_layout.setSpacing(8)

        thumb = QLabel()
        thumb.setFixedSize(_THUMB_PX, _THUMB_PX)
        thumb.setAlignment(Qt.AlignCenter)

        if variant_hash is None:
            thumb.setText("none")
            desc = "No artwork"
        else:
            pixmap, dims = self._load_variant_pixmap(entries, role)
            if pixmap is not None and not pixmap.isNull():
                thumb.setPixmap(
                    pixmap.scaled(_THUMB_PX, _THUMB_PX, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                )
            else:
                thumb.setText("?")
            size_kb = None
            sample_path = entries[0].get("track_path")
            if sample_path:
                pic_bytes = self._read_role_bytes(sample_path, role)
                if pic_bytes is not None:
                    size_kb = len(pic_bytes) / 1024
            parts = []
            if dims and dims[0]:
                parts.append(f"{dims[0]} × {dims[1]} px")  # noqa: RUF001
            if size_kb is not None:
                parts.append(f"{size_kb:,.0f} KB")
            desc = "  ·  ".join(parts) if parts else "embedded image"

        cell_layout.addWidget(thumb)
        cell_layout.addWidget(QLabel(desc))
        cell_layout.addStretch()

        use_btn = QPushButton("Use for all tracks")
        use_btn.clicked.connect(
            lambda _checked=False, c=conflict, h=variant_hash, e=entries: self._resolve(c, h, e)
        )
        cell_layout.addWidget(use_btn)

        self._tree.setItemWidget(row, 0, cell)

    def _load_variant_pixmap(self, entries, role):
        for entry in entries:
            path = entry.get("track_path")
            if not path:
                continue
            pic_bytes = self._read_role_bytes(path, role)
            if pic_bytes is None:
                continue
            pixmap = QPixmap()
            if pixmap.loadFromData(pic_bytes):
                dims = entry.get("dimensions") or {}
                return pixmap, (dims.get("width"), dims.get("height"))
        return None, None

    @staticmethod
    def _read_role_bytes(path: str, role: str) -> bytes | None:
        try:
            embedded = ArtworkExtractor().extract_artwork_by_role(path, Path(path).suffix.lower())
        except Exception:
            logger.exception(f"ArtworkConsistencyDialog: cannot read art from {path}")
            return None
        picture = embedded.get(role)
        return picture["data"] if picture else None

    # ------------------------------------------------------------------ resolve

    def _resolve(self, conflict, variant_hash, entries):
        if self._embed_worker is not None and self._embed_worker.isRunning():
            return

        album_id = conflict["album_id"]
        role = conflict["role"]
        album_name = conflict["album_name"] or f"Album {album_id}"

        if variant_hash is None:
            prompt = f"Remove all {role} artwork from every track of “{album_name}”?"
            image_bytes = None
        else:
            prompt = (
                f"Embed this {role} image into every track of "
                f"“{album_name}”, replacing what the other tracks currently "
                f"have?"
            )
            image_bytes = self._pick_source_bytes(entries, role)
            if image_bytes is None:
                QMessageBox.warning(
                    self,
                    "Cannot Read Image",
                    "None of the tracks in this version could be read for their embedded image.",
                )
                return

        if (
            QMessageBox.question(
                self,
                "Apply to All Tracks",
                prompt,
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            != QMessageBox.Yes
        ):
            return

        album = self.controller.get.get_entity_object("Album", album_id=album_id)
        if album is None:
            QMessageBox.warning(
                self, "Album Not Found", f"Album {album_id} is no longer in the library."
            )
            return

        cache = get_artwork_cache()
        tracks = list(getattr(album, "tracks", None) or [])
        self._tree.setEnabled(False)
        show_status_message(self, f"Reconciling artwork for “{album_name}”…", duration=0)

        worker = CoverEmbedWorker(
            album, tracks, cache, MetadataWriter(self.controller), role, image_bytes
        )
        worker.completed.connect(
            lambda failed, _dims, aid=album_id, rl=role, name=album_name: self._on_resolve_done(
                aid, rl, name, failed
            )
        )
        worker.error.connect(lambda msg, name=album_name: self._on_resolve_error(name, msg))
        worker.finished.connect(worker.deleteLater)
        self._embed_worker = worker
        worker.start()

    def _pick_source_bytes(self, entries, role) -> bytes | None:
        for entry in entries:
            path = entry.get("track_path")
            if not path:
                continue
            data = self._read_role_bytes(path, role)
            if data is not None:
                return data
        return None

    def _on_resolve_done(self, album_id, role, album_name, failed):
        self._embed_worker = None
        self._tree.setEnabled(True)
        cache = get_artwork_cache()
        if cache is not None:
            cache.invalidate(album_id)

        if failed:
            preview = "\n".join(failed[:10])
            if len(failed) > 10:
                preview += f"\n… and {len(failed) - 10} more"
            QMessageBox.warning(
                self,
                "Some Files Not Updated",
                f"Artwork was applied, but {len(failed)} track file(s) could "
                f"not be written:\n\n{preview}",
            )

        self._remove_conflict_row(album_id, role)
        msg = f"Artwork reconciled for “{album_name}” ({role})."
        self._status_label.setText(msg)
        show_status_message(self, msg)

    def _on_resolve_error(self, album_name, message):
        self._embed_worker = None
        self._tree.setEnabled(True)
        logger.error(f"Artwork reconcile failed for {album_name}: {message}")
        QMessageBox.critical(self, "Error", f"Could not reconcile artwork:\n{message}")
        show_status_message(self, f"Artwork reconcile failed: {message}")

    def _remove_conflict_row(self, album_id, role):
        self._conflicts = [
            c for c in self._conflicts if not (c["album_id"] == album_id and c["role"] == role)
        ]
        for i in range(self._tree.topLevelItemCount()):
            item = self._tree.topLevelItem(i)
            conflict = item.data(0, Qt.UserRole)
            if conflict and conflict["album_id"] == album_id and conflict["role"] == role:
                self._tree.takeTopLevelItem(i)
                return

    # ------------------------------------------------------------------ lifecycle

    def closeEvent(self, event):
        if self._scan_worker is not None:
            self._scan_worker.request_cancel()
            self._scan_worker.wait(5000)
            self._scan_worker = None
        if self._embed_worker is not None and self._embed_worker.isRunning():
            self._embed_worker.request_cancel()
            self._embed_worker.wait(5000)
        super().closeEvent(event)
