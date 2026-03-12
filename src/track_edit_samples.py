# ---------------------------------------------------------------------------
# SamplesTab
# ---------------------------------------------------------------------------
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QVBoxLayout,
)

from src.logger_config import logger
from src.track_edit_basetab import _BaseTab


class SamplesTab(_BaseTab):
    def __init__(self, tracks: list, controller, parent=None):
        super().__init__(tracks, controller, parent)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # Search / add used samples
        search_row = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search tracks to sample… (min 2 chars)")
        self._search.textChanged.connect(self._on_search)
        search_row.addWidget(self._search)

        self._combo = QComboBox()
        self._combo.setVisible(False)
        self._combo.currentIndexChanged.connect(self._on_selected)
        search_row.addWidget(self._combo)

        self._add_btn = QPushButton("Add Sample")
        self._add_btn.setEnabled(False)
        self._add_btn.clicked.connect(self._add)
        search_row.addWidget(self._add_btn)
        layout.addLayout(search_row)

        # Samples used list
        layout.addWidget(QLabel("Samples Used (tracks this track samples):"))
        self._used_list = QListWidget()
        self._used_list.itemDoubleClicked.connect(self._open_sampled)
        self._used_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self._used_list.customContextMenuRequested.connect(
            lambda pos: self._list_context_menu(
                self._used_list, pos, self._remove_sample
            )
        )
        layout.addWidget(self._used_list)

        # Sampled-by list (read-only)
        layout.addWidget(QLabel("Sampled By (tracks that sample this track):"))
        self._by_list = QListWidget()
        self._by_list.itemDoubleClicked.connect(self._open_sampler)
        layout.addWidget(self._by_list)

        layout.addWidget(QLabel("Double-click any track to open its editor."))

    def load(self, tracks: list) -> None:
        self.tracks = tracks
        self._used_list.clear()
        self._by_list.clear()

        # Samples tab is single-track only in a meaningful way
        if self.is_multi:
            self._used_list.addItem("(Select a single track to manage samples)")
            self._add_btn.setEnabled(False)
            return

        for sample in self.track.samples_used:
            st = sample.sampled
            if st:
                text = st.track_name + (f"  [{st.album_name}]" if st.album_name else "")
                item = QListWidgetItem(text)
                item.setData(Qt.UserRole, st.track_id)
                self._used_list.addItem(item)

        for sample in self.track.sampled_by_tracks:
            st = sample.sampled_by
            if st:
                text = st.track_name + (f"  [{st.album_name}]" if st.album_name else "")
                item = QListWidgetItem(text)
                item.setData(Qt.UserRole, st.track_id)
                self._by_list.addItem(item)

    def _on_search(self, text: str):
        text = text.strip()
        self._combo.blockSignals(True)
        self._combo.clear()
        if len(text) >= 2:
            results = self.controller.get.get_entity_object("Track", track_name=text)
            if results is not None:
                items = results if isinstance(results, list) else [results]
                for t in items:
                    self._combo.addItem(t.track_name, t.track_id)
            self._combo.setVisible(self._combo.count() > 0)
        else:
            self._combo.setVisible(False)
        self._combo.blockSignals(False)
        self._add_btn.setEnabled(len(text) >= 2 and self._combo.count() > 0)

    def _on_selected(self, index: int):
        if index >= 0:
            self._search.blockSignals(True)
            self._search.setText(self._combo.currentText())
            self._search.blockSignals(False)

    def _add(self):
        if self._combo.currentData() is None:
            return
        sampled_id = self._combo.currentData()
        try:
            self.controller.add.add_entity(
                "TrackSample",
                sampler_id=self.track.track_id,
                sampled_id=sampled_id,
            )
        except Exception as e:
            logger.error(f"Failed to add sample: {e}")
        self._search.clear()
        self._combo.setVisible(False)
        self.load(self.tracks)

    def _remove_sample(self):
        item = self._used_list.currentItem()
        if not item:
            return
        sampled_id = item.data(Qt.UserRole)
        try:
            self.controller.delete.delete_entity(
                "TrackSample",
                sampler_id=self.track.track_id,
                sampled_id=sampled_id,
            )
        except Exception as e:
            logger.error(f"Failed to remove sample: {e}")
        self.load(self.tracks)

    def _open_sampled(self, item):
        self._open_track(item.data(Qt.UserRole))

    def _open_sampler(self, item):
        self._open_track(item.data(Qt.UserRole))

    def _open_track(self, track_id):
        pass

    @staticmethod
    def _list_context_menu(list_widget, pos, remove_cb):
        if list_widget.currentItem():
            menu = QMenu(list_widget)
            menu.addAction("Remove", remove_cb)
            menu.exec(list_widget.mapToGlobal(pos))
