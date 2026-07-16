# ══════════════════════════════════════════════════════════════════════════════
# Tab: Influences
# ══════════════════════════════════════════════════════════════════════════════
from PySide6.QtCore import QStringListModel, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCompleter,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.core.logger_config import logger

# Direction constants — stored in the table's UserRole+1 slot per row.
DIR_INFLUENCED = "influenced"  # this artist -> other artist (other was influenced by this one)
DIR_INFLUENCER = "influencer"  # other artist -> this artist (this one was influenced by other)

_DIRECTION_COLOR = {
    DIR_INFLUENCED: QColor("#2f7dd1"),  # blue: this artist influenced them
    DIR_INFLUENCER: QColor("#c17a2a"),  # amber: they influenced this artist
}


def _esc_amp(text):
    """Escape '&' so it doesn't act as a Qt mnemonic prefix in button/label text."""
    return (text or "").replace("&", "&&")


def _cap(text):
    """Capitalize just the first character — safe for names ('lady Gaga' stays intact)."""
    return text[0].upper() + text[1:] if text else text


def _relationship_sentence(direction, artist_name, other_name=None):
    """
    Always-active-voice sentence naming both artists — 'X influenced Y' — so
    the relationship can never be misread the way passive "Influenced by" or
    a bare arrow could be. When `other_name` isn't known yet (e.g. an empty
    add-bar field), falls back to a grammatically-correct pronoun.
    """
    name = artist_name or "this artist"
    if direction == DIR_INFLUENCED:
        # this artist -> other: this artist is the influencer (subject)
        other = other_name or "them"
        return _cap(f"{name} influenced {other}")
    # other -> this artist: other artist is the influencer (subject)
    other = other_name or "they"
    return _cap(f"{other} influenced {name}")


# ── artist lookup / completer plumbing ──────────────────────────────────────


def _fetch_all_artists(controller):
    """Fetch all artists for completer indexing."""
    try:
        return controller.get.get_all_entities("Artist") or []
    except Exception as e:
        logger.warning(f"Could not fetch artists for completer: {e}")
        return []


def _find_or_create_artist(controller, name, **create_kwargs):
    """
    Look up an artist by name; create one if none is found.
    Raises on error — let the caller catch and show a dialog.
    """
    result = controller.get.get_entity_object("Artist", artist_name=name)
    if result:
        return result[0] if isinstance(result, list) else result
    return controller.add.add_entity("Artist", artist_name=name, **create_kwargs)


class ArtistNameEdit(QLineEdit):
    """
    QLineEdit with a QCompleter over known artists. When the user picks a
    completion, we remember the matched artist_id directly so add-time skips
    the name-lookup roundtrip entirely (and can't collide with a same-named
    artist). Any manual edit after a match clears the lock — typing a new
    name always means "maybe create new."
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setPlaceholderText("Artist name…")
        self._display_to_id = {}
        self._matched_id = None

        self.textEdited.connect(self._on_manual_edit)

    def set_index(self, display_to_id: dict):
        """Rebuild the completer's backing model."""
        self._display_to_id = dict(display_to_id)
        model = QStringListModel(sorted(self._display_to_id.keys()), self)
        completer = QCompleter(model, self)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchContains)
        completer.activated.connect(self._on_completion_picked)
        self.setCompleter(completer)

    def add_to_index(self, display, artist_id):
        """Register a freshly created artist so it's matchable immediately."""
        self._display_to_id[display] = artist_id
        self.set_index(self._display_to_id)

    def _on_completion_picked(self, text):
        self._matched_id = self._display_to_id.get(text)

    def _on_manual_edit(self, _text):
        self._matched_id = None

    def matched_artist_id(self):
        return self._matched_id

    def reset(self):
        self.clear()
        self._matched_id = None


def _artist_display(artist):
    """'Artist Name' or 'Artist Name (Type)' when disambiguation is available."""
    name = getattr(artist, "artist_name", None) or str(artist)
    atype = getattr(artist, "artist_type", None)
    return f"{name} ({atype})" if atype else name


def _build_artist_index(artists):
    """
    display_string -> artist_id, with automatic disambiguation for duplicate
    names (appends artist_id when two artists share a display string).
    """
    index = {}
    seen_names = {}
    for a in artists:
        display = _artist_display(a)
        aid = getattr(a, "artist_id", None)
        if display in seen_names and seen_names[display] != aid:
            # collision: disambiguate both the existing and the new entry
            index.pop(display, None)
            index[f"{display} #{seen_names[display]}"] = seen_names[display]
            display = f"{display} #{aid}"
        seen_names[display] = aid
        index[display] = aid
    return index


