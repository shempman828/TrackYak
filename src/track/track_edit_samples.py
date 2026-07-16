# ---------------------------------------------------------------------------
# SamplesTab
# ---------------------------------------------------------------------------
from __future__ import annotations

from PySide6.QtCore import QStringListModel, Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QCompleter,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.core.logger_config import logger
from src.core.status_utility import show_status_message
from src.track.track_edit_basetab import _BaseTab

# Direction constants for the add bar's toggle.
DIR_USES = "uses"  # this track -> other track (this track samples the other)
DIR_USED_BY = "used_by"  # other track -> this track (the other track samples this one)


def _track_display(track):
    """'Track Name' or 'Track Name  [Album]' when an album is known."""
    name = getattr(track, "track_name", None) or str(track)
    album = getattr(track, "album_name", None)
    return f"{name}  [{album}]" if album else name


def _build_track_index(tracks, exclude_id=None):
    """
    display_string -> track_id, with automatic disambiguation for duplicate
    names (appends track_id when two tracks share a display string).
    """
    index = {}
    seen_names = {}
    for t in tracks:
        tid = getattr(t, "track_id", None)
        if exclude_id is not None and tid == exclude_id:
            continue
        display = _track_display(t)
        if display in seen_names and seen_names[display] != tid:
            index.pop(display, None)
            index[f"{display} #{seen_names[display]}"] = seen_names[display]
            display = f"{display} #{tid}"
        seen_names[display] = tid
        index[display] = tid
    return index


def _sample_sentence(direction, this_name, other_name=None):
    """Always-active-voice sentence — 'X samples Y' — naming both tracks."""
    this = this_name or "This track"
    other = other_name or "the selected track"
    if direction == DIR_USES:
        return f"{this} samples {other}"
    return f"{other} samples {this}"


