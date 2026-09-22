from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFormLayout, QFrame, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from src.album.disc_tab.disc_view import DiscTabView
from src.common.widgets.detail_card import DetailCard
from src.track.edit.track_edit_genres import GenresTab as TrackGenresTab
from src.track.edit.track_edit_roles import RolesTab as TrackRolesTab

if TYPE_CHECKING:
    from src.album.edit.base_album_edit import AlbumEditor


def _format_duration(total_seconds):
    """Render a duration in seconds as H:MM:SS, or M:SS under an hour."""
    total_seconds = int(total_seconds or 0)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


# =========================================================================
# Inner tab classes
# =========================================================================


class DetailsTab:
    """Core metadata: language, type, catalog #, live/compilation flags, sales, MBID."""

    def __init__(self, editor: AlbumEditor):
        self.editor = editor

    def build(self) -> QWidget:
        tab = QWidget()
        outer = QHBoxLayout(tab)
        outer.setSpacing(16)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setAlignment(Qt.AlignTop)

        left = QVBoxLayout()
        left.setSpacing(16)
        left.addWidget(
            self._build_form_card(
                "Identity",
                (
                    ("Language", "album_language"),
                    ("Release Type", "release_type"),
                    ("Catalog Number", "catalog_number"),
                    ("Release Country", "release_country"),
                    ("Media Format", "media_format"),
                ),
            )
        )
        left.addStretch()

        right = QVBoxLayout()
        right.setSpacing(16)
        right.addWidget(
            self._build_form_card(
                "Status & Links",
                (
                    ("Status", "status"),
                    ("Est. Sales", "estimated_sales"),
                    ("MBID", "MBID"),
                    ("Wikipedia Link", "album_wikipedia_link"),
                ),
            )
        )
        right.addWidget(self._build_flags_card())
        right.addStretch()

        outer.addLayout(left, 1)
        outer.addLayout(right, 1)
        return tab

    def _build_form_card(self, title: str, rows: tuple[tuple[str, str], ...]) -> DetailCard:
        card = DetailCard(title)
        form = QFormLayout()
        form.setSpacing(10)
        form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        for label_text, field_name in rows:
            w = self.editor.field_widgets.get(field_name)
            if w:
                form.addRow(f"{label_text}:", w)
        card.body.addLayout(form)
        return card

    def _build_flags_card(self) -> DetailCard:
        card = DetailCard("Flags")
        for field_name in ("is_live", "is_compilation", "art_is_explicit"):
            w = self.editor.field_widgets.get(field_name)
            if w:
                card.body.addWidget(w)
        return card


# -------------------------------------------------------------------------


