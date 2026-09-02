from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from src.album.album_art_worker import CoverEmbedWorker
from src.foundation.logger_config import logger
from src.image.artwork_cache import get_artwork_cache
from src.metadata.metadata_artwork import ArtworkExtractor


class AlbumCoverArtMixin:
    """
    Cover art loading/picking/saving/clearing, plus the Wikipedia image
    import path that feeds into the same save routine, for AlbumEditor.

    Expects the host class to provide: self.album, self.controller,
    self._config, self._metadata_writer, self.cover_label, self.desc_widget,
    self.field_widgets, and to be a QWidget subclass.
    """

    _EMBEDDABLE_EXTENSIONS = ArtworkExtractor.SUPPORTED_EXTENSIONS

    # =========================================================================
    # Cover art — loading helpers
    # =========================================================================

    def _load_album_cover(self):
        """Load the front cover thumbnail into the header label."""
        cache = get_artwork_cache()
        is_explicit = bool(getattr(self.album, "art_is_explicit", False))
        px = cache.get_pixmap(self.album, "front", is_explicit) if cache else None
        if px and not px.isNull():
            size = getattr(self, "_cover_size", 150)
            self.cover_label.setPixmap(
                px.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )
            return
        self.cover_label.setText("No Cover\nImage")

    def _load_artwork_previews(self):
        """Populate all three artwork displays from the current album object."""
        cache = get_artwork_cache()
        is_explicit = bool(getattr(self.album, "art_is_explicit", False))
        for cover_type in ("front", "rear", "liner"):
            display = getattr(self, f"{cover_type}_cover_display", None)
            path_label = getattr(self, f"{cover_type}_path_label", None)
            if display is None:
                continue
            px = cache.get_pixmap(self.album, cover_type, is_explicit) if cache else None
            if px and not px.isNull():
                display.setPixmap(px.scaled(250, 250, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                if path_label:
                    dims = cache.get_dimensions(self.album, cover_type) if cache else None
                    info_parts = ["Embedded in track file(s)"]
                    if dims:
                        info_parts.append(f"{dims[0]} × {dims[1]} px")
                    path_label.setText("  |  ".join(info_parts))
                continue
            display.setText(f"No {cover_type.title()} Cover")
            if path_label:
                path_label.setText("")

    def _load_image_to_label(self, source, label, size=250):
        """Generic helper: load a file path or bytes into a QLabel."""
        px = QPixmap()
        if isinstance(source, bytes):
            px.loadFromData(source)
        else:
            px.load(str(source))

        if not px.isNull():
            label.setPixmap(px.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            label.setText("Invalid Image")

    # =========================================================================
    # Cover art — picking & saving
    # =========================================================================

    def _pick_cover(self, cover_type: str):
        """Open a file dialog and embed the picked image into every track."""
        try:
            last_dir = self._config.get_last_art_dir()
        except AttributeError:
            last_dir = str(Path.home())

        path, _ = QFileDialog.getOpenFileName(
            self,
            f"Select {cover_type.title()} Cover",
            last_dir,
            "Images (*.png *.jpg *.jpeg *.webp *.bmp *.gif)",
        )
        if not path:
            return

        try:
            self._config.set_last_art_dir(str(Path(path).parent))
            self._config.save()
        except AttributeError:
            pass

        try:
            image_bytes = Path(path).read_bytes()
        except OSError as e:
            logger.error(f"Error reading {cover_type} cover file {path}: {e}")
            QMessageBox.critical(self, "Error", f"Could not read image file:\n{e}")
            return

        self._start_cover_embed(cover_type, image_bytes)

    # =========================================================================
    # Cover art — background embed
    # =========================================================================

    def _start_cover_embed(self, cover_type: str, image_bytes):
        """Embed `image_bytes` (or strip, when None) into every track and
        warm the cache on a background thread, so the editor stays
        responsive while mutagen rewrites a dozen FLAC files. The Artwork
        tab / header preview is refreshed in _on_cover_embed_done."""
        worker = getattr(self, "_cover_embed_worker", None)
        if worker is not None and worker.isRunning():
            return  # an embed/clear is already in flight; controls are disabled

        cache = get_artwork_cache()
        tracks = list(getattr(self.album, "tracks", None) or [])

        self._set_cover_controls_enabled(False)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        self._cover_cursor_pushed = True
        path_label = getattr(self, f"{cover_type}_path_label", None)
        if path_label:
            verb = "Removing" if image_bytes is None else "Embedding"
            path_label.setText(f"{verb} artwork in track file(s)…")

        worker = CoverEmbedWorker(
            self.album, tracks, cache, self._metadata_writer, cover_type, image_bytes
        )
        worker.completed.connect(
            lambda failed, dims, ct=cover_type, ib=image_bytes: self._on_cover_embed_done(
                ct, ib, failed, dims
            )
        )
        worker.error.connect(lambda msg, ct=cover_type: self._on_cover_embed_error(ct, msg))
        worker.finished.connect(worker.deleteLater)
        self._cover_embed_worker = worker
        worker.start()

    def _finish_cover_embed(self):
        if getattr(self, "_cover_cursor_pushed", False):
            QApplication.restoreOverrideCursor()
            self._cover_cursor_pushed = False
        self._set_cover_controls_enabled(True)
        self._cover_embed_worker = None

    def _on_cover_embed_done(self, cover_type, image_bytes, failed, dims):
        self._finish_cover_embed()
        self._warn_if_embed_failures(cover_type, failed)

        display = getattr(self, f"{cover_type}_cover_display", None)
        path_label = getattr(self, f"{cover_type}_path_label", None)

        if image_bytes is None:
            if display:
                display.clear()
                display.setText(f"No {cover_type.title()} Cover")
            if path_label:
                path_label.setText("")
            if cover_type == "front":
                self.cover_label.setText("No Cover\nImage")
            return

        if display:
            self._load_image_to_label(image_bytes, display, 250)
        if path_label:
            info_parts = ["Embedded in track file(s)"]
            if dims:
                info_parts.append(f"{dims[0]} × {dims[1]} px")
            path_label.setText("  |  ".join(info_parts))

        # IMPORTANT: always refresh the header thumbnail when front cover changes
        if cover_type == "front":
            self._load_album_cover()

    def _on_cover_embed_error(self, cover_type, message):
        self._finish_cover_embed()
        logger.error(f"Error saving {cover_type} cover: {message}")
        QMessageBox.critical(self, "Error", f"Could not save cover art:\n{message}")
        # Reset the transient "Embedding…" label back to the real state.
        self._load_artwork_previews()

    def _set_cover_controls_enabled(self, enabled: bool):
        for btn in getattr(self, "_cover_buttons", ()):
            btn.setEnabled(enabled)
        # Also gate Save/Cancel so the dialog can't be dismissed mid-embed.
        button_box = getattr(self, "_dialog_button_box", None)
        if button_box is not None:
            button_box.setEnabled(enabled)

    def _cleanup_cover_embed(self):
        """Stop an in-flight embed before the editor is destroyed so the
        worker's signals never land on a dead dialog."""
        worker = getattr(self, "_cover_embed_worker", None)
        if worker is None:
            return
        try:
            if worker.isRunning():
                worker.request_cancel()
                worker.wait(5000)
        except RuntimeError:
            pass
        if getattr(self, "_cover_cursor_pushed", False):
            QApplication.restoreOverrideCursor()
            self._cover_cursor_pushed = False
        self._cover_embed_worker = None

    def _warn_if_embed_failures(self, cover_type: str, failed_paths):
        if not failed_paths:
            return
        preview = "\n".join(failed_paths[:10])
        if len(failed_paths) > 10:
            preview += f"\n… and {len(failed_paths) - 10} more"
        QMessageBox.warning(
            self,
            "Some Files Not Updated",
            f"The {cover_type} cover was saved, but could not be embedded into "
            f"{len(failed_paths)} track file(s):\n\n{preview}",
        )

    def _clear_cover(self, cover_type: str):
        self._start_cover_embed(cover_type, None)
