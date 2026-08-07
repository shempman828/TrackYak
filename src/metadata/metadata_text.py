import re
from typing import Any, Dict, List, Optional, Tuple

from src.core.logger_config import logger
from src.metadata.metadata_mapping import (
    ID3_ALBUM_MAPPINGS,
    ID3_ARTIST_MAPPINGS,
    ID3_DATE_MAPPINGS,
    ID3_DISC_MAPPINGS,
    ID3_GENRE_MAPPINGS,
    ID3_MOOD_MAPPINGS,
    ID3_PUBLISHER_MAPPINGS,
    ID3_SPECIAL_MAPPINGS,
    ID3_TRACK_MAPPINGS,
    MP4_ALBUM_MAPPINGS,
    MP4_ARTIST_MAPPINGS,
    MP4_DATE_MAPPINGS,
    MP4_DISC_MAPPINGS,
    MP4_GENRE_MAPPINGS,
    MP4_MOOD_MAPPINGS,
    MP4_PUBLISHER_MAPPINGS,
    MP4_SPECIAL_MAPPINGS,
    MP4_TRACK_MAPPINGS,
    VORBIS_ALBUM_MAPPINGS,
    VORBIS_ARTIST_MAPPINGS,
    VORBIS_DATE_MAPPINGS,
    VORBIS_DISC_MAPPINGS,
    VORBIS_GENRE_MAPPINGS,
    VORBIS_MOOD_MAPPINGS,
    VORBIS_PUBLISHER_MAPPINGS,
    VORBIS_SPECIAL_MAPPINGS,
    VORBIS_TRACK_MAPPINGS,
    WAV_ALBUM_MAPPINGS,
    WAV_ARTIST_MAPPINGS,
    WAV_DATE_MAPPINGS,
    WAV_DISC_MAPPINGS,
    WAV_GENRE_MAPPINGS,
    WAV_MOOD_MAPPINGS,
    WAV_PUBLISHER_MAPPINGS,
    WAV_SPECIAL_MAPPINGS,
    WAV_TRACK_MAPPINGS,
)


def format_track_number(track: Any) -> Optional[str]:
    """Build the track-number string to write to file metadata. When the
    track has a vinyl side (e.g. "B"), it's prefixed onto the number
    (side "B" + track_number 1 -> "B1"), matching how records are labeled.
    Falls back to a plain number string when there's no side.
    """
    track_number = getattr(track, "track_number", None)
    if track_number is None:
        return None
    side = getattr(track, "side", None)
    if side:
        return f"{side}{track_number}"
    return str(track_number)


def build_iso_date_string(entity: Any, fields: List[str]) -> Optional[str]:
    """Build a YYYY[-MM[-DD]] date string from up to 3 year/month/day DB
    columns on `entity`, named by `fields` (year field first, then month,
    then day). Stops at the first missing/falsy field, so a year-only or
    year+month row doesn't get a partial trailing gap. Returns None if
    even the year field is missing.
    """
    if not fields:
        return None

    parts = []
    for i, field in enumerate(fields):
        value = getattr(entity, field, None)
        if not value:
            break
        width = 4 if i == 0 else 2
        parts.append(str(value).zfill(width))

    return "-".join(parts) if parts else None


