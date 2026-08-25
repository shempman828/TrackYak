"""Regression test for OrganizationPreviewDialog's ops_list scroll feel.

Same root cause found in track_edit_roles.py's _RolesTable (see
tests/album/test_track_credits_tab.py::
test_roles_table_wheel_step_is_fixed_not_row_height_derived) and
mood_autotag_dialog.py's _WordTable: item heights here vary a lot (see
_calculate_item_size -- a long file path gets a much taller item than a
short one), and Qt's default ScrollPerItem mode jumps a full (variable)
item per wheel notch. This one didn't even have the partial ScrollPerPixel
fix the other two started with, so it's the plainest form of the bug.
"""

from PySide6.QtWidgets import QAbstractItemView

from src.file_management.file_organizer_preview_dialog import (
    OrganizationPreviewDialog,
)


def test_ops_list_wheel_step_is_fixed_not_item_height_derived(qapp):
    ops = [
        {"current_path": "/music/a.mp3", "expected_path": "/music/sorted/a.mp3"},
        {
            "current_path": "/music/" + ("very_long_path_segment_" * 10) + ".mp3",
            "expected_path": "/music/sorted/"
            + ("very_long_path_segment_" * 10)
            + ".mp3",
        },
    ]

    dlg = OrganizationPreviewDialog(None, ops)
    try:
        item_heights = [
            dlg.ops_list.item(i).sizeHint().height()
            for i in range(dlg.ops_list.count())
        ]
        # Confirms the fixture actually exercises variable heights -- a
        # false pass here would mean the test isn't testing anything.
        assert max(item_heights) - min(item_heights) > 20

        assert (
            dlg.ops_list.verticalScrollMode() == QAbstractItemView.ScrollPerPixel
        )
        step = dlg.ops_list.verticalScrollBar().singleStep()
        assert step < 40, (
            f"wheel step ({step}px) scales with the tallest ({max(item_heights)}px) "
            "item -- still feels like scrolling by item, not by pixel"
        )
    finally:
        dlg.deleteLater()
