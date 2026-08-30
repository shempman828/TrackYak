"""Developer-mode "Primary Artist Count" album sort — behaviour + injection.

Maps to acceptance criteria 2-9 and 11 in
docs/specs/developer_mode_primary_artist_count_sort.md.
"""

from pathlib import Path
import subprocess

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QWidget

from src.album import album_filtering as album_filtering_module, album_view as album_view_module
from src.album.album_sorting import AlbumSortingMixin
from src.album.album_view import AlbumView
import src.dev as dev_pkg
from src.dev import dev_album_sort, dev_mode

_REPO_ROOT = Path(__file__).resolve().parents[2]


# --------------------------------------------------------------------------- #
# Lightweight stand-ins — the sort logic only touches these attributes.
# --------------------------------------------------------------------------- #
class _Artist:
    def __init__(self, artist_id):
        self.artist_id = artist_id


class _Track:
    def __init__(self, primary_artists):
        self.primary_artists = list(primary_artists)


class _Album:
    def __init__(self, album_name, tracks=(), album_id=None):
        self.album_name = album_name
        self.tracks = list(tracks)
        self.album_id = album_id


class _SortHost(AlbumSortingMixin):
    """Minimal host exercising the real mixin sort path without Qt."""

    def __init__(self, albums, *, criteria=dev_album_sort.CRITERIA, descending=False):
        self.filtered_albums = list(albums)
        self._sort_criteria = criteria
        self._sort_descending = descending
        self._random_keys = {}


def _make_album_view(monkeypatch, albums):
    class _StubWidget(QWidget):
        clicked = Signal(object)

        def __init__(self, album, size=200, parent=None):
            super().__init__(parent)
            self.album = album

    class _FakeCfg:
        def get_album_view_filters(self):
            return {}

        def set_album_view_filters(self, _filters):
            pass

        def save(self):
            pass

    class _Get:
        def get_all_entities(self, *_a, **_k):
            return list(albums)

    class _Ctl:
        get = _Get()

    monkeypatch.setattr(album_view_module, "AlbumWidget", _StubWidget)
    monkeypatch.setattr(album_filtering_module, "app_config", _FakeCfg())
    return AlbumView(_Ctl())


# --------------------------------------------------------------------------- #
# AC4 — owns()
# --------------------------------------------------------------------------- #
def test_owns_only_claims_its_own_criteria():
    assert dev_album_sort.owns("primary_artist_count") is True
    assert dev_album_sort.owns("title") is False
    assert dev_album_sort.owns("album_artist_count") is False


# --------------------------------------------------------------------------- #
# AC6 / AC7 — the dedup count
# --------------------------------------------------------------------------- #
def test_primary_artist_count_dedupes_across_tracks():
    a, b, c = _Artist(1), _Artist(2), _Artist(3)
    album = _Album("x", [_Track([a]), _Track([a, b]), _Track([b, c])])
    assert dev_album_sort.primary_artist_count(album) == 3


def test_primary_artist_count_zero_when_no_tracks_or_no_primaries():
    assert dev_album_sort.primary_artist_count(_Album("empty", [])) == 0
    assert dev_album_sort.primary_artist_count(_Album("nocred", [_Track([]), _Track([])])) == 0


def test_primary_artist_count_falls_back_to_identity_without_id():
    nameless = [_Artist(None), _Artist(None)]
    album = _Album("x", [_Track(nameless)])
    assert dev_album_sort.primary_artist_count(album) == 2


# --------------------------------------------------------------------------- #
# AC8 — sort order through the real mixin
# --------------------------------------------------------------------------- #
def test_sort_order_most_and_fewest_first(dev_config, dev_patches):
    dev_mode.set_enabled(dev_config, True)
    dev_pkg.install()

    albums = [
        _Album("one", [_Track([_Artist(1)])]),
        _Album("three", [_Track([_Artist(1), _Artist(2), _Artist(3)])]),
        _Album("two", [_Track([_Artist(1), _Artist(2)])]),
    ]

    host = _SortHost(albums, descending=True)
    host._sort_filtered()
    assert [a.album_name for a in host.filtered_albums] == ["three", "two", "one"]

    host = _SortHost(albums, descending=False)
    host._sort_filtered()
    assert [a.album_name for a in host.filtered_albums] == ["one", "two", "three"]


