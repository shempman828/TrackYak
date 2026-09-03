# ──────────────────────────────────────────────────────────────────────────────
#  About panel
# ──────────────────────────────────────────────────────────────────────────────

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QLabel, QScrollArea, QSizePolicy, QVBoxLayout, QWidget
from sqlalchemy.exc import SQLAlchemyError

from src.common.layout_utils import clear_layout
from src.foundation.censor import censor_text
from src.foundation.logger_config import logger


class _AboutPanel(QWidget):
    """Read-only description/bio prose for the entities tied to the current track.

    Surfaces existing text columns only — ``Track.track_description``,
    ``Album.album_description``, ``Artist.biography``, ``Publisher.description``,
    ``Genre.description``, ``Mood.mood_description`` — one card per non-blank
    value, in a plain (non-animated) scroll area. Order is fixed:
    Track, Album, Artist(s), Label(s), Genre(s), Mood(s).
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty("bgTransparent", True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._area = QScrollArea()
        self._area.setFrameShape(QFrame.NoFrame)
        self._area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._area.setWidgetResizable(True)

        self._container = QWidget()
        self._container.setProperty("bgTransparent", True)
        self._cards_layout = QVBoxLayout(self._container)
        self._cards_layout.setContentsMargins(0, 8, 0, 32)
        self._cards_layout.setSpacing(8)
        self._cards_layout.setAlignment(Qt.AlignTop)
        self._area.setWidget(self._container)

        root.addWidget(self._area)

    # ── public API ────────────────────────────────────────────────────────

    def load_about(self, track):
        """Rebuild the card list for ``track`` (or show a placeholder)."""
        clear_layout(self._cards_layout)
        self._area.verticalScrollBar().setValue(0)

        if not track:
            self._show_placeholder("No track loaded")
            return

        try:
            entries = self._collect(track)
        except SQLAlchemyError as exc:
            # ORM access spans album/artist/publisher/genre/mood relationships;
            # one broken lazy-load must not blank the whole tab.
            logger.warning(f"_AboutPanel: error reading track descriptions: {exc}")
            entries = []

        if not entries:
            self._show_placeholder("No descriptions available for this track")
            return

        for kind, name, body in entries:
            self._cards_layout.addWidget(self._make_card(kind, name, body))

    # ── internals ─────────────────────────────────────────────────────────

    def _collect(self, track) -> list[tuple[str, str, str]]:
        """Ordered ``(kind, name, body)`` for every related entity carrying a
        non-blank description."""
        out: list[tuple[str, str, str]] = []

        def _add(kind: str, name, text) -> None:
            if text and str(text).strip():
                out.append((kind, censor_text(name or "") or "—", str(text).strip()))

        _add("TRACK", getattr(track, "track_name", None), getattr(track, "track_description", None))

        album = getattr(track, "album", None)
        if album is not None:
            _add(
                "ALBUM",
                getattr(album, "album_name", None),
                getattr(album, "album_description", None),
            )

        for artist in self._ordered_artists(track):
            _add("ARTIST", getattr(artist, "artist_name", None), getattr(artist, "biography", None))

        if album is not None:
            for pub in getattr(album, "publishers", None) or []:
                _add(
                    "LABEL", getattr(pub, "publisher_name", None), getattr(pub, "description", None)
                )

        for genre in getattr(track, "genres", None) or []:
            _add("GENRE", getattr(genre, "genre_name", None), getattr(genre, "description", None))

        for mood in getattr(track, "moods", None) or []:
            _add("MOOD", getattr(mood, "mood_name", None), getattr(mood, "mood_description", None))

        return out

    @staticmethod
    def _ordered_artists(track) -> list:
        """Credited artists, primary first, de-duped by ``artist_id`` (falling
        back to object identity for transient/unsaved rows)."""
        seen: set = set()
        ordered: list = []
        primary = list(getattr(track, "primary_artists", None) or [])
        for artist in (*primary, *(getattr(track, "artists", None) or [])):
            key = getattr(artist, "artist_id", None)
            if key is None:
                key = id(artist)
            if key in seen:
                continue
            seen.add(key)
            ordered.append(artist)
        return ordered

    def _show_placeholder(self, text: str) -> None:
        lbl = QLabel(text)
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setWordWrap(True)
        lbl.setProperty("npRole", "aboutPlaceholder")
        self._cards_layout.addWidget(lbl)

    @staticmethod
    def _make_card(kind: str, name: str, body: str) -> QWidget:
        card = QWidget()
        card.setProperty("npAboutCard", True)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 10, 14, 12)
        lay.setSpacing(3)

        name_lbl = QLabel(name or "—")
        name_lbl.setProperty("npRole", "aboutHeading")
        name_lbl.setWordWrap(True)

        kind_lbl = QLabel(kind)
        kind_lbl.setProperty("npRole", "aboutKind")

        body_lbl = QLabel(body)
        body_lbl.setProperty("npRole", "aboutBody")
        body_lbl.setWordWrap(True)
        body_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)

        lay.addWidget(name_lbl)
        lay.addWidget(kind_lbl)
        lay.addWidget(body_lbl)
        return card
