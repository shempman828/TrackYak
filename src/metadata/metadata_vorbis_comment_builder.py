"""Maps a track-data dict (from TrackDataAssembler) to a Vorbis comment
tag dict. A pure function of its input - no database access of its own;
the sibling track/disc counts and playlist names that TRACKTOTAL/
DISCTOTAL/PLAYLIST need are expected to already be in `data`, put there
by TrackDataAssembler.
"""

from typing import Any, Dict

from src.metadata.metadata_mapping import (
    VORBIS_ALBUM_MAPPINGS,
    VORBIS_DISC_MAPPINGS,
    VORBIS_TRACK_MAPPINGS,
)
from src.metadata.metadata_text import build_iso_date_string, group_artists_by_tag
from src.core.logger_config import logger


class VorbisCommentBuilder:
    """Builds the Vorbis comment tag dict a track's data should have."""

    def build_comments(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Build Vorbis comments from track data.

        Returns Dict[str, str | List[str]]. Lists produce repeated tag keys
        in the output block (e.g. GENRE=Rock / GENRE=Blues as separate entries).
        """
        comments = {}
        track = data["track"]
        album = data["album"]
        disc = data["disc"]
        artists_with_roles = data["artists_with_roles"]
        album_artists_with_roles = data["album_artists_with_roles"]
        genres = data["genres"]
        moods = data["moods"]
        publishers = data["publishers"]
        places = data["places"]

        def _set(tag, value):
            """Set a single-value tag, skipping None / empty."""
            if value is None or value == "":
                return
            comments[tag] = str(value)

        def _set_list(tag, values):
            """Set a multi-value tag as a list (will become repeated entries)."""
            clean = [str(v) for v in values if v is not None and str(v).strip()]
            if clean:
                comments[tag] = clean

        # ----------------------------------------------------------------
        # Track scalar fields (from VORBIS_TRACK_MAPPINGS)
        # Skip fields we handle specially below.
        # ----------------------------------------------------------------
        SKIP_TRACK_FIELDS = {"user_rating"}  # handled separately with scaling

        for tag_name, mapping in VORBIS_TRACK_MAPPINGS.items():
            field_name = mapping["field"]
            if field_name in SKIP_TRACK_FIELDS:
                continue
            field_value = getattr(track, field_name, None)
            if field_value is not None and field_value != "":
                _set(tag_name, field_value)

        # RATING: store as 0–100 integer (widely supported scale)
        if track.user_rating is not None:
            # DB stores 0–10; tag convention is 0–100
            scaled = int(round(float(track.user_rating) * 10))
            _set("RATING", scaled)

        # ----------------------------------------------------------------
        # Album scalar fields
        # ----------------------------------------------------------------
        if album:
            for tag_name, mapping in VORBIS_ALBUM_MAPPINGS.items():
                field_name = mapping["field"]
                field_value = getattr(album, field_name, None)
                if field_value is not None and field_value != "":
                    _set(tag_name, field_value)

        # ----------------------------------------------------------------
        # Disc fields + computed TRACKTOTAL
        # ----------------------------------------------------------------
        if disc:
            for tag_name, mapping in VORBIS_DISC_MAPPINGS.items():
                field_name = mapping["field"]
                field_value = getattr(disc, field_name, None)
                if field_value is not None and field_value != "":
                    _set(tag_name, field_value)

            disc_track_count = data.get("disc_track_count")
            if disc_track_count:
                _set("TRACKTOTAL", disc_track_count)
                _set("TOTALTRACKS", disc_track_count)  # legacy alias

        # DISCTOTAL: count of discs on the album
        album_disc_count = data.get("album_disc_count")
        if album_disc_count:
            _set("DISCTOTAL", album_disc_count)
            _set("TOTALDISCS", album_disc_count)  # legacy alias

        # ----------------------------------------------------------------
        # Date tags — built from separate year/month/day columns
        # ----------------------------------------------------------------
        # Album release date
        if album:
            release_date = build_iso_date_string(
                album, ["release_year", "release_month", "release_day"]
            )
            if release_date:
                _set("DATE", release_date)
                _set("YEAR", str(album.release_year))

        # Track recording date
        recording_date = build_iso_date_string(
            track, ["recorded_year", "recorded_month", "recorded_day"]
        )
        if recording_date:
            _set("RECORDINGDATE", recording_date)
            _set("RECORDEDDATE", recording_date)  # alias used by some taggers

        # Track composed date
        composed_date = build_iso_date_string(
            track, ["composed_year", "composed_month", "composed_day"]
        )
        if composed_date:
            _set("COMPOSEDDATE", composed_date)

        # Remaster year
        if track.remaster_year:
            _set("REMASTERDATE", str(track.remaster_year))

        # First performed (classical)
        if track.first_performed_year:
            _set("FIRSTPERFORMED", str(track.first_performed_year))

        # ----------------------------------------------------------------
        # Artists — Picard-style role handling
        # ----------------------------------------------------------------
        # Map from our role names → Vorbis tag names
        # Roles not in this map fall back to PERFORMER with role annotation
        ROLE_TO_TAG = {
            "Primary Artist": "ARTIST",
            "Album Artist": "ALBUMARTIST",
            "Composer": "COMPOSER",
            "Lyricist": "LYRICIST",
            "Arranger": "ARRANGER",
            "Original Lyricist": "ORIGINALLYRICIST",
            "Original Performer": "ORIGINALPERFORMER",
            "Conductor": "CONDUCTOR",
            "Engineer": "ENGINEER",
            "Mixer": "MIXER",
            "Producer": "PRODUCER",
            "Remixer": "REMIXER",
            "Writer": "WRITER",
            "Orchestra": "ORCHESTRA",
            "Choir": "CHOIR",
            "DJ": "DJ",
            "Mastering Engineer": "MASTERING",
        }

        # MusicBrainz Picard convention: alongside the display name, also
        # write a stable per-artist ID tag so re-imports can resolve identity
        # even when the display name is an alias override, not the artist's
        # canonical name. ARTIST/ALBUMARTIST use Picard's standard tag
        # names; every other role gets an analogous MUSICBRAINZ_{ROLE}ID
        # tag so each artist credit's mbid is encoded whenever the artist
        # has one.
        ID_TAG_MAP = {"ARTIST": "MUSICBRAINZ_ARTISTID", "ALBUMARTIST": "MUSICBRAINZ_ALBUMARTISTID"}
        ID_TAG_MAP.update(
            {
                tag: f"MUSICBRAINZ_{tag}ID"
                for tag in ROLE_TO_TAG.values()
                if tag not in ID_TAG_MAP
            }
        )

        all_artist_data = artists_with_roles + album_artists_with_roles
        artists_by_tag, mbids_by_tag = group_artists_by_tag(
            all_artist_data, ROLE_TO_TAG, ID_TAG_MAP, dedupe=True
        )

        # Accumulate into the same per-tag lists for the PERFORMER fallback
        # below, so emitting the entries later applies one dedup rule.
        def _add_artist(tag, name):
            if not name:
                return
            names = artists_by_tag.setdefault(tag, [])
            if name not in names:
                names.append(name)

        for artist_data in all_artist_data:
            role_name = artist_data["role"].role_name
            artist_name = artist_data["credited_name"]
            if not artist_name:
                continue

            if role_name not in ROLE_TO_TAG:
                # Non-standard role: write as PERFORMER=Artist (Role)
                # This is exactly what MusicBrainz Picard does
                performer_value = f"{artist_name} ({role_name})"
                _add_artist("PERFORMER", performer_value)

        # Additionally, Conductor/Performer always also get a PERFORMER entry
        ALSO_PERFORMER = {"Conductor", "Performer"}
        for artist_data in all_artist_data:
            role_name = artist_data["role"].role_name
            artist_name = artist_data["credited_name"]
            if not artist_name:
                continue
            if role_name in ALSO_PERFORMER:
                performer_value = f"{artist_name} ({role_name})"
                _add_artist("PERFORMER", performer_value)

        for tag, names in artists_by_tag.items():
            _set_list(tag, names)

        for id_tag, mbids in mbids_by_tag.items():
            _set_list(id_tag, mbids)

        # ----------------------------------------------------------------
        # Multi-value fields — genres, moods, publishers, places
        # ----------------------------------------------------------------
        if genres:
            genre_names = [g.genre_name for g in genres if g.genre_name]
            _set_list("GENRE", genre_names)

        if moods:
            mood_names = [m.mood_name for m in moods if m.mood_name]
            _set_list("MOOD", mood_names)

        if publishers:
            # publishers is already a list of strings from get_track_data
            _set_list("LABEL", publishers)
            _set_list("ORGANIZATION", publishers)

        if places:
            place_names = [p.place_name for p in places if p.place_name]
            _set_list("LOCATION", place_names)

        # ----------------------------------------------------------------
        # Playlist tags — written as repeated PLAYLIST= entries.
        # Vorbis natively supports multiple entries with the same key,
        # so each playlist gets its own clean tag line.
        # ----------------------------------------------------------------
        playlist_names = data.get("playlist_names") or []
        if playlist_names:
            _set_list("PLAYLIST", playlist_names)
            logger.debug(
                f"Writing Vorbis PLAYLIST tags for track {track.track_id}: {playlist_names}"
            )

        return comments
