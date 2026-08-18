"""
album_musicbrainz_review_ui.py

UI construction for AlbumMusicBrainzReviewDialog: the unmatched-tracks
table, alias/credit/label/location checkbox groups, and dialog autosizing.

Mixed into AlbumMusicBrainzReviewDialog. Expects the host class to
provide: self.album, self.detail, self.aliases, self._match_summary,
self._guaranteed_missing, self._remaining_mb, self._remaining_local_options,
self._guesses, self._guess_scores, self._manual_combos,
self._alias_checks, self._album_credit_checks, self._label_checks,
self._credit_checks, self._location_checks, self.has_content,
self._on_manual_combo_changed, self._on_accept, self.reject, and to be a
QDialog subclass.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialogButtonBox,
    QGroupBox,
    QHeaderView,
    QLabel,
    QProgressBar,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.album.album_musicbrainz_review_import import _format_mb_track_label
from src.album.album_musicbrainz_track_matching import _SKIP
from src.common.match_confidence import confidence_color, confidence_label
from src.musicbrainz.musicbrainz_artist import MBAlias
from src.musicbrainz.musicbrainz_release import MBLabelInfo


class AlbumMusicBrainzReviewUIMixin:
    def _usable_aliases(self) -> list[MBAlias]:
        existing = {
            (a.alias_name or "").strip().lower()
            for a in (self.album.album_aliases or [])
        }
        own_name = (self.album.album_name or "").strip().lower()
        out = []
        for alias in self.aliases:
            key = alias.name.strip().lower()
            if not key or key == own_name or key in existing:
                continue
            out.append(alias)
        return out

    def _usable_labels(self) -> list[MBLabelInfo]:
        existing_mbids = {
            p.MBID for p in (self.album.publishers or []) if p.MBID
        }
        existing_names = {
            (p.publisher_name or "").strip().lower()
            for p in (self.album.publishers or [])
        }
        out = []
        for label in self.detail.labels:
            if label.mbid in existing_mbids:
                continue
            if label.name.strip().lower() in existing_names:
                continue
            out.append(label)
        return out

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(self._match_summary))

        if self._guaranteed_missing:
            warning = QLabel(
                f"⚠ Error: MusicBrainz lists {self._guaranteed_missing} more "
                f"track(s) than your album has. At least {self._guaranteed_missing} "
                "track(s) below don't exist in your library and can't be "
                "matched to anything -- your album may be missing tracks."
            )
            warning.setWordWrap(True)
            warning.setStyleSheet("color: darkred; font-weight: bold;")
            layout.addWidget(warning)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        inner_layout = QVBoxLayout(inner)

        if self._remaining_mb:
            self.has_content = True
            box = QGroupBox("Unmatched Tracks — pick a local track or skip")
            box_layout = QVBoxLayout(box)

            table = QTableWidget(len(self._remaining_mb), 3)
            table.setHorizontalHeaderLabels(
                ["MusicBrainz Track", "Match to Local Track", "Confidence"]
            )
            table.verticalHeader().setVisible(False)
            table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            table.setSelectionMode(QAbstractItemView.NoSelection)
            header = table.horizontalHeader()
            header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
            header.setSectionResizeMode(1, QHeaderView.Stretch)
            header.setSectionResizeMode(2, QHeaderView.ResizeToContents)

            for row, mbt in enumerate(self._remaining_mb):
                # There is no candidate left at all -- not merely a case that
                # failed to auto-match -- once every unmatched local track has
                # already been claimed. That's the case the row must call out
                # as an error rather than a routine manual pick.
                no_local_candidates = not self._remaining_local_options

                mb_item = QTableWidgetItem(_format_mb_track_label(mbt))
                if no_local_candidates:
                    mb_item.setBackground(QColor(255, 224, 224))
                    bold_font = QFont()
                    bold_font.setBold(True)
                    mb_item.setFont(bold_font)
                table.setItem(row, 0, mb_item)

                combo = QComboBox()
                combo.addItem(_SKIP, None)
                for local in self._remaining_local_options:
                    local_side = f", side {local.side}" if local.side else ""
                    combo.addItem(
                        f"{local.track_name} (currently track {local.track_number or '?'}{local_side})",
                        local,
                    )
                guess = self._guesses.get(id(mbt))
                score = self._guess_scores.get(id(mbt))
                if guess is not None:
                    idx = combo.findData(guess)
                    if idx >= 0:
                        combo.setCurrentIndex(idx)
                table.setCellWidget(row, 1, combo)
                self._manual_combos.append((combo, mbt))

                if no_local_candidates:
                    conf_text = "Not in your album — ERROR"
                    conf_color = QColor(Qt.red)
                elif guess is not None:
                    conf_text = confidence_label(score)
                    conf_color = confidence_color(score)
                else:
                    conf_text = "No suggestion"
                    conf_color = confidence_color(0.0)
                conf_item = QTableWidgetItem(conf_text)
                conf_item.setForeground(conf_color)
                conf_item.setTextAlignment(Qt.AlignCenter)
                if no_local_candidates:
                    conf_item.setBackground(QColor(255, 224, 224))
                    bold_font = QFont()
                    bold_font.setBold(True)
                    conf_item.setFont(bold_font)
                table.setItem(row, 2, conf_item)

            # Connect after every row's combo exists (and its initial guess
            # is set) so a mid-loop selection doesn't fire a refresh against
            # a still-partially-built self._manual_combos.
            for combo, _mbt in self._manual_combos:
                combo.currentIndexChanged.connect(self._on_manual_combo_changed)

            table.resizeRowsToContents()
            table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            row_height_total = sum(
                table.rowHeight(r) for r in range(table.rowCount())
            )
            table.setFixedHeight(
                header.height() + row_height_total + 2 * table.frameWidth()
            )
            box_layout.addWidget(table)
            inner_layout.addWidget(box)

        aliases = self._usable_aliases()
        if aliases:
            self.has_content = True
            box = QGroupBox("Album Aliases")
            box_layout = QVBoxLayout(box)
            for alias in aliases:
                label = f"{alias.name} ({alias.type})" if alias.type else alias.name
                cb = QCheckBox(label)
                cb.setChecked(True)
                box_layout.addWidget(cb)
                self._alias_checks.append((cb, alias))
            inner_layout.addWidget(box)

        if self.detail.credits:
            self.has_content = True
            box = QGroupBox("Album Credits")
            box_layout = QVBoxLayout(box)
            for credit in self.detail.credits:
                cb = QCheckBox(f"{credit.artist_name} — {credit.role_name}")
                cb.setChecked(True)
                box_layout.addWidget(cb)
                self._album_credit_checks.append((cb, credit))
            inner_layout.addWidget(box)

        labels = self._usable_labels()
        if labels:
            self.has_content = True
            box = QGroupBox("Publisher(s)")
            box_layout = QVBoxLayout(box)
            for label in labels:
                text = label.name
                if label.founders:
                    founder_names = ", ".join(f.name for f in label.founders)
                    text += f" — founded by {founder_names}"
                cb = QCheckBox(text)
                cb.setChecked(True)
                box_layout.addWidget(cb)
                self._label_checks.append((cb, label))
            inner_layout.addWidget(box)

        for mbt in self.detail.tracks:
            if not mbt.credits:
                continue
            self.has_content = True
            box = QGroupBox(_format_mb_track_label(mbt))
            box_layout = QVBoxLayout(box)
            for credit in mbt.credits:
                cb = QCheckBox(f"{credit.artist_name} — {credit.role_name}")
                cb.setChecked(True)
                box_layout.addWidget(cb)
                self._credit_checks.append((cb, mbt, credit))
            inner_layout.addWidget(box)

        if self.detail.place_chains:
            self.has_content = True
            box = QGroupBox("Recording Locations")
            box_layout = QVBoxLayout(box)
            for place_mbid, chain in self.detail.place_chains.items():
                tracks_here = [
                    mbt
                    for mbt in self.detail.tracks
                    if mbt.location_place_mbid == place_mbid
                ]
                if not tracks_here:
                    continue
                chain_label = ", ".join(
                    node["name"] for node in chain if node.get("name")
                )
                track_titles = ", ".join(mbt.title for mbt in tracks_here)
                cb = QCheckBox(f"{chain_label}\n  → {track_titles}")
                cb.setChecked(True)
                box_layout.addWidget(cb)
                self._location_checks.append((cb, place_mbid, tracks_here))
            inner_layout.addWidget(box)

        if not self.has_content:
            inner_layout.addWidget(QLabel("Nothing further to review."))

        inner_layout.addStretch(1)
        scroll.setWidget(inner)
        layout.addWidget(scroll, stretch=1)
        self._scroll = scroll

        self.progress_status_label = QLabel()
        self.progress_status_label.setVisible(False)
        layout.addWidget(self.progress_status_label)
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self._on_accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        self._autosize(inner)

    def _autosize(self, content: QWidget) -> None:
        """Grow the dialog to fit the review content, within reason.

        QScrollArea's own sizeHint() doesn't grow with its child widget, so
        left alone the dialog stays pinned to setMinimumSize() no matter how
        wide the credit/alias/track rows actually are. Match the sizing
        convention used by publisher_fuzzy_match._autosize: measure the
        scrolled widget directly, clamp to a fraction of the screen so a
        long review can't blow past it, with the configured minimum as a
        floor.
        """
        hint = content.sizeHint()

        screen = self.screen() or QApplication.primaryScreen()
        available = screen.availableGeometry()

        # Extra room for the scrollbar plus the summary label/buttons/margins
        # that sit outside the scrolled content itself.
        width = hint.width() + 60
        height = hint.height() + 120

        width = max(self.minimumWidth(), min(width, int(available.width() * 0.9)))
        height = max(self.minimumHeight(), min(height, int(available.height() * 0.9)))

        self.resize(width, height)
