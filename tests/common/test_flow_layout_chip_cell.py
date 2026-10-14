"""ChipCell + FlowLayout must measure their chips before the table is shown.

The Roles tab builds its chip cells while the table can still be hidden.
FlowLayout used to skip every widget with isVisible() == False, so the cell
measured only its margins and the row came out one margin tall.
"""

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QPushButton, QTableWidget
import pytest

from src.common.widgets.flow_layout import ChipCell, FlowLayout


@pytest.fixture
def make_table(qapp):
    """Yield a QTableWidget factory; delete every table while Qt is alive."""
    tables = []

    def make() -> QTableWidget:
        table = QTableWidget(1, 1)
        tables.append(table)
        return table

    yield make
    for table in tables:
        table.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def _build_cell(table: QTableWidget) -> FlowLayout:
    cell = ChipCell()
    layout = FlowLayout(cell, margin=4, h_spacing=6, v_spacing=4)
    for i in range(8):
        layout.addWidget(QPushButton(f"Role number {i}"))
    table.setCellWidget(0, 0, cell)
    return layout


def test_flow_layout_measures_chips_in_unshown_table(qapp, make_table):
    hidden_table = make_table()
    shown_table = make_table()
    shown_table.show()
    qapp.processEvents()

    hidden_height = _build_cell(hidden_table).heightForWidth(200)
    shown_height = _build_cell(shown_table).heightForWidth(200)

    assert hidden_height > 8
    assert hidden_height == shown_height


def test_chip_cell_hint_follows_current_width(make_table):
    table = make_table()
    layout = _build_cell(table)
    cell = layout.parentWidget()

    cell.resize(200, 10)
    narrow = cell.sizeHint()
    assert narrow == cell.minimumSizeHint()
    cell.resize(1000, 10)
    wide = cell.sizeHint()

    assert narrow.height() == layout.heightForWidth(200)
    assert wide.height() == layout.heightForWidth(1000)
    assert wide.height() < narrow.height()
