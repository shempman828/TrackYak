"""Maps a track-data dict (from TrackDataAssembler) to a list of ID3
frame bytes, driven by the ID3_*_MAPPINGS tables. A pure function of its
input - no database access of its own.
"""

from typing import Any, Dict, List

from src.metadata.metadata_id3_writer import ID3TagWriter
from src.metadata.metadata_mapping import (
    ID3_ALBUM_MAPPINGS,
    ID3_DATE_MAPPINGS,
    ID3_DISC_MAPPINGS,
    ID3_GENRE_MAPPINGS,
    ID3_MOOD_MAPPINGS,
    ID3_PUBLISHER_MAPPINGS,
    ID3_SPECIAL_MAPPINGS,
    ID3_TRACK_MAPPINGS,
)
from src.metadata.metadata_text import build_iso_date_string, group_artists_by_tag
from src.core.logger_config import logger


class ID3FrameBuilder:
    """Builds the list of ID3 frames a track's data should have."""

    def __init__(self):
        self.id3_writer = ID3TagWriter()

    def build_frames(self, data: Dict[str, Any]) -> List[bytes]:
        """Build ID3 frames from track data with complete role handling."""
        frames = []
        track = data["track"]
        album = data["album"]
        disc = data["disc"]
        artists_with_roles = data["artists_with_roles"]
        album_artists_with_roles = data["album_artists_with_roles"]
        genres = data["genres"]
        moods = data["moods"]
        publishers = data["publishers"]

        # Track mappings
        for tag_id, mapping in ID3_TRACK_MAPPINGS.items():
            field_name = mapping["field"]
            field_value = getattr(track, field_name, None)
            if field_value is not None and field_value != "":
                if mapping["type"] == str:  # noqa: E721
                    if tag_id == "USLT":  # Lyrics
                        frames.append(
                            self.id3_writer.create_lyrics_frame(str(field_value))
                        )
                    elif tag_id == "COMM":  # Comment
                        frames.append(
                            self.id3_writer.create_comment_frame(str(field_value))
                        )
                    else:
                        frames.append(
                            self.id3_writer.create_text_frame(tag_id, str(field_value))
                        )
                elif mapping["type"] == int:  # noqa: E721
                    frames.append(
                        self.id3_writer.create_number_frame(tag_id, int(field_value))
                    )
                elif mapping["type"] == float:  # noqa: E721
                    frames.append(
                        self.id3_writer.create_float_frame(tag_id, float(field_value))
                    )

        # Album mappings
        for tag_id, mapping in ID3_ALBUM_MAPPINGS.items():
            if album:
                field_name = mapping["field"]
                field_value = getattr(album, field_name, None)
                if field_value is not None and field_value != "":
                    frames.append(
                        self.id3_writer.create_text_frame(tag_id, str(field_value))
                    )

        # Artist mappings with proper role handling
        role_to_frame_map = {
            "Composer": "TCOM",
            "Primary Artist": "TPE1",
            "Album Artist": "TPE2",
            "Lyricist": "TEXT",
            "Original Lyricist": "TOLY",
            "Original Performer": "TOPE",
            "Conductor": "TPE3",
        }

        # MusicBrainz Picard convention: alongside the display name, also
        # write a stable per-artist ID as a TXXX frame so re-imports can
        # resolve identity even when the display name is an alias override,
        # not the artist's canonical name. Only the primary/album artist
        # frames have a standard ID counterpart.
        id_frame_map = {"TPE1": "MusicBrainz Artist Id", "TPE2": "MusicBrainz Album Artist Id"}

        # Group artists by role for each frame type. Album artists only
        # count here under the "Album Artist" role, matching the original
        # (pre-consolidation) per-source filtering.
        combined_artists = list(artists_with_roles) + [
            artist_data
            for artist_data in album_artists_with_roles
            if artist_data["role"].role_name == "Album Artist"
        ]
        artists_by_frame, mbids_by_frame = group_artists_by_tag(
            combined_artists, role_to_frame_map, id_frame_map
        )

        # Create frames for each artist type
        for frame_id, artist_names in artists_by_frame.items():
            if artist_names:
                artist_text = " / ".join(artist_names)
                frames.append(self.id3_writer.create_text_frame(frame_id, artist_text))

        # Create the paired MusicBrainz ID TXXX frames, in the same order.
        # group_artists_by_tag already keys mbids_by_frame by the mapped
        # TXXX description (id_frame_map's values), not the source frame id.
        for txxx_description, mbids in mbids_by_frame.items():
            if mbids:
                mbid_text = " / ".join(mbids)
                frames.append(
                    self.id3_writer.create_txxx_frame(txxx_description, mbid_text)
                )

        # Genre mappings
        for tag_id, mapping in ID3_GENRE_MAPPINGS.items():
            if genres:
                genre_names = [genre.genre_name for genre in genres if genre.genre_name]
                if genre_names:
                    genre_text = " / ".join(genre_names)
                    frames.append(self.id3_writer.create_text_frame(tag_id, genre_text))

        # Mood mappings
        for tag_id, mapping in ID3_MOOD_MAPPINGS.items():
            if moods:
                mood_names = [mood.mood_name for mood in moods if mood.mood_name]
                if mood_names:
                    mood_text = " / ".join(mood_names)
                    frames.append(self.id3_writer.create_text_frame(tag_id, mood_text))

        # Publisher mappings
        for tag_id, field_name in ID3_PUBLISHER_MAPPINGS.items():
            if publishers:
                publisher_text = " / ".join(publishers)
                frames.append(self.id3_writer.create_text_frame(tag_id, publisher_text))

        # Disc mappings
        for tag_id, mapping in ID3_DISC_MAPPINGS.items():
            if disc:
                field_name = mapping["field"]
                field_value = getattr(disc, field_name, None)
                if field_value is not None:
                    frames.append(
                        self.id3_writer.create_number_frame(tag_id, int(field_value))
                    )

        # Date mappings with proper formatting. A single-field mapping
        # (type "year") and a 3-field one (type "date") are both just an
        # ISO date string truncated to however many fields resolved.
        for tag_id, mapping in ID3_DATE_MAPPINGS.items():
            entity_type = mapping.get("target")
            entity = track if entity_type == "track" else album
            if not entity:
                continue

            date_text = build_iso_date_string(entity, mapping.get("fields", []))
            if date_text:
                frames.append(self.id3_writer.create_text_frame(tag_id, date_text))

        # Handle special mappings (TMCL, TIPL)
        for tag_id, mapping in ID3_SPECIAL_MAPPINGS.items():
            if mapping["type"] == "special":
                # Build role/artist pairs
                role_artist_pairs = []

                # Include both track and album artists
                all_artists_data = artists_with_roles + album_artists_with_roles

                for artist_data in all_artists_data:
                    role_name = artist_data["role"].role_name
                    artist_name = artist_data["credited_name"]
                    if role_name and artist_name:
                        role_artist_pairs.append(
                            f"{role_name}{mapping['separator']}{artist_name}"
                        )

                if role_artist_pairs:
                    special_text = mapping["separator"].join(role_artist_pairs)
                    frames.append(
                        self.id3_writer.create_text_frame(tag_id, special_text)
                    )

        # ----------------------------------------------------------------
        # Playlist tags — written as TXXX:PLAYLIST
        # Multiple playlists are joined with " ; " in a single TXXX frame
        # because ID3 only allows one TXXX frame per description name.
        # ----------------------------------------------------------------
        playlist_names = data.get("playlist_names") or []
        if playlist_names:
            joined = " ; ".join(playlist_names)
            frames.append(self.id3_writer.create_txxx_frame("PLAYLIST", joined))
            logger.debug(
                f"Writing ID3 TXXX:PLAYLIST for track {track.track_id}: {playlist_names}"
            )

        return frames
