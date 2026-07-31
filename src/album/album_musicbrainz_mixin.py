from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QLineEdit,
    QMessageBox,
    QSpinBox,
)
from sqlalchemy.exc import SQLAlchemyError

from src.album.album_musicbrainz_review_dialog import AlbumMusicBrainzReviewDialog
from src.album.release_type_utils import normalize_release_type
from src.common.nullable_spinbox import NullableSpinBox
from src.core.logger_config import logger
from src.musicbrainz.musicbrainz_client import (
    MusicBrainzLookupError,
    fetch_release_detail,
    fetch_release_group_aliases,
    search_canonical_releases,
)
from src.musicbrainz.musicbrainz_match_dialog import (
    MusicBrainzImportDialog,
    MusicBrainzMatchDialog,
)

# Album-level scalar fields filled onto the open editor's widgets via
# _apply_musicbrainz_enrichment -- fill-blank only, except release_year/
# release_month/release_day, which are overwritten unconditionally (see that
# method's docstring). Everything else a release carries (credits, recording
# locations, discs, track numbers, aliases, barcode, Discogs link) is
# relational/track-level and applied directly to the database by
# AlbumMusicBrainzReviewDialog instead, since there's no corresponding open
# form widget for most of it.
#
# MBID is a partial exception: it does have an open form widget, but is
# written straight to the DB in _apply_release_detail() below rather than
# going through _apply_musicbrainz_enrichment -- so that write must also
# push the value into field_widgets["MBID"] itself, or the next Save (which
# diffs widget text against self.album) would see the still-blank widget as
# a deliberate clear and null the MBID back out.
#
# None of this fires until the user has actually confirmed the match: see
# _apply_release_detail() below, which only reaches these writes after
# AlbumMusicBrainzReviewDialog has been accepted (or found nothing to
# review at all). Cancelling that dialog must leave zero trace, DB or
# widget -- that's the whole point of it being a "review" step.
_SCALAR_ENRICHMENT_FIELDS = (
    "status",
    "release_type",
    "album_language",
    "catalog_number",
    "release_year",
    "release_month",
    "release_day",
)


