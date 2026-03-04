"""
album_wikipedia.py

A single comprehensive dialog for importing data from a Wikipedia article into
an album.  Everything is shown at once so the user can approve or reject each
piece individually without having to repeat the search.

┌──────────────────────────────────────────────────────────────┐
│  Wikipedia Import: <Article Title>                           │
├──────────────┬───────────────────────────────────────────────┤
│  Text Data   │  Images                                       │
│  ──────────  │  ─────────────────────────────────────────── │
│  [x] Desc    │  [img1 thumb]  [img2 thumb]  [img3 thumb] …  │
│  [x] WP link │  Assign: [Front ▾]  Assign: [Skip ▾]  …     │
├──────────────┴───────────────────────────────────────────────┤
│                                        [Cancel]  [Import ✓]  │
└──────────────────────────────────────────────────────────────┘
"""

from pathlib import Path
from typing import List

import requests
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.logger_config import logger

# Options shown in each image's assignment dropdown
IMAGE_ROLES = ["Skip", "Front Cover", "Rear Cover", "Liner Art"]

# Map role name → album attribute name
ROLE_TO_ATTR = {
    "Front Cover": "front_cover_path",
    "Rear Cover": "rear_cover_path",
    "Liner Art": "album_liner_path",
}


# ─────────────────────────────────────────────────────────────────────────────
# Small widget: one image card (thumbnail + assignment combo)
# ─────────────────────────────────────────────────────────────────────────────


class _ImageCard(QFrame):
    """
    Displays a single Wikipedia image with:
      • a thumbnail preview (loaded lazily)
      • a filename label
      • a dropdown to assign it as Front Cover / Rear Cover / Liner / Skip
    """

    THUMB_SIZE = 140

    def __init__(self, image_url: str, index: int, parent=None):
        super().__init__(parent)
        self.image_url = image_url
        self.setFrameStyle(QFrame.StyledPanel)
        self.setFixedWidth(self.THUMB_SIZE + 20)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        # Thumbnail label
        self.thumb = QLabel("Loading…")
        self.thumb.setAlignment(Qt.AlignCenter)
        self.thumb.setFixedSize(self.THUMB_SIZE, self.THUMB_SIZE)
        self.thumb.setStyleSheet("border: 1px solid #444; background: #1e1e1e;")
        self.thumb.setWordWrap(True)
        layout.addWidget(self.thumb)

        # Filename (truncated)
        filename = Path(image_url.split("?")[0]).name
        short_name = filename[:22] + "…" if len(filename) > 22 else filename
        name_lbl = QLabel(short_name)
        name_lbl.setAlignment(Qt.AlignCenter)
        name_lbl.setToolTip(image_url)
        name_lbl.setStyleSheet("font-size: 9px; color: #aaa;")
        name_lbl.setWordWrap(True)
        layout.addWidget(name_lbl)

        # Assignment dropdown
        self.combo = QComboBox()
        self.combo.addItems(IMAGE_ROLES)
        layout.addWidget(self.combo)

        # Load the thumbnail after the widget is shown
        QTimer.singleShot(index * 80, self._load_thumbnail)

    def _load_thumbnail(self):
        try:
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/91.0.4472.124 Safari/537.36"
                )
            }
            resp = requests.get(self.image_url, timeout=10, headers=headers)
            resp.raise_for_status()
            px = QPixmap()
            if px.loadFromData(resp.content):
                self.thumb.setPixmap(
                    px.scaled(
                        self.THUMB_SIZE,
                        self.THUMB_SIZE,
                        Qt.KeepAspectRatio,
                        Qt.SmoothTransformation,
                    )
                )
            else:
                self.thumb.setText("(bad image)")
        except Exception as e:
            logger.debug(f"Thumbnail load failed for {self.image_url}: {e}")
            self.thumb.setText("No preview")

    @property
    def assignment(self) -> str:
        """Return the currently selected role ('Skip', 'Front Cover', etc.)."""
        return self.combo.currentText()


# ─────────────────────────────────────────────────────────────────────────────
# Main import dialog
# ─────────────────────────────────────────────────────────────────────────────