def group_artists_by_tag(
    artist_role_data: List[Dict[str, Any]],
    role_to_tag: Dict[str, str],
    id_tag_map: Dict[str, str],
    dedupe: bool = False,
) -> Tuple[Dict[str, List[str]], Dict[str, List[str]]]:
    """Group artist-role dicts (each with "role" -> object with .role_name,
    "credited_name", and optional "artist_mbid") by output tag/frame id,
    using `role_to_tag` to map role name -> tag. Returns
    (names_by_tag, mbids_by_tag): `mbids_by_tag` only gets entries for tags
    that appear in `id_tag_map` (its values are the paired MusicBrainz-ID
    tag/frame names), and only for artists that actually have an mbid.

    With `dedupe=True`, a repeated (tag, name) or (id_tag, mbid) pair is
    collapsed to its first occurrence - used by the Vorbis builder, which
    emits one tag entry per artist. The ID3 builder instead joins every
    name into a single string per frame, so it leaves dedupe=False and
    lets duplicates show up as repeated segments in that joined text,
    matching its pre-consolidation behavior.
    """
    names_by_tag: Dict[str, List[str]] = {}
    mbids_by_tag: Dict[str, List[str]] = {}

    for artist_data in artist_role_data:
        role_name = artist_data["role"].role_name
        tag = role_to_tag.get(role_name)
        name = artist_data.get("credited_name")
        if not tag or not name:
            continue

        names = names_by_tag.setdefault(tag, [])
        if not dedupe or name not in names:
            names.append(name)

        id_tag = id_tag_map.get(tag)
        mbid = artist_data.get("artist_mbid")
        if id_tag and mbid:
            mbids = mbids_by_tag.setdefault(id_tag, [])
            if not dedupe or mbid not in mbids:
                mbids.append(mbid)

    return names_by_tag, mbids_by_tag


