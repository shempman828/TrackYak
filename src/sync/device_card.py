from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout

from src.common.style_utils import set_style_property
from src.sync.sync_utility import SyncProfile


def format_file_size(bytes_size):
    """Convert bytes to a human-readable string."""
    if not bytes_size:
        return "0 B"
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if bytes_size < 1024.0:
            return f"{bytes_size:.2f} {unit}"
        bytes_size /= 1024.0
    return f"{bytes_size:.2f} PB"


class DeviceCard(QFrame):
    """
    A clickable card representing one sync profile in the sidebar.

    Shows the profile name, sync type (Android USB / Folder), and
    a live connection badge when an Android device is linked.

    on_click is a callable that receives this card — avoids fragile
    parent() chains through scroll area viewports.
    """

    def __init__(self, profile: SyncProfile, on_click, parent=None):
        super().__init__(parent)
        self.profile = profile
        self._on_click = on_click
        self.setObjectName("DeviceCard")
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(64)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._selected = False
        self._connected = False
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(3)

        # Top row: name + badge
        top_row = QHBoxLayout()
        top_row.setSpacing(8)

        self.name_label = QLabel(self.profile.name)
        self.name_label.setObjectName("CardTitle")
        font = QFont()
        font.setBold(True)
        self.name_label.setFont(font)
        top_row.addWidget(self.name_label, 1)

        self.badge = QLabel()
        self.badge.setFixedHeight(18)
        self.badge.setAlignment(Qt.AlignCenter)
        self.badge.setObjectName("CardBadge")
        top_row.addWidget(self.badge)

        layout.addLayout(top_row)

        # Subtitle: path or device info
        self.sub_label = QLabel()
        self.sub_label.setObjectName("CardSub")
        self.sub_label.setWordWrap(True)
        layout.addWidget(self.sub_label)

        self._refresh_display()

    def _refresh_display(self):
        """Update text and badge to reflect current profile state."""
        self.name_label.setText(self.profile.name)

        if self.profile.is_mtp:
            if self._connected:
                self.badge.setText("● USB")
                set_style_property(self.badge, "state", "connected")
            else:
                self.badge.setText("○ USB")
                set_style_property(self.badge, "state", "disconnected")
            self.sub_label.setText(self.profile.music_path or "No path set")
        else:
            self.badge.setText("📁 Folder")
            set_style_property(self.badge, "state", "folder")
            self.sub_label.setText(self.profile.path or "No folder set")

    def set_selected(self, selected: bool):
        self._selected = selected
        set_style_property(self, "selected", selected)

    def set_connected(self, connected: bool):
        self._connected = connected
        self._refresh_display()

    def update_profile(self, profile: SyncProfile):
        self.profile = profile
        self._refresh_display()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._on_click(self)
        super().mousePressEvent(event)
