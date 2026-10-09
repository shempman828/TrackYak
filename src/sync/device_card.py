"""DeviceCard (one sync profile in the sidebar) plus the size/plural text helpers the Sync view shares."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout

from src.common.widgets.style_utils import set_style_property
from src.sync.sync_profile import SyncProfile


def format_file_size(bytes_size):
    """Convert bytes to a human-readable string."""
    if not bytes_size or bytes_size < 0:
        return "0 B"
    if bytes_size < 1024:
        return f"{int(bytes_size)} B"  # whole bytes: "512.00 B" reads as a bug
    bytes_size /= 1024.0
    for unit in ["KB", "MB", "GB", "TB"]:
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
    """A clickable, keyboard-focusable sidebar card for one sync profile."""

    # on_click receives this card: avoids fragile parent() chains through scroll-area viewports.

    def __init__(self, profile: SyncProfile, on_click, parent=None):
        super().__init__(parent)
        self.profile = profile
        self._on_click = on_click
        self.setObjectName("DeviceCard")
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        # Tab reaches the card; Enter/Space selects it (see keyPressEvent).
        self.setFocusPolicy(Qt.StrongFocus)
        self._selected = False
        self._connected = False
        self._track_total: int | None = None
        self._build()

    def _build(self):
        """Create the icon, name + badge, destination and selection rows."""
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
        self.setAccessibleName(f"Sync profile {self.profile.name}")
        self.setAccessibleDescription(f"{self.badge.text()}, {destination}, {self.selection_label.text()}")

    def _set_elided(self, label: QLabel, text: str):
        """Keep long paths on one line: elide the middle, full text in the tooltip."""
        width = max(label.width(), 140)
        label.setText(label.fontMetrics().elidedText(text, Qt.ElideMiddle, width))
        label.setToolTip(text)

    def resizeEvent(self, event):
        """Re-elide the destination to the new width."""
        super().resizeEvent(event)
        self._refresh_display()

    def set_selected(self, selected: bool):
        """Show or clear the selected style."""
        self._selected = selected
        set_style_property(self, "selected", selected)

    def set_connected(self, connected: bool):
        """Show the device as connected or offline."""
        self._connected = connected
        self._refresh_display()

    def set_track_total(self, total: int | None):
        """Show the profile's de-duplicated track count (known once it has been opened)."""
        self._track_total = total
        self._refresh_display()

    def update_profile(self, profile: SyncProfile):
        """Redraw for `profile`."""
        self.profile = profile
        self._refresh_display()

    def mousePressEvent(self, event):
        """Select the card on a left click."""
        if event.button() == Qt.LeftButton:
            self._on_click(self)
        super().mousePressEvent(event)

    def keyPressEvent(self, event):
        """Select the card with Enter, Return or Space."""
        if event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self._on_click(self)
            event.accept()
            return
        super().keyPressEvent(event)
