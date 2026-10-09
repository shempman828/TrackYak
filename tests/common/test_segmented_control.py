"""SegmentedControl: exclusive joined buttons with a QComboBox-like API."""

import pytest

from src.common.widgets.segmented_control import SegmentedControl

pytestmark = pytest.mark.usefixtures("qapp")


def test_first_segment_selected_and_positions_marked():
    control = SegmentedControl(["A", "B", "C"])
    assert control.currentIndex() == 0
    assert control.currentText() == "A"
    assert [control.button(i).property("segment") for i in range(3)] == ["first", "middle", "last"]
    assert SegmentedControl(["Solo"]).button(0).property("segment") == "only"


def test_click_and_programmatic_changes_emit_once():
    control = SegmentedControl(["320", "256", "192"])
    indexes, texts = [], []
    control.currentIndexChanged.connect(indexes.append)
    control.currentTextChanged.connect(texts.append)

    control.button(2).click()
    control.setCurrentText("256")
    control.setCurrentIndex(1)  # no change -> no signal
    control.button(1).click()  # no change -> no signal

    assert indexes == [2, 1]
    assert texts == ["192", "256"]
    assert control.button(1).isChecked() and not control.button(2).isChecked()


def test_block_signals_loads_silently():
    control = SegmentedControl(["A", "B"])
    fired = []
    control.currentIndexChanged.connect(fired.append)
    control.blockSignals(True)
    control.setCurrentIndex(1)
    control.blockSignals(False)
    assert fired == [] and control.currentIndex() == 1


def test_unknown_text_or_index_is_ignored():
    control = SegmentedControl(["A", "B"])
    control.setCurrentText("Z")
    control.setCurrentIndex(9)
    assert control.currentIndex() == 0