class TracksTab:
    """Disc / track management view."""

    def __init__(self, editor: AlbumEditor):
        self.editor = editor

    def build(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        disc_view = DiscTabView(self.editor.album, self.editor.controller, parent=tab)
        # Track add/remove/reassignment here isn't routed through
        # RelationshipHelpers like credits/places/awards are, so the editor
        # needs its own hook to know other tabs' track snapshots (Genres,
        # Track Credits) just went stale.
        disc_view.tracks_changed.connect(self.editor.refresh_view)
        layout.addWidget(disc_view)
        return tab


# -------------------------------------------------------------------------


class ArtworkTab:
    """Front cover, rear cover, and liner art -- each with a pick + clear button."""

    # After any cover change the parent editor's header thumbnail is
    # refreshed immediately (see AlbumCoverArtMixin._on_cover_embed_done)
    # so the two stay in sync.

    def __init__(self, editor: AlbumEditor):
        self.editor = editor

    def build(self) -> QWidget:
        tab = QWidget()
        layout = QHBoxLayout(tab)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(16)

        # Every pick/clear button, so the editor can disable them all while a
        # background embed is in flight (see AlbumCoverArtMixin._start_cover_embed).
        self.editor._cover_buttons = []

        for cover_type, label in (
            ("front", "Front Cover"),
            ("rear", "Rear Cover"),
            ("liner", "Liner Art"),
        ):
            group = QGroupBox(label)
            g_layout = QVBoxLayout(group)

            display = QLabel()
            display.setAlignment(Qt.AlignCenter)
            display.setFixedSize(250, 250)
            display.setProperty("coverPlaceholder", True)
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
            self.editor._cover_buttons += [pick_btn, clear_btn]
            g_layout.addLayout(btn_row)

            path_label = QLabel()
            path_label.setWordWrap(True)
            path_label.setProperty("textRole", "muted")
            setattr(self.editor, f"{cover_type}_path_label", path_label)
            g_layout.addWidget(path_label)

            layout.addWidget(group)

        self.editor._load_artwork_previews()

        # A rebuild (e.g. refresh_view() while this tab is active) replaces
        # _cover_buttons with a fresh, enabled set -- if a background embed
        # is still running, re-disable them immediately instead of letting
        # pick/clear briefly become clickable mid-embed.
        if getattr(self.editor, "_cover_embed_worker", None) is not None:
            self.editor._set_cover_controls_enabled(False)

        return tab


# -------------------------------------------------------------------------


class AliasesTab:
    """List existing aliases; allow adding and removing them inline."""

    def __init__(self, editor: AlbumEditor):
        self.editor = editor

    def build(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        info = QLabel(
            "Aliases are alternative titles for this album (e.g. localized names, working titles)."
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
        self.editor.new_alias_type.setPlaceholderText("Type (e.g. Localized Title, Working Title…)")
        add_row.addWidget(self.editor.new_alias_type, 1)

        add_btn = QPushButton("Add Alias")
        add_btn.clicked.connect(self.editor._add_alias)
        add_row.addWidget(add_btn)

        layout.addWidget(add_group)
        layout.addStretch()
        return tab


# -------------------------------------------------------------------------


class GenresTab:
    """Genres common to every track on the album; edits trickle down to every track."""

    # Reuses the track editor's association-tab widget with the full set of
    # the album's tracks: its existing common-items intersection and
    # write-to-all-tracks behavior double as the album-level view and the
    # trickle-down edit mechanism.

    def __init__(self, editor: AlbumEditor):
        self.editor = editor

    def build(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        tracks = self.editor.album.tracks or []
        if not tracks:
            layout.addWidget(QLabel("This album has no tracks yet."))
            return tab

        info = QLabel(
            "Genres common to every track on this album. Adding or "
            "removing a genre here applies it to all of the album's tracks."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        genres_widget = TrackGenresTab(tracks, self.editor.controller, parent=tab)
        genres_widget.load(tracks)
        layout.addWidget(genres_widget)
        return tab


# -------------------------------------------------------------------------


class TrackCreditsTab:
    """Artist/role credits common to every track on the album; can convert to album-level."""

    # Reuses the track editor's RolesTab with the full set of the album's
    # tracks, the same way GenresTab reuses TrackGenresTab -- edits trickle
    # down via RolesTab's own multi-track intersection/batch-write behavior.
    # Also passes an on_convert_to_album hook so each role chip gets a
    # "→ Album" button that turns a credit shared by every track into a
    # single album-level credit instead (the inverse of the "→ Track"
    # button on the Album credit tab).

    def __init__(self, editor: AlbumEditor):
        self.editor = editor

    def build(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        tracks = self.editor.album.tracks or []
        if not tracks:
            layout.addWidget(QLabel("This album has no tracks yet."))
            return tab

        info = QLabel(
            "Roles common to every track on this album. Adding, removing, "
            "or editing a role here applies it to all of the album's "
            "tracks. Use → Album to convert a shared credit into a single "
            "album-level credit instead (removing it from every track)."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        roles_widget = TrackRolesTab(
            tracks, self.editor.controller, parent=tab, on_convert_to_album=self._convert_to_album
        )
        roles_widget.load(tracks)
        layout.addWidget(roles_widget)
        roles_stretch_idx = layout.count() - 1
        # roles_widget defaults to a Preferred vertical size policy, so
        # without a stretch here this outer layout would hand it any
        # leftover tab space too, on top of the same fix inside RolesTab
        # itself.
        layout.addStretch(1)
        trailing_stretch_idx = layout.count() - 1

        def _sync_outer_stretch(overflow: bool) -> None:
            # Mirrors RolesTab's own internal table/stretch swap: once its
            # table no longer fits its height cap, this tab's roles_widget
            # should claim this layout's leftover space too, instead of the
            # trailing stretch leaving it blank below an inner table that
            # itself wants to be taller.
            layout.setStretch(roles_stretch_idx, 1 if overflow else 0)
            layout.setStretch(trailing_stretch_idx, 0 if overflow else 1)

        roles_widget.overflow_changed.connect(_sync_outer_stretch)
        _sync_outer_stretch(False)
        return tab

    def _convert_to_album(self, artist_id, role_id):
        self.editor.helper.convert_credit_to_album_level(artist_id, role_id)


# -------------------------------------------------------------------------


class AdvancedTab:
    """Metadata-complete flag, ReplayGain, and read-only library stats."""

    def __init__(self, editor: AlbumEditor):
        self.editor = editor

    def build(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(14)

        for field_name in ("first_pass", "second_pass"):
            field_widget = self.editor.field_widgets.get(field_name)
            if field_widget:
                layout.addWidget(field_widget)

        album = self.editor.album
        track_count = len(album.tracks) if album.tracks else 0

        rg_label = QLabel("ReplayGain")
        rg_label.setProperty("title", True)
        layout.addWidget(rg_label)

        rg_row = QHBoxLayout()
        rg_row.setSpacing(12)
        rg_row.addWidget(self._stat_tile("Album Gain (dB)", self._format_decimal(getattr(album, "album_gain", None), 2)))
        rg_row.addWidget(self._stat_tile("Album Peak", self._format_decimal(getattr(album, "album_peak", None), 4)))
        rg_row.addStretch()
        layout.addLayout(rg_row)

        stats_label = QLabel("Library Stats")
        stats_label.setProperty("title", True)
        layout.addWidget(stats_label)

        total_duration = getattr(album, "total_duration", None)
        total_plays = getattr(album, "total_plays", None)

        stats_row = QHBoxLayout()
        stats_row.setSpacing(12)
        stats_row.addWidget(self._stat_tile("Tracks", str(track_count)))
        if total_duration:
            stats_row.addWidget(self._stat_tile("Duration", _format_duration(total_duration)))
            if track_count:
                stats_row.addWidget(self._stat_tile("Avg Track", _format_duration(total_duration / track_count)))
        stats_row.addWidget(self._stat_tile("Plays", str(total_plays) if total_plays is not None else "—"))
        stats_row.addWidget(self._stat_tile("Rating", self._format_decimal(getattr(album, "average_rating", None), 2)))
        stats_row.addStretch()
        layout.addLayout(stats_row)

        if album.tracks:
            rated_tracks = len([t for t in album.tracks if t.user_rating])
            played_tracks = len([t for t in album.tracks if t.play_count and t.play_count > 0])
            tracked_row = QHBoxLayout()
            tracked_row.setSpacing(12)
            tracked_row.addWidget(self._stat_tile("Rated Tracks", f"{rated_tracks}/{track_count}"))
            tracked_row.addWidget(self._stat_tile("Played Tracks", f"{played_tracks}/{track_count}"))
            tracked_row.addStretch()
            layout.addLayout(tracked_row)

        badges_row = QHBoxLayout()
        badges_row.setSpacing(10)

        possibly_incomplete = getattr(album, "possibly_incomplete", None)
        if possibly_incomplete is not None:
            badges_row.addWidget(self._badge("Possibly Incomplete" if possibly_incomplete else "Complete", "warn" if possibly_incomplete else "good"))

        has_all_track_numbers = getattr(album, "has_all_track_numbers", None)
        if has_all_track_numbers is not None:
            badges_row.addWidget(self._badge("Has All Track #s" if has_all_track_numbers else "Missing Track #s", "good" if has_all_track_numbers else "warn"))

        cert = getattr(album, "RIAA_certification", None)
        if cert:
            badges_row.addWidget(self._badge(cert, "neutral"))

        badges_row.addStretch()
        layout.addLayout(badges_row)

        layout.addStretch()
        return tab

    @staticmethod
    def _stat_tile(label_text: str, value_text: str) -> QFrame:
        tile = QFrame()
        tile.setObjectName("StatTile")
        tile.setMinimumWidth(110)
        tile_layout = QVBoxLayout(tile)
        tile_layout.setContentsMargins(12, 10, 12, 10)
        tile_layout.setSpacing(4)
        value_lbl = QLabel(value_text)
        value_lbl.setObjectName("StatTileValue")
        caption_lbl = QLabel(label_text.upper())
        caption_lbl.setObjectName("StatTileLabel")
        tile_layout.addWidget(value_lbl)
        tile_layout.addWidget(caption_lbl)
        return tile

    @staticmethod
    def _badge(text: str, state: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setProperty("badgeState", state)
        return lbl

    @staticmethod
    def _format_decimal(value, places: int) -> str:
        if value is None:
            return "—"
        try:
            return f"{float(value):.{places}f}"
        except (TypeError, ValueError):
            return str(value)
