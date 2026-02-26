import re
from typing import Any, Dict, List, Optional, Tuple

from src.metadata_mapping import (
    ID3_ALBUM_MAPPINGS,
    ID3_ARTIST_MAPPINGS,
    ID3_DATE_MAPPINGS,
    ID3_DISC_MAPPINGS,
    ID3_GENRE_MAPPINGS,
    ID3_MOOD_MAPPINGS,
    ID3_PUBLISHER_MAPPINGS,
    ID3_SPECIAL_MAPPINGS,
    ID3_TRACK_MAPPINGS,
    VORBIS_ALBUM_MAPPINGS,
    VORBIS_ARTIST_MAPPINGS,
    VORBIS_DATE_MAPPINGS,
    VORBIS_DISC_MAPPINGS,
    VORBIS_GENRE_MAPPINGS,
    VORBIS_MOOD_MAPPINGS,
    VORBIS_PUBLISHER_MAPPINGS,
    VORBIS_SPECIAL_MAPPINGS,
    VORBIS_TRACK_MAPPINGS,
)
from src.logger_config import logger


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
        # Add more as needed
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
        if self.format_type not in ["id3", "vorbis"]:
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

        except Exception as e:
            logger.error(f"Error processing metadata for {self.filepath}: {e}")

        return normalized_data

    def _process_simple_mappings_with_filter(
        self, normalized_data: Dict[str, List[Dict[str, Any]]]
    ):
        """Process simple mappings but filter out already-processed PERFORMER tags."""
        mapping_sets = [
            ID3_TRACK_MAPPINGS if self.format_type == "id3" else VORBIS_TRACK_MAPPINGS,
            ID3_ALBUM_MAPPINGS if self.format_type == "id3" else VORBIS_ALBUM_MAPPINGS,
            ID3_DISC_MAPPINGS if self.format_type == "id3" else VORBIS_DISC_MAPPINGS,
            ID3_PUBLISHER_MAPPINGS
            if self.format_type == "id3"
            else VORBIS_PUBLISHER_MAPPINGS,
            ID3_GENRE_MAPPINGS if self.format_type == "id3" else VORBIS_GENRE_MAPPINGS,
            ID3_MOOD_MAPPINGS if self.format_type == "id3" else VORBIS_MOOD_MAPPINGS,
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
        artist_mappings = (
            ID3_ARTIST_MAPPINGS if self.format_type == "id3" else VORBIS_ARTIST_MAPPINGS
        )

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
        special_mappings = (
            ID3_SPECIAL_MAPPINGS
            if self.format_type == "id3"
            else VORBIS_SPECIAL_MAPPINGS
        )

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
        date_mappings = (
            ID3_DATE_MAPPINGS if self.format_type == "id3" else VORBIS_DATE_MAPPINGS
        )

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

        except Exception as e:
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

        except Exception as e:
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
