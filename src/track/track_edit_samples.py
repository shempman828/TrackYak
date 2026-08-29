# ---------------------------------------------------------------------------
# SamplesTab
# ---------------------------------------------------------------------------
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
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
from sqlalchemy.exc import SQLAlchemyError

from src.common.entity_completer_context import track_context_map
from src.common.entity_completer_edit import ContextItemDelegate
from src.core.logger_config import logger
from src.core.status_utility import show_status_message
from src.track.track_edit_basetab import _BaseTab

# Direction constants for the add bar's toggle.
DIR_USES = "uses"  # this track -> other track (this track samples the other)
DIR_USED_BY = "used_by"  # other track -> this track (the other track samples this one)


def _track_display(track):
    """Bare track name -- the value the picked suggestion feeds back into
    the field. Album (and now primary artist) context rides the dimmed
    secondary-text channel of the results dropdown instead of being baked
    into this string (see track_context_map / ContextItemDelegate)."""
    return getattr(track, "track_name", None) or str(track)


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


# Results are searched on demand (min 2 chars) instead of preloading every
# track in the library into a completer index -- that used to run a
# SELECT * FROM Track plus a lazy-loaded album lookup per distinct album on
# every dialog open, regardless of the library's size.
_MAX_SEARCH_RESULTS = 50


class _AddSampleBar(QWidget):
    """Direction toggle + track name search (min 2 chars) + Add, for one new sample link."""

    def __init__(self, controller, on_add, track_name=None, parent=None):
        super().__init__(parent)
        self.controller = controller
        self._on_add = on_add
        self._track_name = track_name or "This track"
        self._exclude_id = None
        self._matched_id = None
        self._display_to_id: dict = {}
        self._display_to_context: dict = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 4, 0, 0)
        outer.setSpacing(4)

        # Direction toggle: "This track samples ___" vs. "___ samples this track".
        self.dir_group = QButtonGroup(self)
        self.dir_group.setExclusive(True)
        self.btn_uses = QPushButton()
        self.btn_uses.setObjectName("dirLeft")
        self.btn_used_by = QPushButton()
        self.btn_used_by.setObjectName("dirRight")
        for b in (self.btn_uses, self.btn_used_by):
            b.setCheckable(True)
            b.setProperty("class", "dirToggle")
            b.setCursor(Qt.PointingHandCursor)
            self.dir_group.addButton(b)
        self.btn_uses.setChecked(True)

        dir_box = QHBoxLayout()
        dir_box.setSpacing(0)
        dir_box.addWidget(self.btn_uses)
        dir_box.addWidget(self.btn_used_by)

        # Track name is searched on demand (min 2 chars) instead of a
        # completer preloaded with every track in the library -- see
        # module docstring / _MAX_SEARCH_RESULTS.
        search_row = QHBoxLayout()
        search_row.setSpacing(6)

        self.name_search = QLineEdit()
        self.name_search.setPlaceholderText("Track name… (min 2 chars)")
        self.name_search.textChanged.connect(self._on_name_search)
        self.name_search.textChanged.connect(self._relabel_toggle)
        self.name_search.returnPressed.connect(self._handle_add)
        search_row.addWidget(self.name_search, 1)

        self.name_combo = QComboBox()
        self.name_combo.setVisible(False)
        self.name_combo.currentIndexChanged.connect(self._on_name_selected)
        self.name_combo.view().setItemDelegate(
            ContextItemDelegate(
                lambda name: self._display_to_context.get(name, ""), self.name_combo.view()
            )
        )
        search_row.addWidget(self.name_combo)

        add_btn = QPushButton("Add")
        add_btn.setFixedWidth(60)
        add_btn.clicked.connect(self._handle_add)
        search_row.addWidget(add_btn)

        outer.addLayout(dir_box)
        outer.addLayout(search_row)

        self._relabel_toggle()

    def set_exclude_id(self, exclude_id):
        self._exclude_id = exclude_id

    def current_direction(self):
        return DIR_USES if self.btn_uses.isChecked() else DIR_USED_BY

    def set_track_name(self, name):
        self._track_name = name or "This track"
        self._relabel_toggle()

    def _relabel_toggle(self, *_args):
        other = self.name_search.text().strip() or None
        uses_sentence = _sample_sentence(DIR_USES, self._track_name, other)
        used_by_sentence = _sample_sentence(DIR_USED_BY, self._track_name, other)
        self.btn_uses.setText(uses_sentence)
        self.btn_uses.setToolTip(uses_sentence + ".")
        self.btn_used_by.setText(used_by_sentence)
        self.btn_used_by.setToolTip(used_by_sentence + ".")

    def _on_name_search(self, text: str):
        self._matched_id = None
        text = text.strip()
        self.name_combo.blockSignals(True)
        self.name_combo.clear()
        if len(text) >= 2:
            tracks = self.controller.get.get_all_entities("Track", track_name__contains=text) or []
            # Cap *before* building the display index: each entry touches
            # track.album_name, a lazy-loaded relationship, so a common
            # substring (e.g. "love") matching thousands of tracks would
            # otherwise trigger thousands of album lookups just to throw
            # all but 50 of them away.
            tracks = tracks[:_MAX_SEARCH_RESULTS]
            self._display_to_id = _build_track_index(tracks, exclude_id=self._exclude_id)
            context_by_id = track_context_map(tracks)
            self._display_to_context = {
                display: context_by_id.get(track_id, "")
                for display, track_id in self._display_to_id.items()
            }
            for display in sorted(self._display_to_id.keys()):
                self.name_combo.addItem(display, self._display_to_id[display])
            self.name_combo.setVisible(self.name_combo.count() > 0)
        else:
            self._display_to_id = {}
            self._display_to_context = {}
            self.name_combo.setVisible(False)
        self.name_combo.blockSignals(False)

    def _on_name_selected(self, index: int):
        if index >= 0:
            self._matched_id = self.name_combo.currentData()
            self.name_search.blockSignals(True)
            self.name_search.setText(self.name_combo.currentText())
            self.name_search.blockSignals(False)
            self._relabel_toggle()

    def _handle_add(self):
        if self._matched_id is None:
            show_status_message(
                self, "No track selected. Choose an existing track from the search results."
            )
            return
        self._on_add(direction=self.current_direction(), matched_track_id=self._matched_id)

    def clear_inputs(self):
        self.name_search.clear()
        self.name_combo.blockSignals(True)
        self.name_combo.clear()
        self.name_combo.setVisible(False)
        self.name_combo.blockSignals(False)
        self._matched_id = None
        self._display_to_id = {}


