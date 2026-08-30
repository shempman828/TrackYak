import struct

from src.core.logger_config import logger
from src.metadata.metadata_byte_utils import syncsafe_to_int
from src.metadata.metadata_mp4_atoms import find_atom, iter_atoms
from src.metadata.metadata_ogg_pages import iter_packets


class RawTagExtractor:
    """Extracts raw (unmapped) tag key/value pairs from audio file bytes,
    one reader per container format.

    This class only knows each format's on-disk tag structure (ID3 frame
    IDs, Vorbis comment keys, MP4 atom names, RIFF INFO chunk IDs) — it has
    no idea what any of those keys *mean*. Turning e.g. "TIT2" or "©nam"
    into Track.track_name is TextMetadataExtractor's job
    (metadata_text.py), driven by the mapping tables in metadata_mapping.py.
    """

    # AIFF's own chunk IDs, mapped to the ID3 frame IDs that
    # ID3_TRACK_MAPPINGS/ID3_ARTIST_MAPPINGS/ID3_ALBUM_MAPPINGS already
    # understand — AIFF resolves to the "id3" format type in
    # TextMetadataExtractor, so raw tags need to speak that vocabulary
    # rather than AIFF's native chunk names.
    _AIFF_CHUNK_TO_ID3 = {b"NAME": "TIT2", b"AUTH": "TPE1", b"(c) ": "TCOP", b"ANNO": "COMM"}

    def __init__(self):
        self.format_handlers = {
            ".mp3": self._extract_id3_tags,
            ".flac": self._extract_flac_tags,
            ".fla": self._extract_flac_tags,
            ".ogg": self._extract_ogg_tags,
            ".oga": self._extract_ogg_tags,
            ".opus": self._extract_ogg_tags,
            ".spx": self._extract_ogg_tags,
            ".m4a": self._extract_mp4_tags,
            ".m4b": self._extract_mp4_tags,
            ".mp4": self._extract_mp4_tags,
            ".aac": self._extract_mp4_tags,
            ".wav": self._extract_wav_tags,
            ".aiff": self._extract_aiff_tags,
            ".aif": self._extract_aiff_tags,
        }

    def extract_raw_tags(self, data: bytes, file_ext: str) -> dict:
        """Extract raw tags from file bytes for whichever format file_ext
        names. Returns {} for unsupported formats or on any parse error —
        every per-format reader below already isolates its own internal
        failures, so this is a last-resort safety net."""
        handler = self.format_handlers.get(file_ext.lower())
        if not handler:
            logger.warning(f"Unsupported file format for raw tag extraction: {file_ext}")
            return {}

        try:
            return handler(data)
        except AttributeError as e:
            logger.warning(f"Error extracting raw tags: {e}")
            return {}

    # ------------------------------------------------------------------ ID3
    # (MP3, and AIFF via _AIFF_CHUNK_TO_ID3 remapping)

    def _extract_id3_tags(self, data):
        """Extract raw ID3 tags without mapping."""
        raw_tags = {}

        try:
            # ID3v2 extraction
            if len(data) >= 10 and data[0:3] == b"ID3":
                version_major = data[3]
                size = syncsafe_to_int(data[6:10])
                frame_data = data[10 : 10 + size]

                if version_major == 2:
                    raw_tags.update(self._parse_id3v2_2_frames(frame_data))
                elif version_major in [3, 4]:
                    raw_tags.update(self._parse_id3v2_3_4_frames(frame_data, version_major))

            # ID3v1 extraction (fallback)
            if len(data) >= 128 and data[-128:-125] == b"TAG":
                raw_tags.update(self._parse_id3v1_tags(data[-128:]))

        except (IndexError, struct.error) as e:
            logger.warning(f"Error extracting raw ID3 tags: {e}")

        return raw_tags

    def _parse_id3v2_2_frames(self, frame_data):
        """Parse raw ID3v2.2 frames."""
        raw_tags = {}
        pos = 0

        while pos < len(frame_data) - 6:
            frame_id = frame_data[pos : pos + 3].decode("ascii", errors="ignore")
            frame_size = struct.unpack(">I", b"\x00" + frame_data[pos + 3 : pos + 6])[0]

            if frame_size == 0:
                break

            frame_content = frame_data[pos + 6 : pos + 6 + frame_size]
            raw_tags[frame_id] = self._decode_id3_text(frame_content)

            pos += 6 + frame_size

        return raw_tags

    def _parse_id3v2_3_4_frames(self, frame_data, version):
        """Parse raw ID3v2.3/2.4 frames."""
        raw_tags = {}
        pos = 0

        while pos < len(frame_data) - 10:
            frame_id = frame_data[pos : pos + 4].decode("ascii", errors="ignore")

            if b"\x00" in frame_id.encode("ascii"):
                break

            if version == 3:
                frame_size = struct.unpack(">I", frame_data[pos + 4 : pos + 8])[0]
            else:
                frame_size = syncsafe_to_int(frame_data[pos + 4 : pos + 8])

            if frame_size == 0:
                break

            frame_content = frame_data[pos + 10 : pos + 10 + frame_size]

            if frame_id == "UFID":
                # UFID structure: owner identifier <text> $00 + identifier
                # <binary, up to 64 bytes> — unlike TXXX/text frames, there
                # is no leading text-encoding byte. Picard writes the
                # MusicBrainz recording (track) ID here, owner
                # "http://musicbrainz.org", identifier as ASCII text.
                try:
                    sep = frame_content.find(b"\x00")
                    if sep == -1:
                        sep = len(frame_content)
                    owner = frame_content[:sep].decode("latin-1", errors="ignore")
                    identifier = (
                        frame_content[sep + 1 :].decode("ascii", errors="ignore").strip("\x00")
                    )

                    storage_key = f"UFID:{owner}" if owner else "UFID"
                    if storage_key not in raw_tags:
                        raw_tags[storage_key] = []
                    raw_tags[storage_key].append(identifier)

                except AttributeError as e:
                    logger.debug(f"Error parsing UFID frame: {e}")
            elif frame_id == "TXXX":
                # TXXX structure: encoding(1) + description(variable) + \x00[\x00] + value
                # We need to extract the description to build the storage key.
                try:
                    encoding = frame_content[0] if frame_content else 0
                    rest = frame_content[1:]  # everything after the encoding byte

                    if encoding in (0x01, 0x02):
                        # UTF-16: null terminator is \x00\x00
                        sep = rest.find(b"\x00\x00")
                        if sep == -1:
                            sep = len(rest)
                        raw_desc = rest[:sep]
                        raw_val = rest[sep + 2 :]  # skip the 2-byte null terminator
                        description = raw_desc.decode("utf-16be", errors="ignore").strip("\x00")
                        value = raw_val.decode("utf-16be", errors="ignore").strip("\x00")
                    else:
                        # ISO-8859-1 or UTF-8: null terminator is \x00
                        sep = rest.find(b"\x00")
                        if sep == -1:
                            sep = len(rest)
                        description = rest[:sep].decode("latin-1", errors="ignore").strip()
                        value = (
                            rest[sep + 1 :]
                            .decode("utf-8" if encoding == 0x03 else "latin-1", errors="ignore")
                            .strip("\x00")
                        )

                    # Store under "TXXX:description" so TXXX:PLAYLIST is preserved
                    storage_key = f"TXXX:{description}" if description else "TXXX"
                    if storage_key not in raw_tags:
                        raw_tags[storage_key] = []
                    raw_tags[storage_key].append(value)

                except AttributeError as e:
                    logger.debug(f"Error parsing TXXX frame: {e}")
            else:
                value = self._decode_id3_text(frame_content)

                if frame_id not in raw_tags:
                    raw_tags[frame_id] = []

                raw_tags[frame_id].append(value)

            pos += 10 + frame_size

        return raw_tags

    def _parse_id3v1_tags(self, tag_data):
        """Parse raw ID3v1 tags."""
        return {
            "TIT2": self._strip_null(tag_data[3:33].decode("latin-1", errors="ignore")),
            "TPE1": self._strip_null(tag_data[33:63].decode("latin-1", errors="ignore")),
            "TALB": self._strip_null(tag_data[63:93].decode("latin-1", errors="ignore")),
            "TYER": self._strip_null(tag_data[93:97].decode("latin-1", errors="ignore")),
            "COMM": self._strip_null(tag_data[97:127].decode("latin-1", errors="ignore")),
        }

    def _decode_id3_text(self, data):
        if not data:
            return ""
        try:
            encoding = data[0]
            text_data = data[1:]
            if encoding == 0:  # ISO-8859-1
                return text_data.decode("latin-1", errors="ignore").strip("\x00")
            if encoding == 1:  # UTF-16 with BOM
                return text_data.decode("utf-16", errors="ignore").strip("\x00")
            if encoding == 3:  # UTF-8
                return text_data.decode("utf-8", errors="ignore").strip("\x00")
            return text_data.decode("latin-1", errors="ignore").strip("\x00")
        except (IndexError, TypeError):
            return data.decode("latin-1", errors="ignore").strip("\x00")

    def _strip_null(self, text):
        return text.strip("\x00")

    # --------------------------------------------------------- Vorbis comment
    # (shared structure behind FLAC's VORBIS_COMMENT block and Ogg
    # Vorbis/Opus's comment header packet)

    def _parse_vorbis_comments(self, data):
        """Parse a raw Vorbis-comment block: vendor string then
        key=value comment strings. Used by both FLAC and Ogg readers."""
        raw_tags = {}
        pos = 0

        try:
            # Skip vendor string
            vendor_len = struct.unpack("<I", data[pos : pos + 4])[0]
            pos += 4 + vendor_len

            # Comment count
            comment_count = struct.unpack("<I", data[pos : pos + 4])[0]
            pos += 4

            for _ in range(comment_count):
                comment_len = struct.unpack("<I", data[pos : pos + 4])[0]
                pos += 4

                comment = data[pos : pos + comment_len].decode("utf-8", errors="ignore")
                pos += comment_len

                if "=" in comment:
                    key, value = comment.split("=", 1)
                    key_upper = key.upper()

                    if key_upper not in raw_tags:
                        raw_tags[key_upper] = []

                    raw_tags[key_upper].append(value)

        except struct.error as e:
            logger.warning(f"Error parsing raw Vorbis comments: {e}")

        return raw_tags

    # ----------------------------------------------------------------- FLAC

    def _extract_flac_tags(self, data):
        """Extract raw FLAC tags without mapping."""
        raw_tags = {}

        try:
            if data[0:4] == b"fLaC":
                pos = 4
                while pos < len(data) - 4:
                    header = struct.unpack(">I", data[pos : pos + 4])[0]
                    pos += 4

                    is_last = (header >> 31) & 1
                    block_type = (header >> 24) & 0x7F
                    block_size = header & 0xFFFFFF

                    if block_type == 4:  # VORBIS_COMMENT
                        raw_tags.update(self._parse_vorbis_comments(data[pos : pos + block_size]))

                    if is_last:
                        break
                    pos += block_size

            logger.debug(f"Raw FLAC tags extracted: {raw_tags}")

        except (IndexError, struct.error) as e:
            logger.warning(f"Error extracting raw FLAC tags: {e}")

        return raw_tags

    # ------------------------------------------------------------------ Ogg

    def _extract_ogg_tags(self, data):
        """Extract raw Vorbis-comment tags from an Ogg Vorbis or Ogg Opus
        container's second packet (the comment header). Both formats use
        the same comment-header layout as FLAC's VORBIS_COMMENT block,
        just behind a different magic prefix."""
        raw_tags = {}

        try:
            packets = list(iter_packets(data, max_packets=2))
            if len(packets) < 2:
                return raw_tags

            comment_packet = packets[1]
            if comment_packet[0:7] == b"\x03vorbis":
                raw_tags.update(self._parse_vorbis_comments(comment_packet[7:]))
            elif comment_packet[0:8] == b"OpusTags":
                raw_tags.update(self._parse_vorbis_comments(comment_packet[8:]))

        except (IndexError, struct.error) as e:
            logger.warning(f"Error extracting raw Ogg tags: {e}")

        return raw_tags

    # ------------------------------------------------------------------ MP4

    def _extract_mp4_tags(self, data):
        """Extract raw MP4/M4A tags from moov/udta/meta/ilst without mapping.

        Only text-value atoms are handled generically; trkn/disk are
        (index, total) integer pairs and are decoded specially.
        """
        raw_tags = {}

        try:
            end = len(data)
            moov = find_atom(data, b"moov", 0, end)
            if not moov:
                return raw_tags

            udta = find_atom(data, b"udta", *moov)
            if not udta:
                return raw_tags

            meta = find_atom(data, b"meta", *udta)
            if not meta:
                return raw_tags
            meta_start, meta_end = meta

            # 'meta' is a full box: 1-byte version + 3-byte flags precede
            # its children.
            ilst = find_atom(data, b"ilst", meta_start + 4, meta_end)
            if not ilst:
                return raw_tags

            for atom_type, child_start, child_end in iter_atoms(data, *ilst):
                if atom_type == b"----":
                    # Freeform atom (iTunes/Picard convention for tags with
                    # no dedicated 4-char atom, e.g. MusicBrainz IDs). All
                    # freeform atoms share this literal type, so they're
                    # distinguished by their 'mean'/'name' children instead.
                    freeform = self._parse_mp4_freeform_atom(data, child_start, child_end)
                    if freeform is None:
                        continue
                    mean, name, value = freeform
                    if not value:
                        continue
                    key = f"----:{mean}:{name}"
                    raw_tags.setdefault(key, []).append(value)
                    continue

                value = self._parse_mp4_ilst_value(data, atom_type, child_start, child_end)
                if value is None or value == "":
                    continue
                key = atom_type.decode("latin-1", errors="ignore")
                raw_tags.setdefault(key, []).append(value)

        except (IndexError, struct.error) as e:
            logger.warning(f"Error extracting raw MP4/M4A tags: {e}")

        return raw_tags

    def _parse_mp4_ilst_value(self, data, atom_type, start, end):
        """Decode a single ilst child atom (e.g. "\\xa9nam") via its nested
        'data' atom into a display string."""
        data_atom = find_atom(data, b"data", start, end)
        if not data_atom:
            return None
        d_start, d_end = data_atom
        if d_end - d_start < 8:
            return None

        type_indicator = struct.unpack(">I", data[d_start : d_start + 4])[0]
        value_bytes = data[d_start + 8 : d_end]

        if atom_type in (b"trkn", b"disk"):
            # (reserved:2)(index:2)(total:2)[(reserved:2)]
            if len(value_bytes) >= 4:
                index = struct.unpack(">H", value_bytes[2:4])[0]
                return str(index)
            return None

        if type_indicator == 21:  # be signed/unsigned integer
            return str(int.from_bytes(value_bytes, "big", signed=False))

        # type 1 (UTF-8 text) and most real-world atoms in practice
        return value_bytes.decode("utf-8", errors="ignore").strip("\x00")

    def _parse_mp4_freeform_atom(self, data, start, end):
        """Decode a '----' freeform atom's 'mean'/'name'/'data' children.

        Returns (mean, name, value) — mean is the reverse-DNS namespace
        (e.g. "com.apple.iTunes"), name is the tag's display name (e.g.
        "MusicBrainz Album Id"), value is its decoded text. Returns None if
        the atom is missing its 'name' or 'data' child.
        """
        mean = name = value = None

        for child_type, child_start, child_end in iter_atoms(data, start, end):
            # 'mean'/'name'/'data' are all full boxes: 4-byte
            # version+flags header precedes their actual content.
            if child_end - child_start < 4:
                continue
            content = data[child_start + 4 : child_end]

            if child_type == b"mean":
                mean = content.decode("utf-8", errors="ignore")
            elif child_type == b"name":
                name = content.decode("utf-8", errors="ignore")
            elif child_type == b"data":
                if len(content) >= 4:
                    # skip the 4-byte locale field that follows the
                    # already-stripped type-indicator+flags header
                    value = content[4:].decode("utf-8", errors="ignore").strip("\x00")

        if name is None or value is None:
            return None
        return mean, name, value

    # ------------------------------------------------------------------ WAV

    def _extract_wav_tags(self, data):
        """Extract raw WAV tags without mapping."""
        raw_tags = {}

        try:
            if data[0:4] == b"RIFF" and data[8:12] == b"WAVE":
                pos = 12
                while pos < len(data) - 8:
                    chunk_id = data[pos : pos + 4]
                    chunk_size = struct.unpack("<I", data[pos + 4 : pos + 8])[0]

                    if chunk_id == b"LIST" and pos + 12 <= len(data):
                        list_type = data[pos + 8 : pos + 12]
                        if list_type == b"INFO":
                            raw_tags.update(
                                self._parse_info_chunk(data[pos + 12 : pos + 8 + chunk_size])
                            )

                    # RIFF chunks are padded to an even byte boundary.
                    pos += 8 + chunk_size + (chunk_size & 1)

        except (IndexError, struct.error) as e:
            logger.warning(f"Error extracting raw WAV tags: {e}")

        return raw_tags

    def _parse_info_chunk(self, data):
        """Parse raw WAV INFO chunk."""
        raw_tags = {}
        pos = 0

        while pos < len(data) - 8:
            chunk_id = data[pos : pos + 4].decode("ascii", errors="ignore")
            chunk_size = struct.unpack("<I", data[pos + 4 : pos + 8])[0]

            if chunk_size > 0 and pos + 8 + chunk_size <= len(data):
                chunk_data = data[pos + 8 : pos + 8 + chunk_size]
                value = chunk_data.decode("utf-8", errors="ignore").strip("\x00")

                if chunk_id not in raw_tags:
                    raw_tags[chunk_id] = []

                raw_tags[chunk_id].append(value)

            pos += 8 + chunk_size + (chunk_size & 1)

        return raw_tags

    # ----------------------------------------------------------------- AIFF

    def _extract_aiff_tags(self, data):
        """Extract raw AIFF tags, remapped to ID3 frame IDs."""
        raw_tags = {}

        try:
            if data[0:4] == b"FORM" and data[8:12] in [b"AIFF", b"AIFC"]:
                pos = 12
                while pos < len(data) - 8:
                    chunk_id = data[pos : pos + 4]
                    chunk_size = struct.unpack(">I", data[pos + 4 : pos + 8])[0]

                    id3_key = self._AIFF_CHUNK_TO_ID3.get(chunk_id)
                    if id3_key:
                        tag_value = (
                            data[pos + 8 : pos + 8 + chunk_size]
                            .decode("ascii", errors="ignore")
                            .strip("\x00")
                        )
                        raw_tags[id3_key] = tag_value

                    # IFF/AIFF chunks are padded to an even byte boundary.
                    pos += 8 + chunk_size + (chunk_size & 1)

        except (IndexError, struct.error) as e:
            logger.warning(f"Error extracting raw AIFF tags: {e}")

        return raw_tags
