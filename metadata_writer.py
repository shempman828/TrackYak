"""
Module for writing database metadata to audio files from scratch.
Supports ID3v2.3/2.4 (MP3) and Vorbis comments (FLAC, OGG).
"""

import os
import struct
from enum import Enum
from typing import Any, Dict, List, Tuple

from logger_config import logger
from metadata_id3_writer import ID3TagWriter
from metadata_mapping import (
    ID3_ALBUM_MAPPINGS,
    ID3_DATE_MAPPINGS,
    ID3_DISC_MAPPINGS,
    ID3_GENRE_MAPPINGS,
    ID3_MOOD_MAPPINGS,
    ID3_PUBLISHER_MAPPINGS,
    ID3_SPECIAL_MAPPINGS,
    ID3_TRACK_MAPPINGS,
    VORBIS_ALBUM_MAPPINGS,
    VORBIS_DATE_MAPPINGS,
    VORBIS_DISC_MAPPINGS,
    VORBIS_GENRE_MAPPINGS,
    VORBIS_MOOD_MAPPINGS,
    VORBIS_PLACE_MAPPINGS,
    VORBIS_PUBLISHER_MAPPINGS,
    VORBIS_SPECIAL_MAPPINGS,
    VORBIS_TRACK_MAPPINGS,
)
from metadata_writer_vorbis import VorbisCommentWriter
from status_utility import StatusManager


class WriteMode(Enum):
    """Modes for writing tags to files."""

    ADD_ONLY = "add_only"  # Only add new tags, don't modify existing ones
    REPLACE_ALL = "replace_all"  # Clear all tags and write only database tags
    UPDATE_EXISTING = (
        "update_existing"  # Replace existing tags and add new ones, but keep others
    )


class AudioFormat(Enum):
    MP3 = "mp3"
    FLAC = "flac"
    OGG = "ogg"
    UNKNOWN = "unknown"


