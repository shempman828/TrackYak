"""SamplesTab: which tracks this track samples, and which tracks sample it."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QButtonGroup, QComboBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox, QPushButton, QVBoxLayout, QWidget

from src.common.widgets.entity_completer_context import track_context_map
from src.common.widgets.entity_completer_edit import ContextItemDelegate
from src.foundation.logger_config import logger
from src.foundation.status_utility import show_status_message
from src.track.edit.track_edit_basetab import _BaseTab

# Direction constants for the add bar's toggle.
DIR_USES = "uses"  # this track -> other track (this track samples the other)
DIR_USED_BY = "used_by"  # other track -> this track (the other track samples this one)


def _track_display(track):
    """Return the bare track name shown for a search result."""
    # Album and artist context go in the dimmed secondary text (see ContextItemDelegate).
    return getattr(track, "track_name", None) or str(track)


def _build_track_index(tracks, exclude_id=None):
    """Return display_string -> track_id, adding ' #id' to names that two tracks share."""
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
        # activated, not currentIndexChanged: the first result is already current after the
        # combo is filled, so picking it would not emit currentIndexChanged.
        self.name_combo.activated.connect(self._on_name_selected)
        self.name_combo.view().setItemDelegate(ContextItemDelegate(lambda name: self._display_to_context.get(name, ""), self.name_combo.view()))
        search_row.addWidget(self.name_combo)

        add_btn = QPushButton("Add")
        add_btn.clicked.connect(self._handle_add)
        search_row.addWidget(add_btn)

        outer.addLayout(dir_box)
        outer.addLayout(search_row)

        self._relabel_toggle()

    def set_exclude_id(self, exclude_id):
        """Hide the track with `exclude_id` (the edited track) from results."""
        self._exclude_id = exclude_id

    def current_direction(self):
        """Return DIR_USES or DIR_USED_BY from the toggle."""
        return DIR_USES if self.btn_uses.isChecked() else DIR_USED_BY

    def set_track_name(self, name):
        """Set the edited track's name used in the toggle labels."""
        self._track_name = name or "This track"
        self._relabel_toggle()

    def _relabel_toggle(self, *_args):
        """Write "X samples Y" sentences on both toggle buttons."""
        other = self.name_search.text().strip() or None
        uses_sentence = _sample_sentence(DIR_USES, self._track_name, other)
        used_by_sentence = _sample_sentence(DIR_USED_BY, self._track_name, other)
        self.btn_uses.setText(uses_sentence)
        self.btn_uses.setToolTip(uses_sentence + ".")
        self.btn_used_by.setText(used_by_sentence)
        self.btn_used_by.setToolTip(used_by_sentence + ".")

    def _on_name_search(self, text: str):
        """Fill the results combo with tracks whose name contains `text` (2+ chars)."""
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
            self._display_to_context = {display: context_by_id.get(track_id, "") for display, track_id in self._display_to_id.items()}
            for display in sorted(self._display_to_id.keys()):
                self.name_combo.addItem(display, self._display_to_id[display])
            self.name_combo.setVisible(self.name_combo.count() > 0)
        else:
            self._display_to_id = {}
            self._display_to_context = {}
            self.name_combo.setVisible(False)
        self.name_combo.blockSignals(False)

    def _on_name_selected(self, index: int):
        """Use the picked result as the track to link."""
        if index >= 0:
            self._matched_id = self.name_combo.currentData()
            self.name_search.blockSignals(True)
            self.name_search.setText(self.name_combo.currentText())
            self.name_search.blockSignals(False)
            self._relabel_toggle()

    def _handle_add(self):
        """Add the link to the picked track (or to the result whose name matches exactly)."""
        if self._matched_id is None:
            self._matched_id = self._display_to_id.get(self.name_search.text().strip())
        if self._matched_id is None:
            show_status_message(self, "No track selected. Choose an existing track from the search results.")
            return
        self._on_add(direction=self.current_direction(), matched_track_id=self._matched_id)

    def clear_inputs(self):
        """Clear the search field and the results."""
        self.name_search.clear()
        self.name_combo.blockSignals(True)
        self.name_combo.clear()
        self.name_combo.setVisible(False)
        self.name_combo.blockSignals(False)
        self._matched_id = None
        self._display_to_id = {}