class TextMetadataExtractor:
    """Extracts and normalizes metadata from audio files using mapping definitions."""

    # File extension to format mapping
    FILE_FORMAT_MAPPING = {
        # ID3 formats
        "mp3": "id3",
        "aiff": "id3",
        "aif": "id3",
        # Vorbis formats
        "flac": "vorbis",
        "ogg": "vorbis",
        "oga": "vorbis",
        "opus": "vorbis",
        "spx": "vorbis",
        # MP4 atom formats
        "m4a": "mp4",
        "m4b": "mp4",
        "mp4": "mp4",
        "aac": "mp4",
        # RIFF WAV INFO chunk
        "wav": "wav",
    }

    # Per-format-type mapping-set lookups, replacing a growing id3/vorbis
    # ternary as more container formats are supported.
    TRACK_MAPPINGS_BY_FORMAT = {
        "id3": ID3_TRACK_MAPPINGS,
        "vorbis": VORBIS_TRACK_MAPPINGS,
        "mp4": MP4_TRACK_MAPPINGS,
        "wav": WAV_TRACK_MAPPINGS,
    }
    ALBUM_MAPPINGS_BY_FORMAT = {
        "id3": ID3_ALBUM_MAPPINGS,
        "vorbis": VORBIS_ALBUM_MAPPINGS,
        "mp4": MP4_ALBUM_MAPPINGS,
        "wav": WAV_ALBUM_MAPPINGS,
    }
    DISC_MAPPINGS_BY_FORMAT = {
        "id3": ID3_DISC_MAPPINGS,
        "vorbis": VORBIS_DISC_MAPPINGS,
        "mp4": MP4_DISC_MAPPINGS,
        "wav": WAV_DISC_MAPPINGS,
    }
    PUBLISHER_MAPPINGS_BY_FORMAT = {
        "id3": ID3_PUBLISHER_MAPPINGS,
        "vorbis": VORBIS_PUBLISHER_MAPPINGS,
        "mp4": MP4_PUBLISHER_MAPPINGS,
        "wav": WAV_PUBLISHER_MAPPINGS,
    }
    GENRE_MAPPINGS_BY_FORMAT = {
        "id3": ID3_GENRE_MAPPINGS,
        "vorbis": VORBIS_GENRE_MAPPINGS,
        "mp4": MP4_GENRE_MAPPINGS,
        "wav": WAV_GENRE_MAPPINGS,
    }
    MOOD_MAPPINGS_BY_FORMAT = {
        "id3": ID3_MOOD_MAPPINGS,
        "vorbis": VORBIS_MOOD_MAPPINGS,
        "mp4": MP4_MOOD_MAPPINGS,
        "wav": WAV_MOOD_MAPPINGS,
    }
    ARTIST_MAPPINGS_BY_FORMAT = {
        "id3": ID3_ARTIST_MAPPINGS,
        "vorbis": VORBIS_ARTIST_MAPPINGS,
        "mp4": MP4_ARTIST_MAPPINGS,
        "wav": WAV_ARTIST_MAPPINGS,
    }
    SPECIAL_MAPPINGS_BY_FORMAT = {
        "id3": ID3_SPECIAL_MAPPINGS,
        "vorbis": VORBIS_SPECIAL_MAPPINGS,
        "mp4": MP4_SPECIAL_MAPPINGS,
        "wav": WAV_SPECIAL_MAPPINGS,
    }
    DATE_MAPPINGS_BY_FORMAT = {
        "id3": ID3_DATE_MAPPINGS,
        "vorbis": VORBIS_DATE_MAPPINGS,
        "mp4": MP4_DATE_MAPPINGS,
        "wav": WAV_DATE_MAPPINGS,
    }

    def __init__(self, filepath: str, file_extension: str, raw_tags: Dict[str, Any]):
        self.filepath = filepath
        self.file_extension = file_extension.lower().lstrip(".")
        self.raw_tags = raw_tags
        self.format_type = self._determine_format_type()

    def _determine_format_type(self) -> str:
        """Determine the metadata format based on file extension."""
        return self.FILE_FORMAT_MAPPING.get(self.file_extension, "unknown")

    def extract_metadata(self) -> Dict[str, List[Dict[str, Any]]]:
        """
        Extract and normalize metadata from raw tags.
        """
        if self.format_type not in self.TRACK_MAPPINGS_BY_FORMAT:
            logger.warning(
                f"Unsupported file format: {self.format_type} for {self.filepath}"
            )
            return {}

        normalized_data = {}

        try:
            # Track which PERFORMER values we've already processed
            self.processed_performers = set()

            # 1. Process PERFORMER tag first with special handling
            if self.format_type == "vorbis" and "PERFORMER" in self.raw_tags:
                self._process_performer_tag(normalized_data)
                # Mark these values as processed
                values = self._get_tag_values("PERFORMER")
                self.processed_performers.update(values)

            # 2. Process other simple mappings (but skip PERFORMER if we already handled it)
            self._process_simple_mappings_with_filter(normalized_data)

            # 3. Process artist mappings (but skip PERFORMER if we already handled it)
            self._process_artist_mappings_with_filter(normalized_data)

            # 4. Process other special mappings
            self._process_special_mappings(normalized_data)

            # 5. Process date mappings
            self._process_date_mappings(normalized_data)

        except (KeyError, IndexError, re.error) as e:
            logger.error(f"Error processing metadata for {self.filepath}: {e}")

        return normalized_data

    def _process_simple_mappings_with_filter(
        self, normalized_data: Dict[str, List[Dict[str, Any]]]
    ):
        """Process simple mappings but filter out already-processed PERFORMER tags."""
        mapping_sets = [
            self.TRACK_MAPPINGS_BY_FORMAT[self.format_type],
            self.ALBUM_MAPPINGS_BY_FORMAT[self.format_type],
            self.DISC_MAPPINGS_BY_FORMAT[self.format_type],
            self.PUBLISHER_MAPPINGS_BY_FORMAT[self.format_type],
            self.GENRE_MAPPINGS_BY_FORMAT[self.format_type],
            self.MOOD_MAPPINGS_BY_FORMAT[self.format_type],
        ]

        for mapping_set in mapping_sets:
            for tag_key, mapping in mapping_set.items():
                # Skip PERFORMER if we already processed it
                if tag_key == "PERFORMER" and tag_key in self.processed_performers:
                    continue

                if tag_key in self.raw_tags:
                    values = self._get_tag_values(tag_key)
                    for value in values:
                        self._add_normalized_field(normalized_data, mapping, value)

    def _process_artist_mappings_with_filter(
        self, normalized_data: Dict[str, List[Dict[str, Any]]]
    ):
        """Process artist mappings but filter out already-processed PERFORMER tags."""
        artist_mappings = self.ARTIST_MAPPINGS_BY_FORMAT[self.format_type]

        for tag_key, mapping in artist_mappings.items():
            # Skip PERFORMER if we already processed it specially
            if tag_key == "PERFORMER" and self.format_type == "vorbis":
                # Check if any raw PERFORMER values haven't been processed yet
                if tag_key in self.raw_tags:
                    raw_values = self._get_tag_values(tag_key)
                    unprocessed_values = [
                        v for v in raw_values if v not in self.processed_performers
                    ]

                    if unprocessed_values:
                        # Process unprocessed values with the simple mapping
                        for value in unprocessed_values:
                            field_data = mapping.copy()
                            field_data["value"] = value
                            field_data["role"] = mapping.get("role", "Performer")
                            field_data["source"] = "simple_mapping"

                            entity = mapping["entity"]
                            if entity not in normalized_data:
                                normalized_data[entity] = []
                            normalized_data[entity].append(field_data)
                continue

            # Normal processing for other tags
            if tag_key in self.raw_tags:
                values = self._get_tag_values(tag_key)
                for value in values:
                    field_data = mapping.copy()
                    field_data["value"] = value
                    if "role_name" in mapping:  # ID3
                        field_data["role"] = mapping["role_name"]
                    elif "role" in mapping:  # Vorbis
                        field_data["role"] = mapping["role"]

                    entity = mapping["entity"]
                    if entity not in normalized_data:
                        normalized_data[entity] = []
                    normalized_data[entity].append(field_data)

    def _process_special_mappings(
        self, normalized_data: Dict[str, List[Dict[str, Any]]]
    ):
        """Process special mappings that require custom parsing."""
        special_mappings = self.SPECIAL_MAPPINGS_BY_FORMAT[self.format_type]

        for tag_key, mapping in special_mappings.items():
            if tag_key in self.raw_tags:
                values = self._get_tag_values(tag_key)
                for value in values:
                    if self.format_type == "id3":
                        self._parse_id3_special_mapping(normalized_data, mapping, value)
                    else:
                        self._parse_vorbis_special_mapping(
                            normalized_data, mapping, value
                        )

    def _process_date_mappings(self, normalized_data: Dict[str, List[Dict[str, Any]]]):
        """Process date mappings with proper splitting."""
        date_mappings = self.DATE_MAPPINGS_BY_FORMAT[self.format_type]

        for tag_key, mapping in date_mappings.items():
            if tag_key in self.raw_tags:
                values = self._get_tag_values(tag_key)
                for value in values:
                    self._parse_date_mapping(normalized_data, mapping, value)

    def _parse_id3_special_mapping(
        self,
        normalized_data: Dict[str, List[Dict[str, Any]]],
        mapping: Dict[str, Any],
        value: str,
    ):
        """Parse ID3 special mappings like TMCL/TIPL."""
        # TMCL/TIPL format: "role1,artist1,role2,artist2,..."
        if "separator" in mapping:
            parts = [part.strip() for part in value.split(mapping["separator"])]
            # Process in pairs: role, artist, role, artist, ...
            for i in range(0, len(parts) - 1, 2):
                if i + 1 < len(parts):
                    role = parts[i]
                    artist = parts[i + 1]
                    self._add_normalized_field(
                        normalized_data,
                        {
                            "field": mapping["artist_field"],
                            "type": "str",
                            "entity": mapping["entity"],
                        },
                        artist,
                        additional_data={"role": role},
                    )

    def _parse_vorbis_special_mapping(
        self,
        normalized_data: Dict[str, List[Dict[str, Any]]],
        mapping: Dict[str, Any],
        value: str,
    ):
        """Parse Vorbis special mappings like PERFORMER with pattern."""
        if "patterns" in mapping:
            artist = value.strip()
            role = mapping.get("default_role", "Performer")

            # Try each pattern
            for pattern in mapping["patterns"]:
                match = re.match(pattern, value)
                if match:
                    artist = match.group("artist").strip()
                    # If pattern has a role group, use it
                    if "role" in match.groupdict():
                        role = match.group("role").strip()
                    break  # Use first matching pattern

            self._add_normalized_field(
                normalized_data,
                {
                    "field": mapping["artist_field"],
                    "type": "str",
                    "entity": mapping["entity"],
                },
                artist,
                additional_data={"role": role},
            )

    def _parse_date_mapping(
        self,
        normalized_data: Dict[str, List[Dict[str, Any]]],
        mapping: Dict[str, Any],
        value: str,
    ):
        """Parse date mappings into year/month/day components."""
        try:
            # Handle different date formats
            if mapping["type"] == "year":
                # Just year
                year = self._safe_int(value.strip())
                if year:
                    field_data = {
                        "field": mapping["fields"][0],  # e.g., "release_year"
                        "value": year,
                        "type": "int",
                        "entity": mapping["entity"],
                    }
                    self._add_to_entity(normalized_data, mapping["entity"], field_data)

            elif mapping["type"] == "date":
                # Try to parse as YYYY, YYYY-MM, or YYYY-MM-DD
                parts = value.strip().split("-")

                # Map parts to field names
                field_mapping = {
                    0: mapping["fields"][0],  # year field e.g., "release_year"
                    1: mapping["fields"][1]
                    if len(mapping["fields"]) > 1
                    else None,  # month field
                    2: mapping["fields"][2]
                    if len(mapping["fields"]) > 2
                    else None,  # day field
                }

                # Add each component that exists
                for i in range(len(parts)):
                    field_name = field_mapping.get(i)
                    if field_name and parts[i].strip():
                        field_value = self._safe_int(parts[i].strip())
                        if field_value is not None:
                            field_data = {
                                "field": field_name,
                                "value": field_value,
                                "type": "int",
                                "entity": mapping["entity"],
                            }
                            self._add_to_entity(
                                normalized_data, mapping["entity"], field_data
                            )

        except KeyError as e:
            logger.warning(f"Error parsing date '{value}': {e}")

    def _get_tag_values(self, tag_key: str) -> List[str]:
        """Get tag values, handling both single values and lists."""
        value = self.raw_tags[tag_key]

        logger.debug(f"Raw tag value for '{tag_key}': {value} (type: {type(value)})")

        if isinstance(value, list):
            # Already a list - process each item
            return [str(v).strip() for v in value if v and str(v).strip()]
        else:
            return [str(value)]

    def _add_normalized_field(
        self,
        normalized_data: Dict[str, List[Dict[str, Any]]],
        mapping: Dict[str, Any],
        value: str,
        additional_data: Optional[Dict[str, Any]] = None,
    ):
        """Add a normalized field to the output data with type conversion."""
        try:
            # Convert value to appropriate type
            converted_value = self._convert_value(value, mapping.get("type", "str"))

            field_data = {
                "field": mapping["field"],
                "value": converted_value,
                "type": mapping.get("type", "str"),
                "entity": mapping["entity"],
            }

            # Add any additional data (like roles)
            if additional_data:
                field_data.update(additional_data)

            self._add_to_entity(normalized_data, mapping["entity"], field_data)

        except KeyError as e:
            logger.warning(
                f"Error processing field {mapping['field']} with value '{value}': {e}"
            )

    def _add_to_entity(
        self,
        normalized_data: Dict[str, List[Dict[str, Any]]],
        entity: str,
        field_data: Dict[str, Any],
    ):
        """Add field data to the appropriate entity list."""
        if entity not in normalized_data:
            normalized_data[entity] = []
        normalized_data[entity].append(field_data)

    def _convert_value(self, value: str, target_type: Any) -> Any:
        """Convert string value to target type safely."""
        # Handle both string type names and actual type objects
        if target_type in [int, "int"]:
            return self._safe_int(value)
        elif target_type in [float, "float"]:
            return self._safe_float(value)
        elif target_type in [str, "str"]:
            return value.strip()
        else:
            return value  # Return as-is for unknown types

    def _safe_int(self, value: str) -> Optional[int]:
        """Safely convert to int, returning None on failure."""
        try:
            # Handle common cases like "1/10" by taking first part
            if "/" in value:
                value = value.split("/")[0]
            return int(float(value))  # Handle "1.0" case
        except (ValueError, TypeError):
            return None

    def _safe_float(self, value: str) -> Optional[float]:
        """Safely convert to float, returning None on failure."""
        try:
            if isinstance(value, str):
                # Remove all characters except digits, decimal points, and minus signs
                # This handles cases like: "-4.9 dB", "+2.1dB", "3,5" (European decimal), etc.
                clean_value = re.sub(r"[^\d\.\-+]", "", value)

                # Handle European decimal commas by converting to points
                if "," in clean_value and "." not in clean_value:
                    clean_value = clean_value.replace(",", ".")

                # Remove any extra minus signs (keep only the first one if multiple exist)
                if clean_value.count("-") > 1:
                    parts = clean_value.split("-")
                    clean_value = "-" + "".join(parts[1:]).replace("-", "")

                # Remove any plus signs (they're redundant for float conversion)
                clean_value = clean_value.replace("+", "")

                # Ensure we don't have empty strings or just punctuation
                if not clean_value or clean_value in [".", "-", "-."]:
                    return None

                return float(clean_value)
            else:
                return float(value)
        except (ValueError, TypeError, AttributeError) as e:
            logger.debug(f"Could not convert '{value}' to float: {e}")
            return None

    def _process_performer_tag(self, normalized_data: Dict[str, List[Dict[str, Any]]]):
        """Special handling for PERFORMER tag which can be in multiple formats."""
        if "PERFORMER" not in self.raw_tags:
            return

        values = self._get_tag_values("PERFORMER")

        for value in values:
            # Try to parse with all known formats
            parsed = self._parse_performer_value(value)

            if parsed:
                artist, role = parsed
                # Add to normalized data
                field_data = {
                    "field": "artist_name",
                    "value": artist,
                    "type": "str",
                    "entity": "Artist",
                    "role": role,
                    "source_tag": "PERFORMER",
                    "parsed_format": "special"
                    if "(" in value or ":" in value or " - " in value
                    else "simple",
                }

                entity = "Artist"
                if entity not in normalized_data:
                    normalized_data[entity] = []
                normalized_data[entity].append(field_data)

    def _parse_performer_value(self, value: str) -> Optional[Tuple[str, str]]:
        """Parse performer value using multiple pattern formats.
        Returns (artist_name, role_name) or None if parsing fails.
        """
        patterns = [
            # 1. MusicBrainz format: "Artist (Role)"
            (r"^(?P<artist>.+?)\s*\((?P<role>.+)\)$", None),
            # 2. Role: Artist format
            (r"^(?P<role>.+?):\s*(?P<artist>.+)$", None),
            # 3. Artist - Role format
            (r"^(?P<artist>.+?)\s*-\s*(?P<role>.+)$", None),
            # 4. Artist with role in square brackets
            (r"^(?P<artist>.+?)\s*\[(?P<role>.+)\]$", None),
            # 5. Common role abbreviations
            (
                r"^(?P<artist>.+?)\s*\((?P<abbr>voc|vox|dr|gtr|bass|keys|cond|arr)\)$",
                lambda m: (
                    m.group("artist"),
                    self._expand_abbreviation(m.group("abbr")),
                ),
            ),
            # 6. Just artist name (fallback)
            (r"^(?P<artist>.+)$", lambda m: (m.group("artist"), "Performer")),
        ]

        for pattern, processor in patterns:
            match = re.match(pattern, value, re.IGNORECASE)
            if match:
                if processor:
                    return processor(match)
                else:
                    return match.group("artist").strip(), match.group("role").strip()

        return None

    def _expand_abbreviation(self, abbr: str) -> str:
        """Expand common role abbreviations."""
        expansions = {
            "voc": "Vocalist",
            "vox": "Vocalist",
            "dr": "Drummer",
            "drm": "Drummer",
            "gtr": "Guitarist",
            "git": "Guitarist",
            "bass": "Bassist",
            "keys": "Keyboardist",
            "cond": "Conductor",
            "arr": "Arranger",
            "prod": "Producer",
            "mix": "Mixer",
            "eng": "Engineer",
        }
        return expansions.get(abbr.lower(), abbr.title())


