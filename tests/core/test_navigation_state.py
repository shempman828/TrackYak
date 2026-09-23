"""Unit tests for GUI's persisted navbar order/visibility handling.

Covers docs/specs/navbar_customization.md AC3, AC4, AC5, AC9. Follows the
lightweight-stand-in pattern from test_queue_toggle_nav_squish.py rather than
constructing a full GUI (which needs a real controller/mediaplayer/db) --
only the attributes _load_navigation_state / _populate_navigation /
apply_navigation_state actually touch are provided.
"""

from PySide6.QtWidgets import QStackedWidget, QTreeWidget, QWidget

from src.core.main_window import GUI
from src.foundation.config_setup import app_config


class _NavHost:
    # apply_navigation_state calls self._populate_navigation() internally;
    # bind the real implementation so the stand-in behaves the same way a
    # real GUI instance would.
    _populate_navigation = GUI._populate_navigation

    def __init__(self, view_names):
        self._view_factories = dict.fromkeys(view_names, lambda: None)
        self.nav_tree = QTreeWidget()
        self._view_cache = {}
        self.stacked_widget = QStackedWidget()
        self.view_registry = {}
        for name in view_names:
            idx = self.stacked_widget.addWidget(QWidget())
            self.view_registry[name] = idx

    def _ensure_view_built(self, view_name):
        self._view_cache[view_name] = True


def _labels(host):
    return [host.nav_tree.topLevelItem(i).text(0) for i in range(host.nav_tree.topLevelItemCount())]


def test_populate_navigation_respects_persisted_order_and_hidden(qapp, monkeypatch):
    host = _NavHost(["Tracks", "Albums", "Artists", "Genres"])
    monkeypatch.setattr(app_config, "get_nav_item_order", lambda: ["Genres", "Tracks", "Artists", "Albums"])
    monkeypatch.setattr(app_config, "get_nav_hidden_items", lambda: ["Artists"])

    GUI._load_navigation_state(host)
    GUI._populate_navigation(host)

    assert _labels(host) == ["Genres", "Tracks", "Albums"]


def test_populate_navigation_appends_unknown_views_visible_by_default(qapp, monkeypatch):
    host = _NavHost(["Tracks", "Albums", "Artists"])
    monkeypatch.setattr(app_config, "get_nav_item_order", lambda: ["Tracks", "Albums"])
    monkeypatch.setattr(app_config, "get_nav_hidden_items", lambda: [])

    GUI._load_navigation_state(host)
    GUI._populate_navigation(host)

    assert _labels(host) == ["Tracks", "Albums", "Artists"]


def test_populate_navigation_never_hides_tracks(qapp, monkeypatch):
    host = _NavHost(["Tracks", "Albums"])
    monkeypatch.setattr(app_config, "get_nav_item_order", lambda: ["Tracks", "Albums"])
    monkeypatch.setattr(app_config, "get_nav_hidden_items", lambda: ["Tracks"])

    GUI._load_navigation_state(host)
    GUI._populate_navigation(host)

    assert "Tracks" in _labels(host)


def test_apply_navigation_state_switches_away_from_now_hidden_view(qapp, monkeypatch):
    host = _NavHost(["Tracks", "Albums", "Artists"])
    saved = {}
    monkeypatch.setattr(app_config, "get_nav_item_order", lambda: [])
    monkeypatch.setattr(app_config, "get_nav_hidden_items", lambda: [])
    monkeypatch.setattr(app_config, "set_nav_item_order", lambda v: saved.__setitem__("order", v))
    monkeypatch.setattr(app_config, "set_nav_hidden_items", lambda v: saved.__setitem__("hidden", v))
    GUI._load_navigation_state(host)
    GUI._populate_navigation(host)
    host.stacked_widget.setCurrentIndex(host.view_registry["Albums"])

    GUI.apply_navigation_state(host, ["Tracks", "Artists", "Albums"], ["Albums"])

    assert saved["order"] == ["Tracks", "Artists", "Albums"]
    assert saved["hidden"] == ["Albums"]
    assert host.stacked_widget.currentIndex() == host.view_registry["Tracks"]


def test_apply_navigation_state_keeps_current_view_when_still_visible(qapp, monkeypatch):
    host = _NavHost(["Tracks", "Albums", "Artists"])
    monkeypatch.setattr(app_config, "get_nav_item_order", lambda: [])
    monkeypatch.setattr(app_config, "get_nav_hidden_items", lambda: [])
    monkeypatch.setattr(app_config, "set_nav_item_order", lambda v: None)
    monkeypatch.setattr(app_config, "set_nav_hidden_items", lambda v: None)
    GUI._load_navigation_state(host)
    GUI._populate_navigation(host)
    host.stacked_widget.setCurrentIndex(host.view_registry["Albums"])

    GUI.apply_navigation_state(host, ["Tracks", "Albums", "Artists"], ["Artists"])

    assert host.stacked_widget.currentIndex() == host.view_registry["Albums"]
