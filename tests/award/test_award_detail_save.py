"""Tests for AwardDetailTab: saving, parent choices, change tracking, relationships and delete."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from PySide6.QtWidgets import QMessageBox

from src.award.award_detail import AwardDetailTab


def _award(award_id=1, name="Grammy", parent_id=None, **fields):
    defaults = {"award_year": 2020, "award_category": "Best Album", "award_description": "", "wikipedia_link": None}
    return SimpleNamespace(award_id=award_id, award_name=name, parent_id=parent_id, **(defaults | fields))


def _make_controller(other_awards=None, associations=None, entities=None):
    controller = MagicMock()

    def get_all_entities(model_name, **kwargs):
        if model_name == "Award":
            awards = other_awards or []
            if "parent_id" in kwargs:
                return [a for a in awards if a.parent_id == kwargs["parent_id"]]
            return list(awards)
        if model_name == "AwardAssociation":
            rows = associations or []
            return [r for r in rows if all(getattr(r, k) == v for k, v in kwargs.items())]
        return list((entities or {}).get(model_name, []))

    controller.get.get_all_entities.side_effect = get_all_entities
    return controller


def _assoc(association_id, entity_id, association_type, entity_type="Artist"):
    return SimpleNamespace(association_id=association_id, award_id=1, entity_type=entity_type, entity_id=entity_id, association_type=association_type)


def test_save_changes_updates_and_emits_saved(qapp):
    controller = _make_controller()
    tab = AwardDetailTab(_award(), controller)
    saved = []
    tab.saved.connect(lambda: saved.append(True))

    tab.name_edit.setText("Grammy Award")
    tab.wikipedia_edit.setText("https://en.wikipedia.org/wiki/Grammy_Award")
    tab._save_changes()

    controller.update.update_entity.assert_called_once_with(
        "Award", 1, award_name="Grammy Award", award_year=2020, award_category="Best Album", award_description=None, wikipedia_link="https://en.wikipedia.org/wiki/Grammy_Award", parent_id=None
    )
    assert saved == [True]
    assert not tab.save_btn.isEnabled()


def test_failed_update_reports_error_and_keeps_changes(qapp):
    controller = _make_controller()
    controller.update.update_entity.return_value = False
    tab = AwardDetailTab(_award(), controller)
    saved = []
    tab.saved.connect(lambda: saved.append(True))

    tab.name_edit.setText("Grammy Award")
    with patch("src.award.award_detail.QMessageBox.critical") as critical:
        tab._save_changes()

    critical.assert_called_once()
    assert saved == []
    assert tab.save_btn.isEnabled()


def test_blank_name_is_rejected(qapp):
    controller = _make_controller()
    tab = AwardDetailTab(_award(), controller)
    tab.name_edit.setText("   ")
    with patch("src.award.award_detail.QMessageBox.warning") as warning:
        tab._save_changes()
    warning.assert_called_once()
    controller.update.update_entity.assert_not_called()


def test_save_button_tracks_real_changes(qapp):
    tab = AwardDetailTab(_award(), _make_controller(other_awards=[_award(2, "Other Award")]))
    assert tab.parent_combo.count() == 2
    assert not tab.save_btn.isEnabled()

    tab.category_edit.setText("Best Song")
    assert tab.save_btn.isEnabled()
    tab.category_edit.setText("Best Album")
    assert not tab.save_btn.isEnabled()


def test_parent_combo_excludes_self_and_descendants(qapp):
    awards = [_award(1, "Grammy"), _award(2, "Child", parent_id=1), _award(3, "Grandchild", parent_id=2), _award(4, "Other")]
    tab = AwardDetailTab(awards[0], _make_controller(other_awards=awards))
    ids = {tab.parent_combo.itemData(i) for i in range(tab.parent_combo.count())}
    assert ids == {None, 4}


def test_all_recipients_are_listed_first(qapp):
    associations = [_assoc(10, 1, "nominee"), _assoc(11, 2, "recipient"), _assoc(12, 3, "recipient")]
    artists = [SimpleNamespace(artist_id=i, artist_name=n) for i, n in ((1, "Cher"), (2, "Abba"), (3, "Björk"))]
    tab = AwardDetailTab(_award(), _make_controller(associations=associations, entities={"Artist": artists}))

    assert tab.recipient_label.text() == "Recipient: Artist: Abba, Artist: Björk"
    texts = [tab.relationship_list.item(i).text() for i in range(tab.relationship_list.count())]
    assert texts == ["Recipient - Artist: Abba", "Recipient - Artist: Björk", "Nominee - Artist: Cher"]


def test_duplicate_relationship_is_skipped(qapp):
    controller = _make_controller(associations=[_assoc(10, 5, "recipient")])
    tab = AwardDetailTab(_award(), controller)
    with patch("src.award.award_detail.show_status_message") as status:
        tab._on_relationship_added("Artist", 5, "recipient")
    status.assert_called_once()
    controller.add.add_entity.assert_not_called()


def test_remove_selected_relationship(qapp):
    controller = _make_controller(associations=[_assoc(10, 5, "nominee")])
    tab = AwardDetailTab(_award(), controller)
    assert not tab.remove_relationship_btn.isEnabled()

    tab.relationship_list.setCurrentRow(0)
    assert tab.remove_relationship_btn.isEnabled()
    with patch("src.award.award_detail.show_status_message"):
        tab._remove_selected_relationship()
    controller.delete.delete_entity.assert_called_once_with("AwardAssociation", 10)


def test_delete_removes_associations_then_emits_deleted(qapp):
    controller = _make_controller()
    tab = AwardDetailTab(_award(), controller)
    deleted = []
    tab.deleted.connect(lambda: deleted.append(True))

    with patch("src.award.award_detail.QMessageBox.question", return_value=QMessageBox.Yes), patch("src.award.award_detail.show_status_message"):
        tab._delete_award()

    assert [c.args[0] for c in controller.delete.delete_entity.call_args_list] == ["AwardAssociation", "Award"]
    assert deleted == [True]


def test_failed_delete_does_not_emit_deleted(qapp):
    controller = _make_controller()
    controller.delete.delete_entity.return_value = False
    tab = AwardDetailTab(_award(), controller)
    deleted = []
    tab.deleted.connect(lambda: deleted.append(True))

    with patch("src.award.award_detail.QMessageBox.question", return_value=QMessageBox.Yes), patch("src.award.award_detail.QMessageBox.critical") as critical:
        tab._delete_award()

    critical.assert_called_once()
    assert deleted == []