def flatten_text_metadata(
    text_metadata: Dict[str, List[Dict[str, Any]]],
) -> Dict[str, Any]:
    """
    Convert TextMetadataExtractor.extract_metadata()'s grouped-by-entity
    structure into the flat field-name dict the rest of the app (import
    mapping, track/album creation) works with.
    """
    flattened = {}

    for entity_type, fields_list in text_metadata.items():
        for field_data in fields_list:
            field_name = field_data["field"]
            entity = field_data["entity"]
            value = field_data["value"]

            # Handle different entity types with proper list management
            if entity == "Track":
                # For track fields, use lists for multi-value capable fields
                multi_value_track_fields = {
                    "comment",
                    "lyrics",
                }  # Add others as needed
                if field_name in multi_value_track_fields:
                    if field_name not in flattened:
                        flattened[field_name] = []
                    if not isinstance(flattened[field_name], list):
                        flattened[field_name] = [flattened[field_name]]
                    flattened[field_name].append(value)
                else:
                    flattened[field_name] = value  # Single value for most track fields

            elif entity == "Album":
                # For album fields, use lists for multi-value capable fields
                multi_value_album_fields = {"album_description"}  # Add others as needed
                # Some mapping "field" names already start with "album_"
                # (e.g. album_language, album_gain) — don't double-prefix
                # those, or downstream readers looking up the un-doubled
                # key (e.g. metadata.get("album_language")) never find it.
                album_field_name = (
                    field_name
                    if field_name.startswith("album_")
                    else f"album_{field_name}"
                )
                if field_name in multi_value_album_fields:
                    if album_field_name not in flattened:
                        flattened[album_field_name] = []
                    if not isinstance(flattened[album_field_name], list):
                        flattened[album_field_name] = [flattened[album_field_name]]
                    flattened[album_field_name].append(value)
                else:
                    flattened[album_field_name] = (
                        value  # Single value for most album fields
                    )

            elif entity == "Artist":
                # For artists, handle roles properly
                role = field_data.get("role", "primary").lower().replace(" ", "_")
                role_key = f"artist_{role}"

                if role_key not in flattened:
                    flattened[role_key] = []

                # Ensure we're working with a list
                if not isinstance(flattened[role_key], list):
                    flattened[role_key] = [flattened[role_key]]

                flattened[role_key].append(value)

                # Also populate the main artist_name for primary artists
                if role == "primary_artist":
                    if "artist_name" not in flattened:
                        flattened["artist_name"] = []
                    if not isinstance(flattened["artist_name"], list):
                        flattened["artist_name"] = [flattened["artist_name"]]
                    flattened["artist_name"].append(value)

            elif entity == "Genre":
                if "genre_name" not in flattened:
                    flattened["genre_name"] = []
                if not isinstance(flattened["genre_name"], list):
                    flattened["genre_name"] = [flattened["genre_name"]]
                flattened["genre_name"].append(value)

            elif entity == "Publisher":
                # Handle multiple publishers
                if "publisher_name" not in flattened:
                    flattened["publisher_name"] = []
                if not isinstance(flattened["publisher_name"], list):
                    flattened["publisher_name"] = [flattened["publisher_name"]]
                flattened["publisher_name"].append(value)

            elif entity == "Disc":
                flattened[field_name] = value  # Usually single values

            elif entity == "Mood":
                if "mood_name" not in flattened:
                    flattened["mood_name"] = []
                if not isinstance(flattened["mood_name"], list):
                    flattened["mood_name"] = [flattened["mood_name"]]
                flattened["mood_name"].append(value)

            elif entity == "Place":
                if "place_name" not in flattened:
                    flattened["place_name"] = []
                if not isinstance(flattened["place_name"], list):
                    flattened["place_name"] = [flattened["place_name"]]
                flattened["place_name"].append(value)

            else:
                # For any other entity, use the field name directly
                flattened[field_name] = value

    # Ensure critical fields exist and are lists where expected
    list_fields = [
        "artist_name",
        "album_artist_name",
        "genre_name",
        "mood_name",
        "place_name",
        "publisher_name",
    ]

    # Also add any artist role fields that should be lists
    artist_role_fields = [key for key in flattened.keys() if key.startswith("artist_")]
    list_fields.extend(artist_role_fields)

    for field in list_fields:
        if field in flattened and not isinstance(flattened[field], list):
            flattened[field] = [flattened[field]]
        elif field not in flattened:
            flattened[field] = []

    return flattened
