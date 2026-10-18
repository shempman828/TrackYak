"""CanonicalAlbumResolver: map a MusicBrainz canonical-album match onto local Album/Artist rows.

Holds no UI. The caller passes in the confirm and warn callbacks, so the
album tab owns every dialog and this module stays Qt-free and testable.
"""

from __future__ import annotations

from collections.abc import Callable

from src.foundation.logger_config import logger

# (entity_type, entity_id, mbid) -- awards enrichment the caller runs later on a worker.
AwardJob = tuple[str, int, str]


class CanonicalAlbumResolver:
    """Resolve or create the local album (and its artists) for a MusicBrainz match."""

    def __init__(self, controller, confirm_create: Callable[[str], bool], warn: Callable[[str, str], None]):
        """confirm_create(details) asks to create a new album; warn(title, text) reports a failure."""
        self.controller = controller
        self._confirm_create = confirm_create
        self._warn = warn

    def resolve_or_create_album(self, enrichment: dict, award_jobs: list[AwardJob]):
        """Return the local album for a MusicBrainz match: by MBID, by name and artist overlap, or new."""
        mbid = enrichment.get("MBID")
        if mbid:
            album = self.controller.get.get_entity_object("Album", MBID=mbid)
            if album is not None:
                return album

        album_name = enrichment.get("album_name")
        artist_credits = enrichment.get("artist_credits") or []
        if album_name:
            candidate_album = self.controller.get.resolve_entity_or_alias("Album", "album_name", album_name)
            if candidate_album is not None:
                # Overlap, not exact-set-equality (unlike get_album_exists) --
                # this is matching one recording's credit against a possibly
                # larger album artist roster, not deduping a full import.
                existing_ids = {a.MBID for a in candidate_album.album_artists if a.MBID}
                credit_ids = {c["mbid"] for c in artist_credits if c.get("mbid")}
                if existing_ids & credit_ids:
                    return candidate_album

        return self.create_album(enrichment, award_jobs)

    def create_album(self, enrichment: dict, award_jobs: list[AwardJob]):
        """Create a new album and its artists from a MusicBrainz match, after confirmation."""
        album_name = enrichment.get("album_name") or "Unknown Album"
        artist_credits = enrichment.get("artist_credits") or []

        if not self._confirm_create(_album_details(enrichment, album_name, artist_credits)):
            return None

        # add_entity catches its own DB errors and returns None.
        new_album = self.controller.add.add_entity(
            "Album",
            album_name=album_name,
            release_year=enrichment.get("release_year"),
            release_month=enrichment.get("release_month"),
            release_day=enrichment.get("release_day"),
            MBID=enrichment.get("MBID"),
            release_group_MBID=enrichment.get("release_group_mbid"),
        )
        if new_album is None:
            logger.error(f"Failed to create album {album_name!r} from MusicBrainz match")
            self._warn("Error", "Could not create the album. See the log for details.")
            return None
        if enrichment.get("release_group_mbid"):
            award_jobs.append(("Album", new_album.album_id, enrichment["release_group_mbid"]))

        failed_artists = []
        for credit in artist_credits:
            artist = self.resolve_or_create_artist(credit.get("mbid"), credit.get("name"), award_jobs)
            if artist is None:
                if credit.get("name"):
                    failed_artists.append(credit["name"])
                continue
            link = self.controller.add.add_entity(
                "AlbumRoleAssociation",
                album_id=new_album.album_id,
                artist_id=artist.artist_id,
                role_id=1,  # "Album Artist" -- seeded convention, see db_defaults.py
            )
            if link is None:
                failed_artists.append(artist.artist_name)
        if failed_artists:
            self._warn("Some Artists Not Linked", "The album was created, but these artists could not be added to it:\n" + "\n".join(failed_artists))

        return new_album

    def resolve_or_create_artist(self, artist_mbid, artist_name, award_jobs: list[AwardJob]):
        """Return the local artist for a credit (by MBID, then name), else a new artist; None on failure."""
        if not artist_name:
            return None
        if artist_mbid:
            artist = self.controller.get.get_entity_object("Artist", MBID=artist_mbid)
            if artist is not None:
                return artist
        artist = self.controller.get.resolve_entity_or_alias("Artist", "artist_name", artist_name)
        if artist is not None and not artist.MBID:
            if artist_mbid and self.controller.update.update_entity("Artist", artist.artist_id, MBID=artist_mbid):
                artist.MBID = artist_mbid
                award_jobs.append(("Artist", artist.artist_id, artist_mbid))
            return artist
        # A name match whose row already carries a (necessarily different)
        # MBID is a distinct real-world artist -- ignore it and create a
        # new Artist instead of merging two different people.
        new_artist = self.controller.add.add_entity("Artist", artist_name=artist_name, MBID=artist_mbid)
        if new_artist is None:
            logger.error(f"Failed to create artist {artist_name!r} from MusicBrainz match")
            return None
        if artist_mbid:
            award_jobs.append(("Artist", new_artist.artist_id, artist_mbid))
        return new_artist


def _album_details(enrichment: dict, album_name: str, artist_credits: list) -> str:
    """Format the match's album fields as aligned 'Label: value' lines for the confirm prompt."""
    artist_names = ", ".join(c["name"] for c in artist_credits if c.get("name")) or "Unknown Artist"
    date_bits = [str(enrichment[k]) for k in ("release_year", "release_month", "release_day") if enrichment.get(k) is not None]
    lines = [f"Album:   {album_name}", f"Artist:  {artist_names}"]
    if date_bits:
        lines.append(f"Release: {'-'.join(date_bits)}")
    if enrichment.get("release_type"):
        lines.append(f"Type:    {enrichment['release_type']}")
    if enrichment.get("status"):
        lines.append(f"Status:  {enrichment['status']}")
    if enrichment.get("country"):
        lines.append(f"Country: {enrichment['country']}")
    if enrichment.get("MBID"):
        lines.append(f"MBID:    {enrichment['MBID']}")
    return "\n".join(lines)