# --------------------------------------------------------------------------- #
# AC9 — stale persisted criteria with the flag off
# --------------------------------------------------------------------------- #
def test_stale_criteria_is_safe_when_flag_off(dev_config, dev_patches):
    dev_mode.set_enabled(dev_config, False)
    dev_pkg.install()

    albums = [_Album("a", [_Track([_Artist(1), _Artist(2)])]), _Album("b", [])]
    host = _SortHost(albums)
    host._sort_filtered()  # must not raise

    assert [host._sort_key(a) for a in albums] == [0, 0]


# --------------------------------------------------------------------------- #
# AC5 — combo integration
# --------------------------------------------------------------------------- #
def test_sort_combo_gains_dev_group_when_enabled(qapp, monkeypatch, dev_config, dev_patches):
    dev_mode.set_enabled(dev_config, True)
    dev_pkg.install()

    view = _make_album_view(monkeypatch, [])
    try:
        model = view.sort_combo.model()
        rows = [
            (model.item(i).text().strip(), model.item(i).data(Qt.UserRole))
            for i in range(model.rowCount())
        ]
        labels = [text for text, _data in rows]
        datas = [data for _text, data in rows]

        assert "Developer" in labels
        header = model.item(labels.index("Developer"))
        assert not (header.flags() & Qt.ItemIsSelectable)
        assert header.font().bold()
        assert ("primary_artist_count", True) in datas
        assert ("primary_artist_count", False) in datas
    finally:
        view.deleteLater()
        qapp.processEvents()


def test_sort_combo_has_no_dev_group_when_disabled(qapp, monkeypatch, dev_config, dev_patches):
    dev_mode.set_enabled(dev_config, False)
    dev_pkg.install()

    view = _make_album_view(monkeypatch, [])
    try:
        model = view.sort_combo.model()
        rows = [
            (model.item(i).text().strip(), model.item(i).data(Qt.UserRole))
            for i in range(model.rowCount())
        ]
        assert "Developer" not in [text for text, _ in rows]
        assert not any(data and data[0] == "primary_artist_count" for _, data in rows)
    finally:
        view.deleteLater()
        qapp.processEvents()


# --------------------------------------------------------------------------- #
# AC2 — install() idempotent
# --------------------------------------------------------------------------- #
def test_install_is_idempotent(dev_config, dev_patches):
    dev_mode.set_enabled(dev_config, True)
    dev_pkg.install()
    dev_pkg.install()

    dev_groups = [g for g in AlbumSortingMixin._SORT_GROUPS if g[0] == "Developer"]
    assert len(dev_groups) == 1

    # One wrap only: a single unpatch fully restores the plain list + method.
    dev_pkg.uninstall()
    assert [g for g in AlbumSortingMixin._SORT_GROUPS if g[0] == "Developer"] == []
    assert dev_album_sort._orig_sort_key is None


# --------------------------------------------------------------------------- #
# AC11 — teardown restores the mixin
# --------------------------------------------------------------------------- #
def test_unpatch_restores_mixin(dev_config, dev_patches):
    orig_groups = list(AlbumSortingMixin._SORT_GROUPS)
    orig_key = AlbumSortingMixin._sort_key

    dev_mode.set_enabled(dev_config, True)
    dev_pkg.install()
    assert any(g[0] == "Developer" for g in AlbumSortingMixin._SORT_GROUPS)
    assert AlbumSortingMixin._sort_key is not orig_key

    dev_pkg.uninstall()
    assert orig_groups == AlbumSortingMixin._SORT_GROUPS
    assert AlbumSortingMixin._sort_key is orig_key


# --------------------------------------------------------------------------- #
# AC3 — no src/ reference to the dev package
# --------------------------------------------------------------------------- #
def test_no_src_module_references_dev_package():
    res = subprocess.run(
        ["grep", "-rn", "--include=*.py", "-e", "src.dev", "-e", "src/dev", "src"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
    )
    offending = [line for line in res.stdout.splitlines() if not line.startswith("src/dev/")]
    assert not offending, "src/ must not reference the dev package:\n" + "\n".join(offending)
