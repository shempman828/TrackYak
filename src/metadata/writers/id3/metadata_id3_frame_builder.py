"""Maps a TrackDataAssembler track-data dict to a list of ID3 frame bytes."""

from typing import Any

from src.foundation.logger_config import logger
from src.metadata.readers.metadata_mapping import (
    ID3_ALBUM_MAPPINGS,
    ID3_DATE_MAPPINGS,
    ID3_DISC_MAPPINGS,
    ID3_GENRE_MAPPINGS,
    ID3_MOOD_MAPPINGS,
    ID3_PUBLISHER_MAPPINGS,
    ID3_SPECIAL_MAPPINGS,
    ID3_TRACK_MAPPINGS,
)
from src.metadata.writers.id3.metadata_id3_writer import ID3TagWriter
from src.metadata.writers.metadata_tag_helpers import build_iso_date_string, format_track_number, group_artists_by_tag

_ROLE_TO_FRAME = {"Composer": "TCOM", "Primary Artist": "TPE1", "Album Artist": "TPE2", "Lyricist": "TEXT", "Original Lyricist": "TOLY", "Original Performer": "TOPE", "Conductor": "TPE3"}

# MusicBrainz Picard convention: a TXXX artist-ID frame beside each artist frame, so re-imports
# can resolve identity when the display name is an alias. Non-Picard roles get an analogous description.
_ID_FRAME_MAP = {"TPE1": "MusicBrainz Artist Id", "TPE2": "MusicBrainz Album Artist Id"}
_ID_FRAME_MAP.update({frame_id: f"MusicBrainz {role_name} Id" for role_name, frame_id in _ROLE_TO_FRAME.items() if frame_id not in _ID_FRAME_MAP})


class ID3FrameBuilder:
    """Builds the list of ID3 frames a track's data should have."""

    def __init__(self):
        """Create the frame writer."""
        self.id3_writer = ID3TagWriter()

    def _keyed_frame(self, tag_id: str, value: Any) -> bytes:
        """Build a frame for a mapping key, routing "TXXX:desc" and "UFID:owner" keys to their frame types."""
        if tag_id.startswith("TXXX:"):
            return self.id3_writer.create_txxx_frame(tag_id[5:], str(value))
        if tag_id.startswith("UFID:"):
            return self.id3_writer.create_ufid_frame(tag_id[5:], str(value))
        return self.id3_writer.create_text_frame(tag_id, str(value))

    def build_frames(self, data: dict[str, Any]) -> list[bytes]:
        """Build ID3 frames from track data."""
        frames = []
        track = data["track"]
        album = data["album"]
        disc = data["disc"]
        artists_with_roles = data["artists_with_roles"]
        album_artists_with_roles = data["album_artists_with_roles"]
        genres = data["genres"]
        moods = data["moods"]
        publishers = data["publishers"]

        for tag_id, mapping in ID3_TRACK_MAPPINGS.items():
            if tag_id == "TRCK":
                # Vinyl side + number (e.g. "B1") when the track has a side.
                track_number_text = format_track_number(track)
                if track_number_text:
                    frames.append(self.id3_writer.create_text_frame(tag_id, track_number_text))
                continue
            field_value = getattr(track, mapping["field"], None)
            if field_value is None or field_value == "":
                continue
            if tag_id == "USLT":
                frames.append(self.id3_writer.create_lyrics_frame(str(field_value)))
            elif tag_id == "COMM":
                frames.append(self.id3_writer.create_comment_frame(str(field_value)))
            elif tag_id == "PCNT":
                frames.append(self.id3_writer.create_counter_frame(int(field_value)))
            elif tag_id == "TLEN":
                # TLEN is milliseconds; the database stores seconds.
                frames.append(self.id3_writer.create_number_frame(tag_id, round(float(field_value) * 1000)))
            elif mapping["type"] is int:
                frames.append(self.id3_writer.create_number_frame(tag_id, int(field_value)))
            elif mapping["type"] is float:
                frames.append(self.id3_writer.create_float_frame(tag_id, float(field_value)))
            else:
                frames.append(self._keyed_frame(tag_id, field_value))

        if album:
            for tag_id, mapping in ID3_ALBUM_MAPPINGS.items():
                field_value = getattr(album, mapping["field"], None)
                if field_value is not None and field_value != "":
                    frames.append(self._keyed_frame(tag_id, field_value))

        # Album artists count here only under the "Album Artist" role.
        combined_artists = list(artists_with_roles) + [artist_data for artist_data in album_artists_with_roles if artist_data["role"].role_name == "Album Artist"]
        artists_by_frame, mbids_by_frame = group_artists_by_tag(combined_artists, _ROLE_TO_FRAME, _ID_FRAME_MAP)

        for frame_id, artist_names in artists_by_frame.items():
            if artist_names:
                frames.append(self.id3_writer.create_text_frame(frame_id, " / ".join(artist_names)))

        # mbids_by_frame is keyed by TXXX description (the _ID_FRAME_MAP values).
        for txxx_description, mbids in mbids_by_frame.items():
            if mbids:
                frames.append(self.id3_writer.create_txxx_frame(txxx_description, " / ".join(mbids)))

        genre_names = [genre.genre_name for genre in genres if genre.genre_name]
        if genre_names:
            frames.extend(self.id3_writer.create_text_frame(tag_id, " / ".join(genre_names)) for tag_id in ID3_GENRE_MAPPINGS)

        mood_names = [mood.mood_name for mood in moods if mood.mood_name]
        if mood_names:
            frames.extend(self.id3_writer.create_text_frame(tag_id, " / ".join(mood_names)) for tag_id in ID3_MOOD_MAPPINGS)

        if publishers:
            frames.extend(self.id3_writer.create_text_frame(tag_id, " / ".join(publishers)) for tag_id in ID3_PUBLISHER_MAPPINGS)

        if disc:
            for tag_id, mapping in ID3_DISC_MAPPINGS.items():
                field_value = getattr(disc, mapping["field"], None)
                if field_value is not None:
                    frames.append(self.id3_writer.create_number_frame(tag_id, int(field_value)))

        # "year" and "date" mappings are both an ISO date string truncated to the fields that resolve.
        for tag_id, mapping in ID3_DATE_MAPPINGS.items():
            entity = track if mapping.get("target") == "track" else album
            if not entity:
                continue
            date_text = build_iso_date_string(entity, mapping.get("fields", []))
            if date_text:
                frames.append(self.id3_writer.create_text_frame(tag_id, date_text))

        all_artists_data = artists_with_roles + album_artists_with_roles
        for tag_id, mapping in ID3_SPECIAL_MAPPINGS.items():
            if mapping["type"] != "special":
                continue
            separator = mapping["separator"]
            role_artist_pairs = [f"{artist_data['role'].role_name}{separator}{artist_data['credited_name']}" for artist_data in _named_credits(all_artists_data)]
            if role_artist_pairs:
                frames.append(self.id3_writer.create_text_frame(tag_id, separator.join(role_artist_pairs)))

        # One TXXX frame per description is allowed, so playlists are joined with " ; ".
        playlist_names = data.get("playlist_names") or []
        if playlist_names:
            frames.append(self.id3_writer.create_txxx_frame("PLAYLIST", " ; ".join(playlist_names)))
            logger.debug(f"Writing ID3 TXXX:PLAYLIST for track {track.track_id}: {playlist_names}")

        return [frame for frame in frames if frame]


def _named_credits(artist_data_list: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the artist credits that have both a role name and a credited name."""
    return [artist_data for artist_data in artist_data_list if artist_data["role"].role_name and artist_data["credited_name"]]