class SamplesTab(_BaseTab):
    """Add and remove sample links of a single track."""

    saves_immediately = True  # add/remove write to the DB at once

    def __init__(self, tracks: list, controller, parent=None):
        super().__init__(tracks, controller, parent)
        self._build_ui()
        if not self.is_multi:
            self.add_bar.set_exclude_id(self.track.track_id)

    def _build_ui(self):
        """Build the add bar and the two sample lists."""
        layout = QVBoxLayout(self)

        self.add_bar = _AddSampleBar(controller=self.controller, on_add=self._handle_add)
        layout.addWidget(self.add_bar)

        # Samples used list
        layout.addWidget(QLabel("Samples Used (tracks this track samples):"))
        self._used_list = QListWidget()
        self._used_list.itemDoubleClicked.connect(self._open_sampled)
        self._used_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self._used_list.customContextMenuRequested.connect(lambda pos: self._list_context_menu(self._used_list, pos, self._remove_used))
        layout.addWidget(self._used_list)

        # Sampled-by list
        layout.addWidget(QLabel("Sampled By (tracks that sample this track):"))
        self._by_list = QListWidget()
        self._by_list.itemDoubleClicked.connect(self._open_sampler)
        self._by_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self._by_list.customContextMenuRequested.connect(lambda pos: self._list_context_menu(self._by_list, pos, self._remove_by))
        layout.addWidget(self._by_list)

        layout.addWidget(QLabel("Double-click any track to open its editor."))

    def load(self, tracks: list) -> None:
        """Show both sample lists of the track (single track only)."""
        self.tracks = tracks
        self._used_list.clear()
        self._by_list.clear()

        if self.is_multi:
            placeholder = QListWidgetItem("(Select a single track to manage samples)")
            placeholder.setFlags(Qt.NoItemFlags)  # not selectable, not removable
            self._used_list.addItem(placeholder)
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
        """Reload the track with fresh sample relationships and redisplay."""
        # expire_on_commit=False: the cached relationships survive a commit, so expire them.
        refreshed = self.controller.get.get_entity_object("Track", track_id=self.track.track_id)
        if refreshed:
            self.tracks[0] = refreshed
            self.controller.get.session.expire(refreshed, ["samples_used", "sampled_by_tracks"])
        self.load(self.tracks)

    def _existing_sample_keys(self):
        """Return the (sampled_by_id, sampled_id) pairs that already exist for this track."""
        keys = {(self.track.track_id, s.sampled_id) for s in self.track.samples_used}
        keys |= {(s.sampled_by_id, self.track.track_id) for s in self.track.sampled_by_tracks}
        return keys

    def _handle_add(self, direction, matched_track_id):
        """Add one sample link in `direction` to the matched track."""
        if matched_track_id == self.track.track_id:
            show_status_message(self, "A track can't sample itself.")
            return

        kwargs = {"sampled_by_id": self.track.track_id, "sampled_id": matched_track_id} if direction == DIR_USES else {"sampled_by_id": matched_track_id, "sampled_id": self.track.track_id}

        if (kwargs["sampled_by_id"], kwargs["sampled_id"]) in self._existing_sample_keys():
            show_status_message(self, "That sample relationship already exists.")
            return

        # add_entity catches its own DB errors and returns None.
        if self.controller.add.add_entity("Samples", **kwargs) is None:
            logger.error(f"Failed to add sample {kwargs}")
            QMessageBox.critical(self, "Error", "Could not add the sample. See the log for details.")
            return

        self.add_bar.clear_inputs()
        self._reload_and_refresh()

    def _delete_sample(self, sampled_by_id, sampled_id):
        """Delete one sample link."""
        if not self.controller.delete.delete_entity("Samples", sampled_by_id=sampled_by_id, sampled_id=sampled_id):
            logger.error(f"Failed to remove sample {sampled_by_id} -> {sampled_id}")
            QMessageBox.critical(self, "Error", "Could not remove the sample. See the log for details.")
            return
        self._reload_and_refresh()

    def _remove_used(self, item):
        """Remove the "samples used" link shown by `item`."""
        other_id = item.data(Qt.UserRole) if item is not None else None
        if other_id is not None:
            self._delete_sample(sampled_by_id=self.track.track_id, sampled_id=other_id)

    def _remove_by(self, item):
        """Remove the "sampled by" link shown by `item`."""
        other_id = item.data(Qt.UserRole) if item is not None else None
        if other_id is not None:
            self._delete_sample(sampled_by_id=other_id, sampled_id=self.track.track_id)

    def _open_sampled(self, item):
        """Open the editor of a sampled track."""
        self._open_track(item.data(Qt.UserRole))

    def _open_sampler(self, item):
        """Open the editor of a sampling track."""
        self._open_track(item.data(Qt.UserRole))

    def _open_track(self, track_id):
        """Open a non-modal editor for `track_id`; reload this tab when it saves."""
        if track_id is None:
            return
        track = self.controller.get.get_entity_object("Track", track_id=track_id)
        if track is None:
            return
        from src.track.edit.track_edit import TrackEditDialog

        dialog = TrackEditDialog(track, self.controller, self)
        dialog.accepted.connect(self._reload_and_refresh)
        self._sample_edit_dialog = dialog
        dialog.show()

    @staticmethod
    def _list_context_menu(list_widget, pos, remove_cb):
        """Offer "Remove" for the item under the cursor."""
        item = list_widget.itemAt(pos)
        if item is None or item.data(Qt.UserRole) is None:
            return
        menu = QMenu(list_widget)
        menu.addAction("Remove", lambda: remove_cb(item))
        menu.exec(list_widget.mapToGlobal(pos))
