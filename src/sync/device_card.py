from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout

from src.common.widgets.style_utils import set_style_property
from src.sync.sync_profile import SyncProfile


def format_file_size(bytes_size):
    """Convert bytes to a human-readable string."""
    if not bytes_size:
        return "0 B"
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if bytes_size < 1024.0:
            return f"{bytes_size:.2f} {unit}"
        bytes_size /= 1024.0
    return f"{bytes_size:.2f} PB"


def plural(count: int, noun: str) -> str:
    """'1 track' / '2 tracks' (thousands separated)."""
    return f"{count:,} {noun}{'' if count == 1 else 's'}"


# Glyphs for the profile's destination kind, shared with SyncView's header.
DEVICE_GLYPH = "📱"
FOLDER_GLYPH = "📁"


class DeviceCard(QFrame):
    """
    A clickable card representing one sync profile in the sidebar.

    Layout: a destination icon (phone / folder) on the left; the profile
    name with a connection badge, the destination path, and a short
    selection summary on the right.

    on_click is a callable that receives this card — avoids fragile
    parent() chains through scroll area viewports.
    """

    def __init__(self, profile: SyncProfile, on_click, parent=None):
        super().__init__(parent)
        self.profile = profile
        self._on_click = on_click
        self.setObjectName("DeviceCard")
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._selected = False
        self._connected = False
        self._track_total: int | None = None
        self._build()

    def _build(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 10, 12, 10)
        layout.setSpacing(10)

        self.icon_label = QLabel()
        self.icon_label.setObjectName("DeviceCardIcon")
        self.icon_label.setFixedSize(34, 34)
        self.icon_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.icon_label, 0, Qt.AlignTop)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(2)

        # Top row: name + badge
        top_row = QHBoxLayout()
        top_row.setSpacing(8)

        self.name_label = QLabel(self.profile.name)
        self.name_label.setObjectName("CardTitle")
        top_row.addWidget(self.name_label, 1)

        self.badge = QLabel()
        self.badge.setFixedHeight(18)
        self.badge.setAlignment(Qt.AlignCenter)
        self.badge.setObjectName("CardBadge")
        top_row.addWidget(self.badge)

        text_col.addLayout(top_row)

        # Destination: device music folder or local path
        self.sub_label = QLabel()
        self.sub_label.setObjectName("CardSub")
        # Width comes from the card, not the (elided) text, so a long path
        # can never push the sidebar wider.
        self.sub_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        text_col.addWidget(self.sub_label)

        # What this profile syncs
        self.selection_label = QLabel()
        self.selection_label.setObjectName("CardSelection")
        text_col.addWidget(self.selection_label)

        layout.addLayout(text_col, 1)

        self._refresh_display()

    def _refresh_display(self):
        """Update text and badge to reflect current profile state."""
        self.name_label.setText(self.profile.name)

        if self.profile.is_mtp:
            self.icon_label.setText(DEVICE_GLYPH)
            if self._connected:
                self.badge.setText("● Connected")
                set_style_property(self.badge, "state", "connected")
            else:
                self.badge.setText("○ Offline")
                set_style_property(self.badge, "state", "disconnected")
            destination = self.profile.music_path or "No music folder set"
        else:
            self.icon_label.setText(FOLDER_GLYPH)
            self.badge.setText("Folder")
            set_style_property(self.badge, "state", "folder")
            destination = self.profile.path or "No folder set"
        self._set_elided(self.sub_label, destination)

        n_playlists = len(self.profile.playlist_ids)
        n_moods = len(self.profile.mood_ids)
        parts = []
        if n_playlists:
            parts.append(plural(n_playlists, "playlist"))
        if n_moods:
            parts.append(plural(n_moods, "mood"))
        if self._track_total is not None and parts:
            parts.append(plural(self._track_total, "track"))
        self.selection_label.setText("  ·  ".join(parts) if parts else "Nothing selected")

    def _set_elided(self, label: QLabel, text: str):
        """Keep long paths on one line: elide the middle, full text in the tooltip."""
        width = max(label.width(), 140)
        label.setText(label.fontMetrics().elidedText(text, Qt.ElideMiddle, width))
        label.setToolTip(text)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh_display()

    def set_selected(self, selected: bool):
        self._selected = selected
        set_style_property(self, "selected", selected)

    def set_connected(self, connected: bool):
        self._connected = connected
        self._refresh_display()

    def set_track_total(self, total: int | None):
        """Show the profile's de-duplicated track count (known once it has been opened)."""
        self._track_total = total
        self._refresh_display()

    def update_profile(self, profile: SyncProfile):
        self.profile = profile
        self._refresh_display()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._on_click(self)
        super().mousePressEvent(event)
