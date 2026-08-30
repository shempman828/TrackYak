from PySide6.QtWidgets import QCheckBox, QComboBox, QDialog, QLineEdit, QMessageBox, QSpinBox
from sqlalchemy.exc import SQLAlchemyError

from src.album.album_musicbrainz_known_entities import known_place_mbids, known_publisher_mbids
from src.album.album_musicbrainz_review_dialog import AlbumMusicBrainzReviewDialog
from src.album.release_type_utils import normalize_release_type
from src.awards.award_series_import import fetch_award_series_relations, import_awards_for_entity
from src.common.nullable_numeric_field import nullable_field_value, set_nullable_field_value
from src.core.logger_config import logger
from src.musicbrainz.musicbrainz_core import MusicBrainzLookupError
from src.musicbrainz.musicbrainz_match_dialog import MusicBrainzImportDialog, MusicBrainzMatchDialog
from src.musicbrainz.musicbrainz_release import (
    fetch_release_detail,
    fetch_release_group_aliases,
    search_canonical_releases,
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
    "release_country",
    "media_format",
    "release_year",
    "release_month",
    "release_day",
)

# The release-date fields are nullable QLineEdits (see
# src/common/nullable_numeric_field.py) indistinguishable by widget type
# from any other text field, so the unconditional-overwrite exception below
# is keyed on field name instead.
_UNCONDITIONAL_OVERWRITE_FIELDS = frozenset({"release_year", "release_month", "release_day"})


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

        # Read only the live widget, never self.album -- self.album isn't
        # updated until Save, so falling back to it would resurrect the
        # very value the user just cleared from the field (#hint bug: a
        # stale 2015 remaster year kept being used as the hint even after
        # being cleared in favor of looking up the real 1961 release).
        year_widget = self.field_widgets.get("release_year")
        expected_year = (
            nullable_field_value(year_widget) if isinstance(year_widget, QLineEdit) else None
        )

        dialog = MusicBrainzMatchDialog(
            entity_label=f"album '{album_name}'",
            search_call=lambda: search_canonical_releases(
                album_name, artist_names, expected_year=expected_year
            ),
            parent=self,
        )
        if dialog.exec() != QDialog.Accepted:
            return

        picked = dialog.result_candidate()
        if picked is None:
            return
        self._fetch_release_and_review(picked.id, album_name)

    def _reimport_musicbrainz(self):
        """Re-fetch this album's already-matched release and check for updates.

        Reuses the same fetch-with-progress -> review flow as a fresh
        lookup, just seeded with the album's existing MBID instead of a
        search result. Unlike a fresh lookup, the user explicitly asked to
        check for changes here, so the "nothing to review" case gets a
        status message instead of silently doing nothing visible.
        """
        mbid = getattr(self.album, "MBID", None)
        if not mbid:
            return
        album_name = self.album.album_name or "this album"
        self._fetch_release_and_review(mbid, album_name, notify_if_no_changes=True)

    def _fetch_release_and_review(
        self, release_mbid: str, album_name: str, *, notify_if_no_changes: bool = False
    ):
        # Awards enrichment rides along only on a fresh match -- the apply
        # step below is gated on `not self.album.MBID` -- so skip its extra
        # network call entirely on a re-import, where the MBID is already
        # set. Captured here rather than read from self.album on the worker
        # thread; nothing writes MBID between now and _apply_release_detail().
        fetch_awards = not getattr(self.album, "MBID", None)

        def _fetch_all(progress):
            detail = fetch_release_detail(
                release_mbid,
                progress_callback=progress,
                known_label_mbids=known_publisher_mbids(self.controller),
                known_place_mbids=known_place_mbids(self.controller),
            )
            aliases = []
            if detail.release_group_mbid:
                try:
                    aliases = fetch_release_group_aliases(detail.release_group_mbid)
                except MusicBrainzLookupError as e:
                    logger.warning(f"Could not fetch album aliases for {album_name}: {e}")
            # Fetch the award series-rels here, on the worker thread:
            # musicbrainzngs retries a stuck request up to 8x at 30s each,
            # so doing this inline on the UI thread in _apply_release_detail()
            # froze the app for minutes with no progress or cancel.
            award_relations = None
            if fetch_awards and detail.release_group_mbid:
                award_relations = fetch_award_series_relations("Album", detail.release_group_mbid)
            return detail, aliases, award_relations

        dialog = MusicBrainzImportDialog(
            entity_label=f"release '{album_name}'",
            fetch_call=_fetch_all,
            supports_progress=True,
            parent=self,
        )
        if dialog.exec() != QDialog.Accepted:
            return

        result = dialog.result_candidate()
        if result is None:
            return
        detail, aliases, award_relations = result
        self._apply_release_detail(
            detail, aliases, award_relations, notify_if_no_changes=notify_if_no_changes
        )

    def _apply_release_detail(
        self, detail, aliases, award_relations, *, notify_if_no_changes: bool = False
    ):
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
            self._expire_musicbrainz_touched_tracks(review)
        else:
            review.apply_immediate_scalars()
            if notify_if_no_changes:
                QMessageBox.information(
                    self,
                    "MusicBrainz",
                    "This album's metadata is already up to date with MusicBrainz.",
                )

        scalar_enrichment = {}
        if detail.status:
            scalar_enrichment["status"] = detail.status
        if detail.release_type:
            scalar_enrichment["release_type"] = normalize_release_type(detail.release_type)
        if detail.language:
            scalar_enrichment["album_language"] = detail.language
        if detail.catalog_number:
            scalar_enrichment["catalog_number"] = detail.catalog_number
        if detail.release_country:
            scalar_enrichment["release_country"] = detail.release_country
        if detail.media_format:
            scalar_enrichment["media_format"] = detail.media_format
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
                update_kwargs = {"MBID": detail.mbid}
                if detail.release_group_mbid:
                    update_kwargs["release_group_MBID"] = detail.release_group_mbid
                self.controller.update.update_entity("Album", self.album.album_id, **update_kwargs)
                # Keep the open editor's widget in sync with the DB write above --
                # otherwise the widget still reads blank, and the next Save (which
                # diffs widget text against self.album) would send MBID=None and
                # wipe out the value we just wrote.
                widget = self.field_widgets.get("MBID")
                if isinstance(widget, QLineEdit):
                    widget.setText(detail.mbid)
                if detail.release_group_mbid:
                    import_awards_for_entity(
                        self.controller.get.session,
                        "Album",
                        self.album.album_id,
                        detail.release_group_mbid,
                        relations=award_relations,
                    )
            except SQLAlchemyError as e:
                logger.warning(f"Could not save MusicBrainz release ID: {e}")

        if detail.discogs_master_url and not self.album.discogs_master_url:
            try:
                self.controller.update.update_entity(
                    "Album", self.album.album_id, discogs_master_url=detail.discogs_master_url
                )
            except SQLAlchemyError as e:
                logger.warning(f"Could not save Discogs master link: {e}")

        self.refresh_view()

    def _expire_musicbrainz_touched_tracks(self, review: AlbumMusicBrainzReviewDialog):
        """review.has_content means the actual track scalar writes
        (track_number, side, disc_id, ...) happened in _ReviewAcceptWorker,
        which runs on its own QThread and therefore its own scoped_session
        Session (see that worker's docstring) -- a different Session object
        than this editor's. SQLAlchemy's same-session bulk-UPDATE-by-primary-
        key sync (relied on elsewhere, e.g. disc drag-and-drop) can't reach
        across that boundary, so the Track objects this editor already
        loaded (and the Tracks tab is about to redisplay) still hold their
        pre-import track_number/side/disc_id. Expire them here, in this
        editor's own session, so refresh_view() below rebuilds the Tracks
        tab from the values the worker actually wrote instead of silently
        redisplaying stale ones. The has_content=False path doesn't need
        this: apply_immediate_scalars() runs on this same thread/session, so
        the bulk-UPDATE sync already applies."""
        session = self.controller.get.session
        for track in review._matched.values():
            session.expire(track)
        for combo, _mbt in review._manual_combos:
            track = combo.currentData()
            if track is not None:
                session.expire(track)

    def _apply_musicbrainz_enrichment(self, enrichment: dict):
        """Fill field widgets from a MusicBrainz enrichment dict.

        Most fields are fill-blank only -- applied where the widget is still
        at its blank/default state, never overwriting something the user
        already filled in or typed moments ago.

        release_year/month/day (nullable QLineEdit fields, see
        _UNCONDITIONAL_OVERWRITE_FIELDS) are the exception: they're written
        unconditionally, overwriting an existing value. Unlike the other
        enrichment fields, a wrong local release date is a common case (bad
        file tags, manual entry error), and by the time this runs the user
        has already explicitly confirmed the MB release match via the
        review dialog -- so MB's date should win over whatever's already in
        the field rather than only filling it in when blank.

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
            if field_name in _UNCONDITIONAL_OVERWRITE_FIELDS:
                set_nullable_field_value(widget, int(value))
            elif isinstance(widget, QComboBox):
                if not widget.currentText().strip():
                    idx = widget.findText(str(value))
                    if idx >= 0:
                        widget.setCurrentIndex(idx)
                    else:
                        widget.setEditText(str(value))
            elif isinstance(widget, QLineEdit):
                if not widget.text().strip():
                    widget.setText(str(value))
            elif isinstance(widget, QSpinBox):
                if widget.value() == widget.minimum():
                    widget.setValue(int(value))
            elif isinstance(widget, QCheckBox) and (
                getattr(self.album, field_name, None) is None and not widget.isChecked()
            ):
                widget.setChecked(bool(value))
