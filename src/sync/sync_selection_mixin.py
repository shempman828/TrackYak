"""SyncSelectionMixin: the playlist/mood checklist and the selection summary for SyncView."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTreeWidgetItem

from src.foundation.logger_config import logger
from src.sync.device_card import format_file_size, plural
from src.sync.sync_items_loader import SyncItemsLoader
from src.sync.sync_selection_tree import SyncSelectionTree


class SyncSelectionMixin:
    """Build the selection tree, track what is ticked, and keep the summary and Sync button current."""

    # Host provides: sync_tree (SyncSelectionTree), sync_manager, current_profile, profiles,
    # profile_store, track_count_label, sync_btn, transcode_mp3_check, bitrate_combo.

    # -----------------------------------------------------------------------
    # Playlist / mood selection tree
    # -----------------------------------------------------------------------

    def _add_hierarchy(self, parent_item: QTreeWidgetItem, items: list[dict], id_key: str):
        """Recursively add items under parent_item, following each item's parent_id."""
        children_map: dict = {}
        for it in items:
            children_map.setdefault(it.get("parent_id"), []).append(it)
        for siblings in children_map.values():
            siblings.sort(key=lambda it: (it.get("name") or "").lower())

        added: set = set()

        def add_level(parent_id, node: QTreeWidgetItem):
            for it in children_map.get(parent_id, []):
                if it[id_key] in added:
                    continue  # guards against a parent_id cycle
                added.add(it[id_key])
                tree_item = SyncSelectionTree.make_item(node, it)
                add_level(it[id_key], tree_item)

        add_level(None, parent_item)
        # A missing parent or a parent_id cycle would otherwise hide the item for good: attach
        # orphans (parent not in the list) at the top level first, then whatever a cycle left over.
        ids = {it[id_key] for it in items}
        by_name = sorted(items, key=lambda it: (it.get("name") or "").lower())
        orphans = [it for it in by_name if it.get("parent_id") not in ids]
        for it in orphans + by_name:
            if it[id_key] not in added:
                added.add(it[id_key])
                add_level(it[id_key], SyncSelectionTree.make_item(parent_item, it))

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
        """Reload playlists and moods off the GUI thread, coalescing calls made while a load is in flight."""
        # Our own flag, not loader.isRunning(): that can still read True inside the finished worker's slot.
        if getattr(self, "_sync_items_loading", False):
            self._sync_items_reload_pending = True
            return
        self._sync_items_reload_pending = False
        self._sync_items_loading = True
        loader = SyncItemsLoader(self.sync_manager)
        loader.loaded.connect(self._on_sync_items_loaded)
        loader.failed.connect(self._on_sync_items_failed)
        self._sync_items_loader = loader
        loader.start()

    def _on_sync_items_loaded(self, playlists: list, moods: list):
        """Apply a loader result, then run any reload requested meanwhile."""
        self._sync_items_loading = False
        self._populate_sync_tree(playlists, moods)
        if getattr(self, "_sync_items_reload_pending", False):
            self._refresh_sync_items()

    def _on_sync_items_failed(self, message: str):
        """Keep the current tree on a load error; the next showEvent retries."""
        self._sync_items_loading = False
        self._sync_items_reload_pending = False
        logger.warning(f"Sync items failed to load, keeping current tree: {message}")

    def _populate_sync_tree(self, playlists: list, moods: list):
        """Rebuild the tree from a loader result (runs on the GUI thread)."""
        self.sync_tree.blockSignals(True)
        self.sync_tree.clear()

        playlists_header = self.sync_tree.add_section("PLAYLISTS")
        self._add_hierarchy(playlists_header, playlists, "playlist_id")

        moods_header = self.sync_tree.add_section("MOODS")
        self._add_hierarchy(moods_header, moods, "mood_id")

        self.sync_tree.expandAll()
        # Re-apply a filter typed while the old tree was on screen.
        self.sync_tree.set_filter_text(self.sync_tree.filter_text())
        self.sync_tree.blockSignals(False)
        # From now on the tree reflects the DB, so saving it can't wipe a profile's selection.
        self._sync_items_ready = True
        self._update_tree_placeholder()

        if self.current_profile:
            self._apply_profile_selection()
        else:
            self.sync_tree.update_section_counts()

    def _update_tree_placeholder(self):
        """Pick the text the tree paints while it has no visible rows."""
        if self.sync_tree.filter_text():
            text = "No playlists or moods match the filter."
        elif self.sync_tree.topLevelItemCount() == 0:
            text = "Loading playlists and moods…"
        else:
            text = "There are no playlists or moods yet. Create one in the library first."
        self.sync_tree.set_placeholder_text(text)

    def _on_sync_filter_changed(self, text: str):
        """Apply the filter box text to the tree."""
        self.sync_tree.set_filter_text(text)
        self._update_tree_placeholder()

    def _apply_profile_selection(self):
        """Tick the checkboxes that match the current profile's saved playlist/mood IDs."""
        if not self.current_profile:
            return
        saved_playlist_ids = set(self.current_profile.playlist_ids)
        saved_mood_ids = set(self.current_profile.mood_ids)
        self.sync_tree.blockSignals(True)
        for item in self._iter_sync_items():
            data = item.data(0, Qt.UserRole)
            checked = data["mood_id"] in saved_mood_ids if data["kind"] == "mood" else data["playlist_id"] in saved_playlist_ids
            item.setCheckState(0, Qt.Checked if checked else Qt.Unchecked)
        self.sync_tree.blockSignals(False)
        self._update_selected_items()

    def _save_current_profile_selections(self):
        """Write current checkbox state back into the active profile and persist."""
        # Before the first load (or after a failed one) the tree is empty and would wipe the selection.
        if not self.current_profile or not getattr(self, "_sync_items_ready", False):
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
        self.profile_store.save(self.profiles)

    def _update_selected_items(self):
        """Rebuild self.selected_items and update the selection summary label."""
        self.sync_tree.refresh_partial_states()
        self.sync_tree.update_section_counts()
        self.selected_items = []
        playlist_ids = []
        mood_ids = []
        for item in self._iter_sync_items():
            if item.checkState(0) == Qt.Checked:
                data = item.data(0, Qt.UserRole)
                self.selected_items.append(data)
                if data["kind"] == "mood":
                    mood_ids.append(data["mood_id"])
                else:
                    playlist_ids.append(data["playlist_id"])

        if self.selected_items:
            # One deduped query, not a sum of per-item aggregates: a track in
            # two selected playlists (or a playlist and a mood) must count once,
            # the same way it lands on the device only once.
            self._selection_totals = self.sync_manager.selection_totals(playlist_ids, mood_ids)
            total_tracks, total_size, total_lossless_size, total_lossless_duration = self._selection_totals
            size_text = self._selection_size_text(total_size, total_lossless_size, total_lossless_duration)
            self.track_count_label.setText(f"{selection_description(len(playlist_ids), len(mood_ids))}  ·  {plural(total_tracks, 'track')}  ·  {size_text}")
        else:
            self._selection_totals = (0, 0, 0, 0.0)
            self.track_count_label.setText("Nothing selected")

        self._update_sync_button_state()

    def _selection_size_text(self, total_size, lossless_size, lossless_duration):
        """Size text for the summary: a post-conversion estimate while MP3 conversion is on, else the raw size."""
        check = getattr(self, "transcode_mp3_check", None)
        if check is not None and check.isEnabled() and check.isChecked() and lossless_duration > 0:
            kbps = int(self.bitrate_combo.currentText())
            estimated = (total_size - lossless_size) + lossless_duration * kbps * 1000 / 8
            return f"~{format_file_size(estimated)} after conversion"
        return format_file_size(total_size)

    def _on_sync_item_changed(self, item: QTreeWidgetItem, column: int):
        """A row was ticked or unticked."""
        if item.data(0, Qt.UserRole) is None:
            return  # category header — not selectable
        self._update_selected_items()
        self._save_current_profile_selections()

    def _on_sync_tree_bulk_changed(self):
        """A context-menu "with sub-items" change (made with signals blocked)."""
        self._update_selected_items()
        self._save_current_profile_selections()

    def _set_visible_items_checked(self, state):
        """Tick/untick every row the filter shows; hidden rows keep their state."""
        self.sync_tree.blockSignals(True)
        for item in list(self.sync_tree.visible_checkable_items()):
            item.setCheckState(0, state)
        self.sync_tree.blockSignals(False)
        self._update_selected_items()
        self._save_current_profile_selections()

    def _select_all_items(self):
        """Tick every visible row."""
        self._set_visible_items_checked(Qt.Checked)

    def _select_no_items(self):
        """Untick every visible row."""
        self._set_visible_items_checked(Qt.Unchecked)

    def _expand_all_items(self):
        """Expand every folder."""
        self.sync_tree.expandAll()

    def _collapse_all_items(self):
        """Collapse every folder."""
        self.sync_tree.collapseAll()

    # -----------------------------------------------------------------------
    # Sync button state
    # -----------------------------------------------------------------------

    def _update_sync_button_state(self):
        """Enable the sync button only when a valid destination and selection exist."""
        if not self.current_profile:
            reason = "Select or create a profile first."
        elif not (self.current_profile.device_uri or self.current_profile.path):
            reason = "Set a destination for this profile in Options."
        elif not self.selected_items:
            reason = "Select at least one playlist or mood."
        else:
            reason = ""
        self.sync_btn.setEnabled(not reason)
        self.sync_btn.setToolTip(reason or "Copy the selection to the destination.")


def selection_description(n_playlists: int, n_moods: int) -> str:
    """'3 playlists + 1 mood' -- the playlist/mood part of a selection summary."""
    parts = []
    if n_playlists:
        parts.append(plural(n_playlists, "playlist"))
    if n_moods:
        parts.append(plural(n_moods, "mood"))
    return " + ".join(parts)
