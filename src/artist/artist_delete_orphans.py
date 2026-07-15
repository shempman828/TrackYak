"""Dialog for reviewing and bulk-deleting artists with no links to anything else."""

from typing import Any, List

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)


class OrphanArtistDialog(QDialog):
    """Shows every artist that has no roles, influences, memberships, places,
    or awards, and lets the user deselect any they'd rather keep before the
    remaining ones are permanently deleted."""

    def __init__(self, orphans: List[Any], parent=None):
        super().__init__(parent)
        self.orphans = sorted(orphans, key=lambda a: a.artist_name.lower())
        self.setWindowTitle("Delete Unused Artists")
        self.setMinimumSize(500, 500)
        self._checkboxes = []
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        lbl_instructions = QLabel(
            f"Found {len(self.orphans)} artist(s) with no roles, influences, "
            "group memberships, places, or awards. All are selected for deletion "
            "by default — uncheck any you want to keep."
        )
        lbl_instructions.setWordWrap(True)
        layout.addWidget(lbl_instructions)

        toggle_row = QHBoxLayout()
        select_all_btn = QPushButton("Select All")
        select_all_btn.clicked.connect(lambda: self._set_all_checked(True))
        toggle_row.addWidget(select_all_btn)

        deselect_all_btn = QPushButton("Deselect All")
        deselect_all_btn.clicked.connect(lambda: self._set_all_checked(False))
        toggle_row.addWidget(deselect_all_btn)
        toggle_row.addStretch()
        layout.addLayout(toggle_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        list_layout = QVBoxLayout(content)
        list_layout.setSpacing(2)

        for artist in self.orphans:
            checkbox = QCheckBox(artist.artist_name or "(no name)")
            checkbox.setChecked(True)
            checkbox.artist_id = artist.artist_id
            self._checkboxes.append(checkbox)
            list_layout.addWidget(checkbox)

        list_layout.addStretch()
        scroll.setWidget(content)
        layout.addWidget(scroll, stretch=1)

        btn_box = QHBoxLayout()
        btn_box.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_box.addWidget(cancel_btn)

        self.delete_btn = QPushButton("Delete Selected")
        self.delete_btn.setDefault(True)
        self.delete_btn.clicked.connect(self.accept)
        btn_box.addWidget(self.delete_btn)

        layout.addLayout(btn_box)

    def _set_all_checked(self, checked: bool):
        for checkbox in self._checkboxes:
            checkbox.setChecked(checked)

    def selected_artist_ids(self) -> List[int]:
        """Return the artist_ids of every still-checked row."""
        return [cb.artist_id for cb in self._checkboxes if cb.isChecked()]
