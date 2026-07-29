from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QDialog, QFileDialog, QMessageBox

from src.core.logger_config import logger
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
                display.setPixmap(
                    px.scaled(250, 250, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                )
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
            label.setPixmap(
                px.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )
        else:
            label.setText("Invalid Image")

    # =========================================================================
    # Cover art — picking & saving
    # =========================================================================

    def change_front_cover(self):
        self._pick_cover("front")

    def change_rear_cover(self):
        self._pick_cover("rear")

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

            failed = self._embed_cover_to_tracks(cover_type, image_bytes)
            self._warn_if_embed_failures(cover_type, failed)

            cache = get_artwork_cache()
            if cache:
                cache.store(self.album, cover_type, image_bytes)

            # Update the Artwork tab preview
            display = getattr(self, f"{cover_type}_cover_display", None)
            path_label = getattr(self, f"{cover_type}_path_label", None)
            if display:
                self._load_image_to_label(image_bytes, display, 250)
            if path_label:
                dims = cache.get_dimensions(self.album, cover_type) if cache else None
                info_parts = ["Embedded in track file(s)"]
                if dims:
                    info_parts.append(f"{dims[0]} × {dims[1]} px")
                path_label.setText("  |  ".join(info_parts))

            # IMPORTANT: always refresh the header thumbnail when front cover changes
            if cover_type == "front":
                self._load_album_cover()

        except Exception as e:
            logger.error(f"Error saving {cover_type} cover: {e}")
            QMessageBox.critical(self, "Error", f"Could not save cover art:\n{e}")

    def _embed_cover_to_tracks(self, cover_type: str, image_bytes):
        """Embed (image_bytes given) or strip (image_bytes=None) the given
        cover role into every FLAC/MP3 track of this album. Returns the
        list of track file paths that failed, so callers can surface one
        warning."""
        failed = []
        for track in getattr(self.album, "tracks", None) or []:
            file_path = getattr(track, "track_file_path", None)
            if not file_path or Path(file_path).suffix.lower() not in self._EMBEDDABLE_EXTENSIONS:
                continue
            try:
                success = self._metadata_writer.write_artwork_to_file(
                    file_path, cover_type, image_bytes
                )
            except Exception as e:
                logger.error(f"Error embedding {cover_type} cover into {file_path}: {e}")
                success = False
            if not success:
                failed.append(file_path)
        return failed

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
        failed = self._embed_cover_to_tracks(cover_type, None)
        self._warn_if_embed_failures(cover_type, failed)

        cache = get_artwork_cache()
        if cache:
            cache.store(self.album, cover_type, None)

        display = getattr(self, f"{cover_type}_cover_display", None)
        path_label = getattr(self, f"{cover_type}_path_label", None)
        if display:
            display.clear()
            display.setText(f"No {cover_type.title()} Cover")
        if path_label:
            path_label.setText("")

        if cover_type == "front":
            self.cover_label.setText("No Cover\nImage")

    # =========================================================================
    # Wikipedia search
    # =========================================================================

    def _search_wikipedia(self):
        try:
            from src.wikipedia_seach import download_wikipedia_image, search_wikipedia
        except ImportError as e:
            QMessageBox.critical(
                self, "Import Error", f"Wikipedia module not found: {e}"
            )
            return

        query = self.album.album_name or ""
        title, summary, _full, link, images = search_wikipedia(query, self)

        if not title:
            return

        try:
            from src.album_wikipedia import AlbumWikipediaImportDialog
        except ImportError as e:
            QMessageBox.critical(self, "Import Error", f"Import dialog not found: {e}")
            return

        dlg = AlbumWikipediaImportDialog(title, summary, link, images, parent=self)
        if dlg.exec() != QDialog.Accepted:
            return

        selected = dlg.get_selected_imports()

        if selected.get("description"):
            self._set_desc_widget(selected["description"])

        if selected.get("link"):
            w = self.field_widgets.get("album_wikipedia_link")
            if w is not None and hasattr(w, "setText"):
                w.setText(selected["link"])
            # Update the album object so _rebuild_link_buttons picks up the new URL
            self.album.album_wikipedia_link = selected["link"]
            self._rebuild_link_buttons()

        role_to_cover = {
            "Front Cover": "front",
            "Rear Cover": "rear",
            "Liner Art": "liner",
        }
        for img_info in selected.get("images", []):
            url = img_info["url"]
            role = img_info["role"]
            cover_type = role_to_cover.get(role)
            if not cover_type:
                continue
            self._save_wikipedia_image(url, cover_type, download_wikipedia_image)

    def _set_desc_widget(self, text: str):
        if self.desc_widget is None:
            return
        if hasattr(self.desc_widget, "setPlainText"):
            self.desc_widget.setPlainText(text)
        elif hasattr(self.desc_widget, "setText"):
            self.desc_widget.setText(text)

    def _save_wikipedia_image(self, url: str, cover_type: str, download_fn):
        """Download url and save it as the given cover type."""
        image_bytes = download_fn(url)
        if not image_bytes:
            QMessageBox.warning(
                self,
                "Download Failed",
                f"Could not download image for {cover_type} cover:\n{url}",
            )
            return

        failed = self._embed_cover_to_tracks(cover_type, image_bytes)
        self._warn_if_embed_failures(cover_type, failed)

        cache = get_artwork_cache()
        if cache:
            cache.store(self.album, cover_type, image_bytes)

        display = getattr(self, f"{cover_type}_cover_display", None)
        path_label = getattr(self, f"{cover_type}_path_label", None)
        if display:
            self._load_image_to_label(image_bytes, display, 250)
        if path_label:
            dims = cache.get_dimensions(self.album, cover_type) if cache else None
            info_parts = ["Embedded in track file(s)"]
            if dims:
                info_parts.append(f"{dims[0]} × {dims[1]} px")
            path_label.setText("  |  ".join(info_parts))

        if cover_type == "front":
            self._load_album_cover()
