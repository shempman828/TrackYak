from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.display.display_settings import apply_scaled_style
from src.foundation.logger_config import logger


class ClusterNamesDialog(QDialog):
    """Rename every cluster at once, across every eligible dendrogram
    level.

    Renaming clusters one at a time (a separate dialog per cluster, opened
    by double-clicking its legend row) meant losing sight of every other
    cluster's name/preview while editing one -- awkward for telling similar
    clusters apart or renaming several in one pass. This shows every
    cluster's color, size, and representative artists alongside its name
    field in a single scrollable list, so the whole set can be reviewed and
    renamed together. A level toggle (mirroring the legend panel's) lets
    the same session rename communities at multiple granularities --
    edits made at one level are held in memory and survive switching to
    another level and back, all committed together on OK.
    """

    def __init__(self, rows_by_level, active_level, parent=None):
        super().__init__(parent)
        total_rows = sum(len(rows) for rows in rows_by_level.values())
        logger.debug(
            f"Opening cluster names dialog ({len(rows_by_level)} levels, "
            f"{total_rows} clusters total)"
        )
        self.setWindowTitle("Rename Clusters")
        self.resize(420, 520)

        self._edits = {}  # level -> {community_index: QLineEdit}
        self._row_widgets = {}  # level -> QWidget (the scroll area's content)

        layout = QVBoxLayout(self)

        self._levels = sorted(rows_by_level.keys())
        if len(self._levels) > 1:
            level_row = QHBoxLayout()
            level_row.setSpacing(4)
            self._level_group = QButtonGroup(self)
            self._level_group.setExclusive(True)
            for level in self._levels:
                button = QPushButton(f"Level {level}")
                button.setCheckable(True)
                button.setChecked(level == active_level)
                button.setCursor(Qt.PointingHandCursor)
                self._level_group.addButton(button, level)
                level_row.addWidget(button)
            level_row.addStretch()
            self._level_group.idClicked.connect(self._show_level)
            layout.addLayout(level_row)
        else:
            self._level_group = None

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        layout.addWidget(self._scroll, 1)

        for level, rows in rows_by_level.items():
            self._row_widgets[level] = self._build_level_widget(level, rows)

        self._active_level = active_level if active_level in rows_by_level else self._levels[0]
        self._show_level(self._active_level)

        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def _build_level_widget(self, level, rows):
        container = QWidget()
        rows_layout = QVBoxLayout(container)
        rows_layout.setSpacing(12)

        edits = {}
        for community_index, color, count, name, representative_artists in rows:
            row_widget = QWidget()
            row_layout = QVBoxLayout(row_widget)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(2)

            header = QHBoxLayout()
            header.setSpacing(6)
            swatch = QLabel()
            swatch.setFixedSize(12, 12)
            apply_scaled_style(swatch, f"background-color: {color.name()}; border-radius: 3px;")
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
            edits[community_index] = edit

            rows_layout.addWidget(row_widget)

        rows_layout.addStretch()
        self._edits[level] = edits
        return container

    def _show_level(self, level):
        self._active_level = level
        # takeWidget() first: setWidget() over an already-set widget deletes
        # the old one, which would destroy the QLineEdits (and their
        # in-progress edits) for whichever level we're switching away from.
        self._scroll.takeWidget()
        self._scroll.setWidget(self._row_widgets[level])

    def cluster_names(self):
        """Return {level: {community_index: new_name}} for every cluster
        shown, across every level visited in this session."""
        return {
            level: {community_index: edit.text().strip() for community_index, edit in edits.items()}
            for level, edits in self._edits.items()
        }