# ── filter chips ─────────────────────────────────────────────────────────────


class _FilterChips(QWidget):
    """
    Small exclusive chip row: All / "<artist> influenced them" / "They influenced <artist>".
    Always active voice and names the edited artist explicitly so the filter can't be misread.
    """

    CHIP_STYLE = """
        QPushButton {
            border: 1px solid palette(mid);
            border-radius: 11px;
            padding: 3px 12px;
            background: palette(base);
        }
        QPushButton:checked {
            background: palette(highlight);
            color: palette(highlighted-text);
            border-color: palette(highlight);
        }
        QPushButton:hover:!checked {
            background: palette(alternate-base);
        }
    """

    def __init__(self, on_changed, parent=None):
        super().__init__(parent)
        self._on_changed = on_changed
        self._artist_name = "this artist"
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.group = QButtonGroup(self)
        self.group.setExclusive(True)

        self._buttons = {}
        for key in (None, DIR_INFLUENCED, DIR_INFLUENCER):
            btn = QPushButton()
            btn.setCheckable(True)
            btn.setStyleSheet(self.CHIP_STYLE)
            btn.setCursor(Qt.PointingHandCursor)
            self.group.addButton(btn)
            self._buttons[key] = btn
            layout.addWidget(btn)
        layout.addStretch()

        self._buttons[None].setChecked(True)
        self.group.buttonClicked.connect(self._emit_change)
        self._counts = {}
        self._relabel()

    def _emit_change(self, _btn):
        self._on_changed(self.current_filter())

    def current_filter(self):
        for key, btn in self._buttons.items():
            if btn.isChecked():
                return key
        return None

    def set_artist_name(self, name):
        self._artist_name = name or "this artist"
        self._relabel()

    def set_counts(self, counts: dict):
        """counts: {None: total, DIR_INFLUENCED: n, DIR_INFLUENCER: n}"""
        self._counts = counts
        self._relabel()

    def _relabel(self):
        labels = {
            None: "All",
            DIR_INFLUENCED: _relationship_sentence(DIR_INFLUENCED, self._artist_name),
            DIR_INFLUENCER: _relationship_sentence(DIR_INFLUENCER, self._artist_name),
        }
        for key, btn in self._buttons.items():
            n = self._counts.get(key, 0)
            btn.setText(_esc_amp(f"{labels[key]} ({n})"))


# ── add bar ──────────────────────────────────────────────────────────────────


