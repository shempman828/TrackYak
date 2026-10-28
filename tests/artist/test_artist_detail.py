"""Artist detail panel: header type badge, biography block, and credits."""

from types import SimpleNamespace

from PySide6.QtWidgets import QApplication, QFrame, QLabel, QScrollArea, QVBoxLayout, QWidget
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.artist.detail.artist_detail import ArtistDetailTab
from src.artist.detail.artist_detail_awards import AwardsWidget
from src.artist.detail.artist_detail_bio import BioWidget
from src.artist.detail.artist_detail_credits import CreditsWidget, RoleSection
from src.artist.detail.artist_detail_header import ArtistInfobox
from src.db.db_helpers.get import GetFromDB
from src.db.db_tables import Album, AlbumRoleAssociation, Artist, Award, AwardAssociation, Role, Track, TrackArtistRole
from src.db.db_tables.base import Base

# ---------------------------------------------------------------------------
# Unit tests for the artist-detail ArtistInfobox.
# ---------------------------------------------------------------------------


# Qt's "no maximum" sentinel for widget dimensions.
QWIDGETSIZE_MAX = (1 << 24) - 1


def _artist(type_names):
    return SimpleNamespace(
        types=[SimpleNamespace(type_name=n) for n in type_names],
        profile_pic_path="",
        begin_year=None,
        end_year=None,
        begin_month=None,
        end_month=None,
        begin_day=None,
        end_day=None,
    )


def _infobox(type_names):
    # Held by the caller: without a live reference Qt GCs the box and
    # deletes its child labels out from under the assertions.
    return ArtistInfobox(_artist(type_names))


def _type_badge(box):
    return box.findChild(QLabel, "TypeBadge")


def test_type_badge_shows_all_types(qapp):
    names = ["Person", "Group", "Orchestra", "Choir", "Character", "Other"]
    box = _infobox(names)
    for name in names:
        assert name in _type_badge(box).text()


def test_type_badge_wraps_and_is_not_height_capped(qapp):
    box = _infobox(["Person", "Group", "Orchestra"])
    badge = _type_badge(box)
    assert badge.wordWrap() is True
    # A hard setFixedHeight() pins both min and max to 24; the badge must
    # be free to grow past a single line.
    assert badge.maximumHeight() == QWIDGETSIZE_MAX


def test_type_badge_falls_back_to_artist_label(qapp):
    box = _infobox([])
    assert _type_badge(box).text() == "Artist"


# ---------------------------------------------------------------------------
# Unit tests for the artist-detail BioWidget.
# ---------------------------------------------------------------------------


def _artist_bio(**kw):
    base = {"biography": "", "MBID": "", "wikipedia_link": "", "website_link": ""}
    base.update(kw)
    return SimpleNamespace(**base)


def _labels(widget):
    return [lbl.text() for lbl in widget.findChildren(QLabel)]


def _separators(widget):
    # QLabel subclasses QFrame, so match the HLine separator by shape.
    return [f for f in widget.findChildren(QFrame) if f.frameShape() == QFrame.HLine]


def test_no_placeholder_label_when_biography_missing(qapp):
    widget = BioWidget(_artist_bio())
    assert "No biography available." not in _labels(widget)


def test_no_separator_when_biography_missing(qapp):
    widget = BioWidget(_artist_bio(MBID="abc-123"))
    assert _separators(widget) == []


def test_separator_present_between_bio_and_links(qapp):
    widget = BioWidget(_artist_bio(biography="A real bio.", wikipedia_link="http://x"))
    assert len(_separators(widget)) == 1


def test_no_trailing_separator_when_bio_but_no_links(qapp):
    widget = BioWidget(_artist_bio(biography="A real bio."))
    assert _separators(widget) == []


def test_is_empty_flag_tracks_presence_of_any_content(qapp):
    assert BioWidget(_artist_bio()).is_empty is True
    assert BioWidget(_artist_bio(biography="hi")).is_empty is False
    assert BioWidget(_artist_bio(MBID="abc")).is_empty is False
    assert BioWidget(_artist_bio(wikipedia_link="http://x")).is_empty is False
    assert BioWidget(_artist_bio(website_link="http://y")).is_empty is False


