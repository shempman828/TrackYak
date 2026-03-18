# =========================================================================
# Inner tab classes
# =========================================================================

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.base_album_edit import AlbumEditor
from src.disc_view import DiscManagementView


class DetailsTab:
    """Core metadata: language, type, catalog #, flags, sales, MBID."""

    def __init__(self, editor: "AlbumEditor"):
        self.editor = editor

    def build(self) -> QWidget:
        tab = QWidget()
        outer = QHBoxLayout(tab)
        outer.setSpacing(24)
        outer.setContentsMargins(12, 12, 12, 12)

        left = QVBoxLayout()
        left.setSpacing(10)

        def _row(label_text, field_name):
            row = QHBoxLayout()
            lbl = QLabel(label_text)
            lbl.setFixedWidth(130)
            row.addWidget(lbl)
            w = self.editor.field_widgets.get(field_name)
            if w:
                row.addWidget(w, 1)
            left.addLayout(row)

        _row("Language:", "album_language")
        _row("Release Type:", "release_type")
        _row("Catalog Number:", "catalog_number")
        _row("MBID:", "MBID")
        _row("Status:", "status")
        _row("Est. Sales:", "estimated_sales")
        left.addStretch()

        right = QVBoxLayout()
        right.setSpacing(10)
        for field_name in ("is_fixed", "is_live", "is_compilation"):
            w = self.editor.field_widgets.get(field_name)
            if w:
                right.addWidget(w)
        right.addStretch()

        outer.addLayout(left, 1)
        outer.addLayout(right, 1)
        return tab


# -------------------------------------------------------------------------


