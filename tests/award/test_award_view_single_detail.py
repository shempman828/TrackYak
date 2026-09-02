"""Regression test for the Awards view opening a new tab per click.

AwardView used a QTabWidget and called addTab() for every distinct award the
user selected, so tabs accumulated without bound. It now behaves like the
other entity views: a single detail panel that is swapped in place, so only
one award is ever shown at a time.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

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
        SimpleNamespace(
            award_id=1,
            award_name="Grammy",
            award_year=2021,
            award_category="Best Album",
            award_description="",
            parent_id=None,
        ),
        SimpleNamespace(
            award_id=2,
            award_name="Mercury Prize",
            award_year=2020,
            award_category=None,
            award_description="",
            parent_id=None,
        ),
        SimpleNamespace(
            award_id=3,
            award_name="Polaris",
            award_year=2019,
            award_category=None,
            award_description="",
            parent_id=None,
        ),
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
    awards = [
        SimpleNamespace(
            award_id=1,
            award_name="Grammy",
            award_year=2021,
            award_category=None,
            award_description="",
            parent_id=None,
        )
    ]
    view = AwardView(_make_controller(awards))

    _select_top_level(view, 0)
    assert isinstance(view._current_detail, AwardDetailTab)

    view._current_detail.save_requested.emit()  # emitted by _delete_award

    assert view._current_detail is None
    # Placeholder is shown again (not explicitly hidden); the view itself is
    # never shown in the offscreen test, so check isHidden() rather than
    # isVisible(), which is gated on ancestor visibility.
    assert not view._placeholder.isHidden()
