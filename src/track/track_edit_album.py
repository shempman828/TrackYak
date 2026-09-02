# ---------------------------------------------------------------------------
# AlbumsTab — manage a track's album relationships
# ---------------------------------------------------------------------------
from __future__ import annotations

import sqlite3
import webbrowser

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.exc import SQLAlchemyError

from src.award.award_series_import import import_awards_for_entity
from src.common.entity_completer_context import album_context_map
from src.common.entity_completer_edit import (
    build_entity_search_widget,
    find_or_create_by_name,
    get_cached_entities,
    register_cached_entity,
)
from src.foundation.logger_config import logger
from src.image.artwork_cache import get_artwork_cache
from src.musicbrainz.musicbrainz_artist import suggest_artist_names
from src.musicbrainz.musicbrainz_core import MusicBrainzLookupError
from src.musicbrainz.musicbrainz_match_dialog import MusicBrainzImportDialog, MusicBrainzMatchDialog
from src.musicbrainz.musicbrainz_recording import search_canonical_album_for_recording
from src.track.track_edit_basetab import _BaseTab

_ART_SIZE = 96


class AlbumsTab(_BaseTab):
    def __init__(self, tracks: list, controller, parent=None, dialog=None):
        super().__init__(tracks, controller, parent)
        self._dialog = dialog
        self._wiki_link = ""
        self._mb_link = ""
        self._build_ui()

    def _live_track_name(self) -> str:
        """Current track title, reflecting an unsaved edit on the Basic tab
        if there is one -- see TrackEditDialog.get_live_track_name."""
        if self._dialog is not None:
            return self._dialog.get_live_track_name()
        return self.track.track_name

    # ── UI ────────────────────────────────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # ── Current album group ─────────────────────────────────────────
        current_group = QGroupBox("Current Album")
        current_layout = QVBoxLayout(current_group)

        info_row = QHBoxLayout()
        info_row.setSpacing(12)

        self._art_label = QLabel()
        self._art_label.setFixedSize(_ART_SIZE, _ART_SIZE)
        self._art_label.setAlignment(Qt.AlignCenter)
        self._art_label.setProperty("textRole", "note")
        info_row.addWidget(self._art_label)

        text_col = QVBoxLayout()
        text_col.setSpacing(2)

        self._primary_label = QLabel("—")
        primary_font = self._primary_label.font()
        primary_font.setBold(True)
        primary_font.setPointSize(primary_font.pointSize() + 2)
        self._primary_label.setFont(primary_font)
        self._primary_label.setWordWrap(True)
        text_col.addWidget(self._primary_label)

        self._primary_artist_label = QLabel("—")
        self._primary_artist_label.setProperty("textRole", "note")
        self._primary_artist_label.setWordWrap(True)
        text_col.addWidget(self._primary_artist_label)

        self._primary_year_label = QLabel("—")
        self._primary_year_label.setProperty("textRole", "note")
        text_col.addWidget(self._primary_year_label)

        text_col.addStretch(1)
        info_row.addLayout(text_col, stretch=1)
        current_layout.addLayout(info_row)

        btn_row = QHBoxLayout()
        self._open_primary_btn = QPushButton("Edit Album")
        self._open_primary_btn.setEnabled(False)
        self._open_primary_btn.clicked.connect(self._open_primary_album)
        btn_row.addWidget(self._open_primary_btn)

        self._remove_primary_btn = QPushButton("Remove Relationship")
        self._remove_primary_btn.setEnabled(False)
        self._remove_primary_btn.setToolTip(
            "Detaches this track from its album (track stays in library)"
        )
        self._remove_primary_btn.clicked.connect(self._remove_primary_album)
        btn_row.addWidget(self._remove_primary_btn)

        self._change_album_btn = QPushButton("Change Album")
        self._change_album_btn.setCheckable(True)
        self._change_album_btn.setToolTip(
            "Search for a different album, or manage virtual appearances"
        )
        self._change_album_btn.toggled.connect(self._on_change_album_toggled)
        btn_row.addWidget(self._change_album_btn)

        self._wiki_open_btn = QPushButton("🌐 Wikipedia")
        self._wiki_open_btn.setToolTip("Open the album's Wikipedia page in your browser")
        self._wiki_open_btn.clicked.connect(self._open_wiki_link)
        self._wiki_open_btn.setVisible(False)
        btn_row.addWidget(self._wiki_open_btn)

        self._mb_open_btn = QPushButton("🎵 MusicBrainz")
        self._mb_open_btn.setToolTip("Open the album's MusicBrainz page in your browser")
        self._mb_open_btn.clicked.connect(self._open_mb_link)
        self._mb_open_btn.setVisible(False)
        btn_row.addWidget(self._mb_open_btn)

        self._find_canonical_btn = QPushButton("🎵 Find Canonical Album")
        self._find_canonical_btn.setToolTip(
            "Search MusicBrainz for the earliest release(s) of this "
            "recording by its primary artist, and link or create the "
            "matching local album"
        )
        self._find_canonical_btn.setEnabled(not self.is_multi)
        self._find_canonical_btn.clicked.connect(self._find_canonical_album)
        btn_row.addWidget(self._find_canonical_btn)

        btn_row.addStretch(1)
        current_layout.addLayout(btn_row)
        layout.addWidget(current_group)

        # ── Set current album group (revealed by "Change Album") ────────
        set_group = QGroupBox("Set Current Album (search existing or create new)")
        set_layout = QVBoxLayout(set_group)

        add_row = QHBoxLayout()
        self._album_search = build_entity_search_widget(
            self.controller,
            "Album",
            "album_name",
            "album_id",
            "Search albums…",
            context_builder=album_context_map,
        )
        self._album_search.textChanged.connect(self._on_album_search_changed)
        self._album_search.returnPressed.connect(self._set_primary_album)
        add_row.addWidget(self._album_search)

        self._set_primary_btn = QPushButton("Set as Current Album")
        self._set_primary_btn.setEnabled(False)
        self._set_primary_btn.clicked.connect(self._set_primary_album)
        add_row.addWidget(self._set_primary_btn)
        set_layout.addLayout(add_row)
        set_group.setVisible(False)
        self._set_group = set_group
        layout.addWidget(set_group)

        # ── Virtual appearances group ─────────────────────────────────────
        virtual_group = QGroupBox("Virtual Appearances (track borrowed by other albums)")
        virtual_layout = QVBoxLayout(virtual_group)

        self._virtual_table = QTableWidget(0, 5)
        self._virtual_table.setHorizontalHeaderLabels(["Album", "Track #", "Disc #", "Side", ""])
        self._virtual_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self._virtual_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self._virtual_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self._virtual_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self._virtual_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self._virtual_table.verticalHeader().setVisible(False)
        # Sized by row count (see _update_virtual_table_height) rather than
        # expanding to fill the tab — this section is empty for most tracks.
        self._virtual_table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        virtual_layout.addWidget(self._virtual_table)

        # ── Add virtual appearance ────────────────────────────────────────
        virt_add_row = QHBoxLayout()
        self._virt_search = build_entity_search_widget(
            self.controller,
            "Album",
            "album_name",
            "album_id",
            "Search albums…",
            context_builder=album_context_map,
        )
        self._virt_search.textChanged.connect(self._on_virt_search_changed)
        self._virt_search.returnPressed.connect(self._add_virtual)
        virt_add_row.addWidget(self._virt_search)

        self._virt_track_num = QSpinBox()
        self._virt_track_num.setRange(0, 999)
        self._virt_track_num.setSpecialValueText("Track #")
        self._virt_track_num.setToolTip("Track number in this virtual appearance")
        virt_add_row.addWidget(self._virt_track_num)

        self._virt_disc_num = QSpinBox()
        self._virt_disc_num.setRange(0, 99)
        self._virt_disc_num.setSpecialValueText("Disc #")
        self._virt_disc_num.setToolTip("Disc number in this virtual appearance")
        virt_add_row.addWidget(self._virt_disc_num)

        self._virt_add_btn = QPushButton("Add Virtual Appearance")
        self._virt_add_btn.setEnabled(False)
        self._virt_add_btn.clicked.connect(self._add_virtual)
        virt_add_row.addWidget(self._virt_add_btn)
        virtual_layout.addLayout(virt_add_row)
        virtual_group.setVisible(False)
        self._virtual_group = virtual_group
        layout.addWidget(virtual_group)

        layout.addStretch(1)
        self._update_virtual_table_height()

    def _on_change_album_toggled(self, checked: bool) -> None:
        self._set_group.setVisible(checked)
        self._virtual_group.setVisible(checked)
        self._change_album_btn.setText("Hide Album Search" if checked else "Change Album")

    # ── Loading ───────────────────────────────────────────────────────────

    def load(self, tracks: list) -> None:
        self.tracks = tracks

        if self.is_multi:
            track_albums = [getattr(t, "album", None) for t in self.tracks]
            album_ids = {a.album_id if a else None for a in track_albums}
            if len(album_ids) == 1:
                album = track_albums[0]
                self._set_primary_display(album)
                self._remove_primary_btn.setEnabled(bool(album))
            else:
                self._primary_label.setText(f"(multiple albums across {len(self.tracks)} tracks)")
                self._primary_artist_label.setText("")
                self._primary_year_label.setText("")
                self._art_label.clear()
                self._wiki_link = ""
                self._mb_link = ""
                self._wiki_open_btn.setVisible(False)
                self._mb_open_btn.setVisible(False)
                self._remove_primary_btn.setEnabled(True)
            self._open_primary_btn.setEnabled(False)
            self._set_primary_btn.setEnabled(bool(self._album_search.text().strip()))
            self._virt_add_btn.setEnabled(False)
            self._virtual_table.setRowCount(0)
            self._update_virtual_table_height()
            return

        # Primary album
        album = getattr(self.track, "album", None)
        self._set_primary_display(album)
        self._open_primary_btn.setEnabled(bool(album))
        self._remove_primary_btn.setEnabled(bool(album))

        # Virtual appearances
        self._virtual_table.setRowCount(0)
        for link in getattr(self.track, "virtual_appearances", []):
            alb = getattr(link, "album", None)
            if alb:
                self._add_virtual_row(
                    virtual_id=link.virtual_id,
                    album_name=alb.album_name,
                    album_id=alb.album_id,
                    track_num=link.virtual_track_number,
                    disc_num=link.virtual_disc_number,
                    side=link.virtual_side,
                )
        self._update_virtual_table_height()

    def _set_primary_display(self, album) -> None:
        """Populate the Current Album header (art, name, artist, year)."""
        if not album:
            self._primary_label.setText("— (none)")
            self._primary_artist_label.setText("")
            self._primary_year_label.setText("")
            self._art_label.clear()
            self._wiki_link = ""
            self._mb_link = ""
            self._wiki_open_btn.setVisible(False)
            self._mb_open_btn.setVisible(False)
            return

        self._primary_label.setText(album.album_name or "—")
        self._primary_artist_label.setText(
            getattr(album, "album_artist_names", None) or "Unknown Artist"
        )
        year = getattr(album, "release_year", None)
        self._primary_year_label.setText(str(year) if year else "Year unknown")
        self._load_album_art(album)

        self._wiki_link = getattr(album, "album_wikipedia_link", None) or ""
        self._wiki_open_btn.setVisible(bool(self._wiki_link))

        mbid = getattr(album, "MBID", None)
        self._mb_link = f"https://musicbrainz.org/release/{mbid}" if mbid else ""
        self._mb_open_btn.setVisible(bool(self._mb_link))

    def _load_album_art(self, album) -> None:
        pixmap = None
        try:
            cache = get_artwork_cache()
            if cache:
                is_explicit = bool(getattr(album, "art_is_explicit", False))
                pixmap = cache.get_pixmap(album, "front", is_explicit)
        except (OSError, sqlite3.Error) as e:
            logger.warning(f"Failed to load album art for tab display: {e}")
            pixmap = None

        if pixmap and not pixmap.isNull():
            self._art_label.setText("")
            self._art_label.setPixmap(
                pixmap.scaled(_ART_SIZE, _ART_SIZE, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )
        else:
            self._art_label.setPixmap(QPixmap())
            self._art_label.setText("No Art")

    def _update_virtual_table_height(self) -> None:
        """Keep the table sized to its contents so an empty/short list of
        virtual appearances doesn't reserve a big block of the tab."""
        header_height = self._virtual_table.horizontalHeader().height()
        row_height = self._virtual_table.verticalHeader().defaultSectionSize()
        row_count = self._virtual_table.rowCount()
        visible_rows = max(row_count, 1)  # room for the "no rows" empty state
        visible_rows = min(visible_rows, 4)  # cap height; extra rows scroll
        frame = 2 * self._virtual_table.frameWidth()
        height = header_height + visible_rows * row_height + frame
        self._virtual_table.setFixedHeight(height)

    def _add_virtual_row(self, virtual_id, album_name, album_id, track_num, disc_num, side):
        row = self._virtual_table.rowCount()
        self._virtual_table.insertRow(row)

        alb_item = QTableWidgetItem(album_name)
        alb_item.setData(Qt.UserRole, album_id)
        alb_item.setData(Qt.UserRole + 1, virtual_id)
        alb_item.setFlags(alb_item.flags() & ~Qt.ItemIsEditable)
        self._virtual_table.setItem(row, 0, alb_item)

        self._virtual_table.setItem(row, 1, QTableWidgetItem(str(track_num) if track_num else ""))
        self._virtual_table.setItem(row, 2, QTableWidgetItem(str(disc_num) if disc_num else ""))
        self._virtual_table.setItem(row, 3, QTableWidgetItem(side or ""))

        btn_widget = QWidget()
        btn_layout = QHBoxLayout(btn_widget)
        btn_layout.setContentsMargins(2, 2, 2, 2)

        edit_btn = QPushButton("Edit Album")
        edit_btn.clicked.connect(lambda _c, aid=album_id: self._open_album_by_id(aid))
        btn_layout.addWidget(edit_btn)

        rm_btn = QPushButton("Remove")
        rm_btn.clicked.connect(lambda _c, vid=virtual_id: self._remove_virtual(vid))
        btn_layout.addWidget(rm_btn)

        self._virtual_table.setCellWidget(row, 4, btn_widget)

    # ── Primary album search / set / remove ──────────────────────────────

    def _on_album_search_changed(self, text: str):
        self._set_primary_btn.setEnabled(bool(text.strip()))

    def _known_albums(self, widget) -> list:
        """Candidate set for find_or_create_by_name's case-insensitive
        duplicate check: the full cached Album table when it's small enough
        to preload, else the bounded search widget's last on-demand query."""
        cached = get_cached_entities(self.controller, "Album")
        if cached is not None:
            return cached
        known_matches = getattr(widget, "known_matches", None)
        return known_matches() if known_matches is not None else []

    def _resolve_album(self, widget):
        """Resolve the album named in `widget` to an ORM object: the
        completer's locked pick if there is one, else find-or-create by the
        typed name (an existing album always wins over a same-named
        duplicate -- see find_or_create_by_name). A freshly created album is
        hot-registered into the completer index and shared cache."""
        matched_id = widget.matched_id()
        if matched_id is not None:
            return self.controller.get.get_entity_object("Album", album_id=matched_id)
        name = widget.text().strip()
        if not name:
            return None
        known = self._known_albums(widget)
        album = find_or_create_by_name(self.controller, "Album", "album_name", name, known)
        if album is not None and album not in known:
            # Deferred: this can run nested inside the completer's own
            # keyPressEvent (Enter -> returnPressed), and add_to_index()
            # rebuilds the QCompleter in place -- doing that mid key-dispatch
            # corrupts its internals. See track_edit_places.py _add().
            aid, aname = album.album_id, album.album_name
            QTimer.singleShot(0, lambda: widget.add_to_index(aname, aid))
            register_cached_entity("Album", album)
        return album

    def _set_primary_album(self):
        album = self._resolve_album(self._album_search)
        if not album:
            if self._album_search.text().strip():
                QMessageBox.warning(self, "Error", "Could not resolve or create album.")
            return

        if self.is_multi:
            confirm = QMessageBox.question(
                self,
                "Set Primary Album",
                f"Set '{album.album_name}' as the primary album for all "
                f"{len(self.tracks)} selected tracks?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if confirm != QMessageBox.Yes:
                return
            track_ids = [track.track_id for track in self.tracks]
            if not self.controller.update.update_entities(
                "Track", track_ids, album_id=album.album_id
            ):
                QMessageBox.warning(self, "Error", "Failed to set album for the selected tracks.")
        else:
            try:
                self.controller.update.update_entity(
                    "Track", self.track.track_id, album_id=album.album_id
                )
            except SQLAlchemyError as e:
                logger.error(f"Failed to set primary album: {e}")
                QMessageBox.warning(self, "Error", f"Failed to set album:\n{e}")
                return

        self._album_search.reset()
        self._refresh_tracks()
        self.load(self.tracks)

    def _remove_primary_album(self):
        if self.is_multi:
            question = (
                f"Detach all {len(self.tracks)} selected tracks from their "
                "primary album?\nThe tracks will remain in the library but "
                "will have no album."
            )
        else:
            question = (
                "Detach this track from its primary album?\n"
                "The track will remain in the library but will have no album."
            )
        confirm = QMessageBox.question(
            self, "Remove Album Relationship", question, QMessageBox.Yes | QMessageBox.No
        )
        if confirm != QMessageBox.Yes:
            return

        if self.is_multi:
            track_ids = [track.track_id for track in self.tracks]
            if not self.controller.update.update_entities("Track", track_ids, album_id=None):
                QMessageBox.warning(
                    self, "Error", "Failed to remove album for the selected tracks."
                )
        else:
            try:
                self.controller.update.update_entity("Track", self.track.track_id, album_id=None)
            except SQLAlchemyError as e:
                logger.error(f"Failed to remove primary album: {e}")
                QMessageBox.warning(self, "Error", f"Failed to remove album:\n{e}")
                return

        self._refresh_tracks()
        self.load(self.tracks)

    def _refresh_tracks(self):
        # track.album is cached on the Track instance once accessed, and
        # expire_on_commit=False means a plain commit() won't invalidate it,
        # so re-fetching the same identity-mapped Track below is a no-op
        # unless we explicitly expire the relationship first.
        session = self.controller.get.session
        updated_tracks = []
        for track in self.tracks:
            session.expire(track, ["album"])
            updated = self.controller.get.get_entity_object("Track", track_id=track.track_id)
            updated_tracks.append(updated if updated else track)
        self.tracks = updated_tracks

    def _open_primary_album(self):
        album = getattr(self.track, "album", None)
        if album:
            self._open_album_by_id(album.album_id)

    def _open_wiki_link(self):
        if self._wiki_link:
            webbrowser.open(self._wiki_link)

    def _open_mb_link(self):
        if self._mb_link:
            webbrowser.open(self._mb_link)

    # ── Find canonical album (MusicBrainz) ─────────────────────────────────

    def _find_canonical_album(self):
        track_name = self._live_track_name()
        artist_name = self.track.primary_artist_names
        if artist_name == "Unknown Artist":
            artist_name = None

        dialog = MusicBrainzMatchDialog(
            entity_label=f"canonical album for '{track_name}'",
            search_call=lambda: search_canonical_album_for_recording(
                track_name, artist_name, recording_mbid=self.track.MBID
            ),
            parent=self,
        )
        if dialog.exec() != QDialog.Accepted:
            if dialog.candidate_count() == 0:
                self._offer_artist_name_suggestions(artist_name)
            return

        picked = dialog.result_candidate()
        if picked is None:
            return

        # Best-effort awards enrichment for every entity this match creates
        # or links (the new album, its artists, the track) is deferred into
        # this list and run on a worker thread at the end -- see
        # _import_award_data. Doing it inline froze the editor for minutes
        # when MusicBrainz was slow (musicbrainzngs retries a stuck request
        # 8x at a 30s socket timeout each).
        award_jobs: list[tuple[str, int, str]] = []

        album = self._resolve_or_create_album_from_mb(picked.enrichment, award_jobs)
        if album is None:
            return

        # Stamp the specific recording onto the track too, not just the
        # album -- fill-blank only, same convention as the Identification
        # tab's own MB lookup. Without this, picking one of several
        # distinct recordings of the same title (see
        # search_canonical_album_for_recording's "Recording N of M"
        # labeling) would leave the track's own identity ambiguous even
        # after the user has explicitly resolved which performance it is.
        update_kwargs = {"album_id": album.album_id}
        recording_mbid = picked.enrichment.get("recording_mbid")
        if recording_mbid and not self.track.MBID:
            update_kwargs["MBID"] = recording_mbid

        try:
            self.controller.update.update_entity("Track", self.track.track_id, **update_kwargs)
        except SQLAlchemyError as e:
            logger.error(f"Failed to set canonical album: {e}")
            QMessageBox.warning(self, "Error", f"Failed to set album:\n{e}")
            return

        if "MBID" in update_kwargs:
            award_jobs.append(("Track", self.track.track_id, update_kwargs["MBID"]))

        self._import_award_data(award_jobs)

        self._refresh_tracks()
        self.load(self.tracks)

    def _import_award_data(self, award_jobs: list[tuple[str, int, str]]) -> None:
        """Run best-effort awards enrichment for a canonical-album match's
        entities on a worker thread.

        Each job is a MusicBrainz lookup, and musicbrainzngs retries a stuck
        request up to 8x at a 30s socket timeout -- inline on the UI thread
        that froze the track editor for minutes with no feedback. Off the UI
        thread, MusicBrainzImportDialog gives it a spinner and a working
        Cancel (which detaches the worker to finish on its own). The writes
        run against the worker thread's own scoped session, same as every
        other MusicBrainzWorker call in this codebase.
        """
        if not award_jobs:
            return

        def _run():
            session = self.controller.get.session
            for entity_type, entity_id, mbid in award_jobs:
                import_awards_for_entity(session, entity_type, entity_id, mbid)
            return len(award_jobs)

        MusicBrainzImportDialog(entity_label="award data", fetch_call=_run, parent=self).exec()

    def _offer_artist_name_suggestions(self, artist_name: str | None) -> None:
        """Called when a canonical-album search finds zero matches at all --
        as opposed to the user just skipping a non-empty picker list. Most
        likely cause is a misspelled/incomplete artist credit on the track,
        so surface a few similarly-named MusicBrainz artists rather than
        just leaving the user with a bare empty dialog."""
        if not artist_name:
            return
        try:
            suggestions = suggest_artist_names(artist_name)
        except MusicBrainzLookupError as e:
            logger.warning(f"Could not fetch artist name suggestions: {e}")
            return
        if not suggestions:
            return
        QMessageBox.information(
            self,
            "No Matches Found",
            f"No MusicBrainz releases were found for artist '{artist_name}'.\n\n"
            "Similar artists on MusicBrainz:\n" + "\n".join(f"• {s}" for s in suggestions),
        )

    def _resolve_or_create_album_from_mb(
        self, enrichment: dict, award_jobs: list[tuple[str, int, str]]
    ):
        """MBID match, then name/alias match confirmed by artist overlap,
        else create a new album -- same tiered idea as
        AlbumMusicBrainzReviewDialog._resolve_artist, adapted for Album.

        Any (entity_type, entity_id, mbid) that needs awards enrichment is
        appended to `award_jobs` rather than looked up inline -- see
        _import_award_data."""
        mbid = enrichment.get("MBID")
        if mbid:
            album = self.controller.get.get_entity_object("Album", MBID=mbid)
            if album is not None:
                return album

        album_name = enrichment.get("album_name")
        artist_credits = enrichment.get("artist_credits") or []
        if album_name:
            candidate_album = self.controller.get.resolve_entity_or_alias(
                "Album", "album_name", album_name
            )
            if candidate_album is not None:
                # Overlap, not exact-set-equality (unlike get_album_exists) --
                # this is matching one recording's credit against a possibly
                # larger album artist roster, not deduping a full import.
                existing_ids = {a.MBID for a in candidate_album.album_artists if a.MBID}
                credit_ids = {c["mbid"] for c in artist_credits if c.get("mbid")}
                if existing_ids & credit_ids:
                    return candidate_album

        return self._create_album_from_mb(enrichment, award_jobs)

    def _create_album_from_mb(self, enrichment: dict, award_jobs: list[tuple[str, int, str]]):
        album_name = enrichment.get("album_name") or "Unknown Album"
        artist_credits = enrichment.get("artist_credits") or []
        artist_names = (
            ", ".join(c["name"] for c in artist_credits if c.get("name")) or "Unknown Artist"
        )

        date_bits = [
            str(enrichment[k])
            for k in ("release_year", "release_month", "release_day")
            if enrichment.get(k) is not None
        ]
        detail_lines = [f"Album:   {album_name}", f"Artist:  {artist_names}"]
        if date_bits:
            detail_lines.append(f"Release: {'-'.join(date_bits)}")
        if enrichment.get("release_type"):
            detail_lines.append(f"Type:    {enrichment['release_type']}")
        if enrichment.get("status"):
            detail_lines.append(f"Status:  {enrichment['status']}")
        if enrichment.get("country"):
            detail_lines.append(f"Country: {enrichment['country']}")
        if enrichment.get("MBID"):
            detail_lines.append(f"MBID:    {enrichment['MBID']}")

        confirm = QMessageBox.question(
            self,
            "Create New Album",
            "No matching album found locally. Create a new album with "
            "these details?\n\n" + "\n".join(detail_lines),
            QMessageBox.Yes | QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return None

        try:
            new_album = self.controller.add.add_entity(
                "Album",
                album_name=album_name,
                release_year=enrichment.get("release_year"),
                release_month=enrichment.get("release_month"),
                release_day=enrichment.get("release_day"),
                MBID=enrichment.get("MBID"),
                release_group_MBID=enrichment.get("release_group_mbid"),
            )
            if enrichment.get("release_group_mbid"):
                award_jobs.append(("Album", new_album.album_id, enrichment["release_group_mbid"]))
            for credit in artist_credits:
                artist = self._resolve_or_create_artist(
                    credit.get("mbid"), credit.get("name"), award_jobs
                )
                if artist is None:
                    continue
                self.controller.add.add_entity(
                    "AlbumRoleAssociation",
                    album_id=new_album.album_id,
                    artist_id=artist.artist_id,
                    role_id=1,  # "Album Artist" -- seeded convention, see db_defaults.py
                )
        except SQLAlchemyError as e:
            logger.error(f"Failed to create album from MusicBrainz match: {e}")
            QMessageBox.warning(self, "Error", f"Failed to create album:\n{e}")
            return None

        return new_album

    def _resolve_or_create_artist(
        self, artist_mbid, artist_name, award_jobs: list[tuple[str, int, str]]
    ):
        if not artist_name:
            return None
        if artist_mbid:
            artist = self.controller.get.get_entity_object("Artist", MBID=artist_mbid)
            if artist is not None:
                return artist
        artist = self.controller.get.resolve_entity_or_alias("Artist", "artist_name", artist_name)
        if artist is not None and not artist.MBID:
            if artist_mbid:
                self.controller.update.update_entity("Artist", artist.artist_id, MBID=artist_mbid)
                artist.MBID = artist_mbid
                award_jobs.append(("Artist", artist.artist_id, artist_mbid))
            return artist
        # A name match whose row already carries a (necessarily different)
        # MBID is a distinct real-world artist -- ignore it and create a
        # new Artist instead of merging two different people.
        new_artist = self.controller.add.add_entity(
            "Artist", artist_name=artist_name, MBID=artist_mbid
        )
        if artist_mbid:
            award_jobs.append(("Artist", new_artist.artist_id, artist_mbid))
        return new_artist

    # ── Virtual appearance search / add / remove ──────────────────────────

    def _on_virt_search_changed(self, text: str):
        self._virt_add_btn.setEnabled(bool(text.strip()))

    def _add_virtual(self):
        album = self._resolve_album(self._virt_search)
        if not album:
            if self._virt_search.text().strip():
                QMessageBox.warning(self, "Error", "Could not resolve or create album.")
            return
        track_num = self._virt_track_num.value() or None
        disc_num = self._virt_disc_num.value() or None
        try:
            self.controller.add.add_entity(
                "AlbumVirtualTrack",
                album_id=album.album_id,
                track_id=self.track.track_id,
                virtual_track_number=track_num,
                virtual_disc_number=disc_num,
            )
        except SQLAlchemyError as e:
            logger.error(f"Failed to add virtual appearance: {e}")
            QMessageBox.warning(self, "Error", f"Failed to add virtual appearance:\n{e}")
            return
        self._virt_search.reset()
        self._virt_track_num.setValue(0)
        self._virt_disc_num.setValue(0)
        # virtual_appearances is cached on the Track instance once accessed;
        # expire it before re-fetching (see _refresh_tracks for the same
        # pattern with the album relationship).
        self.controller.get.session.expire(self.track, ["virtual_appearances"])
        updated = self.controller.get.get_entity_object("Track", track_id=self.track.track_id)
        if updated:
            self.tracks = [updated]
        self.load(self.tracks)

    def _remove_virtual(self, virtual_id: int):
        try:
            self.controller.delete.delete_entity("AlbumVirtualTrack", virtual_id=virtual_id)
        except SQLAlchemyError as e:
            logger.error(f"Failed to remove virtual appearance: {e}")
            QMessageBox.warning(self, "Error", f"Failed to remove:\n{e}")
            return
        self.controller.get.session.expire(self.track, ["virtual_appearances"])
        updated = self.controller.get.get_entity_object("Track", track_id=self.track.track_id)
        if updated:
            self.tracks = [updated]
        self.load(self.tracks)

    # ── Open album editor ─────────────────────────────────────────────────

    def _open_album_by_id(self, album_id: int):
        try:
            from src.album.base_album_edit import AlbumEditor

            album = self.controller.get.get_entity_object("Album", album_id=album_id)
            if album:
                dlg = AlbumEditor(self.controller, album, self)
                dlg.exec()
                # Refresh track data after album edit closes; expire the
                # cached album relationship first (see _refresh_tracks).
                self.controller.get.session.expire(self.track, ["album"])
                updated = self.controller.get.get_entity_object(
                    "Track", track_id=self.track.track_id
                )
                if updated:
                    self.tracks = [updated]
                self.load(self.tracks)
        except (SQLAlchemyError, RuntimeError) as e:
            logger.error(f"Failed to open album editor: {e}")
            QMessageBox.warning(self, "Error", f"Could not open album editor:\n{e}")