# ---------------------------------------------------------------------------
# Regression test: the artist-detail Credits section used to render inside
# ---------------------------------------------------------------------------


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
        session.add(TrackArtistRole(track_id=t.track_id, artist_id=artist.artist_id, role_id=guitar.role_id))
    session.add(AlbumRoleAssociation(album_id=album.album_id, artist_id=artist.artist_id, role_id=producer.role_id))
    session.commit()
    return session.get(Artist, artist.artist_id)


def test_credits_render_inline_without_nested_scroll_area(qapp, controller):
    artist = _artist_with_credits(controller.get.session)
    widget = CreditsWidget(artist, controller)
    holder = QWidget()
    QVBoxLayout(holder).addWidget(widget)

    assert not widget.isHidden()
    # The self-contained nested scroll area (the root cause) is gone.
    assert widget.findChild(QScrollArea) is None


def test_role_sections_collapsed_by_default(qapp, controller):
    artist = _artist_with_credits(controller.get.session, n_track_credits=6)
    tab = ArtistDetailTab(artist, controller)
    tab.resize(1000, 900)
    tab.show()
    qapp.processEvents()
    qapp.processEvents()

    sections = {s.role_name: s for s in tab.findChildren(RoleSection)}
    assert set(sections) == {"Guitar", "Producer"}

    for section in sections.values():
        assert section.is_loaded  # data is still loaded eagerly...
        assert not section.table_container.isVisible()  # ...but the table starts hidden
        assert section.toggle_btn.text() == "▶"

    tab.deleteLater()


def test_role_tables_show_every_row_once_expanded(qapp, controller):
    artist = _artist_with_credits(controller.get.session, n_track_credits=6)
    tab = ArtistDetailTab(artist, controller)
    tab.resize(1000, 900)
    tab.show()
    qapp.processEvents()
    qapp.processEvents()

    sections = {s.role_name: s for s in tab.findChildren(RoleSection)}
    assert set(sections) == {"Guitar", "Producer"}

    for section in sections.values():
        section.toggle_section()
        qapp.processEvents()

        assert section.table_container.isVisible()
        assert section.toggle_btn.text() == "▼"
        table = section.table_widget
        assert table is not None
        # No internal vertical scroll: the last row is fully within the viewport.
        last = table.rowCount() - 1
        bottom = table.rowViewportPosition(last) + table.rowHeight(last)
        assert bottom <= table.viewport().height() + 1
        assert not table.verticalScrollBar().isVisible()

        # Toggling again collapses it.
        section.toggle_section()
        assert not section.table_container.isVisible()
        assert section.toggle_btn.text() == "▶"

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


def test_building_detail_tab_never_shows_a_top_level_window(qapp, controller):
    """Credits and Awards used to call setVisible(True) on themselves before
    ArtistDetailTab parented them, briefly mapping a throwaway top-level
    window. On Wayland that requests an xdg-activation token which is never
    used, so GNOME kept a busy cursor up after every artist click."""
    session = controller.get.session
    artist = _artist_with_credits(session)
    award = Award(award_name="Test Award", award_year=1972)
    session.add(award)
    session.flush()
    session.add(AwardAssociation(award_id=award.award_id, entity_id=artist.artist_id, entity_type="Artist"))
    session.commit()

    shown = []

    class _Spy(QWidget):
        def eventFilter(self, obj, event):
            if event.type() == event.Type.Show and isinstance(obj, QWidget) and obj.isWindow():
                shown.append(type(obj).__name__)
            return False

    spy = _Spy()
    QApplication.instance().installEventFilter(spy)
    try:
        tab = ArtistDetailTab(artist, controller)
    finally:
        QApplication.instance().removeEventFilter(spy)

    assert shown == []
    # Both sections still made it into the article.
    assert tab.findChild(CreditsWidget) is not None
    assert not tab.findChild(CreditsWidget).isHidden()
    assert not tab.findChild(AwardsWidget).isHidden()
    tab.deleteLater()