class _AddInfluenceBar(QWidget):
    """
    Single add row for either direction: a direction toggle, artist name
    (with completer), description, and Add. Replaces the old per-panel
    duplicated toolbar.
    """

    DIR_TOGGLE_STYLE = """
        QPushButton {
            border: 1px solid palette(mid);
            padding: 4px 10px;
            background: palette(base);
        }
        QPushButton:checked {
            background: palette(highlight);
            color: palette(highlighted-text);
            border-color: palette(highlight);
        }
        QPushButton#dirLeft { border-top-left-radius: 6px; border-bottom-left-radius: 6px; }
        QPushButton#dirRight { border-top-right-radius: 6px; border-bottom-right-radius: 6px; }
    """

    def __init__(self, on_add, artist_name=None, parent=None):
        super().__init__(parent)
        self._on_add = on_add
        self._artist_name = artist_name or "this artist"

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(6)

        # Direction toggle. Each label is a full "X influenced Y" sentence
        # naming the current artist explicitly. It live-updates as the name
        # field is typed into — "Prince influenced them" becomes "Prince
        # influenced Lady Gaga" — so the direction can't be picked backwards.
        self.dir_group = QButtonGroup(self)
        self.dir_group.setExclusive(True)
        self.btn_influenced = QPushButton()
        self.btn_influenced.setObjectName("dirLeft")
        self.btn_influencer = QPushButton()
        self.btn_influencer.setObjectName("dirRight")
        for b in (self.btn_influenced, self.btn_influencer):
            b.setCheckable(True)
            b.setStyleSheet(self.DIR_TOGGLE_STYLE)
            b.setCursor(Qt.PointingHandCursor)
            self.dir_group.addButton(b)
        self.btn_influenced.setChecked(True)

        dir_box = QHBoxLayout()
        dir_box.setSpacing(0)
        dir_box.addWidget(self.btn_influenced)
        dir_box.addWidget(self.btn_influencer)

        self.name_edit = ArtistNameEdit()
        self.name_edit.textChanged.connect(self._relabel_toggle)
        self.desc_edit = QLineEdit()
        self.desc_edit.setPlaceholderText("Description (optional)")

        self._relabel_toggle()

        add_btn = QPushButton("Add")
        add_btn.setFixedWidth(60)
        add_btn.clicked.connect(self._handle_add)
        self.name_edit.returnPressed.connect(self._handle_add)
        self.desc_edit.returnPressed.connect(self._handle_add)

        layout.addLayout(dir_box)
        layout.addWidget(self.name_edit, 3)
        layout.addWidget(self.desc_edit, 3)
        layout.addWidget(add_btn)

    def current_direction(self):
        return DIR_INFLUENCED if self.btn_influenced.isChecked() else DIR_INFLUENCER

    def set_artist_name(self, name):
        self._artist_name = name or "this artist"
        self._relabel_toggle()

    def _relabel_toggle(self, *_args):
        name = self._artist_name
        other = self.name_edit.text().strip() or None

        influenced_sentence = _relationship_sentence(DIR_INFLUENCED, name, other)
        influencer_sentence = _relationship_sentence(DIR_INFLUENCER, name, other)

        self.btn_influenced.setText(_esc_amp(influenced_sentence))
        self.btn_influenced.setToolTip(influenced_sentence + ".")
        self.btn_influencer.setText(_esc_amp(influencer_sentence))
        self.btn_influencer.setToolTip(influencer_sentence + ".")

    def _handle_add(self):
        name = self.name_edit.text().strip()
        if not name:
            return
        self._on_add(
            direction=self.current_direction(),
            name=name,
            description=self.desc_edit.text().strip() or None,
            matched_artist_id=self.name_edit.matched_artist_id(),
        )

    def clear_inputs(self):
        self.name_edit.reset()
        self.desc_edit.clear()

    def set_completer_index(self, index):
        self.name_edit.set_index(index)

    def register_new_artist(self, display, artist_id):
        self.name_edit.add_to_index(display, artist_id)


# ── main tab ─────────────────────────────────────────────────────────────────


