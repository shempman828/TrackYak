"""Regression test: the artist-detail Credits section used to render inside
its own nested QScrollArea capped at 400px with no minimum. That inner
scroll area froze at its collapsed sizeHint (~190px), so every credit
table was clamped to a 2-row sliver behind a second, nested vertical
scrollbar, and every role started collapsed -- the credits were, in
practice, not visible.

Credits must now render inline (no nested scroll area -- the article's own
scroll area does the scrolling), with each role expanded by default and
its table tall enough to show every row. The Year column must be
populated from Album.release_year (the old code read a non-existent
`year` attribute, so it was always blank).
"""

from PySide6.QtWidgets import QScrollArea
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.artist.artist_detail import ArtistDetailTab
from src.artist.artist_detail_credits import CreditsWidget, RoleSection
from src.db.db_helpers.get import GetFromDB
from src.db.db_tables import Album, AlbumRoleAssociation, Artist, Role, Track, TrackArtistRole
from src.db.db_tables.base import Base


class _Controller:
    def __init__(self, session):
        self.get = GetFromDB(session)


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


def _artist_with_credits(session, n_track_credits=6):
    artist = Artist(artist_name="Test Artist")
    guitar = Role(role_name="Guitar")
    producer = Role(role_name="Producer")
    album = Album(album_name="Test Album", release_year=1971)
    tracks = [Track(track_name=f"Track {i}", album=album) for i in range(n_track_credits)]
    session.add_all([artist, guitar, producer, album, *tracks])
    session.flush()
    for t in tracks:
        session.add(
            TrackArtistRole(track_id=t.track_id, artist_id=artist.artist_id, role_id=guitar.role_id)
        )
    session.add(
        AlbumRoleAssociation(
            album_id=album.album_id, artist_id=artist.artist_id, role_id=producer.role_id
        )
    )
    session.commit()
    return session.get(Artist, artist.artist_id)


def test_credits_render_inline_without_nested_scroll_area(qapp, controller):
    artist = _artist_with_credits(controller.get.session)
    widget = CreditsWidget(artist, controller)

    assert not widget.isHidden()
    # The self-contained nested scroll area (the root cause) is gone.
    assert widget.findChild(QScrollArea) is None


def test_role_tables_show_every_row(qapp, controller):
    artist = _artist_with_credits(controller.get.session, n_track_credits=6)
    tab = ArtistDetailTab(artist, controller)
    tab.resize(1000, 900)
    tab.show()
    qapp.processEvents()
    qapp.processEvents()

    sections = {s.role_name: s for s in tab.findChildren(RoleSection)}
    assert set(sections) == {"Guitar", "Producer"}

    for section in sections.values():
        assert section.is_loaded
        assert section.table_container.isVisible()  # expanded by default
        table = section.table_widget
        assert table is not None
        # No internal vertical scroll: the last row is fully within the viewport.
        last = table.rowCount() - 1
        bottom = table.rowViewportPosition(last) + table.rowHeight(last)
        assert bottom <= table.viewport().height() + 1
        assert not table.verticalScrollBar().isVisible()

    tab.deleteLater()


def test_year_column_populated_from_release_year(qapp, controller):
    artist = _artist_with_credits(controller.get.session, n_track_credits=1)
    widget = CreditsWidget(artist, controller)

    guitar = widget.role_sections["Guitar"]
    table = guitar.table_widget
    assert table.item(0, 2).text() == "1971"


def test_widget_hidden_when_artist_has_no_credits(qapp, controller):
    artist = Artist(artist_name="Nobody")
    controller.get.session.add(artist)
    controller.get.session.commit()
    artist = controller.get.session.get(Artist, artist.artist_id)

    widget = CreditsWidget(artist, controller)
    assert widget.isHidden()