class SamplesTab(_BaseTab):
    def __init__(self, tracks: list, controller, parent=None):
        super().__init__(tracks, controller, parent)
        self._build_ui()
        if not self.is_multi:
            self.add_bar.set_exclude_id(self.track.track_id)

    def _build_ui(self):
        layout = QVBoxLayout(self)

        self.add_bar = _AddSampleBar(controller=self.controller, on_add=self._handle_add)
        layout.addWidget(self.add_bar)

        # Samples used list
        layout.addWidget(QLabel("Samples Used (tracks this track samples):"))
        self._used_list = QListWidget()
        self._used_list.itemDoubleClicked.connect(self._open_sampled)
        self._used_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self._used_list.customContextMenuRequested.connect(
            lambda pos: self._list_context_menu(self._used_list, pos, self._remove_used)
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

    def _reload_and_refresh(self):
        # get_entity_object returns the SAME identity-mapped Track instance
        # (this session never expunges it) -- reassigning self.tracks[0] to
        # it is a no-op for staleness purposes. The actual write above went
        # through the Samples model directly, not by mutating
        # track.samples_used/sampled_by_tracks, and the session is opened
        # with expire_on_commit=False (src/db/db_engine.py), so those two
        # relationship collections -- once loaded by the load() call below --
        # would otherwise keep showing pre-edit state indefinitely. Expire
        # them so the reload actually reflects the DB write just made.
        try:
            refreshed = self.controller.get.get_entity_object("Track", track_id=self.track.track_id)
            if refreshed:
                self.tracks[0] = refreshed
                self.controller.get.session.expire(refreshed, ["samples_used", "sampled_by_tracks"])
        except SQLAlchemyError as e:
            logger.warning(f"Could not reload track: {e}")
        self.load(self.tracks)

    def _existing_sample_keys(self):
        """Set of (sampled_by_id, sampled_id) already present for this track."""
        keys = {(self.track.track_id, s.sampled_id) for s in self.track.samples_used}
        keys |= {(s.sampled_by_id, self.track.track_id) for s in self.track.sampled_by_tracks}
        return keys

    def _handle_add(self, direction, matched_track_id):
        if matched_track_id == self.track.track_id:
            show_status_message(self, "A track can't sample itself.")
            return

        if direction == DIR_USES:
            kwargs = {"sampled_by_id": self.track.track_id, "sampled_id": matched_track_id}
        else:
            kwargs = {"sampled_by_id": matched_track_id, "sampled_id": self.track.track_id}

        if (kwargs["sampled_by_id"], kwargs["sampled_id"]) in self._existing_sample_keys():
            show_status_message(self, "That sample relationship already exists.")
            return

        try:
            result = self.controller.add.add_entity("Samples", **kwargs)
            if result is None:
                raise RuntimeError("add_entity returned None")
        except (SQLAlchemyError, RuntimeError) as e:
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
        except (SQLAlchemyError, RuntimeError) as e:
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
        dialog.accepted.connect(self._reload_and_refresh)
        self._sample_edit_dialog = dialog
        dialog.show()

    @staticmethod
    def _list_context_menu(list_widget, pos, remove_cb):
        if list_widget.currentItem():
            menu = QMenu(list_widget)
            menu.addAction("Remove", remove_cb)
            menu.exec(list_widget.mapToGlobal(pos))
