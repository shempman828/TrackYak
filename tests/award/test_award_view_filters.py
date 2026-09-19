"""Tests for AwardView's Award Name filter (MultiSelectFilterButton wired
into _update_filters / _filter_awards)."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from PySide6.QtCore import Qt

from src.award.award_view import AwardView


def _award(award_id, name, year=None, category=None, parent_id=None):
    return SimpleNamespace(
        award_id=award_id,
        award_name=name,
        award_year=year,
        award_category=category,
        award_description="",
        parent_id=parent_id,
    )


def _make_controller(awards):
    controller = MagicMock()

    def get_all_entities(model_name, **kwargs):
        if model_name == "Award":
            return list(awards)
        return []

    def get_entity_object(model_name, **kwargs):
        if model_name == "Award":
            award_id = kwargs.get("award_id")
            return next((a for a in awards if a.award_id == award_id), None)
        return None

    controller.get.get_all_entities.side_effect = get_all_entities
    controller.get.get_entity_object.side_effect = get_entity_object
    return controller


def _check_only(button, *names) -> None:
    for i in range(button._list_widget.count()):
        item = button._list_widget.item(i)
        item.setCheckState(Qt.Checked if item.text() in names else Qt.Unchecked)


def _visible_names(view) -> set[str]:
    tree = view.award_tree
    names = set()

    def walk(item):
        names.add(item.text(0))
        for i in range(item.childCount()):
            walk(item.child(i))

    for i in range(tree.topLevelItemCount()):
        walk(tree.topLevelItem(i))
    return names


def test_committing_one_name_shows_only_that_award(qapp):
    awards = [
        _award(1, "Grammy Award", year=2021),
        _award(2, "Mercury Prize", year=2020),
        _award(3, "Polaris", year=2019),
    ]
    view = AwardView(_make_controller(awards))

    _check_only(view.name_filter, "Mercury Prize")
    view.name_filter._commit_and_close()

    assert view.award_tree.topLevelItemCount() == 1
    assert "Mercury Prize" in view.award_tree.topLevelItem(0).text(0)


def test_all_selected_is_equivalent_to_no_name_filter(qapp):
    awards = [_award(1, "Grammy Award", year=2021), _award(2, "Mercury Prize", year=2020)]
    view = AwardView(_make_controller(awards))

    assert view.award_tree.topLevelItemCount() == 2

    _check_only(view.name_filter, "Grammy Award", "Mercury Prize")
    view.name_filter._commit_and_close()

    assert view.name_filter.committed_selection() == set()
    assert view.award_tree.topLevelItemCount() == 2


def test_name_filter_combines_with_year_filter_via_and(qapp):
    awards = [_award(1, "Grammy Award", year=2021), _award(2, "Grammy Award", year=2020)]
    view = AwardView(_make_controller(awards))

    _check_only(view.name_filter, "Grammy Award")
    view.name_filter._commit_and_close()
    assert view.award_tree.topLevelItemCount() == 2  # both years still shown

    view.year_filter.setCurrentText("2020")
    assert view.award_tree.topLevelItemCount() == 1

    # A year that "Grammy Award" doesn't have -> empty result.
    view.year_filter.addItem("1999")
    view.year_filter.setCurrentText("1999")
    assert view.award_tree.topLevelItemCount() == 0


def test_reload_after_delete_drops_stale_name_from_filter(qapp):
    awards = [_award(1, "Grammy Award", year=2021), _award(2, "Mercury Prize", year=2020)]
    view = AwardView(_make_controller(awards))

    _check_only(view.name_filter, "Mercury Prize")
    view.name_filter._commit_and_close()
    assert view.award_tree.topLevelItemCount() == 1

    # "Mercury Prize" is gone from the backing data; reload must not raise,
    # and the filter falls back to "All" since its selected name no longer
    # exists.
    remaining = [awards[0]]
    view.controller.get.get_all_entities.side_effect = lambda model_name, **kw: (
        list(remaining) if model_name == "Award" else []
    )
    view.load_awards()

    assert view.name_filter.committed_selection() == set()
    assert view.name_filter.button_text() == "All"
