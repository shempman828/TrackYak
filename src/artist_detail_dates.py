from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from src.artist_date_format import format_artist_dates


class DateDisplayWidget(QWidget):
    """Widget to display formatted dates using DateFormatter"""

    def __init__(self, artist, parent=None):
        super().__init__(parent)
        self.artist = artist
        self.init_ui()

    def format_lifespan(self) -> str:
        """Format the artist's lifespan (birth to death) using DateFormatter"""
        formatted_date = format_artist_dates(self.artist)

        if formatted_date is False:
            return ""
        return formatted_date

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        # Date information using DateFormatter
        date_text = self.format_lifespan()

        if date_text:
            self.date_label = QLabel(date_text)
            self.date_label.setObjectName("DateLabel")
            self.date_label.setWordWrap(True)
            layout.addWidget(self.date_label)

        # Age display
        age = self.artist.age
        if age is not None:
            age_label = QLabel(f"Age: {age} years")
            age_label.setObjectName("AgeLabel")
            layout.addWidget(age_label)
