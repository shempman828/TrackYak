"""Filing-name (Artist.sort_name) ordering: the sort-key fallback rules,
the Add Member combo, and the orphan-artist delete dialog
(docs/specs/artist_sort_name_ordering.md), plus the batched orphan delete.
"""

from PySide6.QtWidgets import QComboBox, QDialog, QWidget
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.artist.artist_sort import artist_filing_name, artist_sort_key
from src.artist.dialogs.artist_delete_orphans import OrphanArtistDialog
from src.artist.dialogs.artist_group_dialog import _populate_individual_artist_combo
from src.artist.view.artist_view_dedup import ArtistDedupMixin
from src.db.db_helpers.delete import DeleteDB
from src.db.db_helpers.get import GetFromDB
from src.db.db_tables.artist import Artist
from src.db.db_tables.base import Base

# ---------------------------------------------------------------------------
# AC1 -- artist_sort_key / artist_filing_name fallback rules.
# ---------------------------------------------------------------------------


class StubArtist:
    def __init__(self, artist_name=None, sort_name=None):
        if artist_name is not None:
            self.artist_name = artist_name
        if sort_name is not None:
            self.sort_name = sort_name


def test_uses_sort_name_when_set():
    a = StubArtist(artist_name="The Beatles", sort_name="Beatles, The")
    assert artist_filing_name(a) == "Beatles, The"
    assert artist_sort_key(a) == "beatles, the"


def test_falls_back_to_name_when_sort_name_none():
    a = StubArtist(artist_name="Bee Gees", sort_name=None)
    assert artist_filing_name(a) == "Bee Gees"
    assert artist_sort_key(a) == "bee gees"


def test_falls_back_to_name_when_sort_name_blank():
    a = StubArtist(artist_name="Tom Waits", sort_name="   ")
    assert artist_filing_name(a) == "Tom Waits"
    assert artist_sort_key(a) == "tom waits"


def test_empty_string_when_both_missing():
    assert artist_filing_name(StubArtist()) == ""
    assert artist_sort_key(StubArtist()) == ""
    assert artist_filing_name(StubArtist(artist_name=None, sort_name=None)) == ""


def test_missing_sort_name_attribute_entirely():
    """Objects that never define sort_name (stubs, partial rows) must not raise."""
    a = StubArtist(artist_name="Neil Young")
    assert not hasattr(a, "sort_name")
    assert artist_sort_key(a) == "neil young"


# ---------------------------------------------------------------------------
# AC4 -- the "Add Member" individual-artist combo is ordered by filing
# ---------------------------------------------------------------------------


class StubArtistDialog:
    def __init__(self, artist_id, artist_name, sort_name=None, isgroup=False):
        self.artist_id = artist_id
        self.artist_name = artist_name
        self.sort_name = sort_name
        self.isgroup = isgroup


class StubGetController:
    def __init__(self, artists):
        self._artists = artists

    def get_all_entities(self, entity_type):
        return list(self._artists)


class StubController:
    def __init__(self, artists):
        self.get = StubGetController(artists)


def test_combo_ordered_by_filing_name(qapp):
    artists = [
        StubArtistDialog(1, "Tom Waits", "Waits, Tom"),
        StubArtistDialog(2, "The Beatles", "Beatles, The"),
        StubArtistDialog(3, "Bee Gees", "Bee Gees"),
        StubArtistDialog(4, "The Kinks", isgroup=True),  # filtered out (group)
    ]
    combo = QComboBox()
    _populate_individual_artist_combo(combo, StubController(artists))

    # index 0 is the "-- Select Artist --" placeholder
    items = [combo.itemText(i) for i in range(1, combo.count())]
    assert items == ["The Beatles", "Bee Gees", "Tom Waits"]
    assert "Waits, Tom" not in items


# ---------------------------------------------------------------------------
# AC5 -- the orphan-artist delete dialog lists candidates in filing-name
# ---------------------------------------------------------------------------


class StubArtistOrphans:
    def __init__(self, artist_id, artist_name, sort_name=None):
        self.artist_id = artist_id
        self.artist_name = artist_name
        self.sort_name = sort_name


ORPHANS = [
    StubArtistOrphans(1, "Tom Waits", "Waits, Tom"),
    StubArtistOrphans(2, "The Beatles", "Beatles, The"),
    StubArtistOrphans(3, "Bee Gees", "Bee Gees"),
    StubArtistOrphans(4, "ABBA", None),  # no sort_name -> falls back to "abba"
]


def test_orphans_sorted_by_filing_name(qapp):
    dlg = OrphanArtistDialog(list(ORPHANS))
    assert [a.artist_name for a in dlg.orphans] == ["ABBA", "The Beatles", "Bee Gees", "Tom Waits"]


def test_checkbox_labels_show_plain_name(qapp):
    dlg = OrphanArtistDialog(list(ORPHANS))
    labels = [cb.text() for cb in dlg._checkboxes]
    assert labels == ["ABBA", "The Beatles", "Bee Gees", "Tom Waits"]
    assert "Beatles, The" not in labels


# ---------------------------------------------------------------------------
# Regression test: find_orphan_artists() used to delete selected artists one
# ---------------------------------------------------------------------------


class _Controller:
    def __init__(self, session):
        self.get = GetFromDB(session)
        self.delete = DeleteDB(session)


class _ArtistDedupHost(QWidget, ArtistDedupMixin):
    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.load_calls = 0

    def load_artists(self):
        self.load_calls += 1


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


@pytest.fixture
def controller(session):
    return _Controller(session)


def _make_orphan_artists(session, count):
    artists = [Artist(artist_name=f"Orphan {i}") for i in range(count)]
    session.add_all(artists)
    session.commit()
    return artists


def test_find_orphan_artists_batches_delete_into_one_commit(qapp, controller, monkeypatch):
    orphans = _make_orphan_artists(controller.get.session, 5)
    host = _ArtistDedupHost(controller)

    monkeypatch.setattr(OrphanArtistDialog, "exec_", lambda self: QDialog.Accepted)
    monkeypatch.setattr(
        OrphanArtistDialog,
        "selected_artist_ids",
        lambda self: [a.artist_id for a in orphans],
    )

    calls = []
    real_delete_entity = controller.delete.delete_entity

    def spying_delete_entity(*args, **kwargs):
        calls.append((args, kwargs))
        return real_delete_entity(*args, **kwargs)

    monkeypatch.setattr(controller.delete, "delete_entity", spying_delete_entity)

    host.find_orphan_artists()

    assert len(calls) == 1, "delete_entity must be called once for the whole batch, not per artist"
    _args, kwargs = calls[0]
    assert kwargs.get("entity_ids") == [a.artist_id for a in orphans]
    assert "entity_id" not in kwargs

    remaining = controller.get.get_all_entities("Artist")
    assert remaining == []
    assert host.load_calls == 1
