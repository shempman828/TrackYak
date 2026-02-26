from typing import List

from PySide6.QtCore import QPropertyAnimation, QTimer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QWidget


class AliasesCarousel(QWidget):
    """A widget that cycles through artist aliases with animation"""

    def __init__(self, aliases: List[str], parent=None):
        super().__init__(parent)
        self.aliases = aliases
        self.current_index = 0
        self.timer = QTimer()
        self.timer.timeout.connect(self.next_alias)

        self.init_ui()

        if len(self.aliases) > 1:
            self.timer.start(3000)  # Change every 3 seconds

    def init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # Alias icon/label
        self.icon_label = QLabel("🎭")
        self.icon_label.setFixedWidth(20)

        # Alias text with animation
        self.alias_label = QLabel()
        self.alias_label.setObjectName("AliasLabel")
        self.alias_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        layout.addWidget(self.icon_label)
        layout.addWidget(self.alias_label)
        layout.addStretch()

        # Set initial alias
        if self.aliases:
            self._set_alias_text(self.aliases[0])
        else:
            self.alias_label.setText("No aliases")
            self.alias_label.setStyleSheet("color: gray;")

    def _set_alias_text(self, text: str):
        """Set alias text with fade animation"""
        # Create fade out animation
        fade_out = QPropertyAnimation(self.alias_label, b"windowOpacity")
        fade_out.setDuration(200)
        fade_out.setStartValue(1.0)
        fade_out.setEndValue(0.0)

        # Create fade in animation
        fade_in = QPropertyAnimation(self.alias_label, b"windowOpacity")
        fade_in.setDuration(200)
        fade_in.setStartValue(0.0)
        fade_in.setEndValue(1.0)

        # Chain animations
        fade_out.finished.connect(lambda: self._update_alias_text(text, fade_in))
        fade_out.start()

    def _update_alias_text(self, text: str, fade_in_anim):
        """Update text and fade in"""
        self.alias_label.setText(text)
        fade_in_anim.start()

    def next_alias(self):
        """Switch to next alias"""
        if len(self.aliases) <= 1:
            return

        self.current_index = (self.current_index + 1) % len(self.aliases)
        self._set_alias_text(self.aliases[self.current_index])

    def stop(self):
        """Stop the carousel timer"""
        self.timer.stop()
