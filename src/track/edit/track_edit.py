# track_edit.py
"""Track editing dialog."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QButtonGroup, QDialog, QFrame, QHBoxLayout, QLabel, QMessageBox, QPushButton, QScrollArea, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget
from sqlalchemy.exc import SQLAlchemyError

from src.foundation.logger_config import logger
from src.image.artwork_cache import get_artwork_cache
from src.track.edit.track_edit_advanced import AdvancedTab
from src.track.edit.track_edit_album import AlbumsTab
from src.track.edit.track_edit_awards import AwardsTab
from src.track.edit.track_edit_basetab import _BaseTab
from src.track.edit.track_edit_classical import ClassicalTab
from src.track.edit.track_edit_fieldform import FieldFormTab
from src.track.edit.track_edit_genres import GenresTab
from src.track.edit.track_edit_identity import IdentificationTab
from src.track.edit.track_edit_lyrics import LyricsTab
from src.track.edit.track_edit_moods import MoodsTab
from src.track.edit.track_edit_places import PlacesTab
from src.track.edit.track_edit_roles import RolesTab
from src.track.edit.track_edit_samples import SamplesTab
from src.track.edit.track_edit_usedin import UsedInTab

_COVER_SIZE = 76


def _format_duration(seconds) -> str:
    """m:ss below an hour, h:mm:ss above."""
    total = int(seconds or 0)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _safe_attr(obj, name: str, default=None):
    """Read a track attribute that may be an association proxy onto a
    missing relationship (e.g. album_name with no album) without letting
    the header take the whole dialog down."""
    try:
        return getattr(obj, name, default)
    except (AttributeError, SQLAlchemyError) as e:
        logger.debug(f"Header could not read {name}: {e}")
        return default


# ---------------------------------------------------------------------------
# Header — who is being edited
# ---------------------------------------------------------------------------


class _EditHeader(QFrame):
    """Cover, title, "artist · album · year" byline and format badges for
    the track(s) being edited, pinned above every tab so the user always
    knows what they're changing. In multi-track mode it names the batch and
    states once that edits apply to all of them."""

    def __init__(self, tracks: list, parent=None):
        super().__init__(parent)
        self.setObjectName("TrackEditHeader")
        self._tracks = tracks
        is_multi = len(tracks) > 1

        row = QHBoxLayout(self)
        row.setContentsMargins(14, 12, 14, 12)
        row.setSpacing(14)

        self._cover = QLabel()
        self._cover.setObjectName("TrackEditCover")
        self._cover.setFixedSize(_COVER_SIZE, _COVER_SIZE)
        self._cover.setAlignment(Qt.AlignCenter)
        self._load_cover()
        row.addWidget(self._cover, alignment=Qt.AlignTop)

        text_col = QVBoxLayout()
        text_col.setSpacing(4)

        self._title = QLabel()
        self._title.setObjectName("TrackEditTitle")
        self._title.setTextInteractionFlags(Qt.TextSelectableByMouse)
        text_col.addWidget(self._title)

        self._byline = QLabel()
        self._byline.setObjectName("TrackEditByline")
        self._byline.setTextInteractionFlags(Qt.TextSelectableByMouse)
        text_col.addWidget(self._byline)

        badges = QHBoxLayout()
        badges.setSpacing(6)
        for text, state in self._badges():
            badge = QLabel(text)
            badge.setProperty("badgeState", state)
            badges.addWidget(badge)
        badges.addStretch()
        text_col.addLayout(badges)
        text_col.addStretch()

        row.addLayout(text_col, 1)

        if is_multi:
            self._title.setText(f"Editing {len(tracks)} tracks")
            names = [str(_safe_attr(t, "track_name") or "Untitled") for t in tracks[:3]]
            more = f" and {len(tracks) - 3} more" if len(tracks) > 3 else ""
            self._byline.setText(", ".join(names) + more)
            self._byline.setToolTip("\n".join(str(_safe_attr(t, "track_name") or "Untitled") for t in tracks[:40]))
        else:
            self.set_title(_safe_attr(tracks[0], "track_name") or "")
            parts = [_safe_attr(tracks[0], "primary_artist_names"), _safe_attr(tracks[0], "album_name"), _safe_attr(tracks[0], "release_year")]
            self._byline.setText("  ·  ".join(str(p) for p in parts if p not in (None, "", "Unknown Artist")))

    def set_title(self, text: str) -> None:
        """Mirror the Basic tab's (possibly unsaved) title as the user types."""
        self._title.setText(text.strip() or "Untitled track")

    def _badges(self) -> list[tuple[str, str]]:
        tracks = self._tracks
        if len(tracks) > 1:
            total = sum(float(_safe_attr(t, "duration") or 0) for t in tracks)
            out = [(f"{len(tracks)} TRACKS", "neutral"), (_format_duration(total), "neutral")]
            out.append(("CHANGES APPLY TO ALL", "warn"))
            return out
        track = tracks[0]
        out = []
        ext = _safe_attr(track, "file_extension")
        if ext:
            out.append((str(ext).lstrip(".").upper(), "neutral"))
        duration = _safe_attr(track, "duration")
        if duration:
            out.append((_format_duration(duration), "neutral"))
        if _safe_attr(track, "is_explicit"):
            out.append(("EXPLICIT", "warn"))
        return out

    def _load_cover(self) -> None:
        """Front cover of the track's album -- or, for a batch, of the album
        they all share. Falls back to a note glyph."""
        albums = {_safe_attr(t, "album_id") for t in self._tracks}
        album = _safe_attr(self._tracks[0], "album") if len(albums) == 1 else None
        cache = get_artwork_cache()
        if album is not None and cache is not None:
            try:
                # Imported lazily: src.album.edit pulls in track edit tabs.
                from src.album.edit.base_album_widget import rounded_pixmap

                px = cache.get_pixmap(album, "front", bool(_safe_attr(album, "art_is_explicit", False)))
                if px and not px.isNull():
                    scaled = px.scaled(_COVER_SIZE, _COVER_SIZE, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
                    self._cover.setPixmap(rounded_pixmap(scaled))
                    return
            except (OSError, RuntimeError, SQLAlchemyError) as e:
                logger.debug(f"Track edit header: no cover ({e})")
        self._cover.setText("♫")
        self._cover.setProperty("coverPlaceholder", True)


# ---------------------------------------------------------------------------
# Sidebar — grouped tab navigation
# ---------------------------------------------------------------------------


class _EditNav(QScrollArea):
    """Tab navigation with group headings and a per-tab "unsaved" dot.

    Plain QPushButtons (not QListWidget rows) so every state -- hover,
    current, dirty -- is styled in dark_mode.qss, like the main NavTree.
    """

    current_changed = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("EditNav")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFixedWidth(184)

        content = QWidget()
        content.setObjectName("EditNavContent")
        self._layout = QVBoxLayout(content)
        self._layout.setContentsMargins(6, 4, 6, 8)
        self._layout.setSpacing(1)
        self._layout.addStretch()
        self.setWidget(content)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._group.idToggled.connect(lambda idx, on: on and self.current_changed.emit(idx))
        self._buttons: list[QPushButton] = []
        self._dots: list[QLabel] = []

    def add_group(self, title: str) -> None:
        heading = QLabel(title.upper())
        heading.setObjectName("EditNavGroup")
        self._layout.insertWidget(self._layout.count() - 1, heading)

    def add_item(self, label: str) -> int:
        index = len(self._buttons)
        btn = QPushButton(label.replace("&", "&&"))  # literal '&', not a mnemonic
        btn.setObjectName("EditNavItem")
        btn.setCheckable(True)
        btn.setFocusPolicy(Qt.TabFocus)
        btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        inner = QHBoxLayout(btn)
        inner.setContentsMargins(0, 0, 10, 0)
        inner.addStretch()
        dot = QLabel("●")
        dot.setObjectName("EditNavDot")
        dot.setToolTip("Unsaved changes")
        dot.hide()
        inner.addWidget(dot)
        self._group.addButton(btn, index)
        self._layout.insertWidget(self._layout.count() - 1, btn)
        self._buttons.append(btn)
        self._dots.append(dot)
        return index

    def count(self) -> int:
        return len(self._buttons)

    def set_current(self, index: int) -> None:
        if 0 <= index < len(self._buttons):
            self._buttons[index].setChecked(True)

    def current(self) -> int:
        return self._group.checkedId()

    def set_dirty(self, index: int, dirty: bool) -> None:
        self._dots[index].setVisible(dirty)


# ---------------------------------------------------------------------------
# TrackEditDialog — the main dialog
# ---------------------------------------------------------------------------


class TrackEditDialog(QDialog):
    """
    Edit one track — or bulk-edit many at once.

    Usage:
        # Single track
        dlg = TrackEditDialog(track, controller, parent)
        # Multiple tracks
        dlg = TrackEditDialog([t1, t2, t3], controller, parent)

    Two save models live side by side: scalar field tabs collect edits and
    write them on Save, while relationship tabs (genres, roles, places, …;
    `_BaseTab.saves_immediately`) write each add/remove at once. The footer
    counts the former; a notice on the latter says so.
    """

    field_modified = Signal()

    def __init__(self, track_or_tracks: Any | list, controller, parent=None):
        # Qt.Window makes this a proper independent top-level window
        # so it can be moved freely, separate from the parent window.
        super().__init__(parent, Qt.Window)
        # Dialog is shown non-modally (see callers' .show() usage) so it
        # doesn't block the parent window; clean up automatically on close
        # since there's no exec() return value to trigger disposal.
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setObjectName("TrackEditDialog")

        # Normalise to a list
        if isinstance(track_or_tracks, list):
            self.tracks = track_or_tracks
        else:
            self.tracks = [track_or_tracks]

        self.controller = controller
        self.is_multi = len(self.tracks) > 1

        # Convenience property
        self.track = self.tracks[0]

        # Set once the user has agreed to lose unsaved edits (or saved), so
        # reject() -> closeEvent() doesn't ask twice.
        self._close_approved = False
        self._pending_count = 0

        title = f"Edit {len(self.tracks)} Tracks" if self.is_multi else f"Edit Track: {self.track.track_name}"
        self.setWindowTitle(title)
        self.setMinimumSize(940, 680)

        # _build_ui() selects nav row 0, which builds and loads that tab via
        # _on_nav -> _ensure_tab_built. Every other tab builds and loads
        # itself lazily on first visit.
        self._build_ui()

    # ── UI Construction ───────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 10)
        root.setSpacing(10)

        self._header = _EditHeader(self.tracks)
        root.addWidget(self._header)

        body = QHBoxLayout()
        body.setSpacing(12)

        self._nav = _EditNav()
        self._nav.current_changed.connect(self._on_nav)
        body.addWidget(self._nav)

        right = QVBoxLayout()
        right.setSpacing(8)

        self._immediate_note = QLabel("Changes on this tab save immediately — Save and Cancel do not affect them.")
        self._immediate_note.setObjectName("ImmediateSaveNote")
        self._immediate_note.setWordWrap(True)
        self._immediate_note.hide()
        right.addWidget(self._immediate_note)

        self._stack = QStackedWidget()
        right.addWidget(self._stack, 1)
        body.addLayout(right, 1)
        root.addLayout(body, 1)

        root.addWidget(self._build_footer())

        # Tabs are built and loaded lazily, on first navigation to each one
        # (see _ensure_tab_built), rather than all 18 up front -- some tabs
        # (e.g. Samples, Roles) do real DB work in __init__/load(), which
        # used to make every dialog open pay for every tab regardless of
        # which ones the user actually visits. Only the nav button is cheap
        # to build eagerly, so that's all _add_tab does here.
        self._tab_factories: list = []
        self._tabs: list[_BaseTab | None] = []

        self._nav.add_group("Metadata")
        self._add_tab("Basic", lambda: FieldFormTab("Basic", self.tracks, self.controller))
        self._add_tab("Artists & Roles", lambda: RolesTab(self.tracks, self.controller))
        self._add_tab("Album", lambda: AlbumsTab(self.tracks, self.controller, dialog=self))
        self._add_tab("Dates", lambda: FieldFormTab("Date", self.tracks, self.controller))
        self._add_tab("Genres", lambda: GenresTab(self.tracks, self.controller))
        self._add_tab("Moods", lambda: MoodsTab(self.tracks, self.controller))

        self._nav.add_group("Content")
        self._add_tab("Description", lambda: FieldFormTab("Description", self.tracks, self.controller))
        self._add_tab("Lyrics", lambda: LyricsTab(self.tracks, self.controller))
        self._add_tab("Classical", lambda: ClassicalTab(self.tracks, self.controller, dialog=self))

        self._nav.add_group("Context")
        self._add_tab("Places", lambda: PlacesTab(self.tracks, self.controller))
        self._add_tab("Awards", lambda: AwardsTab(self.tracks, self.controller))
        self._add_tab("Used In", lambda: UsedInTab(self.tracks, self.controller))
        self._add_tab("Samples", lambda: SamplesTab(self.tracks, self.controller))

        self._nav.add_group("Library")
        self._add_tab("Properties", lambda: FieldFormTab("Properties", self.tracks, self.controller))
        self._add_tab("Identification", lambda: IdentificationTab(self.tracks, self.controller, dialog=self))
        self._add_tab("Aliases", lambda: FieldFormTab("Alias", self.tracks, self.controller))
        self._add_tab("User Data", lambda: FieldFormTab("User", self.tracks, self.controller))
        self._add_tab("Advanced", self._make_advanced_tab)

        # Keyboard shortcuts Ctrl+1 … Ctrl+9 for first 9 tabs
        for i in range(min(9, len(self._tabs))):
            sc = QShortcut(QKeySequence(f"Ctrl+{i + 1}"), self)
            sc.activated.connect(lambda idx=i: self._nav.set_current(idx))

        # Coalesces the burst of `changed` signals one keystroke can cause.
        self._dirty_timer = QTimer(self)
        self._dirty_timer.setSingleShot(True)
        self._dirty_timer.setInterval(0)
        self._dirty_timer.timeout.connect(self._refresh_dirty_state)

        self._nav.set_current(0)
        self._refresh_dirty_state()

    def _build_footer(self) -> QWidget:
        footer = QFrame()
        footer.setObjectName("EditFooter")
        row = QHBoxLayout(footer)
        row.setContentsMargins(4, 8, 0, 0)
        row.setSpacing(8)

        self._footer_status = QLabel()
        self._footer_status.setProperty("textRole", "muted")
        row.addWidget(self._footer_status)

        # Save failures show here, in place, instead of in a modal box.
        self._error_label = QLabel()
        self._error_label.setObjectName("EditErrorBanner")
        self._error_label.setWordWrap(True)
        self._error_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._error_label.hide()
        row.addWidget(self._error_label, 1)
        row.addStretch()

        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setAutoDefault(False)
        self._cancel_btn.clicked.connect(self.reject)
        row.addWidget(self._cancel_btn)

        self._save_btn = QPushButton("Save")
        self._save_btn.setObjectName("PrimaryButton")
        self._save_btn.setDefault(True)
        self._save_btn.setMinimumWidth(96)
        self._save_btn.clicked.connect(self._on_save)
        row.addWidget(self._save_btn)
        return footer

    def get_live_track_name(self) -> str:
        """Track title as currently typed in the Basic tab, if it's been
        built, else falls back to the last-saved value. Basic is always
        tab 0 and is built eagerly on dialog open (see _build_ui), so the
        live value is normally available -- used by tabs whose MusicBrainz
        searches (e.g. Albums' "Find Canonical Album") must reflect an
        unsaved title edit rather than the stale value on self.track."""
        basic_tab = self._tabs[0]
        if isinstance(basic_tab, FieldFormTab):
            value = basic_tab.get_field_value("track_name")
            if value:
                return value
        return self.track.track_name

    def set_live_track_name(self, value: str) -> bool:
        """Write `value` into the Basic tab's track-title field and mark it
        dirty, so it's saved along with everything else on Save. Basic is
        tab 0 and built eagerly on open, so this is always available.
        Mirrors get_live_track_name(); used by the Classical tab's
        parse-from-title action."""
        basic_tab = self._tabs[0]
        if isinstance(basic_tab, FieldFormTab):
            return basic_tab.set_field_value("track_name", value)
        return False

    def _make_advanced_tab(self):
        advanced_tab = AdvancedTab(self.tracks, self.controller, dialog=self)
        advanced_tab.tracks_analyzed.connect(self._on_tracks_analyzed)
        return advanced_tab

    def _add_tab(self, label: str, factory):
        self._nav.add_item(label)
        self._stack.addWidget(QWidget())  # placeholder, replaced on first visit
        self._tab_factories.append(factory)
        self._tabs.append(None)

    def _on_nav(self, row: int):
        tab = self._ensure_tab_built(row)
        self._stack.setCurrentIndex(row)
        self._immediate_note.setVisible(bool(getattr(tab, "saves_immediately", False)))

    def _ensure_tab_built(self, row: int) -> _BaseTab:
        tab = self._tabs[row]
        if tab is not None:
            return tab

        factory = self._tab_factories[row]
        try:
            tab = factory()
            tab.load(self.tracks)
        except Exception as e:
            # Intentional broad boundary catch: dispatches to 18 heterogeneous
            # tab classes (DB reads, dict/attr access, UI construction) via the
            # shared _BaseTab interface -- a bug in any one tab must not block
            # the whole edit dialog from opening.
            logger.error(f"Error building/loading tab at row {row}: {e}", exc_info=True)
            tab = QWidget()

        placeholder = self._stack.widget(row)
        self._stack.insertWidget(row, tab)
        self._stack.removeWidget(placeholder)
        placeholder.deleteLater()
        self._tabs[row] = tab

        if isinstance(tab, _BaseTab):
            tab.changed.connect(self._dirty_timer.start)
        if row == 0 and isinstance(tab, FieldFormTab):
            title_widget = tab._widgets.get("track_name")
            if title_widget is not None:
                title_widget.textChanged.connect(self._header.set_title)
        return tab

    # ── Unsaved-change tracking ───────────────────────────────────────────

    def _refresh_dirty_state(self) -> None:
        """Recount pending (Save-bound) edits: per-tab dots, footer text,
        and the Save button's label."""
        total = 0
        for row, tab in enumerate(self._tabs):
            pending: set = set()
            if isinstance(tab, _BaseTab):
                try:
                    pending = tab.pending_changes()
                except Exception as e:
                    # Intentional broad boundary catch: pending_changes reads
                    # live widget state on 18 heterogeneous tabs -- one bad
                    # tab must not freeze the footer for the rest.
                    logger.exception(f"Error reading pending changes from {type(tab).__name__}: {e}")
            self._nav.set_dirty(row, bool(pending))
            total += len(pending)
        self._pending_count = total

        if total:
            self._footer_status.setText(f"{total} unsaved change{'s' if total != 1 else ''}")
            self._save_btn.setText("Save")
        else:
            self._footer_status.setText("No unsaved changes")
            # Nothing to write, but still a deliberate "close and refresh the
            # list" -- relationship tabs may have written already.
            self._save_btn.setText("Done")
        self._error_label.hide()

    def _confirm_discard(self) -> bool:
        """True if the dialog may close: nothing pending, already approved,
        or the user agrees to discard."""
        if self._close_approved:
            return True
        self._refresh_dirty_state()
        if not self._pending_count:
            return True
        n = self._pending_count
        answer = QMessageBox.question(
            self, "Discard changes?", f"You have {n} unsaved change{'s' if n != 1 else ''}. Discard {'them' if n != 1 else 'it'}?", QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Cancel
        )
        self._close_approved = answer == QMessageBox.Discard
        return self._close_approved

    def done(self, result: int) -> None:
        if result == QDialog.Accepted:
            self._close_approved = True
        elif not self._confirm_discard():
            return
        super().done(result)

    # ── Data ──────────────────────────────────────────────────────────────

    def _on_tracks_analyzed(self):
        """Audio analysis updated the track(s) in place (e.g. bpm, key,
        gain) — refresh every already-built tab's displayed values. This
        leaves any unsaved edits the user already made untouched. Tabs the
        user hasn't visited yet pick up the fresh values naturally when
        they're built."""
        for tab in self._tabs:
            if tab is None:
                continue
            try:
                tab.refresh_values(self.tracks)
            except Exception as e:
                # Intentional broad boundary catch: refresh_values is
                # overridden differently by each of the 18 tab classes -- a
                # bug in one tab's refresh must not stop the rest from
                # picking up the new analysis values.
                logger.error(f"Error refreshing tab {type(tab).__name__}: {e}", exc_info=True)

    # ── Save ──────────────────────────────────────────────────────────────

    def _on_save(self):
        try:
            # Collect scalar field changes from all tabs
            all_changes: dict[str, Any] = {}
            for tab in self._tabs:
                if tab is None:
                    continue  # never visited -> no user edits to collect
                try:
                    changes = tab.collect_changes()
                    all_changes.update(changes)
                except Exception as e:
                    # Intentional broad boundary catch: collect_changes is
                    # overridden differently by each of the 18 tab classes --
                    # a bug in one tab's collection must not prevent saving
                    # the changes already gathered from the others.
                    logger.exception(f"Error collecting changes from {type(tab).__name__}: {e}")

            if all_changes:
                track_ids = [track.track_id for track in self.tracks]
                self.controller.update.update_entities("Track", track_ids, **all_changes)
                logger.info(f"Saved {len(self.tracks)} track(s), fields: {list(all_changes.keys())}")

            self.field_modified.emit()
            self.accept()

        except (SQLAlchemyError, RuntimeError) as e:
            logger.error(f"Error saving track(s): {e}", exc_info=True)
            self._footer_status.setText("")
            self._error_label.setText(f"Could not save: {e}")
            self._error_label.show()

    # ── Cleanup ───────────────────────────────────────────────────────────

    def closeEvent(self, event) -> None:
        if not self._confirm_discard():
            event.ignore()
            return
        for tab in self._tabs:
            if tab is None:
                continue  # never built -> nothing to clean up
            try:
                tab.cleanup()
            except Exception as e:
                # Intentional broad boundary catch: shutdown/cleanup code for
                # 18 heterogeneous tab classes (some tear down background
                # QThreads) -- one tab failing to clean up must not stop the
                # rest from releasing their resources while the dialog closes.
                logger.exception(f"Error cleaning up tab {type(tab).__name__}: {e}")
        super().closeEvent(event)


# ---------------------------------------------------------------------------
# Backwards-compatibility alias so existing callers don't need changes
# ---------------------------------------------------------------------------

# Any code that imported MultiTrackEditDialog can now pass a list to
# TrackEditDialog instead. We keep the name around to avoid import errors.
MultiTrackEditDialog = TrackEditDialog
