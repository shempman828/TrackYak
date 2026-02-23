from typing import List, Optional

import requests

# Qt imports
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)


class WikipediaImageDialog(QDialog):
    """Dialog allowing selection and preview of Wikipedia images."""

    def __init__(self, images: List[str], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.images = images
        self.selected_image: Optional[str] = None
        self._init_ui()

    def _init_ui(self) -> None:
        """Set up dialog layout and widgets."""
        self.setWindowTitle("Select Wikipedia Image")
        self.resize(700, 500)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Select an image:"))

        # Image list
        self.image_list = QListWidget()
        self.image_list.setAlternatingRowColors(True)
        self.image_list.currentItemChanged.connect(self._on_selection_changed)
        layout.addWidget(self.image_list)

        # Preview section
        preview_layout = QHBoxLayout()

        self.preview_label = QLabel("Select an image to preview")
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setFixedSize(200, 200)
        preview_layout.addWidget(self.preview_label)

        self.info_label = QLabel("Select an image from the list to see its preview")
        self.info_label.setWordWrap(True)
        preview_layout.addWidget(self.info_label)

        layout.addLayout(preview_layout)

        # Buttons
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

        self._load_image_urls()

    def _load_image_urls(self) -> None:
        """Populate the list with image URLs and filenames."""
        from pathlib import Path
        from urllib.parse import urlparse

        for i, img_url in enumerate(self.images, start=1):
            filename = Path(urlparse(img_url).path).name
            item = QListWidgetItem(f"{i}. {filename}")
            item.setData(Qt.UserRole, img_url)
            item.setToolTip(img_url)
            self.image_list.addItem(item)

    def _on_selection_changed(
        self, current: QListWidgetItem, _: QListWidgetItem
    ) -> None:
        """Load a preview when the selection changes."""
        if not current:
            self.preview_label.setText("Select an image to preview")
            self.info_label.setText("Select an image from the list")
            return

        url = current.data(Qt.UserRole)
        self.info_label.setText(f"URL: {url}\n\nClick OK to use this image.")
        self.preview_label.setText("Loading preview...")
        QTimer.singleShot(100, lambda: self._load_preview(url))

    def _load_preview(self, url: str) -> None:
        """Fetch and display an image preview."""
        try:
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/91.0.4472.124 Safari/537.36"
                )
            }
            response = requests.get(url, timeout=10, headers=headers)
            response.raise_for_status()

            pixmap = QPixmap()
            if pixmap.loadFromData(response.content):
                scaled = pixmap.scaled(
                    200, 200, Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
                self.preview_label.setPixmap(scaled)
            else:
                self.preview_label.setText("Invalid image format")

        except requests.exceptions.HTTPError as e:
            code = e.response.status_code if e.response else "?"
            if code == 403:
                self.preview_label.setText("Preview not available")
                self.info_label.setText(
                    f"URL: {url}\n\nWikipedia may block direct access.\nImage will still download when selected."
                )
            else:
                self.preview_label.setText(f"HTTP Error: {code}")
        except Exception as e:
            self.preview_label.setText("Error loading preview")
            self.info_label.setText(
                f"URL: {url}\n\nError: {e}\nImage will still download when selected."
            )

    def get_selected_image(self) -> Optional[str]:
        """Return the currently selected image URL, if any."""
        current = self.image_list.currentItem()
        return current.data(Qt.UserRole) if current else None
