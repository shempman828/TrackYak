from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.core.logger_config import logger
from src.display.display_settings import apply_scaled_style


class ClusterNamesDialog(QDialog):
    """Rename every cluster at once.

    Renaming clusters one at a time (a separate dialog per cluster, opened
    by double-clicking its legend row) meant losing sight of every other
    cluster's name/preview while editing one -- awkward for telling similar
    clusters apart or renaming several in one pass. This shows every
    cluster's color, size, and representative artists alongside its name
    field in a single scrollable list, so the whole set can be reviewed and
    renamed together.
    """

    def __init__(self, rows, parent=None):
        super().__init__(parent)
        logger.debug(f"Opening cluster names dialog ({len(rows)} clusters)")
        self.setWindowTitle("Rename Clusters")
        self.resize(420, 480)

        layout = QVBoxLayout(self)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        container = QWidget()
        rows_layout = QVBoxLayout(container)
        rows_layout.setSpacing(12)

        self._edits = {}
        for community_index, color, count, name, representative_artists in rows:
            row_widget = QWidget()
            row_layout = QVBoxLayout(row_widget)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(2)

            header = QHBoxLayout()
            header.setSpacing(6)
            swatch = QLabel()
            swatch.setFixedSize(12, 12)
            apply_scaled_style(
                swatch, f"background-color: {color.name()}; border-radius: 3px;"
            )
            header.addWidget(swatch)
            header.addWidget(QLabel(f"{count} artist{'s' if count != 1 else ''}"))
            header.addStretch()
            row_layout.addLayout(header)

            if representative_artists:
                preview = QLabel("Includes: " + ", ".join(representative_artists))
                preview.setWordWrap(True)
                preview.setObjectName("ClusterPreviewLabel")
                row_layout.addWidget(preview)

            edit = QLineEdit(name)
            edit.setPlaceholderText("Unnamed cluster")
            row_layout.addWidget(edit)
            self._edits[community_index] = edit

            rows_layout.addWidget(row_widget)

        rows_layout.addStretch()
        scroll.setWidget(container)
        layout.addWidget(scroll, 1)

        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def cluster_names(self):
        """Return {community_index: new_name} for every cluster shown."""
        return {
            community_index: edit.text().strip()
            for community_index, edit in self._edits.items()
        }