class AlbumWikipediaImportDialog(QDialog):
    """
    Shows all importable Wikipedia data at once.

    After exec() returns Accepted, call get_selected_imports() to retrieve
    what the user approved.

    Parameters
    ----------
    title    : Wikipedia article title
    summary  : Article summary / lead paragraph
    link     : Full Wikipedia URL
    images   : List of image URLs found on the page
    parent   : Parent QWidget
    """

    def __init__(
        self,
        title: str,
        summary: str,
        link: str,
        images: List[str],
        parent=None,
    ):
        super().__init__(parent)
        self.wiki_title = title
        self.summary = summary
        self.link = link
        self.images = images
        self._image_cards: List[_ImageCard] = []

        self.setWindowTitle(f"Import from Wikipedia — {title}")
        self.setMinimumSize(820, 560)
        self.resize(1000, 640)

        self._build_ui()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(10)

        # Article title header
        header = QLabel(f"<b>Wikipedia article:</b> {self.wiki_title}")
        header.setStyleSheet("font-size: 13px;")
        root.addWidget(header)

        # Main splitter: text on left, images on right
        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)

        splitter.addWidget(self._build_text_panel())
        splitter.addWidget(self._build_image_panel())
        splitter.setSizes([340, 640])

        root.addWidget(splitter, 1)

        # Buttons
        btn_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btn_box.button(QDialogButtonBox.Ok).setText("Import Selected ✓")
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        root.addWidget(btn_box)

    def _build_text_panel(self) -> QWidget:
        """Left panel: description and Wikipedia link checkboxes."""
        group = QGroupBox("Text Data")
        layout = QVBoxLayout(group)
        layout.setSpacing(10)

        # Description checkbox
        self.cb_description = QCheckBox("Import description / summary")
        self.cb_description.setChecked(True)
        layout.addWidget(self.cb_description)

        # Description preview (read-only)
        self.desc_preview = QTextEdit()
        self.desc_preview.setReadOnly(False)  # user can trim it before importing
        self.desc_preview.setPlainText(self.summary)
        self.desc_preview.setMinimumHeight(180)
        self.desc_preview.setToolTip(
            "You can edit this text before importing. "
            "Uncheck the box above to skip it entirely."
        )
        layout.addWidget(self.desc_preview)

        # Wikipedia link checkbox
        self.cb_link = QCheckBox("Import Wikipedia link")
        self.cb_link.setChecked(True)
        layout.addWidget(self.cb_link)

        link_lbl = QLabel(self.link)
        link_lbl.setStyleSheet("color: #7ab4f5; font-size: 10px;")
        link_lbl.setWordWrap(True)
        link_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(link_lbl)

        layout.addStretch()
        return group

    def _build_image_panel(self) -> QWidget:
        """Right panel: scrollable grid of image cards."""
        group = QGroupBox(
            f"Images ({len(self.images)} found)  —  assign each one or leave as Skip"
        )
        outer = QVBoxLayout(group)

        if not self.images:
            outer.addWidget(QLabel("No images found on this Wikipedia page."))
            return group

        # Scroll area containing the grid
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        grid_widget = QWidget()
        self._grid = QGridLayout(grid_widget)
        self._grid.setSpacing(10)
        self._grid.setContentsMargins(6, 6, 6, 6)

        # Fill in cards — 5 columns wide
        COLS = 5
        for i, url in enumerate(self.images):
            card = _ImageCard(url, i)
            self._image_cards.append(card)
            self._grid.addWidget(card, i // COLS, i % COLS)

        scroll.setWidget(grid_widget)
        outer.addWidget(scroll)

        return group

    # ── Result extraction ─────────────────────────────────────────────────────

    def get_selected_imports(self) -> dict:
        """
        Call this after exec() == QDialog.Accepted.

        Returns a dict with these keys (value is None if the user skipped it):
          'description' : str or None
          'link'        : str or None
          'images'      : list of {'url': str, 'role': str}
                          role is 'Front Cover' | 'Rear Cover' | 'Liner Art'
        """
        result = {
            "description": None,
            "link": None,
            "images": [],
        }

        if self.cb_description.isChecked():
            text = self.desc_preview.toPlainText().strip()
            if text:
                result["description"] = text

        if self.cb_link.isChecked() and self.link:
            result["link"] = self.link

        for card in self._image_cards:
            role = card.assignment
            if role != "Skip":
                result["images"].append({"url": card.image_url, "role": role})

        return result
