"""Tests for AddInfluenceDialog and RemoveInfluenceDialog validation, failure handling, and selection."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox
import pytest

from src.influences.influences_dialog import AddInfluenceDialog, RemoveInfluenceDialog


@pytest.fixture
def no_message_boxes(monkeypatch):
    critical = MagicMock()
    monkeypatch.setattr(QMessageBox, "critical", critical)
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_k: QMessageBox.Yes)
    return critical


@pytest.fixture
def status_messages(monkeypatch):
    messages = []
    monkeypatch.setattr("src.influences.influences_dialog.show_status_message", lambda _w, msg, *_a: messages.append(msg))
    return messages


def _controller(next_artist_id=100, influence_result=True, existing=None):
    controller = MagicMock()
    ids = iter(range(next_artist_id, next_artist_id + 10))

    def add_entity(model, **kwargs):
        if model == "Artist":
            return SimpleNamespace(artist_id=next(ids), **kwargs)
        return SimpleNamespace(**kwargs) if influence_result else None

    controller.add.add_entity.side_effect = add_entity
    controller.get.get_entity_object.return_value = existing
    return controller


def _dialog(qapp, controller, artists=((1, "Miles Davis"), (2, "John Coltrane"))):
    dialog = AddInfluenceDialog(controller, list(artists))
    dialog.accept = MagicMock()
    return dialog


def _fill(dialog, influencer, influenced):
    dialog.influencer_field.field.setText(influencer)
    dialog.influenced_field.field.setText(influenced)


def test_add_influence_failed_insert_keeps_dialog_open(qapp, no_message_boxes):
    # add_entity returns None (no exception) on a failed commit; this used to count as success.
    dialog = _dialog(qapp, _controller(influence_result=False))
    _fill(dialog, "Miles Davis", "John Coltrane")

    dialog.add_influence()

    dialog.accept.assert_not_called()
    no_message_boxes.assert_called_once()
    assert dialog.added_influence is None


def test_add_influence_failed_artist_creation_shows_clear_error(qapp, no_message_boxes):
    controller = MagicMock()
    controller.add.add_entity.return_value = None
    dialog = _dialog(qapp, controller)
    _fill(dialog, "Brand New Artist", "John Coltrane")

    dialog.add_influence()

    dialog.accept.assert_not_called()
    message = no_message_boxes.call_args.args[2]
    assert "Brand New Artist" in message
    assert "NoneType" not in message


def test_add_influence_rejects_existing_relationship(qapp, no_message_boxes, status_messages):
    controller = _controller(existing=SimpleNamespace())
    dialog = _dialog(qapp, controller)
    _fill(dialog, "Miles Davis", "John Coltrane")

    dialog.add_influence()

    dialog.accept.assert_not_called()
    assert any("already exists" in m for m in status_messages)
    controller.add.add_entity.assert_not_called()


def test_add_influence_self_check_uses_ids_for_same_named_artists(qapp, no_message_boxes, status_messages):
    # Two different artists that share a name are a valid pair.
    dialog = _dialog(qapp, _controller(), artists=((1, "Nirvana"), (2, "Nirvana")))
    _fill(dialog, "Nirvana", "Nirvana")
    dialog.influenced_field.field.matched_id = lambda: 2

    dialog.add_influence()

    dialog.accept.assert_called_once()
    assert dialog.added_influence == ((1, "Nirvana"), (2, "Nirvana"))


def test_add_influence_blocks_self_influence(qapp, no_message_boxes, status_messages):
    controller = _controller()
    dialog = _dialog(qapp, controller)
    _fill(dialog, "Miles Davis", "miles davis")

    dialog.add_influence()

    dialog.accept.assert_not_called()
    assert any("cannot influence themselves" in m for m in status_messages)
    controller.add.add_entity.assert_not_called()


def test_add_influence_creates_new_artists_and_reports_pair(qapp, no_message_boxes):
    dialog = _dialog(qapp, _controller(next_artist_id=50))
    _fill(dialog, "New Influencer", "John Coltrane")

    dialog.add_influence()

    dialog.accept.assert_called_once()
    assert dialog.added_influence == ((50, "New Influencer"), (2, "John Coltrane"))
    assert dialog.get_created_artists() == [(50, "New Influencer")]


def _influences():
    return [
        {"influencer_id": 1, "influenced_id": 2, "influencer_name": "Miles Davis", "influenced_name": "John Coltrane", "description": "Modal jazz"},
        {"influencer_id": 3, "influenced_id": 4, "influencer_name": "Muddy Waters", "influenced_name": "The Rolling Stones", "description": None},
    ]


def test_remove_dialog_keyboard_navigation_selects_row(qapp):
    dialog = RemoveInfluenceDialog(MagicMock(), _influences())

    dialog.results_list.setCurrentRow(1)

    assert dialog.selected_influence["influencer_id"] == 3
    assert dialog.remove_button.isEnabled()
    assert dialog.results_list.item(1).data(Qt.UserRole)["influenced_id"] == 4


def test_remove_dialog_filter_clears_selection(qapp):
    dialog = RemoveInfluenceDialog(MagicMock(), _influences())
    dialog.results_list.setCurrentRow(0)

    dialog.search_box.setText("muddy")

    assert dialog.results_list.count() == 1
    assert dialog.selected_influence is None
    assert not dialog.remove_button.isEnabled()


def test_remove_dialog_success_sets_removed_influence_without_popup(qapp, no_message_boxes, monkeypatch):
    information = MagicMock()
    monkeypatch.setattr(QMessageBox, "information", information)
    controller = MagicMock()
    controller.delete.delete_entity.return_value = True
    dialog = RemoveInfluenceDialog(controller, _influences())
    dialog.results_list.setCurrentRow(0)

    dialog.remove_influence()

    assert dialog.removed_influence["influencer_id"] == 1
    information.assert_not_called()
    controller.delete.delete_entity.assert_called_once_with("ArtistInfluence", influencer_id=1, influenced_id=2)
