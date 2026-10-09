from PySide6.QtCore import Qt
from PySide6.QtWidgets import QButtonGroup, QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget

from src.foundation.display_settings import apply_scaled_style
from src.foundation.logger_config import logger


class ClusterNamesDialog(QDialog):
    """Rename every cluster across every eligible level in one dialog; edits survive level switches."""

    def __init__(self, rows_by_level, active_level, parent=None):
        super().__init__(parent)
        total_rows = sum(len(rows) for rows in rows_by_level.values())
        logger.debug(f"Opening cluster names dialog ({len(rows_by_level)} levels, {total_rows} clusters total)")
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
                # Match the legend panel's granularity buttons: label by the
                # number of clusters at this level, not a raw dendrogram index.
                button = QPushButton(f"{len(rows_by_level[level])} groups")
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

        self._error_label = QLabel()
        self._error_label.setProperty("textRole", "error")
        self._error_label.setWordWrap(True)
        self._error_label.hide()
        layout.addWidget(self._error_label)

        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def _build_level_widget(self, level, rows):
        """Build the scrollable rows (swatch, size, preview, name field) for one level."""
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
        """Show one level's rows, keeping every level's widgets alive."""
        self._active_level = level
        if self._level_group is not None and self._level_group.button(level) is not None:
            self._level_group.button(level).setChecked(True)
        # takeWidget() first: setWidget() deletes the old widget and its unsaved edits.
        self._scroll.takeWidget()
        self._scroll.setWidget(self._row_widgets[level])

    def _duplicate_names(self):
        """Return (level, name) for the first name used by two clusters at one level, else None."""
        for level in self._levels:
            seen = set()
            for edit in self._edits.get(level, {}).values():
                key = edit.text().strip().casefold()
                if not key:
                    continue
                if key in seen:
                    return level, edit.text().strip()
                seen.add(key)
        return None

    def accept(self):
        """Refuse duplicate names at one level, because names identify clusters across recomputes."""
        duplicate = self._duplicate_names()
        if duplicate is not None:
            level, name = duplicate
            self._show_level(level)
            self._error_label.setText(f'Two clusters in this view are both named "{name}". Give each cluster a different name.')
            self._error_label.show()
            return
        self._error_label.hide()
        super().accept()

    def cluster_names(self):
        """Return {level: {community_index: name}} for every cluster in the dialog."""
        return {level: {community_index: edit.text().strip() for community_index, edit in edits.items()} for level, edits in self._edits.items()}