class InfluencesTab(QWidget):
    """
    Unified influence table (both directions, one artist/description grid)
    with filter chips instead of two permanently-split panels. Row count no
    longer dictates how much screen space a direction "deserves" — whichever
    direction has more entries just gets more visible rows.
    """

    # Artist column stays for sorting/scanning; Relationship spells out the
    # full "X influenced Y" sentence naming both artists so it's unambiguous
    # on its own, without needing to cross-reference the Artist column.
    COLUMNS = ["Artist", "Relationship", "Description"]

    def __init__(self, controller, artist, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.artist = artist
        self._rows = []  # raw data: list of dicts {direction, name, description, influence_id}
        self._build_ui()
        self._refresh_completer_index()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        self.filter_chips = _FilterChips(on_changed=lambda _f: self._render())
        self.filter_chips.set_artist_name(getattr(self.artist, "artist_name", None))
        layout.addWidget(self.filter_chips)

        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeToContents
        )
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeToContents
        )
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        layout.addWidget(self.table, 1)

        self.add_bar = _AddInfluenceBar(
            on_add=self._handle_add,
            artist_name=getattr(self.artist, "artist_name", None),
        )
        layout.addWidget(self.add_bar)

        remove_row = QHBoxLayout()
        remove_row.addStretch()
        self.remove_btn = QPushButton("Remove Selected")
        self.remove_btn.clicked.connect(self._handle_remove_selected)
        remove_row.addWidget(self.remove_btn)
        layout.addLayout(remove_row)

    # ── data loading ────────────────────────────────────────────────────────

    def load(self, artist):
        self.artist = artist
        artist_name = getattr(artist, "artist_name", None)
        self.filter_chips.set_artist_name(artist_name)
        self.add_bar.set_artist_name(artist_name)
        self._rows = [
            {
                "direction": DIR_INFLUENCED,
                "name": rel.influenced.artist_name,
                "description": rel.description or "",
                "influence_id": getattr(rel, "influence_id", None),
            }
            for rel in getattr(artist, "influencer_relations", [])
            if rel.influenced is not None
        ] + [
            {
                "direction": DIR_INFLUENCER,
                "name": rel.influencer.artist_name,
                "description": rel.description or "",
                "influence_id": getattr(rel, "influence_id", None),
            }
            for rel in getattr(artist, "influenced_relations", [])
            if rel.influencer is not None
        ]
        self._render()

    def _reload_and_refresh(self):
        try:
            refreshed = self.controller.get.get_entity_object(
                "Artist", artist_id=self.artist.artist_id
            )
            if refreshed:
                self.artist = refreshed
        except Exception as e:
            logger.warning(f"Could not reload artist: {e}")
        self.load(self.artist)

    def _refresh_completer_index(self):
        artists = _fetch_all_artists(self.controller)
        index = _build_artist_index(artists)
        self.add_bar.set_completer_index(index)

    # ── rendering ────────────────────────────────────────────────────────────

    def _render(self):
        active_filter = self.filter_chips.current_filter()
        visible = [
            r
            for r in self._rows
            if active_filter is None or r["direction"] == active_filter
        ]

        artist_name = getattr(self.artist, "artist_name", None)

        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        for r in visible:
            row = self.table.rowCount()
            self.table.insertRow(row)

            self.table.setItem(row, 0, QTableWidgetItem(r["name"]))

            sentence = _relationship_sentence(r["direction"], artist_name, r["name"])
            rel_item = QTableWidgetItem(sentence)
            rel_item.setForeground(_DIRECTION_COLOR[r["direction"]])
            rel_item.setData(Qt.UserRole, r["influence_id"])
            rel_item.setData(Qt.UserRole + 1, r["direction"])
            self.table.setItem(row, 1, rel_item)

            self.table.setItem(row, 2, QTableWidgetItem(r["description"]))
        self.table.setSortingEnabled(True)

        counts = {None: len(self._rows)}
        for key in (DIR_INFLUENCED, DIR_INFLUENCER):
            counts[key] = sum(1 for r in self._rows if r["direction"] == key)
        self.filter_chips.set_counts(counts)

    # ── add / remove ─────────────────────────────────────────────────────────

    def _handle_add(self, direction, name, description, matched_artist_id):
        try:
            if matched_artist_id is not None:
                other = self.controller.get.get_entity_object(
                    "Artist", artist_id=matched_artist_id
                )
                other = other[0] if isinstance(other, list) else other
            else:
                other = _find_or_create_artist(self.controller, name)
                # newly created (or newly matched-by-name) artist: make it
                # immediately available in the completer without a full reload
                self.add_bar.register_new_artist(
                    _artist_display(other), getattr(other, "artist_id", None)
                )
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not find/create artist:\n{e}")
            return

        if direction == DIR_INFLUENCED:
            kwargs = dict(
                influencer_id=self.artist.artist_id, influenced_id=other.artist_id
            )
        else:
            kwargs = dict(
                influencer_id=other.artist_id, influenced_id=self.artist.artist_id
            )

        try:
            self.controller.add.add_entity(
                "ArtistInfluence", description=description, **kwargs
            )
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not add influence:\n{e}")
            return

        self._reload_and_refresh()
        self.add_bar.clear_inputs()

    def _selected_influence_ids(self):
        ids = []
        for idx in self.table.selectionModel().selectedRows():
            item = self.table.item(idx.row(), 1)
            iid = item.data(Qt.UserRole) if item else None
            if iid is not None:
                ids.append(iid)
        return ids

    def _handle_remove_selected(self):
        ids = self._selected_influence_ids()
        if not ids:
            QMessageBox.information(
                self, "No Selection", "Please select one or more rows first."
            )
            return
        if len(ids) > 1:
            reply = QMessageBox.question(
                self,
                "Remove Influences",
                f"Remove {len(ids)} selected influence relations?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return
        self._delete_influences(ids)

    def _delete_influences(self, influence_ids):
        errors = []
        for iid in influence_ids:
            try:
                self.controller.delete.delete_entity("ArtistInfluence", iid)
            except Exception as e:
                errors.append(str(e))
        if errors:
            QMessageBox.critical(
                self, "Error", "Could not remove some influences:\n" + "\n".join(errors)
            )
        self._reload_and_refresh()

    def _show_context_menu(self, pos):
        if not self.table.itemAt(pos):
            return
        menu = QMenu(self)
        remove_action = menu.addAction("Remove Selected")
        action = menu.exec(self.table.viewport().mapToGlobal(pos))
        if action == remove_action:
            self._handle_remove_selected()
