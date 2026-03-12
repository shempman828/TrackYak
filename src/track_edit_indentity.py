# ---------------------------------------------------------------------------
# IdentificationTab — like FieldFormTab("Identification") but adds Wikipedia
# ---------------------------------------------------------------------------
from __future__ import annotations


from PySide6.QtWidgets import (
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
)

from src.logger_config import logger
from src.wikipedia_seach import search_wikipedia
from src.track_edit_fieldform import FieldFormTab, _read_widget, _write_widget


class IdentificationTab(FieldFormTab):
    def __init__(self, tracks: list, controller, parent=None):
        super().__init__("Identification", tracks, controller, parent)
        self._inject_wikipedia_button()

    def _inject_wikipedia_button(self):
        """Replace the plain Wikipedia link field with one that has a search button."""
        wiki_widget = self._widgets.get("track_wikipedia_link")
        if wiki_widget is None:
            return

        # Find the row in the form layout and replace the field widget
        form = self.layout()
        for i in range(form.rowCount()):
            label_item = form.itemAt(i, QFormLayout.LabelRole)  # noqa: F841
            field_item = form.itemAt(i, QFormLayout.FieldRole)
            if field_item and field_item.widget() is wiki_widget:
                # Build a row widget: [line edit] [search button]
                container = QWidget()
                row = QHBoxLayout(container)
                row.setContentsMargins(0, 0, 0, 0)
                row.addWidget(wiki_widget)
                btn = QPushButton("Search Wikipedia")
                btn.clicked.connect(self._search_wikipedia)
                row.addWidget(btn)
                form.removeRow(i)
                label_item_widget = QLabel("Wikipedia Link:")
                form.insertRow(i, label_item_widget, container)
                break

    def _search_wikipedia(self):
        try:
            query = self.track.track_name if not self.is_multi else ""
            title, summary, _full, link, _images = search_wikipedia(query, self)
            if not link:
                return
            wiki_w = self._widgets.get("track_wikipedia_link")
            if wiki_w:
                wiki_w.setText(link)
                self._mark_dirty("track_wikipedia_link")
            # Optionally pre-fill description if it is empty
            desc_w = self._widgets.get("track_description")
            if desc_w and summary and not _read_widget(desc_w).strip():
                desc_text = summary[:500] + ("..." if len(summary) > 500 else "")
                _write_widget(desc_w, desc_text)
                self._mark_dirty("track_description")
        except Exception as e:
            logger.error(f"Wikipedia search error: {e}")