class AlbumMusicBrainzMixin:
    """
    MusicBrainz release lookup and enrichment for AlbumEditor.

    Expects the host class to provide: self.controller, self.album,
    self.field_widgets, self.refresh_view(), and to be a QWidget
    subclass.

    Three steps:
      1. Search the `release` endpoint (not release-group) and let the user
         confirm/override the auto-ranked canonical pick.
      2. Fetch full per-release detail (credits, recording locations, disc
         layout, aliases) for that one release.
      3. Hand everything to AlbumMusicBrainzReviewDialog for track matching
         and a final review/confirm step; only once that's accepted (or
         found nothing worth reviewing) do the album-level scalar widgets
         get filled and the MBID/Discogs links written. Cancelling at any
         step, including that final review, leaves the album untouched.
    """

    def _lookup_musicbrainz(self):
        title_widget = self.field_widgets.get("album_name")
        album_name = (
            title_widget.text().strip()
            if isinstance(title_widget, QLineEdit)
            else (self.album.album_name or "")
        ).strip()
        if not album_name:
            QMessageBox.warning(
                self, "MusicBrainz Lookup", "Enter an album title before looking it up."
            )
            return

        artist_names = getattr(self.album, "album_artist_names", None)
        if artist_names in (None, "Unknown Artist"):
            artist_names = None

        dialog = MusicBrainzMatchDialog(
            entity_label=f"album '{album_name}'",
            search_call=lambda: search_canonical_releases(album_name, artist_names),
            parent=self,
        )
        if dialog.exec() != QDialog.Accepted:
            return

        picked = dialog.result_candidate()
        if picked is None:
            return
        self._fetch_release_and_review(picked.id, album_name)

    def _fetch_release_and_review(self, release_mbid: str, album_name: str):
        def _fetch_all(progress):
            detail = fetch_release_detail(release_mbid, progress_callback=progress)
            aliases = []
            if detail.release_group_mbid:
                try:
                    aliases = fetch_release_group_aliases(detail.release_group_mbid)
                except MusicBrainzLookupError as e:
                    logger.warning(
                        f"Could not fetch album aliases for {album_name}: {e}"
                    )
            return detail, aliases

        dialog = MusicBrainzImportDialog(
            entity_label=f"release details for '{album_name}'",
            fetch_call=_fetch_all,
            supports_progress=True,
            parent=self,
        )
        if dialog.exec() != QDialog.Accepted:
            return

        result = dialog.result_candidate()
        if result is None:
            return
        detail, aliases = result
        self._apply_release_detail(detail, aliases)

    def _apply_release_detail(self, detail, aliases):
        # Build the review dialog first -- construction is pure computation
        # (track matching, deciding what's worth showing), no DB writes --
        # so has_content is known before anything commits. Cancelling here
        # must leave the album completely untouched, so every write below
        # is gated on either having nothing to review, or the user
        # explicitly accepting.
        review = AlbumMusicBrainzReviewDialog(
            self.controller, self.album, detail, aliases, parent=self
        )
        if review.has_content:
            if review.exec() != QDialog.Accepted:
                return
        else:
            review.apply_immediate_scalars()

        scalar_enrichment = {}
        if detail.status:
            scalar_enrichment["status"] = detail.status
        if detail.release_type:
            scalar_enrichment["release_type"] = normalize_release_type(
                detail.release_type
            )
        if detail.language:
            scalar_enrichment["album_language"] = detail.language
        if detail.catalog_number:
            scalar_enrichment["catalog_number"] = detail.catalog_number
        if detail.release_year:
            scalar_enrichment["release_year"] = detail.release_year
        if detail.release_month:
            scalar_enrichment["release_month"] = detail.release_month
        if detail.release_day:
            scalar_enrichment["release_day"] = detail.release_day
        if scalar_enrichment:
            self._apply_musicbrainz_enrichment(scalar_enrichment)

        if detail.mbid and not self.album.MBID:
            try:
                self.controller.update.update_entity(
                    "Album",
                    self.album.album_id,
                    MBID=detail.mbid,
                )
                # Keep the open editor's widget in sync with the DB write above --
                # otherwise the widget still reads blank, and the next Save (which
                # diffs widget text against self.album) would send MBID=None and
                # wipe out the value we just wrote.
                widget = self.field_widgets.get("MBID")
                if isinstance(widget, QLineEdit):
                    widget.setText(detail.mbid)
            except SQLAlchemyError as e:
                logger.warning(f"Could not save MusicBrainz release ID: {e}")

        if detail.discogs_master_url and not self.album.discogs_master_url:
            try:
                self.controller.update.update_entity(
                    "Album",
                    self.album.album_id,
                    discogs_master_url=detail.discogs_master_url,
                )
            except SQLAlchemyError as e:
                logger.warning(f"Could not save Discogs master link: {e}")

        self.refresh_view()

    def _apply_musicbrainz_enrichment(self, enrichment: dict):
        """Fill field widgets from a MusicBrainz enrichment dict.

        Most fields are fill-blank only -- applied where the widget is still
        at its blank/default state, never overwriting something the user
        already filled in or typed moments ago.

        NullableSpinBox fields (release_year/month/day) are the exception:
        they're written unconditionally, overwriting an existing value.
        Unlike the other enrichment fields, a wrong local release date is a
        common case (bad file tags, manual entry error), and by the time
        this runs the user has already explicitly confirmed the MB release
        match via the review dialog -- so MB's date should win over
        whatever's already in the field rather than only filling it in when
        blank.

        QCheckBox fields (is_live/is_compilation) have no blank state at
        all, so they fall back to the originally-loaded album's value being
        None, combined with the widget still being unchecked -- applied
        only when both hold, so a deliberate manual uncheck just before the
        lookup is never clobbered.

        QComboBox fields (status) are "blank" at their empty first entry --
        applied by selecting a matching existing item, or setting the edit
        text directly if the value isn't one of the preset choices.
        """
        for field_name, value in enrichment.items():
            widget = self.field_widgets.get(field_name)
            if widget is None:
                continue
            if isinstance(widget, QComboBox):
                if not widget.currentText().strip():
                    idx = widget.findText(str(value))
                    if idx >= 0:
                        widget.setCurrentIndex(idx)
                    else:
                        widget.setEditText(str(value))
            elif isinstance(widget, QLineEdit):
                if not widget.text().strip():
                    widget.setText(str(value))
            elif isinstance(widget, NullableSpinBox):
                widget.setValue(int(value))
            elif isinstance(widget, QSpinBox):
                if widget.value() == widget.minimum():
                    widget.setValue(int(value))
            elif isinstance(widget, QCheckBox) and (
                getattr(self.album, field_name, None) is None and not widget.isChecked()
            ):
                widget.setChecked(bool(value))
