from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)

from src.core.logger_config import logger


class ClusterNameDialog(QDialog):
    """Name-entry dialog for a Louvain cluster.

    Lists a handful of the cluster's most prominent artists alongside the
    name field, so the user can tell clusters apart at a glance instead of
    having to guess from the raw member count.
    """

    def __init__(self, representative_artists, current_name="", parent=None):
        super().__init__(parent)
        logger.debug(
            f"Opening cluster name dialog ({len(representative_artists)} representative artists)"
        )
        self.setWindowTitle("Rename Cluster")
        self.setMinimumWidth(320)

        layout = QVBoxLayout(self)

        if representative_artists:
            artists_label = QLabel("Includes: " + ", ".join(representative_artists))
            artists_label.setWordWrap(True)
            artists_label.setStyleSheet("color: palette(mid);")
            layout.addWidget(artists_label)

        form = QFormLayout()
        self._name_edit = QLineEdit(current_name)
        self._name_edit.selectAll()
        form.addRow(QLabel("Cluster name:"), self._name_edit)
        layout.addLayout(form)

        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

        self._name_edit.setFocus()

    def cluster_name(self):
        """The name entered by the user."""
        return self._name_edit.text()
