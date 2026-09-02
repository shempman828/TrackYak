"""Regression test for the Award Detail "Save Changes" crash.

AwardDetailTab._save_changes() reads self.parent_combo.currentData(), but
init_ui() never called _populate_parent_combo(), so self.parent_combo was
never created. Every save raised AttributeError, caught by the tab's own
except clause and surfaced as "Failed to save changes.", so Name/Year/
Category/Description edits never persisted either.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

from PySide6.QtWidgets import QTabWidget

from src.award.award_detail import AwardDetailTab


def _make_controller(other_awards=None):
    controller = MagicMock()

    def get_all_entities(model_name, **kwargs):
        if model_name == "Award":
            return other_awards or []
        return []  # AwardAssociation lookups used by _refresh_recipient_display

    controller.get.get_all_entities.side_effect = get_all_entities
    return controller


def test_save_changes_persists_without_crashing(qapp):
    award = SimpleNamespace(
        award_id=1,
        award_name="Grammy",
        award_year=2020,
        award_category="Best Album",
        award_description="",
        parent_id=None,
    )
    controller = _make_controller()

    tab = AwardDetailTab(award, controller)
    # _save_changes() walks self.parent() looking for an enclosing QTabWidget
    # to rename; give it one so that walk (and the parent().parent() lookup
    # after it) resolves instead of hitting a None and going through the
    # modal QMessageBox.critical error path.
    tab_widget = QTabWidget()
    tab_widget.addTab(tab, "Grammy")

    assert hasattr(tab, "parent_combo")

    tab.name_edit.setText("Grammy Award")
    tab._save_changes()

    controller.update.update_entity.assert_called_once_with(
        "Award",
        1,
        award_name="Grammy Award",
        award_year=2020,
        award_category="Best Album",
        award_description=None,
        parent_id=None,
    )
    assert not tab.save_btn.isEnabled()


def test_parent_combo_population_does_not_enable_save_button(qapp):
    award = SimpleNamespace(
        award_id=1,
        award_name="Grammy",
        award_year=2020,
        award_category=None,
        award_description=None,
        parent_id=None,
    )
    controller = _make_controller(
        other_awards=[
            SimpleNamespace(award_id=2, award_name="Other Award", award_year=2019)
        ]
    )

    tab = AwardDetailTab(award, controller)

    assert tab.parent_combo.count() == 2
    assert not tab.save_btn.isEnabled()
