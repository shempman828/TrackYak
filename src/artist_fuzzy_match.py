from typing import Any, List

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.logger_config import logger


# Fuzzy Match Dialog
# -------------------------
class FuzzyMatchDialog(QDialog):
    """Dialog to display fuzzy matches and allow merging."""

    def __init__(self, matches: List[tuple], controller: Any, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.matches = sorted(
            matches, key=lambda x: x[2], reverse=True
        )  # x[2] is the score
        self.setWindowTitle("Merge Artists")
        self.setMinimumSize(600, 400)
        self.init_ui()

    def init_ui(self) -> None:
        layout = QVBoxLayout(self)

        # Instructions
        lbl_instructions = QLabel(
            "✔ Check pairs to merge | 🅐🅑 Select which artist to keep | ✖ Leave unchecked to ignore"
        )
        layout.addWidget(lbl_instructions)

        # Scrollable match list
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        self.match_layout = QVBoxLayout(content)
        self.match_layout.setSpacing(10)

        # Add each match pair with controls
        self.match_widgets = []
        for artist_a, artist_b, score in self.matches:
            frame = QFrame()
            frame.setFrameShape(QFrame.StyledPanel)
            hbox = QHBoxLayout(frame)

            # Checkbox to enable/disable merging for this pair
            chk_merge = QCheckBox()
            chk_merge.setChecked(False)  # Default to unchecked
            hbox.addWidget(chk_merge)

            # Radio buttons for artist selection
            radio_a = QRadioButton(artist_a.artist_name)
            radio_a.artist = artist_a
            radio_b = QRadioButton(artist_b.artist_name)
            radio_b.artist = artist_b
            radio_a.setChecked(True)  # Default to first artist

            hbox.addWidget(radio_a)
            hbox.addWidget(radio_b)
            hbox.addWidget(QLabel(f"Similarity: {score}%"))
            hbox.addStretch()

            self.match_widgets.append((chk_merge, radio_a, radio_b))
            self.match_layout.addWidget(frame)

        scroll.setWidget(content)
        layout.addWidget(scroll)

        # Action buttons
        btn_box = QHBoxLayout()
        btn_merge = QPushButton("Merge Checked Pairs")
        btn_merge.clicked.connect(self._perform_merge)
        btn_box.addWidget(btn_merge)

        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        btn_box.addWidget(btn_cancel)

        layout.addLayout(btn_box)

    def _perform_merge(self) -> None:
        """Only merge checked pairs with user-selected canonical artist."""
        success_count = 0
        for idx, (chk_merge, radio_a, radio_b) in enumerate(self.match_widgets):
            if not chk_merge.isChecked():
                continue  # Skip unchecked pairs

            # Determine which artist to keep
            if radio_a.isChecked():
                old_artist = radio_b.artist
                new_artist = radio_a.artist
            else:
                old_artist = radio_a.artist
                new_artist = radio_b.artist

            try:
                logger.info(
                    f"Merging {old_artist.artist_name} (ID: {old_artist.artist_id}) into {new_artist.artist_name} (ID: {new_artist.artist_id})"
                )
                self.controller.merge.merge_entities(
                    "Artist",
                    old_artist.artist_id,
                    new_artist.artist_id,
                )
                logger.info(
                    f"adding alias for {old_artist.artist_name} to {new_artist.artist_name}"
                )
                self.controller.add.add_entity(
                    "Alias",
                    artist_id=new_artist.artist_id,
                    alias_name=old_artist.artist_name,
                )
                success_count += 1
            except Exception as e:
                logger.error(
                    f"Failed to merge {old_artist.artist_name} → {new_artist.artist_name}: {e}"
                )

        if success_count > 0:
            QMessageBox.information(
                self,
                "Merge Complete",
                f"Successfully merged {success_count}/{len(self.match_widgets)} pairs",
            )
            self.accept()
        else:
            QMessageBox.warning(
                self,
                "No Merges",
                "No pairs were merged (none checked or errors occurred)",
            )
