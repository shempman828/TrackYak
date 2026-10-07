"""Extracts raw, unmapped tag key/value pairs from audio file bytes."""

import struct
from typing import ClassVar

from src.foundation.logger_config import logger
from src.metadata.metadata_byte_utils import decode_id3_string, id3_frames_offset, is_valid_frame_id, parse_id3_lang_frame, split_id3_string, syncsafe_to_int
from src.metadata.metadata_ogg_pages import iter_packets
from src.metadata.readers.metadata_mp4_atoms import find_atom, iter_atoms


class RawTagExtractor:
    """Extracts raw, unmapped tag key/value pairs from audio file bytes, one reader per container format."""

    # AIFF chunk IDs mapped to ID3 frame IDs: AIFF resolves to the "id3" mapping set in TextMetadataExtractor.
    _AIFF_CHUNK_TO_ID3: ClassVar[dict[bytes, str]] = {b"NAME": "TIT2", b"AUTH": "TPE1", b"(c) ": "TCOP", b"ANNO": "COMM"}

    # ID3v2.2 3-character frame IDs mapped to their v2.3/2.4 equivalents, so v2.2 tags use the same mappings.
    _ID3V22_TO_V23: ClassVar[dict[str, str]] = {
        "TT1": "TIT1",
        "TT2": "TIT2",
        "TT3": "TIT3",
        "TP1": "TPE1",
        "TP2": "TPE2",
        "TP3": "TPE3",
        "TP4": "TPE4",
        "TCM": "TCOM",
        "TXT": "TEXT",
        "TAL": "TALB",
        "TRK": "TRCK",
        "TPA": "TPOS",
        "TYE": "TYER",
        "TCO": "TCON",
        "TBP": "TBPM",
        "TKE": "TKEY",
        "TCR": "TCOP",
        "TPB": "TPUB",
        "TLA": "TLAN",
        "TRC": "TSRC",
        "TOA": "TOPE",
        "TOL": "TOLY",
        "TLE": "TLEN",
        "COM": "COMM",
        "ULT": "USLT",
        "TXX": "TXXX",
        "CNT": "PCNT",
        "UFI": "UFID",
        "PIC": "APIC",
    }

    # Frames whose NUL separators delimit role/name pairs, not separate values.
    _PAIRED_LIST_FRAMES = frozenset({"TIPL", "TMCL", "IPLS"})

    def __init__(self):
        """Register the per-extension raw tag readers."""
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
        """Extract raw tags for the format file_ext names; return {} if unsupported or unparseable."""
        handler = self.format_handlers.get(file_ext.lower())
        if not handler:
            logger.warning(f"Unsupported file format for raw tag extraction: {file_ext}")
            return {}

        try:
            return handler(data)
        except AttributeError as e:
            # Last-resort net: each reader already handles its own parse errors.
            logger.warning(f"Error extracting raw tags: {e}")
            return {}

    # ------------------------------------------------------------------ ID3
    # (MP3, and AIFF via _AIFF_CHUNK_TO_ID3 remapping)

    def _extract_id3_tags(self, data):
        """Extract raw ID3v2 tags, with ID3v1 only filling keys that v2 does not have."""
        raw_tags = {}

        try:
            if len(data) >= 10 and data[0:3] == b"ID3":
                version_major = data[3]
                tag_end = 10 + syncsafe_to_int(data[6:10])
                frame_data = data[id3_frames_offset(data) : tag_end]

                if version_major == 2:
                    raw_tags.update(self._parse_id3v2_2_frames(frame_data))
                elif version_major in [3, 4]:
                    raw_tags.update(self._parse_id3v2_3_4_frames(frame_data, version_major))

            if len(data) >= 128 and data[-128:-125] == b"TAG":
                for key, value in self._parse_id3v1_tags(data[-128:]).items():
                    # v1 fields are truncated to 30 characters, so v2 always wins.
                    if value and key not in raw_tags:
                        raw_tags[key] = [value]

        except (IndexError, struct.error) as e:
            logger.warning(f"Error extracting raw ID3 tags: {e}")

        return raw_tags

    def _parse_id3v2_2_frames(self, frame_data):
        """Parse raw ID3v2.2 frames, storing them under their v2.3 frame IDs."""
        raw_tags = {}
        pos = 0

        while pos < len(frame_data) - 6:
            if not is_valid_frame_id(frame_data[pos : pos + 3]):
                break
            frame_id = frame_data[pos : pos + 3].decode("ascii")
            frame_size = struct.unpack(">I", b"\x00" + frame_data[pos + 3 : pos + 6])[0]

            if frame_size == 0:
                break

            frame_content = frame_data[pos + 6 : pos + 6 + frame_size]
            self._store_id3_frame(raw_tags, self._ID3V22_TO_V23.get(frame_id, frame_id), frame_content)

            pos += 6 + frame_size

        return raw_tags

    def _parse_id3v2_3_4_frames(self, frame_data, version):
        """Parse raw ID3v2.3/2.4 frames."""
        raw_tags = {}
        pos = 0

        while pos < len(frame_data) - 10:
            if not is_valid_frame_id(frame_data[pos : pos + 4]):
                break  # padding or a corrupt frame header
            frame_id = frame_data[pos : pos + 4].decode("ascii")

            frame_size = struct.unpack(">I", frame_data[pos + 4 : pos + 8])[0] if version == 3 else syncsafe_to_int(frame_data[pos + 4 : pos + 8])

            if frame_size == 0:
                break

            frame_content = frame_data[pos + 10 : pos + 10 + frame_size]
            self._store_id3_frame(raw_tags, frame_id, frame_content)

            pos += 10 + frame_size

        return raw_tags

    def _store_id3_frame(self, raw_tags, frame_id, content):
        """Decode one ID3 frame body and append its value(s) under the right raw-tag key."""
        if frame_id == "APIC" or not content:
            return

        if frame_id == "UFID":
            # owner <latin-1> NUL + identifier <binary>; no encoding byte.
            owner, identifier = split_id3_string(content, 0)
            key = f"UFID:{owner.decode('latin-1', errors='ignore')}" if owner else "UFID"
            raw_tags.setdefault(key, []).append(identifier.decode("ascii", errors="ignore").strip("\x00"))
            return

        if frame_id == "TXXX":
            encoding = content[0]
            description, value = split_id3_string(content[1:], encoding)
            description_text = decode_id3_string(description, encoding).strip()
            key = f"TXXX:{description_text}" if description_text else "TXXX"
            raw_tags.setdefault(key, []).append(decode_id3_string(value, encoding))
            return

        if frame_id in ("COMM", "USLT"):
            parsed = parse_id3_lang_frame(content)
            if parsed is None:
                return
            encoding, _language, description, text = parsed
            # Descriptions such as iTunNORM are tool data, not a user comment.
            key = f"{frame_id}:{description}" if description else frame_id
            raw_tags.setdefault(key, []).append(decode_id3_string(text, encoding))
            return

        if frame_id == "PCNT":
            raw_tags.setdefault(frame_id, []).append(str(int.from_bytes(content, "big")))
            return

        text = decode_id3_string(content[1:], content[0]) if content[0] <= 3 else decode_id3_string(content, 0)
        if frame_id in self._PAIRED_LIST_FRAMES:
            raw_tags.setdefault(frame_id, []).append(text)
            return
        # v2.4 stores multiple values separated by NUL.
        raw_tags.setdefault(frame_id, []).extend(part for part in text.split("\x00") if part)

    def _parse_id3v1_tags(self, tag_data):
        """Parse a 128-byte ID3v1 tag."""
        return {
            "TIT2": tag_data[3:33].decode("latin-1", errors="ignore").strip("\x00 "),
            "TPE1": tag_data[33:63].decode("latin-1", errors="ignore").strip("\x00 "),
            "TALB": tag_data[63:93].decode("latin-1", errors="ignore").strip("\x00 "),
            "TYER": tag_data[93:97].decode("latin-1", errors="ignore").strip("\x00 "),
            "COMM": tag_data[97:127].decode("latin-1", errors="ignore").strip("\x00 "),
        }

    # --------------------------------------------------------- Vorbis comment
    # (shared structure behind FLAC's VORBIS_COMMENT block and Ogg
    # Vorbis/Opus's comment header packet)

    def _parse_vorbis_comments(self, data):
        """Parse a raw Vorbis-comment block (vendor string, then KEY=value entries)."""
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
        """Extract raw Vorbis-comment tags from an Ogg Vorbis or Opus comment header packet."""
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
        """Extract raw MP4/M4A tags from moov/udta/meta/ilst without mapping."""
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
        """Decode one ilst child atom through its nested 'data' atom into a display string."""
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
        """Decode a '----' freeform atom into (mean, name, value), or None if 'name' or 'data' is missing."""
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
            elif child_type == b"data" and len(content) >= 4:
                # Skip the 4-byte locale field after the type-indicator+flags header.
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
                            raw_tags.update(self._parse_info_chunk(data[pos + 12 : pos + 8 + chunk_size]))

                    # RIFF chunks are padded to an even byte boundary.
                    pos += 8 + chunk_size + (chunk_size & 1)

        except (IndexError, struct.error) as e:
            logger.warning(f"Error extracting raw WAV tags: {e}")

        return raw_tags

    def _parse_info_chunk(self, data):
        """Parse a raw WAV LIST/INFO chunk."""
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
                        tag_value = data[pos + 8 : pos + 8 + chunk_size].decode("ascii", errors="ignore").strip("\x00")
                        raw_tags.setdefault(id3_key, []).append(tag_value)

                    # IFF/AIFF chunks are padded to an even byte boundary.
                    pos += 8 + chunk_size + (chunk_size & 1)

        except (IndexError, struct.error) as e:
            logger.warning(f"Error extracting raw AIFF tags: {e}")

        return raw_tags
