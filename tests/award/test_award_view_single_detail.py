"""Regression test for the Awards view opening a new tab per click.

AwardView used a QTabWidget and called addTab() for every distinct award the
user selected, so tabs accumulated without bound. It now behaves like the
other entity views: a single detail panel that is swapped in place, so only
one award is ever shown at a time.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractItemView, QDialog
import pytest

from src.award.award_detail import AwardDetailTab
from src.award.award_view import AwardView


def _make_controller(awards):
    controller = MagicMock()

    def get_all_entities(model_name, **kwargs):
        if model_name == "Award":
            return list(awards)
        return []  # AwardAssociation lookups from AwardDetailTab

    def get_entity_object(model_name, **kwargs):
        if model_name == "Award":
            award_id = kwargs.get("award_id")
            return next((a for a in awards if a.award_id == award_id), None)
        return None

    controller.get.get_all_entities.side_effect = get_all_entities
    controller.get.get_entity_object.side_effect = get_entity_object
    return controller


def _select_top_level(view, index):
    item = view.award_tree.topLevelItem(index)
    view.award_tree.setCurrentItem(item)  # single-selects, fires itemSelectionChanged
    return item


def test_selecting_awards_swaps_one_detail_panel(qapp):
    awards = [
        SimpleNamespace(award_id=1, award_name="Grammy", award_year=2021, award_category="Best Album", award_description="", wikipedia_link=None, parent_id=None),
        SimpleNamespace(award_id=2, award_name="Mercury Prize", award_year=2020, award_category=None, award_description="", wikipedia_link=None, parent_id=None),
        SimpleNamespace(award_id=3, award_name="Polaris", award_year=2019, award_category=None, award_description="", wikipedia_link=None, parent_id=None),
    ]
    view = AwardView(_make_controller(awards))

    assert view.award_tree.topLevelItemCount() == 3
    assert view._current_detail is None

    _select_top_level(view, 0)
    first = view._current_detail
    assert isinstance(first, AwardDetailTab)
    assert first.award.award_id == 1

    _select_top_level(view, 1)
    _select_top_level(view, 2)

    # Only one detail panel exists, and it's the most recent selection.
    panels = view.detail_container.findChildren(AwardDetailTab)
    assert len(panels) == 1
    assert view._current_detail.award.award_id == 3
    # The previous panels were torn down, not left parented.
    assert first not in panels


def test_deleting_shown_award_clears_the_panel(qapp):
    awards = [SimpleNamespace(award_id=1, award_name="Grammy", award_year=2021, award_category=None, award_description="", wikipedia_link=None, parent_id=None)]
    view = AwardView(_make_controller(awards))

    _select_top_level(view, 0)
    assert isinstance(view._current_detail, AwardDetailTab)

    awards.clear()  # the award is gone from the DB
    view._current_detail.deleted.emit()  # emitted by _delete_award

    assert view._current_detail is None
    assert view.award_tree.topLevelItemCount() == 0  # the list is reloaded too
    # Placeholder is shown again (not explicitly hidden); the view itself is
    # never shown in the offscreen test, so check isHidden() rather than
    # isVisible(), which is gated on ancestor visibility.
    assert not view._placeholder.isHidden()


def _award(award_id, name, parent_id=None):
    return SimpleNamespace(award_id=award_id, award_name=name, award_year=None, award_category=None, award_description="", wikipedia_link=None, parent_id=parent_id)


def test_save_rebuilds_panel_and_keeps_selection(qapp):
    awards = [_award(1, "Grammy"), _award(2, "Mercury Prize")]
    view = AwardView(_make_controller(awards))
    _select_top_level(view, 1)
    first = view._current_detail

    awards[1].award_name = "Mercury Music Prize"
    first.saved.emit()

    assert view._current_detail is not first
    assert view._current_detail.name_edit.text() == "Mercury Music Prize"
    assert [i.data(0, Qt.UserRole) for i in view.award_tree.selectedItems()] == [2]


@pytest.mark.parametrize(("indicator", "expected"), [(QAbstractItemView.OnItem, 2), (QAbstractItemView.AboveItem, 1), (QAbstractItemView.BelowItem, 1), (QAbstractItemView.OnViewport, None)])
def test_drop_parent_follows_the_drop_indicator(qapp, indicator, expected):
    view = AwardView(_make_controller([_award(1, "Grammy"), _award(2, "Best Album", parent_id=1)]))
    target = view.award_tree.topLevelItem(0).child(0)
    assert view._drop_parent_id(target, indicator) == expected


def test_reparent_skips_cycles_and_moves_only_top_selected(qapp):
    awards = [_award(1, "A"), _award(2, "B", parent_id=1), _award(3, "C")]
    controller = _make_controller(awards)
    view = AwardView(controller)

    for item in (view._find_item(1), view._find_item(2)):
        item.setSelected(True)
    assert view._top_selected_ids() == [1]

    assert view._reparent_awards([1], 2) is False  # 2 is a child of 1
    controller.update.update_entity.assert_not_called()

    assert view._reparent_awards([1], 3) is True
    controller.update.update_entity.assert_called_once_with("Award", 1, parent_id=3)


def test_failed_move_is_reported(qapp):
    controller = _make_controller([_award(1, "A"), _award(2, "B")])
    controller.update.update_entity.return_value = False
    view = AwardView(controller)
    with patch("src.award.award_view.QMessageBox.warning") as warning:
        assert view._reparent_awards([1], 2) is False
    warning.assert_called_once()


def test_new_award_is_selected_after_creation(qapp):
    awards = [_award(1, "Grammy")]
    view = AwardView(_make_controller(awards))

    def fake_dialog(*_args, **_kwargs):
        awards.append(_award(9, "Polaris"))
        return SimpleNamespace(exec=lambda: QDialog.Accepted, created_award_id=9)

    with patch("src.award.award_view.AddAwardDialog", side_effect=fake_dialog):
        view._create_new_award()

    assert view._current_detail.award.award_id == 9