class MetadataWriter:
    """Main class for writing database metadata to audio files."""

    def __init__(self, controller):
        self.controller = controller
        self.id3_writer = ID3TagWriter()
        self.vorbis_writer = VorbisCommentWriter()
        self.status_manager = StatusManager

    def detect_audio_format(self, file_path: str) -> AudioFormat:
        """Detect audio format from file extension."""
        ext = os.path.splitext(file_path)[1].lower()
        if ext in [".mp3", ".mp2", ".mp1"]:
            return AudioFormat.MP3
        elif ext in [".flac"]:
            return AudioFormat.FLAC
        elif ext in [".ogg", ".oga"]:
            return AudioFormat.OGG
        else:
            return AudioFormat.UNKNOWN

    def get_track_data(self, track_id: int) -> Dict[str, Any]:
        """Get complete track data from database using controller helpers."""
        try:
            # Get track using controller
            track = self.controller.get.get_entity_object("Track", track_id=track_id)
            if not track:
                return {}

            # Get album using controller
            album = None
            if track.album_id:
                album = self.controller.get.get_entity_object(
                    "Album", album_id=track.album_id
                )

            # Get disc using controller
            disc = None
            if track.disc_id:
                disc = self.controller.get.get_entity_object(
                    "Disc", disc_id=track.disc_id
                )

            # Get track artists with roles using controller
            track_artist_roles = self.controller.get.get_all_entities(
                "TrackArtistRole", track_id=track_id
            )

            artists_with_roles = []
            for tar in track_artist_roles:
                artist = self.controller.get.get_entity_object(
                    "Artist", artist_id=tar.artist_id
                )
                role = self.controller.get.get_entity_object(
                    "Role", role_id=tar.role_id
                )
                if artist and role:
                    artists_with_roles.append({"artist": artist, "role": role})

            # Get album artists using controller
            album_artists_with_roles = []
            if album:
                album_roles = self.controller.get.get_all_entities(
                    "AlbumRoleAssociation", album_id=album.album_id
                )
                for ar in album_roles:
                    artist = self.controller.get.get_entity_object(
                        "Artist", artist_id=ar.artist_id
                    )
                    role = self.controller.get.get_entity_object(
                        "Role", role_id=ar.role_id
                    )
                    if artist and role:
                        album_artists_with_roles.append(
                            {"artist": artist, "role": role}
                        )

            # Get genres using controller
            track_genres = self.controller.get.get_all_entities(
                "TrackGenre", track_id=track_id
            )
            genres = []
            for tg in track_genres:
                genre = self.controller.get.get_entity_object(
                    "Genre", genre_id=tg.genre_id
                )
                if genre:
                    genres.append(genre)

            # Get moods using controller
            mood_tracks = self.controller.get.get_all_entities(
                "MoodTrackAssociation", track_id=track_id
            )
            moods = []
            for mt in mood_tracks:
                mood = self.controller.get.get_entity_object("Mood", mood_id=mt.mood_id)
                if mood:
                    moods.append(mood)

            # Get publishers from album using controller
            publishers = []
            if album:
                album_publishers = self.controller.get.get_all_entities(
                    "AlbumPublisher", album_id=album.album_id
                )
                for ap in album_publishers:
                    publisher = self.controller.get.get_entity_object(
                        "Publisher", publisher_id=ap.publisher_id
                    )
                    if publisher and publisher.publisher_name:
                        publishers.append(publisher.publisher_name)

            # Get places using controller
            place_associations = self.controller.get.get_all_entities(
                "PlaceAssociation", entity_id=track_id, entity_type="Track"
            )
            places = []
            for pa in place_associations:
                place = self.controller.get.get_entity_object(
                    "Place", place_id=pa.place_id
                )
                if place:
                    places.append(place)

            return {
                "track": track,
                "album": album,
                "disc": disc,
                "artists_with_roles": artists_with_roles,
                "album_artists_with_roles": album_artists_with_roles,
                "genres": genres,
                "moods": moods,
                "publishers": publishers,
                "places": places,
            }
        except Exception as e:
            logger.debug(f"Error getting track data for ID {track_id}: {e}")
            return {}

    def build_id3_frames_from_data(self, data: Dict[str, Any]) -> List[bytes]:
        """Build ID3 frames from database data with complete role handling."""
        frames = []
        track = data["track"]
        album = data["album"]
        disc = data["disc"]
        artists_with_roles = data["artists_with_roles"]
        album_artists_with_roles = data["album_artists_with_roles"]
        genres = data["genres"]
        moods = data["moods"]
        publishers = data["publishers"]
        data["places"]

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

        # Group artists by role for each frame type
        artists_by_frame = {}
        for artist_data in artists_with_roles:
            role_name = artist_data["role"].role_name
            frame_id = role_to_frame_map.get(role_name)
            if frame_id and artist_data["artist"].artist_name:
                if frame_id not in artists_by_frame:
                    artists_by_frame[frame_id] = []
                artists_by_frame[frame_id].append(artist_data["artist"].artist_name)

        # Also include album artists
        for artist_data in album_artists_with_roles:
            role_name = artist_data["role"].role_name
            if role_name == "Album Artist" and artist_data["artist"].artist_name:
                if "TPE2" not in artists_by_frame:
                    artists_by_frame["TPE2"] = []
                artists_by_frame["TPE2"].append(artist_data["artist"].artist_name)

        # Create frames for each artist type
        for frame_id, artist_names in artists_by_frame.items():
            if artist_names:
                artist_text = " / ".join(artist_names)
                frames.append(self.id3_writer.create_text_frame(frame_id, artist_text))

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

        # Date mappings with proper formatting
        for tag_id, mapping in ID3_DATE_MAPPINGS.items():
            entity_type = mapping.get("target")
            entity = track if entity_type == "track" else album

            if entity:
                fields = mapping.get("fields", [])
                date_parts = []
                for field in fields:
                    field_value = getattr(entity, field, None)
                    if field_value:
                        if "year" in field:
                            date_parts.append(str(field_value).zfill(4))
                        else:
                            date_parts.append(str(field_value).zfill(2))

                if date_parts:
                    if mapping["type"] == "date":
                        if len(date_parts) == 3:  # Full date
                            date_text = (
                                f"{date_parts[0]}-{date_parts[1]}-{date_parts[2]}"
                            )
                        elif len(date_parts) == 2:  # Year-month
                            date_text = f"{date_parts[0]}-{date_parts[1]}"
                        else:  # Year only
                            date_text = date_parts[0]
                        frames.append(
                            self.id3_writer.create_text_frame(tag_id, date_text)
                        )
                    elif mapping["type"] == "year":
                        frames.append(
                            self.id3_writer.create_text_frame(tag_id, date_parts[0])
                        )

        # Handle special mappings (TMCL, TIPL)
        for tag_id, mapping in ID3_SPECIAL_MAPPINGS.items():
            if mapping["type"] == "special":
                # Build role/artist pairs
                role_artist_pairs = []

                # Include both track and album artists
                all_artists_data = artists_with_roles + album_artists_with_roles

                for artist_data in all_artists_data:
                    role_name = artist_data["role"].role_name
                    artist_name = artist_data["artist"].artist_name
                    if role_name and artist_name:
                        role_artist_pairs.append(
                            f"{role_name}{mapping['separator']}{artist_name}"
                        )

                if role_artist_pairs:
                    special_text = mapping["separator"].join(role_artist_pairs)
                    frames.append(
                        self.id3_writer.create_text_frame(tag_id, special_text)
                    )

        return frames

    def build_vorbis_comments_from_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Build Vorbis comments from database data with complete role handling."""
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

        # Track mappings
        for tag_name, mapping in VORBIS_TRACK_MAPPINGS.items():
            field_name = mapping["field"]
            field_value = getattr(track, field_name, None)
            if field_value is not None and field_value != "":
                comments[tag_name] = str(field_value)

        # Album mappings
        for tag_name, mapping in VORBIS_ALBUM_MAPPINGS.items():
            if album:
                field_name = mapping["field"]
                field_value = getattr(album, field_name, None)
                if field_value is not None and field_value != "":
                    comments[tag_name] = str(field_value)

        # Artist mappings with proper role handling
        role_to_field_map = {
            "Primary Artist": "ARTIST",
            "Conductor": ["PERFORMER", "CONDUCTOR"],
            "Album Artist": "ALBUMARTIST",
            "Composer": "COMPOSER",
            "Lyricist": "LYRICIST",
            "Arranger": ["ARRANGER", "ARRANGEMENT"],
            "Original Performer": "ORIGINALPERFORMER",
            "Engineer": "ENGINEER",
            "Mixer": "MIXER",
            "Producer": "PRODUCER",
            "Remixer": "REMIXER",
            "Writer": "WRITER",
            "Vocalist": ["VOCALS", "VOCALIST"],
            "Narrator": ["SPOKEN", "NARRATOR"],
            "Orchestra": "ORCHESTRA",
            "Choir": "CHOIR",
            "DJ": "DJ",
            "Mastering Engineer": "MASTERING",
        }

        # Group artists by field
        artists_by_field = {}
        for artist_data in artists_with_roles + album_artists_with_roles:
            role_name = artist_data["role"].role_name
            artist_name = artist_data["artist"].artist_name
            if not artist_name:
                continue

            fields = role_to_field_map.get(role_name, [])
            if isinstance(fields, str):
                fields = [fields]

            for field in fields:
                if field not in artists_by_field:
                    artists_by_field[field] = []
                artists_by_field[field].append(artist_name)

        # Create comments for each artist field
        for field, artist_names in artists_by_field.items():
            if artist_names:
                comments[field] = " / ".join(artist_names)

        # Genre mappings
        for tag_name, mapping in VORBIS_GENRE_MAPPINGS.items():
            if genres:
                genre_names = [genre.genre_name for genre in genres if genre.genre_name]
                if genre_names:
                    comments[tag_name] = " / ".join(genre_names)

        # Mood mappings
        for tag_name, mapping in VORBIS_MOOD_MAPPINGS.items():
            if moods:
                mood_names = [mood.mood_name for mood in moods if mood.mood_name]
                if mood_names:
                    comments[tag_name] = " / ".join(mood_names)

        # Publisher mappings
        for tag_name, mapping in VORBIS_PUBLISHER_MAPPINGS.items():
            if publishers:
                comments[tag_name] = " / ".join(publishers)

        # Disc mappings
        for tag_name, mapping in VORBIS_DISC_MAPPINGS.items():
            if disc:
                field_name = mapping["field"]
                field_value = getattr(disc, field_name, None)
                if field_value is not None:
                    comments[tag_name] = str(field_value)

        # Place mappings
        for tag_name, mapping in VORBIS_PLACE_MAPPINGS.items():
            if places:
                place_names = [place.place_name for place in places if place.place_name]
                if place_names:
                    comments[tag_name] = " / ".join(place_names)

        # Date mappings with proper formatting
        for tag_name, mapping in VORBIS_DATE_MAPPINGS.items():
            entity_type = mapping.get("target")
            entity = track if entity_type == "track" else album

            if entity:
                fields = mapping.get("fields", [])
                date_parts = []
                for field in fields:
                    field_value = getattr(entity, field, None)
                    if field_value:
                        if "year" in field:
                            date_parts.append(str(field_value).zfill(4))
                        else:
                            date_parts.append(str(field_value).zfill(2))

                if date_parts:
                    if mapping["type"] == "date":
                        if len(date_parts) == 3:  # Full date
                            comments[tag_name] = (
                                f"{date_parts[0]}-{date_parts[1]}-{date_parts[2]}"
                            )
                        elif len(date_parts) == 2:  # Year-month
                            comments[tag_name] = f"{date_parts[0]}-{date_parts[1]}"
                        else:  # Year only
                            comments[tag_name] = date_parts[0]
                    elif mapping["type"] == "year":
                        comments[tag_name] = date_parts[0]

        # Handle special mappings (PERFORMER with instrument pattern)
        for tag_name, mapping in VORBIS_SPECIAL_MAPPINGS.items():
            if mapping["type"] == "special":
                performer_data = []
                for artist_data in artists_with_roles:
                    role_name = artist_data["role"].role_name
                    artist_name = artist_data["artist"].artist_name
                    if (
                        role_name
                        and artist_name
                        and role_name not in ["Primary Artist", "Album Artist"]
                    ):
                        # Format as "Artist (Role)" for MusicBrainz compatibility
                        performer_data.append(f"{artist_name} ({role_name})")

                if performer_data:
                    comments[tag_name] = " / ".join(performer_data)

        return comments

    def read_existing_id3_tags(self, file_path: str) -> Dict[str, Any]:
        """Read existing ID3 tags from file with full parsing."""
        existing_tags = {}
        try:
            with open(file_path, "rb") as f:
                # Read ID3 header
                header = f.read(10)
                if not header.startswith(b"ID3"):
                    return existing_tags

                # Parse header
                _version_major, _version_minor = header[3], header[4]
                header[5]
                size = self._parse_sync_safe_int(header[6:10])

                # Read tag data
                tag_data = f.read(size)
                pos = 0

                while pos < len(tag_data) - 10:
                    # Read frame header
                    frame_header = tag_data[pos : pos + 10]
                    if len(frame_header) < 10:
                        break

                    frame_id = frame_header[0:4].decode("ascii", errors="ignore")
                    frame_size = self._parse_sync_safe_int(frame_header[4:8])
                    frame_header[8:10]

                    # Skip invalid frames
                    if frame_size == 0 or pos + 10 + frame_size > len(tag_data):
                        break

                    # Read frame data
                    frame_data = tag_data[pos + 10 : pos + 10 + frame_size]

                    # Parse frame content based on frame type
                    if frame_id.startswith("T"):  # Text frame
                        encoding = frame_data[0]
                        if encoding == 0x00:  # ISO-8859-1
                            text = frame_data[1:].decode("iso-8859-1", errors="ignore")
                        elif encoding == 0x01:  # UTF-16 with BOM
                            text = frame_data[3:].decode("utf-16", errors="ignore")
                        elif encoding == 0x02:  # UTF-16BE without BOM
                            text = frame_data[1:].decode("utf-16be", errors="ignore")
                        elif encoding == 0x03:  # UTF-8
                            text = frame_data[1:].decode("utf-8", errors="ignore")
                        else:
                            text = frame_data[1:].decode("utf-8", errors="ignore")

                        existing_tags[frame_id] = text.strip("\x00")

                    elif frame_id in ["COMM", "USLT"]:  # Comment/Lyrics frames
                        encoding = frame_data[0]
                        frame_data[1:4].decode("iso-8859-1", errors="ignore")
                        content = frame_data[4:]

                        if encoding == 0x00:  # ISO-8859-1
                            text = content.decode("iso-8859-1", errors="ignore")
                        elif encoding == 0x01:  # UTF-16 with BOM
                            text = content.decode("utf-16", errors="ignore")
                        elif encoding == 0x02:  # UTF-16BE without BOM
                            text = content.decode("utf-16be", errors="ignore")
                        elif encoding == 0x03:  # UTF-8
                            text = content.decode("utf-8", errors="ignore")
                        else:
                            text = content.decode("utf-8", errors="ignore")

                        existing_tags[frame_id] = text.strip("\x00")

                    pos += 10 + frame_size

        except Exception as e:
            logger.debug(f"Error reading existing ID3 tags: {e}")

        return existing_tags

    def read_existing_vorbis_comments(self, file_path: str) -> Dict[str, Any]:
        """Read existing Vorbis comments from file with full parsing."""
        existing_comments = {}
        try:
            with open(file_path, "rb") as f:
                file_data = f.read()

                # Find Vorbis comment block
                comment_start = file_data.find(b"\x03vorbis")
                if comment_start == -1:
                    return existing_comments

                pos = comment_start + 7  # Skip "\x03vorbis"

                # Read vendor length
                if pos + 4 > len(file_data):
                    return existing_comments
                vendor_length = struct.unpack("<I", file_data[pos : pos + 4])[0]
                pos += 4

                # Skip vendor string
                pos += vendor_length

                # Read comment count
                if pos + 4 > len(file_data):
                    return existing_comments
                comment_count = struct.unpack("<I", file_data[pos : pos + 4])[0]
                pos += 4

                # Read each comment
                for _ in range(comment_count):
                    if pos + 4 > len(file_data):
                        break

                    comment_length = struct.unpack("<I", file_data[pos : pos + 4])[0]
                    pos += 4

                    if pos + comment_length > len(file_data):
                        break

                    comment_data = file_data[pos : pos + comment_length]
                    pos += comment_length

                    # Parse comment (FIELD=VALUE)
                    try:
                        comment_str = comment_data.decode("utf-8", errors="ignore")
                        if "=" in comment_str:
                            field, value = comment_str.split("=", 1)
                            # Unescape value
                            value = value.replace("\\=", "=")
                            existing_comments[field.upper()] = value
                    except Exception:
                        continue

        except Exception as e:
            logger.debug(f"Error reading existing Vorbis comments: {e}")

        return existing_comments

    def _parse_sync_safe_int(self, data: bytes) -> int:
        """Parse sync-safe integer from ID3 tag."""
        value = 0
        for byte in data:
            value = (value << 7) | (byte & 0x7F)
        return value

    def _find_mp3_audio_start(self, file_path: str) -> int:
        """Find the start of MP3 audio data (after ID3 tag)."""
        try:
            with open(file_path, "rb") as f:
                header = f.read(10)
                if header.startswith(b"ID3"):
                    size = self._parse_sync_safe_int(header[6:10])
                    return 10 + size
                else:
                    return 0
        except Exception:
            return 0

    def _find_flac_metadata_blocks(self, file_path: str) -> List[Tuple[int, int, int]]:
        """Find FLAC metadata blocks and their positions."""
        blocks = []
        try:
            with open(file_path, "rb") as f:
                # Check FLAC signature
                if f.read(4) != b"fLaC":
                    return blocks

                # Read metadata blocks
                while True:
                    header = f.read(4)
                    if len(header) < 4:
                        break

                    is_last = (header[0] & 0x80) >> 7
                    block_type = header[0] & 0x7F
                    block_size = struct.unpack(">I", b"\x00" + header[1:4])[0]

                    current_pos = f.tell()
                    blocks.append((block_type, current_pos, block_size))

                    # Skip block data
                    f.seek(block_size, 1)

                    if is_last:
                        break

        except Exception as e:
            logger.debug(f"Error finding FLAC metadata blocks: {e}")

        return blocks

    def write_metadata_to_file(
        self, track_id: int, file_path: str, mode: WriteMode = WriteMode.UPDATE_EXISTING
    ) -> bool:
        """Write database metadata to audio file with complete file handling."""
        try:
            self.status_manager.start_task(
                f"Writing metadata to {os.path.basename(file_path)}"
            )

            if not os.path.exists(file_path):
                self.status_manager.end_task(
                    f"File not found: {os.path.basename(file_path)}", 3000
                )
                raise FileNotFoundError(f"Audio file not found: {file_path}")

            data = self.get_track_data(track_id)
            if not data or not data.get("track"):
                self.status_manager.end_task(f"No data for track ID: {track_id}", 3000)
                raise ValueError(f"No data found for track ID: {track_id}")

            audio_format = self.detect_audio_format(file_path)

            if audio_format == AudioFormat.MP3:
                result = self._write_id3_metadata(file_path, data, mode)
            elif audio_format in [AudioFormat.FLAC, AudioFormat.OGG]:
                result = self._write_vorbis_metadata(
                    file_path, data, mode, audio_format
                )
            else:
                self.status_manager.end_task(
                    f"Unsupported format: {audio_format}", 3000
                )
                raise ValueError(f"Unsupported audio format: {audio_format}")

            if result:
                self.status_manager.end_task(
                    f"Updated {os.path.basename(file_path)}", 3000
                )
            else:
                self.status_manager.end_task(
                    f"Failed to update {os.path.basename(file_path)}", 3000
                )

            return result

        except Exception as e:
            logger.debug(f"Error writing metadata to {file_path}: {e}")
            self.status_manager.end_task(f"Error: {os.path.basename(file_path)}", 3000)
            return False

    def _write_id3_metadata(
        self, file_path: str, data: Dict[str, Any], mode: WriteMode
    ) -> bool:
        """Write ID3 metadata to MP3 file with complete file handling."""
        try:
            # Create backup
            backup_path = file_path + ".bak"
            import shutil

            shutil.copy2(file_path, backup_path)

            # Build new frames
            new_frames = self.build_id3_frames_from_data(data)
            new_tag = self.id3_writer.build_id3_tag(new_frames)

            # Read existing audio data
            audio_start = self._find_mp3_audio_start(file_path)

            with open(file_path, "r+b") as f:
                # Read audio data
                f.seek(audio_start)
                audio_data = f.read()

                # Write new tag and audio data
                f.seek(0)
                f.write(new_tag)
                f.write(audio_data)
                f.truncate()

            # Remove backup if successful
            os.remove(backup_path)
            return True

        except Exception as e:
            logger.debug(f"Error writing ID3 metadata: {e}")
            # Restore backup if exists
            if os.path.exists(backup_path):
                shutil.copy2(backup_path, file_path)
                os.remove(backup_path)
            return False

    def _write_vorbis_metadata(
        self,
        file_path: str,
        data: Dict[str, Any],
        mode: WriteMode,
        audio_format: AudioFormat,
    ) -> bool:
        """Write Vorbis metadata to FLAC/OGG file with complete file handling."""
        try:
            # Create backup
            backup_path = file_path + ".bak"
            import shutil

            shutil.copy2(file_path, backup_path)

            # Build new comments
            new_comments = self.build_vorbis_comments_from_data(data)
            new_vorbis_block = self.vorbis_writer.build_vorbis_comments(new_comments)

            if audio_format == AudioFormat.FLAC:
                success = self._write_flac_metadata(file_path, new_vorbis_block)
            else:  # OGG
                success = self._write_ogg_metadata(file_path, new_vorbis_block)

            if success:
                os.remove(backup_path)
            else:
                # Restore backup
                shutil.copy2(backup_path, file_path)
                os.remove(backup_path)

            return success

        except Exception as e:
            logger.debug(f"Error writing Vorbis metadata: {e}")
            # Restore backup if exists
            if os.path.exists(backup_path):
                shutil.copy2(backup_path, file_path)
                os.remove(backup_path)
            return False

    def _write_flac_metadata(self, file_path: str, new_comment_block: bytes) -> bool:
        """Replace Vorbis comment block in FLAC file."""
        try:
            blocks = self._find_flac_metadata_blocks(file_path)
            if not blocks:
                return False

            # Find the Vorbis comment block (type 4)
            comment_block_info = None
            other_blocks = []
            for block_type, pos, size in blocks:
                if block_type == 4:  # VORBIS_COMMENT
                    comment_block_info = (block_type, pos, size)
                else:
                    other_blocks.append((block_type, pos, size))

            # Read the entire file
            with open(file_path, "rb") as f:
                file_data = f.read()

            # Reconstruct file with new comment block
            with open(file_path, "wb") as f:
                # Write FLAC signature
                f.write(b"fLaC")

                # Write other blocks (except the last one flag)
                for i, (block_type, pos, size) in enumerate(other_blocks):
                    is_last = (
                        1
                        if (i == len(other_blocks) - 1 and not comment_block_info)
                        else 0
                    )
                    block_header = struct.pack(">B", (is_last << 7) | block_type)
                    block_header += struct.pack(">I", size)[1:]  # 3-byte size
                    f.write(block_header)

                    # Write block data
                    block_data = file_data[pos : pos + size]
                    f.write(block_data)

                # Write new comment block
                if new_comment_block:
                    is_last = 1
                    block_header = struct.pack(
                        ">B", (is_last << 7) | 4
                    )  # VORBIS_COMMENT
                    block_header += struct.pack(">I", len(new_comment_block))[1:]
                    f.write(block_header)
                    f.write(new_comment_block)

            return True

        except Exception as e:
            logger.debug(f"Error writing FLAC metadata: {e}")
            return False

    def _write_ogg_metadata(self, file_path: str, new_comment_block: bytes) -> bool:
        """Replace Vorbis comment block in OGG file."""
        try:
            # OGG files are more complex - for now, we'll use a simplified approach
            # that may not work for all OGG files
            with open(file_path, "r+b") as f:
                file_data = f.read()

                # Find existing comment block
                comment_start = file_data.find(b"\x03vorbis")
                if comment_start == -1:
                    return False

                # Find the end of the comment block by looking for the next OGG page
                next_ogg = file_data.find(b"OggS", comment_start + 1)
                if next_ogg == -1:
                    return False

                # Replace the comment block
                new_file_data = (
                    file_data[:comment_start] + new_comment_block + file_data[next_ogg:]
                )

                f.seek(0)
                f.write(new_file_data)
                f.truncate()

            return True

        except Exception as e:
            logger.debug(f"Error writing OGG metadata: {e}")
            return False

    def write_metadata_to_track(
        self, track_id: int, mode: WriteMode = WriteMode.UPDATE_EXISTING
    ) -> bool:
        """Write metadata to a track's audio file using controller helpers."""
        try:
            # Get track using controller
            track = self.controller.get.get_entity_object("Track", track_id=track_id)
            if not track or not track.track_file_path:
                self.status_manager.show_message(
                    f"Track {track_id} has no file path", 3000
                )
                logger.debug(f"Track {track_id} has no file path")
                return False

            if not os.path.exists(track.track_file_path):
                self.status_manager.show_message(
                    f"File not found: {track.track_file_path}", 3000
                )
                logger.debug(f"Track file not found: {track.track_file_path}")
                return False

            return self.write_metadata_to_file(track_id, track.track_file_path, mode)
        except Exception as e:
            logger.debug(f"Error writing metadata to track {track_id}: {e}")
            self.status_manager.show_message(f"Error updating track {track_id}", 3000)
            return False

    def batch_write_metadata(
        self, track_ids: List[int], mode: WriteMode = WriteMode.UPDATE_EXISTING
    ) -> Dict[int, bool]:
        """Write metadata to multiple tracks with progress reporting."""
        results = {}
        total = len(track_ids)

        self.status_manager.start_task(f"Updating metadata for {total} files")

        success_count = 0
        for i, track_id in enumerate(track_ids):
            logger.debug(f"Processing track {i + 1}/{total} (ID: {track_id})")

            # Update status with progress
            progress_msg = f"Processing file {i + 1}/{total}"
            self.status_manager.show_message(progress_msg, 0)  # Persistent

            results[track_id] = self.write_metadata_to_track(track_id, mode)

            if results[track_id]:
                success_count += 1

        self.status_manager.end_task(
            f"Updated {success_count}/{total} files successfully", 5000
        )

        logger.debug(f"Batch write completed: {success_count}/{total} successful")

        return results
