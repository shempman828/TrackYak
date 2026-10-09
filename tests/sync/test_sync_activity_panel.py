"""SyncActivityPanel: stat tiles + per-playlist result rows for a sync run."""

import pytest

from src.sync.sync_activity_panel import SyncActivityPanel

pytestmark = pytest.mark.usefixtures("qapp")


def _result(name, success=True, copied=0, skipped=0, failed=0, transcoded=0, failures=()):
    return {
        "playlist_name": name,
        "success": success,
        "message": "done",
        "tracks_copied": copied,
        "tracks_skipped": skipped,
        "tracks_failed": failed,
        "tracks_transcoded": transcoded,
        "failures": list(failures),
    }


def test_results_accumulate_into_tiles_and_rows():
    panel = SyncActivityPanel()
    panel.begin("Pixel 8")
    panel.add_result(_result("A", copied=3, skipped=2, transcoded=1))
    panel.add_result(
        _result("B", success=False, failed=1, failures=[{"artist": "X", "title": "Y", "reason": "source file not found"}])
    )
    panel.add_removed(["old.mp3", "Old.m3u"])

    values = {key: tile.value_label.text() for key, tile in panel._tiles.items()}
    assert values == {"copied": "3", "skipped": "2", "transcoded": "1", "failed": "1", "removed": "2"}
    assert panel.results_tree.topLevelItemCount() == 3
    failed_row = panel.results_tree.topLevelItem(1)
    assert failed_row.text(0).startswith("✗")
    assert failed_row.child(0).text(1) == "source file not found"
    assert failed_row.isExpanded()


def test_begin_resets_and_clear_returns_to_empty_state():
    panel = SyncActivityPanel()
    panel.begin("dest")
    panel.add_result(_result("A", copied=5))
    panel.begin("dest")
    assert panel.results_tree.topLevelItemCount() == 0
    assert panel._tiles["copied"].value_label.text() == "0"
    assert not panel.clear_btn.isEnabled()  # can't clear a running sync

    panel.finish("Sync complete", "ok")
    assert panel.clear_btn.isEnabled()
    panel.clear()
    assert panel._stack.currentIndex() == 0
