from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QTreeWidgetItem

from src.sync.device_card import format_file_size


class SyncSelectionMixin:
    """
    Playlist/mood checklist tree and the derived selection state (track
    count label, sync button enablement) for SyncView.

    Expects the host class to provide: self.sync_tree, self.sync_manager,
    self.current_profile, self.profiles, self.profile_store,
    self.clear_before_sync_check, self.track_count_label, self.sync_btn,
    self.transcode_mp3_check, self.bitrate_combo.
    """

    # -----------------------------------------------------------------------
    # Playlist / mood selection tree
    # -----------------------------------------------------------------------

    def _add_hierarchy(self, parent_item: QTreeWidgetItem, items: list[dict], id_key: str):
        """Recursively add items under parent_item, following each item's parent_id."""
        children_map: dict = {}
        for it in items:
            children_map.setdefault(it.get("parent_id"), []).append(it)
        for siblings in children_map.values():
            siblings.sort(key=lambda it: it["name"].lower())

        def add_level(parent_id, node: QTreeWidgetItem):
            for it in children_map.get(parent_id, []):
                tree_item = QTreeWidgetItem(node, [f"{it['name']}  ({it['track_count']} tracks)"])
                tree_item.setFlags(tree_item.flags() | Qt.ItemIsUserCheckable)
                tree_item.setCheckState(0, Qt.Unchecked)
                tree_item.setToolTip(0, it.get("description") or "")
                tree_item.setData(0, Qt.UserRole, it)
                add_level(it[id_key], tree_item)

        add_level(None, parent_item)

    def _iter_sync_items(self):
        """Yield every checkable (playlist/mood) QTreeWidgetItem in the tree."""

        def walk(item):
            for i in range(item.childCount()):
                child = item.child(i)
                if child.data(0, Qt.UserRole) is not None:
                    yield child
                yield from walk(child)

        yield from walk(self.sync_tree.invisibleRootItem())

    def _refresh_sync_items(self):
        """Reload playlists and moods from the database into the sync tree."""
        self.sync_tree.blockSignals(True)
        self.sync_tree.clear()

        header_font = QFont()
        header_font.setBold(True)

        playlists = self.sync_manager.get_playlists()
        playlists_header = QTreeWidgetItem(self.sync_tree, [f"PLAYLISTS  ({len(playlists)})"])
        playlists_header.setFlags(Qt.ItemIsEnabled)
        playlists_header.setFont(0, header_font)
        self._add_hierarchy(playlists_header, playlists, "playlist_id")

        moods = self.sync_manager.get_moods()
        moods_header = QTreeWidgetItem(self.sync_tree, [f"MOODS  ({len(moods)})"])
        moods_header.setFlags(Qt.ItemIsEnabled)
        moods_header.setFont(0, header_font)
        self._add_hierarchy(moods_header, moods, "mood_id")

        self.sync_tree.expandAll()
        self.sync_tree.blockSignals(False)

        if self.current_profile:
            self._apply_profile_selection()

    def _apply_profile_selection(self):
        """Tick the checkboxes that match the current profile's saved playlist/mood IDs."""
        if not self.current_profile:
            return
        saved_playlist_ids = set(self.current_profile.playlist_ids)
        saved_mood_ids = set(self.current_profile.mood_ids)
        self.sync_tree.blockSignals(True)
        for item in self._iter_sync_items():
            data = item.data(0, Qt.UserRole)
            if data["kind"] == "mood":
                checked = data["mood_id"] in saved_mood_ids
            else:
                checked = data["playlist_id"] in saved_playlist_ids
            item.setCheckState(0, Qt.Checked if checked else Qt.Unchecked)
        self.sync_tree.blockSignals(False)
        self._update_selected_items()

    def _save_current_profile_selections(self):
        """Write current checkbox state back into the active profile and persist."""
        if not self.current_profile:
            return
        playlist_ids = []
        mood_ids = []
        for item in self._iter_sync_items():
            if item.checkState(0) == Qt.Checked:
                data = item.data(0, Qt.UserRole)
                if data["kind"] == "mood":
                    mood_ids.append(data["mood_id"])
                else:
                    playlist_ids.append(data["playlist_id"])
        self.current_profile.playlist_ids = playlist_ids
        self.current_profile.mood_ids = mood_ids
        self.current_profile.clear_before_sync = self.clear_before_sync_check.isChecked()
        self.profile_store.save(self.profiles)

    def _update_selected_items(self):
        """Rebuild self.selected_items and update the track count label."""
        self.selected_items = []
        total_tracks = 0
        total_size = 0
        total_lossless_size = 0
        total_lossless_duration = 0.0
        for item in self._iter_sync_items():
            if item.checkState(0) == Qt.Checked:
                data = item.data(0, Qt.UserRole)
                self.selected_items.append(data)
                total_tracks += data.get("track_count", 0)
                total_size += data.get("size", 0) or 0
                total_lossless_size += data.get("lossless_size", 0) or 0
                total_lossless_duration += data.get("lossless_duration", 0) or 0

        if self.selected_items:
            n_playlists = sum(1 for it in self.selected_items if it["kind"] == "playlist")
            n_moods = sum(1 for it in self.selected_items if it["kind"] == "mood")
            parts = []
            if n_playlists:
                parts.append(f"{n_playlists} playlist(s)")
            if n_moods:
                parts.append(f"{n_moods} mood(s)")
            size_text = self._selection_size_text(
                total_size, total_lossless_size, total_lossless_duration
            )
            self.track_count_label.setText(
                f"{' + '.join(parts)}  ·  {total_tracks} tracks  ·  {size_text}"
            )
        else:
            self.track_count_label.setText("")

        self._update_sync_button_state()

    def _selection_size_text(self, total_size, lossless_size, lossless_duration):
        """
        Human-readable size for the selection summary. When "Convert lossless
        files to MP3" is on and the selection actually contains lossless audio,
        show a post-conversion estimate (lossy tracks unchanged, lossless
        tracks re-sized as CBR MP3 at the chosen bitrate) instead of the raw
        library footprint.
        """
        check = getattr(self, "transcode_mp3_check", None)
        if check is not None and check.isEnabled() and check.isChecked() and lossless_duration > 0:
            kbps = int(self.bitrate_combo.currentText())
            estimated = (total_size - lossless_size) + lossless_duration * kbps * 1000 / 8
            return f"~{format_file_size(estimated)} after conversion"
        return format_file_size(total_size)

    def _on_sync_item_changed(self, item: QTreeWidgetItem, column: int):
        if item.data(0, Qt.UserRole) is None:
            return  # category header — not selectable
        self._update_selected_items()
        self._save_current_profile_selections()

    def _select_all_items(self):
        self.sync_tree.blockSignals(True)
        for item in self._iter_sync_items():
            item.setCheckState(0, Qt.Checked)
        self.sync_tree.blockSignals(False)
        self._update_selected_items()
        self._save_current_profile_selections()

    def _select_no_items(self):
        self.sync_tree.blockSignals(True)
        for item in self._iter_sync_items():
            item.setCheckState(0, Qt.Unchecked)
        self.sync_tree.blockSignals(False)
        self._update_selected_items()
        self._save_current_profile_selections()

    # -----------------------------------------------------------------------
    # Sync button state
    # -----------------------------------------------------------------------

    def _update_sync_button_state(self):
        """Enable the sync button only when a valid destination and selection exist."""
        if not self.current_profile or not self.selected_items:
            self.sync_btn.setEnabled(False)
            return

        has_destination = bool(self.current_profile.device_uri) or bool(self.current_profile.path)
        self.sync_btn.setEnabled(has_destination)