class _TrackNameEdit(QLineEdit):
    """
    QLineEdit with a QCompleter over known tracks. Sampling always targets an
    existing track, so unlike a name field that can create-on-miss, the add
    button here stays disabled until a completion is actually picked.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setPlaceholderText("Track name…")
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

    def _on_completion_picked(self, text):
        self._matched_id = self._display_to_id.get(text)

    def _on_manual_edit(self, _text):
        self._matched_id = None

    def matched_track_id(self):
        return self._matched_id

    def reset(self):
        self.clear()
        self._matched_id = None


class _AddSampleBar(QWidget):
    """Direction toggle + track name (with completer) + Add, for one new sample link."""

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

    def __init__(self, on_add, track_name=None, parent=None):
        super().__init__(parent)
        self._on_add = on_add
        self._track_name = track_name or "This track"

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(6)

        # Direction toggle: "This track samples ___" vs. "___ samples this track".
        self.dir_group = QButtonGroup(self)
        self.dir_group.setExclusive(True)
        self.btn_uses = QPushButton()
        self.btn_uses.setObjectName("dirLeft")
        self.btn_used_by = QPushButton()
        self.btn_used_by.setObjectName("dirRight")
        for b in (self.btn_uses, self.btn_used_by):
            b.setCheckable(True)
            b.setStyleSheet(self.DIR_TOGGLE_STYLE)
            b.setCursor(Qt.PointingHandCursor)
            self.dir_group.addButton(b)
        self.btn_uses.setChecked(True)

        dir_box = QHBoxLayout()
        dir_box.setSpacing(0)
        dir_box.addWidget(self.btn_uses)
        dir_box.addWidget(self.btn_used_by)

        self.name_edit = _TrackNameEdit()
        self.name_edit.textChanged.connect(self._relabel_toggle)
        self._relabel_toggle()

        add_btn = QPushButton("Add")
        add_btn.setFixedWidth(60)
        add_btn.clicked.connect(self._handle_add)
        self.name_edit.returnPressed.connect(self._handle_add)

        layout.addLayout(dir_box)
        layout.addWidget(self.name_edit, 1)
        layout.addWidget(add_btn)

    def current_direction(self):
        return DIR_USES if self.btn_uses.isChecked() else DIR_USED_BY

    def set_track_name(self, name):
        self._track_name = name or "This track"
        self._relabel_toggle()

    def _relabel_toggle(self, *_args):
        other = self.name_edit.text().strip() or None
        uses_sentence = _sample_sentence(DIR_USES, self._track_name, other)
        used_by_sentence = _sample_sentence(DIR_USED_BY, self._track_name, other)
        self.btn_uses.setText(uses_sentence)
        self.btn_uses.setToolTip(uses_sentence + ".")
        self.btn_used_by.setText(used_by_sentence)
        self.btn_used_by.setToolTip(used_by_sentence + ".")

    def _handle_add(self):
        matched_id = self.name_edit.matched_track_id()
        if matched_id is None:
            show_status_message(
                self, "No track selected. Choose an existing track from the completion list."
            )
            return
        self._on_add(direction=self.current_direction(), matched_track_id=matched_id)

    def clear_inputs(self):
        self.name_edit.reset()

    def set_completer_index(self, index):
        self.name_edit.set_index(index)


class SamplesTab(_BaseTab):
    def __init__(self, tracks: list, controller, parent=None):
        super().__init__(tracks, controller, parent)
        self._build_ui()
        self._refresh_completer_index()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        self.add_bar = _AddSampleBar(on_add=self._handle_add)
        layout.addWidget(self.add_bar)

        # Samples used list
        layout.addWidget(QLabel("Samples Used (tracks this track samples):"))
        self._used_list = QListWidget()
        self._used_list.itemDoubleClicked.connect(self._open_sampled)
        self._used_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self._used_list.customContextMenuRequested.connect(
            lambda pos: self._list_context_menu(
                self._used_list, pos, self._remove_used
            )
        )
        layout.addWidget(self._used_list)

        # Sampled-by list
        layout.addWidget(QLabel("Sampled By (tracks that sample this track):"))
        self._by_list = QListWidget()
        self._by_list.itemDoubleClicked.connect(self._open_sampler)
        self._by_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self._by_list.customContextMenuRequested.connect(
            lambda pos: self._list_context_menu(self._by_list, pos, self._remove_by)
        )
        layout.addWidget(self._by_list)

        layout.addWidget(QLabel("Double-click any track to open its editor."))

    def load(self, tracks: list) -> None:
        self.tracks = tracks
        self._used_list.clear()
        self._by_list.clear()

        # Samples tab is single-track only in a meaningful way
        if self.is_multi:
            self._used_list.addItem("(Select a single track to manage samples)")
            self.add_bar.setEnabled(False)
            return

        self.add_bar.setEnabled(True)
        self.add_bar.set_track_name(self.track.track_name)

        for sample in self.track.samples_used:
            st = sample.sampled
            if st:
                text = _track_display(st)
                item = QListWidgetItem(text)
                item.setData(Qt.UserRole, st.track_id)
                self._used_list.addItem(item)

        for sample in self.track.sampled_by_tracks:
            st = sample.sampled_by
            if st:
                text = _track_display(st)
                item = QListWidgetItem(text)
                item.setData(Qt.UserRole, st.track_id)
                self._by_list.addItem(item)

    def _refresh_completer_index(self):
        if self.is_multi:
            return
        tracks = self.controller.get.get_all_entities("Track") or []
        index = _build_track_index(tracks, exclude_id=self.track.track_id)
        self.add_bar.set_completer_index(index)

    def _reload_and_refresh(self):
        try:
            refreshed = self.controller.get.get_entity_object(
                "Track", track_id=self.track.track_id
            )
            if refreshed:
                self.tracks[0] = refreshed
        except Exception as e:
            logger.warning(f"Could not reload track: {e}")
        self.load(self.tracks)
        self._refresh_completer_index()

    def _existing_sample_keys(self):
        """Set of (sampled_by_id, sampled_id) already present for this track."""
        keys = {(self.track.track_id, s.sampled_id) for s in self.track.samples_used}
        keys |= {
            (s.sampled_by_id, self.track.track_id) for s in self.track.sampled_by_tracks
        }
        return keys

    def _handle_add(self, direction, matched_track_id):
        if matched_track_id == self.track.track_id:
            show_status_message(self, "A track can't sample itself.")
            return

        if direction == DIR_USES:
            kwargs = dict(sampled_by_id=self.track.track_id, sampled_id=matched_track_id)
        else:
            kwargs = dict(sampled_by_id=matched_track_id, sampled_id=self.track.track_id)

        if (kwargs["sampled_by_id"], kwargs["sampled_id"]) in self._existing_sample_keys():
            show_status_message(self, "That sample relationship already exists.")
            return

        try:
            result = self.controller.add.add_entity("Samples", **kwargs)
            if result is None:
                raise RuntimeError("add_entity returned None")
        except Exception as e:
            logger.error(f"Failed to add sample: {e}")
            QMessageBox.critical(self, "Error", f"Could not add sample:\n{e}")
            return

        self.add_bar.clear_inputs()
        self._reload_and_refresh()

    def _delete_sample(self, sampled_by_id, sampled_id):
        try:
            ok = self.controller.delete.delete_entity(
                "Samples", sampled_by_id=sampled_by_id, sampled_id=sampled_id
            )
            if not ok:
                raise RuntimeError("delete_entity returned False")
        except Exception as e:
            logger.error(f"Failed to remove sample: {e}")
            QMessageBox.critical(self, "Error", f"Could not remove sample:\n{e}")
            return
        self._reload_and_refresh()

    def _remove_used(self):
        item = self._used_list.currentItem()
        if not item:
            return
        other_id = item.data(Qt.UserRole)
        self._delete_sample(sampled_by_id=self.track.track_id, sampled_id=other_id)

    def _remove_by(self):
        item = self._by_list.currentItem()
        if not item:
            return
        other_id = item.data(Qt.UserRole)
        self._delete_sample(sampled_by_id=other_id, sampled_id=self.track.track_id)

    def _open_sampled(self, item):
        self._open_track(item.data(Qt.UserRole))

    def _open_sampler(self, item):
        self._open_track(item.data(Qt.UserRole))

    def _open_track(self, track_id):
        if track_id is None:
            return
        track = self.controller.get.get_entity_object("Track", track_id=track_id)
        if track is None:
            return
        from src.track.track_edit import TrackEditDialog

        dialog = TrackEditDialog(track, self.controller, self)
        if dialog.exec() == QDialog.Accepted:
            self._reload_and_refresh()

    @staticmethod
    def _list_context_menu(list_widget, pos, remove_cb):
        if list_widget.currentItem():
            menu = QMenu(list_widget)
            menu.addAction("Remove", remove_cb)
            menu.exec(list_widget.mapToGlobal(pos))
