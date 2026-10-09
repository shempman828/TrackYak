"""SyncDeviceMixin: MTP device detection, linking, and connection polling for SyncView."""

from PySide6.QtWidgets import QInputDialog, QMessageBox

from src.foundation.logger_config import logger
from src.foundation.status_utility import show_status_message
from src.sync.mtp_list_worker import MtpListWorker
from src.sync.mtp_manager import MtpManager, mtp_available
from src.sync.sync_profile import SyncProfile

# How often connection badges are refreshed while the view is visible.
MTP_POLL_INTERVAL_MS = 5000

_NO_DEVICES_HELP = "Make sure your phone is connected via USB and set to File Transfer mode (pull down the notification shade and tap the USB notification)."


class SyncDeviceMixin:
    """Scan for MTP devices off the GUI thread, link them to profiles, and keep connection badges current."""

    # Host provides: mtp_manager, current_profile, profiles, profile_store, cards, sync_worker,
    # _mtp_poll_timer, _known_mtp_devices, _mtp_list_worker, _link_worker, _detect_worker,
    # detect_btn, empty_detect_btn, link_device_btn, change_destination_btn, device_nickname_edit,
    # _add_and_select_profile(), _sync_destination_mode_control(), _refresh_header(),
    # _refresh_current_card(), _update_sync_button_state(), _scan_in_progress().

    # -----------------------------------------------------------------------
    # Polling
    # -----------------------------------------------------------------------

    def _start_mtp_polling(self):
        """Refresh connection badges now and then every MTP_POLL_INTERVAL_MS."""
        if not mtp_available():
            return
        self._refresh_mtp_devices()
        self._mtp_poll_timer.start(MTP_POLL_INTERVAL_MS)

    def _stop_mtp_polling(self):
        """Stop the background badge refresh (the view is hidden)."""
        self._mtp_poll_timer.stop()

    def _refresh_mtp_devices(self):
        """Enumerate connected MTP devices on a worker thread; badges update when the result arrives."""
        # Never call gio on the GUI thread: a wedged MTP backend can block its caller.
        if not mtp_available() or self._scan_in_progress(self._mtp_list_worker):
            return
        worker = MtpListWorker(self.mtp_manager)
        worker.ready.connect(self._on_mtp_devices_listed)
        self._mtp_list_worker = worker
        worker.start()

    def _on_mtp_devices_listed(self, devices: list):
        """Apply a background MTP scan result to the sidebar and the header."""
        self._known_mtp_devices = devices
        self._update_connection_badges({d.uri for d in devices})
        self._refresh_header()

    def _update_connection_badges(self, connected_uris: set):
        """Flip each card's USB badge to match `connected_uris`."""
        for card in self.cards:
            if card.profile.device_uri:
                card.set_connected(card.profile.device_uri in connected_uris)

    # -----------------------------------------------------------------------
    # Linking a device to the current profile
    # -----------------------------------------------------------------------

    def _link_device(self):
        """Scan for MTP devices off the GUI thread, then offer a picker."""
        if not mtp_available() or self._scan_in_progress(self._link_worker):
            return
        self.link_device_btn.setText("Scanning…")
        self.link_device_btn.setEnabled(False)
        self.change_destination_btn.setEnabled(False)
        # The result is applied only to the profile that asked, even if the user switches cards meanwhile.
        self._link_target = self.current_profile
        worker = MtpListWorker(self.mtp_manager)
        worker.ready.connect(self._on_link_devices_listed)
        self._link_worker = worker
        worker.start()

    def _on_link_devices_listed(self, devices: list):
        """Link the scanned device (asking when there are several) to the profile that started the scan."""
        self.link_device_btn.setEnabled(True)
        self.change_destination_btn.setEnabled(True)
        self._known_mtp_devices = devices
        self._update_connection_badges({d.uri for d in devices})
        target = getattr(self, "_link_target", None)
        self._link_target = None
        if not self.current_profile or target is not self.current_profile:
            self._refresh_header()
            return
        if not devices:
            show_status_message(self, f"No Devices Found: No Android devices were detected. {_NO_DEVICES_HELP}")
            self._sync_destination_mode_control()
            self._refresh_header()
            return

        if len(devices) == 1:
            chosen = devices[0]
        else:
            options = [d.display_name for d in devices]
            choice, ok = QInputDialog.getItem(self, "Link Device", "Select device:", options, 0, False)
            if not ok or self.current_profile is not target:
                self._sync_destination_mode_control()
                self._refresh_header()
                return
            chosen = devices[options.index(choice)]

        target.device_uri = chosen.uri
        target.device_name = chosen.short_name
        self.profile_store.save(self.profiles)
        logger.info(f"Linked device '{chosen.display_name}' to profile '{target.name}'")
        show_status_message(self, f"Linked {chosen.display_name}.")

        self.device_nickname_edit.setText(chosen.short_name)
        self._sync_destination_mode_control()
        self._refresh_header()
        self._refresh_current_card()
        self._update_sync_button_state()

    def _unlink_device(self):
        """Turn the current profile back into a folder profile."""
        if not self.current_profile:
            return
        logger.info(f"Unlinked device from profile '{self.current_profile.name}'")
        self.current_profile.device_uri = ""
        self.current_profile.device_name = ""
        self.profile_store.save(self.profiles)
        self.device_nickname_edit.clear()
        self._sync_destination_mode_control()
        self._refresh_header()
        self._refresh_current_card()
        self._update_sync_button_state()

    # -----------------------------------------------------------------------
    # Detect: offer profiles for devices that don't have one
    # -----------------------------------------------------------------------

    def _detect_devices(self):
        """Scan for MTP devices (off the GUI thread) and offer profiles for unknown ones."""
        if not mtp_available() or self._scan_in_progress(self._detect_worker):
            return
        self.detect_btn.setText("⟳ Scanning…")
        self.detect_btn.setEnabled(False)
        self.empty_detect_btn.setText("Scanning…")
        self.empty_detect_btn.setEnabled(False)
        worker = MtpListWorker(self.mtp_manager)
        worker.ready.connect(self._on_detect_devices_listed)
        self._detect_worker = worker
        worker.start()

    def _on_detect_devices_listed(self, devices: list):
        """Offer a new profile for every connected device that no profile links to."""
        self._known_mtp_devices = devices
        logger.info(f"MTP device scan found {len(devices)} device(s)")
        self.detect_btn.setText("⟳ Detect")
        # Profile actions stay locked while a sync runs.
        self.detect_btn.setEnabled(not self._scan_in_progress(self.sync_worker))
        self.empty_detect_btn.setText("Detect Android device")
        self.empty_detect_btn.setEnabled(True)

        if not devices:
            show_status_message(self, f"No Devices Found: No devices detected via USB. {_NO_DEVICES_HELP}")
            return

        known_uris = {p.device_uri for p in self.profiles if p.device_uri}
        new_devices = [d for d in devices if d.uri not in known_uris]

        self._update_connection_badges({d.uri for d in devices})
        self._refresh_header()

        if not new_devices:
            show_status_message(self, f"{len(devices)} device(s) connected — all already have profiles.")
            return

        for device in new_devices:
            reply = QMessageBox.question(self, "New Device Found", f"Found: {device.display_name}\n\nCreate a sync profile for this device?", QMessageBox.Yes | QMessageBox.No)
            if reply == QMessageBox.Yes:
                self._add_and_select_profile(SyncProfile(name=device.short_name, path="", device_uri=device.uri, device_name=device.short_name, music_path=MtpManager.DEFAULT_MUSIC_PATH))
