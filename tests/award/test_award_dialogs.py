"""Tests for AddAwardDialog and AwardRelationshipDialog."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from PySide6.QtWidgets import QDialogButtonBox

from src.award.award_new import AddAwardDialog
from src.award.award_relationship_dialog import _CREATE_TAB, SEARCH_LIMIT, AwardRelationshipDialog


def _controller(entities=None):
    controller = MagicMock()
    controller.get.get_all_entities.side_effect = lambda model_name, **kw: list((entities or {}).get(model_name, []))
    return controller


# ─────────────────────────────────────────────── AddAwardDialog


def test_ok_is_disabled_until_a_name_is_typed(qapp):
    dialog = AddAwardDialog(_controller())
    ok = dialog.button_box.button(QDialogButtonBox.Ok)
    assert not ok.isEnabled()
    dialog.award_name_edit.setText("Grammy")
    assert ok.isEnabled()


def test_invalid_year_keeps_dialog_open(qapp):
    controller = _controller()
    dialog = AddAwardDialog(controller)
    dialog.award_name_edit.setText("Grammy")
    dialog.award_year_edit.setText("12")
    with patch("src.award.award_new.QMessageBox.warning") as warning:
        dialog.accept_dialog()
    warning.assert_called_once()
    controller.add.add_entity.assert_not_called()


def test_without_entity_creates_award_only(qapp):
    controller = _controller()
    controller.add.add_entity.return_value = SimpleNamespace(award_id=7)
    dialog = AddAwardDialog(controller)
    dialog.award_name_edit.setText("Grammy")
    dialog.award_year_edit.setText("1959")
    dialog.accept_dialog()

    controller.add.add_entity.assert_called_once()
    assert controller.add.add_entity.call_args.kwargs["award_year"] == 1959
    assert dialog.created_award_id == 7


def test_failed_association_removes_the_new_award(qapp):
    controller = _controller()
    controller.add.add_entity.side_effect = [SimpleNamespace(award_id=7), None]
    dialog = AddAwardDialog(controller, "Artist", 3, entity_name="Björk")
    dialog.award_name_edit.setText("Polar Music Prize")
    with patch("src.award.award_new.QMessageBox.critical") as critical:
        dialog.accept_dialog()

    critical.assert_called_once()
    controller.delete.delete_entity.assert_called_once_with("Award", 7)
    assert dialog.created_award_id is None


# ─────────────────────────────────────────────── AwardRelationshipDialog


def test_place_is_not_offered(qapp):
    dialog = AwardRelationshipDialog(SimpleNamespace(award_id=1), _controller())
    types = [dialog.entity_type_combo.itemText(i) for i in range(dialog.entity_type_combo.count())]
    assert "Place" not in types


def test_default_relationship_is_preselected(qapp):
    dialog = AwardRelationshipDialog(SimpleNamespace(award_id=1), _controller(), default_relationship="nominee")
    assert dialog._relationship_type() == "nominee"


def test_search_filters_and_caps_in_sql(qapp):
    controller = _controller()
    dialog = AwardRelationshipDialog(SimpleNamespace(award_id=1), controller)
    controller.get.get_all_entities.reset_mock()
    dialog.search_edit.setText("bjo")
    dialog._perform_search()
    controller.get.get_all_entities.assert_called_with("Artist", limit=SEARCH_LIMIT + 1, artist_name__contains="bjo")


def test_search_shows_truncation_note_and_tolerates_missing_names(qapp):
    artists = [SimpleNamespace(artist_id=i, artist_name=None if i == 0 else f"A{i}") for i in range(SEARCH_LIMIT + 1)]
    dialog = AwardRelationshipDialog(SimpleNamespace(award_id=1), _controller({"Artist": artists}))
    assert dialog.results_list.topLevelItemCount() == SEARCH_LIMIT
    assert str(SEARCH_LIMIT) in dialog.results_note.text()


def test_selected_result_emits_type_and_id(qapp):
    dialog = AwardRelationshipDialog(SimpleNamespace(award_id=1), _controller({"Artist": [SimpleNamespace(artist_id=5, artist_name="Björk")]}))
    received = []
    dialog.relationship_added.connect(lambda *args: received.append(args))
    dialog._on_entity_selected(dialog.results_list.topLevelItem(0))
    assert received == [("Artist", 5, "recipient")]


def test_create_tab_is_disabled_for_tracks(qapp):
    dialog = AwardRelationshipDialog(SimpleNamespace(award_id=1), _controller())
    assert dialog.tabs.isTabEnabled(_CREATE_TAB)
    dialog.entity_type_combo.setCurrentText("Track")
    assert not dialog.tabs.isTabEnabled(_CREATE_TAB)