class TracksTab:
    """Disc / track management view."""

    def __init__(self, editor: "AlbumEditor"):
        self.editor = editor

    def build(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        disc_view = DiscManagementView(
            self.editor.album, self.editor.controller, parent=tab
        )
        layout.addWidget(disc_view)
        return tab


# -------------------------------------------------------------------------


class ArtworkTab:
    """Front cover, rear cover, and liner art — each with a pick + clear button.

    After any cover change the parent editor's header thumbnail is refreshed
    immediately so the two stay in sync.
    """

    def __init__(self, editor: "AlbumEditor"):
        self.editor = editor

    def build(self) -> QWidget:
        tab = QWidget()
        layout = QHBoxLayout(tab)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(16)

        for cover_type, attr, label in (
            ("front", "front_cover_path", "Front Cover"),
            ("rear", "rear_cover_path", "Rear Cover"),
            ("liner", "album_liner_path", "Liner Art"),
        ):
            group = QGroupBox(label)
            g_layout = QVBoxLayout(group)

            display = QLabel()
            display.setAlignment(Qt.AlignCenter)
            display.setFixedSize(250, 250)
            display.setStyleSheet("border: 1px solid #555; background: #2a2a2a;")
            display.setWordWrap(True)
            # Store on the editor so _pick_cover / _clear_cover can reach them
            setattr(self.editor, f"{cover_type}_cover_display", display)
            g_layout.addWidget(display)

            btn_row = QHBoxLayout()
            pick_btn = QPushButton("Choose…")
            pick_btn.clicked.connect(
                lambda checked=False, ct=cover_type: self.editor._pick_cover(ct)
            )
            clear_btn = QPushButton("Clear")
            clear_btn.clicked.connect(
                lambda checked=False, ct=cover_type: self.editor._clear_cover(ct)
            )
            btn_row.addWidget(pick_btn)
            btn_row.addWidget(clear_btn)
            g_layout.addLayout(btn_row)

            path_label = QLabel()
            path_label.setWordWrap(True)
            path_label.setStyleSheet("color: #888; font-size: 10px;")
            setattr(self.editor, f"{cover_type}_path_label", path_label)
            g_layout.addWidget(path_label)

            layout.addWidget(group)

        self.editor._load_artwork_previews()
        return tab


# -------------------------------------------------------------------------


class AliasesTab:
    """List existing aliases; allow adding and removing them inline."""

    def __init__(self, editor: "AlbumEditor"):
        self.editor = editor

    def build(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        info = QLabel(
            "Aliases are alternative titles for this album "
            "(e.g. localized names, working titles)."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        # Alias list container — rebuilt on refresh
        self.editor.aliases_container = QWidget()
        self.editor.aliases_layout = QVBoxLayout(self.editor.aliases_container)
        self.editor.aliases_layout.setSpacing(4)
        layout.addWidget(self.editor.aliases_container)

        self.editor._refresh_aliases_list()

        # Inline add row
        add_group = QGroupBox("Add New Alias")
        add_row = QHBoxLayout(add_group)

        self.editor.new_alias_name = QLineEdit()
        self.editor.new_alias_name.setPlaceholderText("Alias name…")
        add_row.addWidget(self.editor.new_alias_name, 2)

        self.editor.new_alias_type = QLineEdit()
        self.editor.new_alias_type.setPlaceholderText(
            "Type (e.g. Localized Title, Working Title…)"
        )
        add_row.addWidget(self.editor.new_alias_type, 1)

        add_btn = QPushButton("Add Alias")
        add_btn.clicked.connect(self.editor._add_alias)
        add_row.addWidget(add_btn)

        layout.addWidget(add_group)
        layout.addStretch()
        return tab


# -------------------------------------------------------------------------


class AdvancedTab:
    """ReplayGain, Wikipedia link, and read-only library stats."""

    def __init__(self, editor: "AlbumEditor"):
        self.editor = editor

    def build(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        def _row(label_text, field_name):
            row = QHBoxLayout()
            lbl = QLabel(label_text)
            lbl.setFixedWidth(160)
            row.addWidget(lbl)
            w = self.editor.field_widgets.get(field_name)
            if w:
                row.addWidget(w, 1)
            layout.addLayout(row)

        _row("Wikipedia Link:", "album_wikipedia_link")

        rg_label = QLabel("ReplayGain")
        rg_label.setStyleSheet("font-weight: bold; margin-top: 6px;")
        layout.addWidget(rg_label)
        _row("Album Gain (dB):", "album_gain")
        _row("Album Peak:", "album_peak")

        stats_label = QLabel("Library Stats")
        stats_label.setStyleSheet("font-weight: bold; margin-top: 6px;")
        layout.addWidget(stats_label)

        def _read_only_row(label_text, value_text):
            row = QHBoxLayout()
            lbl = QLabel(label_text)
            lbl.setFixedWidth(160)
            row.addWidget(lbl)
            val = QLabel(value_text)
            row.addWidget(val)
            row.addStretch()
            layout.addLayout(row)

        album = self.editor.album
        track_count = len(album.tracks) if album.tracks else 0
        _read_only_row("Track Count:", str(track_count))

        total_plays = getattr(album, "total_plays", None)
        _read_only_row(
            "Total Plays:", str(total_plays) if total_plays is not None else "—"
        )

        avg_rating = getattr(album, "average_rating", None)
        if avg_rating is not None:
            try:
                display_rating = f"{float(avg_rating):.2f}"
            except (TypeError, ValueError):
                display_rating = str(avg_rating)
        else:
            display_rating = "—"
        _read_only_row("Average Rating:", display_rating)

        possibly_incomplete = getattr(album, "possibly_incomplete", None)
        inc_text = (
            "—"
            if possibly_incomplete is None
            else ("Yes ⚠️" if possibly_incomplete else "No")
        )
        _read_only_row("Possibly Incomplete:", inc_text)

        has_all_track_numbers = getattr(album, "has_all_track_numbers", None)
        tn_text = (
            "—"
            if has_all_track_numbers is None
            else ("Yes ✓" if has_all_track_numbers else "No ✗")
        )
        _read_only_row("Has All Track #s:", tn_text)

        cert = getattr(album, "RIAA_certification", None)
        if cert:
            _read_only_row("RIAA Certification:", cert)

        layout.addStretch()
        return tab
