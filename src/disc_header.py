from PySide6.QtWidgets import (
    QLabel,
)

from src.logger_config import logger


class DiscHeader(QLabel):
    """A specialized header that handles track drops safely."""

    def __init__(self, text, disc, controller, refresh_callback, parent=None):
        super().__init__(text, parent)
        self.disc = disc
        self.controller = controller
        self.refresh_callback = refresh_callback
        self.setAcceptDrops(True)
        self.setStyleSheet("""
            QLabel {
                font-weight: bold; font-size: 14px; margin-top: 10px;
                padding: 5px; border-radius: 4px; background: #f8f8f8;
            }
            QLabel[hover="true"] { background: #e1f5fe; border: 1px dashed #0288d1; }
        """)

    def dragEnterEvent(self, event):
        if event.mimeData().hasText():
            self.setProperty("hover", "true")
            self.style().unpolish(self)
            self.style().polish(self)
            event.acceptProposedAction()

    def dragLeaveEvent(self, event):
        self.setProperty("hover", "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def dropEvent(self, event):
        self.setProperty("hover", "false")
        self.style().unpolish(self)
        self.style().polish(self)

        try:
            track_id = int(event.mimeData().text())
            target_disc_id = self.disc.disc_id if self.disc else None

            # Update database
            success = self.controller.update.update_entity(
                "Track", track_id, disc_id=target_disc_id
            )

            if success:
                self.refresh_callback()
            event.acceptProposedAction()
        except Exception as e:
            logger.error(f"Drop failed: {e}")
