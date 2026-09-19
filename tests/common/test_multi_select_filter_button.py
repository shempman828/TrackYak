"""Tests for the searchable, checkable multi-select filter button used by
AwardView's Award Name filter (and reusable elsewhere)."""

from PySide6.QtCore import Qt

from src.common.widgets.multi_select_filter_button import MultiSelectFilterButton


def _check(button: MultiSelectFilterButton, *names: str) -> None:
    """Set only the given names checked; leave everything else unchecked."""
    for i in range(button._list_widget.count()):
        item = button._list_widget.item(i)
        item.setCheckState(Qt.Checked if item.text() in names else Qt.Unchecked)


def test_set_values_sorts_and_defaults_to_all_checked(qapp):
    button = MultiSelectFilterButton()
    button.set_values(["B", "A", "C"])

    names = [button._list_widget.item(i).text() for i in range(button._list_widget.count())]
    assert names == ["A", "B", "C"]
    assert all(
        button._list_widget.item(i).checkState() == Qt.Checked
        for i in range(button._list_widget.count())
    )
    assert button.committed_selection() == set()
    assert button.button_text() == "All"


def test_search_hides_rows_without_changing_checked_state(qapp):
    button = MultiSelectFilterButton()
    button.set_values(["Grammy Award", "Mercury Prize", "Grand Prix"])
    _check(button, "Mercury Prize")  # only one checked, rest unchecked

    button._search_edit.setText("gra")

    visible = {
        button._list_widget.item(i).text()
        for i in range(button._list_widget.count())
        if not button._list_widget.item(i).isHidden()
    }
    assert visible == {"Grammy Award", "Grand Prix"}

    # Checked state is untouched by the search.
    states = {
        button._list_widget.item(i).text(): button._list_widget.item(i).checkState()
        for i in range(button._list_widget.count())
    }
    assert states["Mercury Prize"] == Qt.Checked
    assert states["Grammy Award"] == Qt.Unchecked
    assert states["Grand Prix"] == Qt.Unchecked


def test_ok_commits_the_checked_subset(qapp):
    button = MultiSelectFilterButton()
    button.set_values(["Album of the Year", "Best New Artist", "Grammy Award"])
    _check(button, "Album of the Year", "Best New Artist")

    received = []
    button.selection_changed.connect(lambda: received.append(button.committed_selection()))
    button._commit_and_close()

    assert button.committed_selection() == {"Album of the Year", "Best New Artist"}
    assert button.button_text() == "2 selected"
    assert received == [{"Album of the Year", "Best New Artist"}]


def test_ok_with_single_checked_shows_its_name_on_the_button(qapp):
    button = MultiSelectFilterButton()
    button.set_values(["Album of the Year", "Best New Artist"])
    _check(button, "Album of the Year")

    button._commit_and_close()

    assert button.committed_selection() == {"Album of the Year"}
    assert button.button_text() == "Album of the Year"


def test_cancel_reverts_to_last_committed_state(qapp):
    button = MultiSelectFilterButton()
    button.set_values(["A", "B", "C"])
    _check(button, "A")
    button._commit_and_close()
    assert button.committed_selection() == {"A"}

    # Toggle things around, then Cancel instead of OK.
    _check(button, "B", "C")
    button._revert_and_close()

    assert button.committed_selection() == {"A"}
    assert button.button_text() == "A"

    # Reopening the popup (aboutToShow) restores the checkbox states too.
    button._sync_checkboxes_to_committed()
    states = {
        button._list_widget.item(i).text(): button._list_widget.item(i).checkState()
        for i in range(button._list_widget.count())
    }
    assert states == {"A": Qt.Checked, "B": Qt.Unchecked, "C": Qt.Unchecked}


def test_select_all_only_affects_visible_rows(qapp):
    button = MultiSelectFilterButton()
    button.set_values(["Grammy Award", "Mercury Prize", "Grand Prix"])
    _check(button)  # nothing checked to start

    button._search_edit.setText("gra")  # hides Mercury Prize
    button._on_select_all_clicked(True)

    states = {
        button._list_widget.item(i).text(): button._list_widget.item(i).checkState()
        for i in range(button._list_widget.count())
    }
    assert states["Grammy Award"] == Qt.Checked
    assert states["Grand Prix"] == Qt.Checked
    assert states["Mercury Prize"] == Qt.Unchecked  # hidden row untouched


def test_select_all_checkbox_reflects_partial_and_full_selection(qapp):
    button = MultiSelectFilterButton()
    button.set_values(["A", "B"])

    assert button._select_all_checkbox.checkState() == Qt.Checked  # all checked by default

    button._list_widget.item(0).setCheckState(Qt.Unchecked)
    assert button._select_all_checkbox.checkState() == Qt.PartiallyChecked

    button._list_widget.item(1).setCheckState(Qt.Unchecked)
    assert button._select_all_checkbox.checkState() == Qt.Unchecked


def test_set_values_preserves_selection_for_surviving_names_and_drops_the_rest(qapp):
    button = MultiSelectFilterButton()
    button.set_values(["A", "B", "C"])
    _check(button, "A", "B")
    button._commit_and_close()
    assert button.committed_selection() == {"A", "B"}

    # "B" is gone in the refreshed list; "A" survives.
    button.set_values(["A", "C", "D"])

    assert button.committed_selection() == {"A"}
    assert button.button_text() == "A"
    states = {
        button._list_widget.item(i).text(): button._list_widget.item(i).checkState()
        for i in range(button._list_widget.count())
    }
    assert states == {"A": Qt.Checked, "C": Qt.Unchecked, "D": Qt.Unchecked}


def test_checking_every_row_manually_is_treated_as_all(qapp):
    button = MultiSelectFilterButton()
    button.set_values(["A", "B"])
    _check(button, "A", "B")  # everything checked, same as the default

    button._commit_and_close()

    assert button.committed_selection() == set()
    assert button.button_text() == "All"


def test_unchecking_every_row_is_also_treated_as_all(qapp):
    button = MultiSelectFilterButton()
    button.set_values(["A", "B"])
    _check(button)  # nothing checked

    button._commit_and_close()

    assert button.committed_selection() == set()
    assert button.button_text() == "All"
