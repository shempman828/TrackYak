"""Chip row: chips keep their given order, pan only on screen when overflowing, and carry names for tooltips and screen readers."""

import pytest

from src.nowplaying.nowplaying_chip import _Chip, _ScrollingChipRow


@pytest.fixture
def row(qapp):
    r = _ScrollingChipRow()
    r.resize(600, 36)
    yield r
    r.deleteLater()


def _order(row):
    return [row._row.itemAt(i).widget() for i in range(row._row.count()) if not row._row.itemAt(i).widget().isHidden()]


def test_chip_order_follows_the_given_order_not_history(row):
    bpm, key = _Chip("♩", "120 BPM"), _Chip("key", "C")
    row.set_chips([key])  # first track only has a key
    row.set_chips([bpm, key])
    assert _order(row) == [bpm, key]


def test_hidden_chips_stay_alive_for_the_next_track(row):
    a, b = _Chip("a", "1"), _Chip("b", "2")
    row.set_chips([a, b])
    row.set_chips([b])
    row.set_chips([a, b])
    assert _order(row) == [a, b]


def test_pan_starts_only_when_visible_and_overflowing(row):
    chips = [_Chip("x", "a long chip value " * 3) for _ in range(6)]
    row.set_chips(chips)
    assert not row._pan_timer.isActive()  # not on screen yet
    row.show()
    assert row._pan_timer.isActive()
    row.hide()
    assert not row._pan_timer.isActive()


def test_resize_rechecks_the_pan(row):
    row.show()
    row.set_chips([_Chip("x", "short")])
    assert not row._pan_timer.isActive()
    row.resize(20, 36)
    assert row._pan_timer.isActive()
    row.resize(2000, 36)
    assert not row._pan_timer.isActive()


def test_chip_tooltip_and_accessible_name(qapp):
    chip = _Chip("♩", "—", tooltip="Tempo")
    chip.set_value("120 BPM")
    assert chip.toolTip() == "Tempo"
    assert chip.accessibleName() == "Tempo: 120 BPM"
    assert chip.text() == "♩  120 BPM"
