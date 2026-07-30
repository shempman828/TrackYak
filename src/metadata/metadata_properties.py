import struct

from src.core.logger_config import logger
from src.metadata.metadata_mp4_atoms import find_atom, iter_atoms


class AudioPropertiesExtractor:
    """Dedicated audio technical properties extraction."""

    def __init__(self):
        self.format_handlers = {
            ".mp3": self._extract_mp3_properties,
            ".flac": self._extract_flac_properties,
            ".wav": self._extract_wav_properties,
            ".aiff": self._extract_aiff_properties,
            ".aif": self._extract_aiff_properties,
            ".m4a": self._extract_mp4_properties,
            ".m4b": self._extract_mp4_properties,
            ".mp4": self._extract_mp4_properties,
            ".aac": self._extract_mp4_properties,
            ".ogg": self._extract_ogg_properties,
            ".oga": self._extract_ogg_properties,
            ".opus": self._extract_ogg_properties,
            ".spx": self._extract_ogg_properties,
        }

    def extract_audio_properties(self, data, file_ext):
        """
        Extract technical audio properties from file bytes already read by
        the caller.

        Args:
            data: Full file contents
            file_ext: File extension (.mp3, .flac, etc.)

        Returns:
            Dictionary with audio technical properties
        """
        properties = {}

        try:
            handler = self.format_handlers.get(file_ext.lower())
            if not handler:
                logger.debug(f"No audio properties handler for format: {file_ext}")
                return properties

            format_properties = handler(data)
            properties.update(format_properties)

            # Add file size if not already present
            if "file_size" not in properties:
                properties["file_size"] = len(data)

            logger.debug(f"Extracted audio properties: {list(properties.keys())}")

        except AttributeError as e:
            logger.warning(f"Error extracting audio properties: {e}")

        return properties

    # ------------------------------------------------------------------ MP3

    def _mp3_audio_start(self, data):
        """Return the offset where MP3 frame data begins, skipping any
        leading ID3v2 tag. Without this, frame-sync scanning routinely
        false-positives inside embedded artwork (APIC) bytes, which are
        full of 0xFF bytes from JPEG markers."""
        if len(data) >= 10 and data[0:3] == b"ID3":
            return 10 + self._syncsafe_to_int(data[6:10])
        return 0

    def _find_next_mp3_sync(self, data, start):
        pos = start
        while pos < len(data) - 4:
            if data[pos] == 0xFF and (data[pos + 1] & 0xE0) == 0xE0:
                header = struct.unpack(">I", data[pos : pos + 4])[0]
                if (header & 0xFFE00000) == 0xFFE00000:
                    return pos
            pos += 1
        return None

    def _parse_mp3_vbr_header(self, data, frame_pos, version_bits, channel_mode):
        """Look for a LAME/Xing "Xing"/"Info" tag or a Fraunhofer "VBRI" tag
        right after the first frame's side info, and return the
        encoder-reported {frames, bytes} if present. Virtually every modern
        encoder writes one of these (CBR included), giving an exact
        duration/bitrate without scanning the whole stream."""
        try:
            is_mono = channel_mode == 3
            if version_bits == 3:  # MPEG1
                side_info = 17 if is_mono else 32
            else:  # MPEG2 / 2.5
                side_info = 9 if is_mono else 17

            xing_pos = frame_pos + 4 + side_info
            if data[xing_pos : xing_pos + 4] in (b"Xing", b"Info"):
                p = xing_pos + 4
                if p + 4 > len(data):
                    return None
                flags = struct.unpack(">I", data[p : p + 4])[0]
                p += 4
                result = {}
                if flags & 0x1 and p + 4 <= len(data):
                    result["frames"] = struct.unpack(">I", data[p : p + 4])[0]
                    p += 4
                if flags & 0x2 and p + 4 <= len(data):
                    result["bytes"] = struct.unpack(">I", data[p : p + 4])[0]
                return result or None

            # VBRI sits at a fixed offset (32 bytes after the frame header),
            # independent of channel mode.
            vbri_pos = frame_pos + 4 + 32
            if data[vbri_pos : vbri_pos + 4] == b"VBRI":
                p = vbri_pos + 4 + 2 + 2 + 2  # skip version, delay, quality
                if p + 8 > len(data):
                    return None
                total_bytes = struct.unpack(">I", data[p : p + 4])[0]
                total_frames = struct.unpack(">I", data[p + 4 : p + 8])[0]
                return {"frames": total_frames, "bytes": total_bytes}

        except (struct.error, IndexError):
            pass

        return None

    def _extract_mp3_properties(self, data):
        """Extract MP3 audio technical properties."""
        properties = {}

        try:
            audio_start = self._mp3_audio_start(data)
            pos = self._find_next_mp3_sync(data, audio_start)
            if pos is None:
                return properties

            header = struct.unpack(">I", data[pos : pos + 4])[0]
            version_bits = (header >> 19) & 0x3
            channel_mode = (header >> 6) & 0x3

            bitrate = self._parse_mp3_bitrate(header)
            sample_rate = self._parse_mp3_sample_rate(header)
            if not sample_rate:
                return properties

            channels = self._parse_mp3_channels(header)
            # Layer III samples per frame: 1152 for MPEG1, 576 for MPEG2/2.5.
            samples_per_frame = 1152 if version_bits == 3 else 576

            properties["sample_rate"] = sample_rate
            properties["channels"] = channels

            vbr_info = self._parse_mp3_vbr_header(
                data, pos, version_bits, channel_mode
            )
            if vbr_info and vbr_info.get("frames"):
                duration = vbr_info["frames"] * samples_per_frame / sample_rate
                if duration > 0:
                    properties["duration"] = duration
                    # bit_rate is stored/displayed in kbps throughout the app.
                    if vbr_info.get("bytes"):
                        properties["bit_rate"] = int(
                            vbr_info["bytes"] * 8 / duration / 1000
                        )
                    elif bitrate:
                        properties["bit_rate"] = bitrate // 1000
                return properties

            # No encoder VBR header found — fall back to scanning the whole
            # remaining stream so duration/bitrate reflect the actual track
            # rather than a handful of frames.
            frame_count = 0
            total_bitrate = 0
            total_samples = 0
            scan_pos = pos
            while scan_pos < len(data) - 4:
                if data[scan_pos] == 0xFF and (data[scan_pos + 1] & 0xE0) == 0xE0:
                    frame_header = struct.unpack(
                        ">I", data[scan_pos : scan_pos + 4]
                    )[0]
                    if (frame_header & 0xFFE00000) != 0xFFE00000:
                        scan_pos += 1
                        continue

                    frame_bitrate = self._parse_mp3_bitrate(frame_header)
                    frame_sample_rate = self._parse_mp3_sample_rate(frame_header)
                    frame_size = self._parse_mp3_frame_size(
                        frame_header, frame_bitrate, frame_sample_rate
                    )

                    if frame_bitrate and frame_sample_rate and frame_size:
                        total_bitrate += frame_bitrate
                        total_samples += samples_per_frame
                        frame_count += 1
                        scan_pos += frame_size
                    else:
                        scan_pos += 1
                else:
                    scan_pos += 1

            if frame_count > 0:
                properties["bit_rate"] = (total_bitrate // frame_count) // 1000
                properties["duration"] = total_samples / sample_rate
            elif bitrate:
                properties["bit_rate"] = bitrate // 1000

        except (IndexError, struct.error) as e:
            logger.warning(f"Error extracting MP3 properties: {e}")

        return properties

    # ----------------------------------------------------------------- FLAC

    def _extract_flac_properties(self, data):
        """Extract FLAC audio technical properties."""
        properties = {}

        try:
            if data[:4] != b"fLaC":
                return properties

            file_size = len(data)
            pos = 4

            while pos + 4 <= file_size:
                header = struct.unpack(">I", data[pos : pos + 4])[0]
                pos += 4

                is_last = (header >> 31) & 1
                block_type = (header >> 24) & 0x7F
                block_size = header & 0xFFFFFF

                if pos + block_size > file_size:
                    break

                # STREAMINFO block (type 0)
                if block_type == 0 and block_size >= 34:
                    stream_info = data[pos : pos + 34]
                    bits = int.from_bytes(stream_info[10:18], "big")

                    sample_rate = (bits >> 44) & 0xFFFFF
                    channels = ((bits >> 41) & 0x7) + 1
                    bit_depth = ((bits >> 36) & 0x1F) + 1
                    total_samples = bits & 0xFFFFFFFFF  # 36 bits

                    properties.update(
                        {
                            "sample_rate": sample_rate,
                            "channels": channels,
                            "bit_depth": bit_depth,
                        }
                    )

                    # Calculate duration and bitrate
                    if total_samples > 0 and sample_rate > 0:
                        duration = total_samples / sample_rate
                        properties["duration"] = duration

                        if duration > 0:
                            bit_rate_kbps = (file_size * 8) / duration / 1000
                            properties["bit_rate"] = int(bit_rate_kbps)

                    break  # Found STREAMINFO, no need to continue

                pos += block_size
                if is_last:
                    break

        except (IndexError, struct.error) as e:
            logger.warning(f"Error extracting FLAC properties: {e}")

        return properties

    # ------------------------------------------------------------------ WAV

    def _extract_wav_properties(self, data):
        """Extract WAV audio technical properties."""
        properties = {}

        try:
            if data[0:4] != b"RIFF" or data[8:12] != b"WAVE":
                return properties

            pos = 12
            channels = sample_rate = bits_per_sample = None
            data_chunk_size = None

            while pos < len(data) - 8:
                chunk_id = data[pos : pos + 4]
                chunk_size = struct.unpack("<I", data[pos + 4 : pos + 8])[0]

                if chunk_id == b"fmt " and chunk_size >= 16:
                    fmt_data = data[pos + 8 : pos + 8 + chunk_size]

                    audio_format = struct.unpack("<H", fmt_data[0:2])[0]
                    channels = struct.unpack("<H", fmt_data[2:4])[0]
                    sample_rate = struct.unpack("<I", fmt_data[4:8])[0]

                    properties.update(
                        {
                            "audio_format": audio_format,
                            "channels": channels,
                            "sample_rate": sample_rate,
                        }
                    )

                    # bits_per_sample is within the standard 16-byte PCM fmt
                    # chunk (offset 14:16); the extra bytes some encoders add
                    # (making it 18) are only an extensible-format cbSize
                    # field and don't affect where bits_per_sample lives.
                    if chunk_size >= 16:
                        bits_per_sample = struct.unpack("<H", fmt_data[14:16])[0]
                        properties["bit_depth"] = bits_per_sample

                elif chunk_id == b"data":
                    # A truncated/streamed file may claim more data than is
                    # actually present; clamp to what we actually have.
                    data_chunk_size = min(chunk_size, len(data) - (pos + 8))

                # RIFF chunks are padded to an even byte boundary.
                pos += 8 + chunk_size + (chunk_size & 1)

            if data_chunk_size and channels and sample_rate and bits_per_sample:
                bytes_per_sample = bits_per_sample // 8
                if bytes_per_sample > 0:
                    total_frames = data_chunk_size / (channels * bytes_per_sample)
                    duration = total_frames / sample_rate
                    if duration > 0:
                        properties["duration"] = duration
                        # bit_rate is stored/displayed in kbps throughout the app.
                        properties["bit_rate"] = (
                            sample_rate * channels * bits_per_sample
                        ) // 1000

        except (IndexError, struct.error) as e:
            logger.warning(f"Error extracting WAV properties: {e}")

        return properties

    # ----------------------------------------------------------------- AIFF

    def _extract_aiff_properties(self, data):
        """Extract AIFF audio technical properties."""
        properties = {}

        try:
            if data[0:4] != b"FORM" or data[8:12] not in [b"AIFF", b"AIFC"]:
                return properties

            pos = 12

            while pos < len(data) - 8:
                chunk_id = data[pos : pos + 4]
                chunk_size = struct.unpack(">I", data[pos + 4 : pos + 8])[0]

                if chunk_id == b"COMM" and chunk_size >= 18:
                    comm_data = data[pos + 8 : pos + 8 + chunk_size]

                    channels = struct.unpack(">H", comm_data[0:2])[0]
                    total_frames = struct.unpack(">I", comm_data[2:6])[0]
                    bit_depth = struct.unpack(">H", comm_data[6:8])[0]

                    # Sample rate (80-bit IEEE 754 extended precision float)
                    sample_rate = self._parse_aiff_sample_rate(comm_data[8:18])

                    properties.update(
                        {
                            "channels": channels,
                            "bit_depth": bit_depth,
                            "sample_rate": sample_rate,
                        }
                    )

                    if sample_rate and total_frames > 0:
                        properties["duration"] = total_frames / sample_rate
                        # Uncompressed PCM: exact, same formula as WAV.
                        # bit_rate is stored/displayed in kbps throughout
                        # the app.
                        properties["bit_rate"] = (
                            sample_rate * channels * bit_depth
                        ) // 1000

                    break

                # IFF/AIFF chunks are padded to an even byte boundary.
                pos += 8 + chunk_size + (chunk_size & 1)

        except (IndexError, struct.error) as e:
            logger.warning(f"Error extracting AIFF properties: {e}")

        return properties

    def _parse_aiff_sample_rate(self, sample_rate_data):
        """Decode an 80-bit IEEE 754 extended-precision float, as used by
        AIFF's COMM sampleRate field: 1 sign bit + 15 exponent bits, then a
        64-bit mantissa with an explicit (non-implicit) leading integer bit."""
        try:
            exponent_word = struct.unpack(">H", sample_rate_data[0:2])[0]
            mantissa = struct.unpack(">Q", sample_rate_data[2:10])[0]

            if exponent_word == 0 and mantissa == 0:
                return 0

            sign = -1 if exponent_word & 0x8000 else 1
            exponent = exponent_word & 0x7FFF

            # mantissa is already normalized to [2**63, 2**64); dividing by
            # 2**63 gives the [1, 2) significand IEEE 754 assumes.
            value = sign * mantissa * (2.0 ** (exponent - 16383 - 63))
            return int(round(value))

        except (struct.error, OverflowError) as e:
            logger.warning(f"Error parsing AIFF sample rate: {e}")
            return None

    # ------------------------------------------------------------------ MP4

    def _extract_mp4_properties(self, data):
        """Extract MP4/M4A/AAC audio technical properties from
        moov/trak/mdia. Raw ADTS .aac streams (no MP4 container) have no
        moov atom and simply yield no properties here."""
        properties = {}

        try:
            end = len(data)
            moov = find_atom(data, b"moov", 0, end)
            if not moov:
                return properties

            timescale = duration_units = None
            channels = sample_rate = None

            for atom_type, trak_start, trak_end in iter_atoms(data, *moov):
                if atom_type != b"trak":
                    continue

                mdia = find_atom(data, b"mdia", trak_start, trak_end)
                if not mdia:
                    continue

                trak_timescale = trak_duration = None
                mdhd = find_atom(data, b"mdhd", *mdia)
                if mdhd:
                    trak_timescale, trak_duration = self._parse_mp4_mdhd(data, *mdhd)

                trak_channels = trak_sample_rate = None
                minf = find_atom(data, b"minf", *mdia)
                if minf:
                    stbl = find_atom(data, b"stbl", *minf)
                    if stbl:
                        stsd = find_atom(data, b"stsd", *stbl)
                        if stsd:
                            parsed = self._parse_mp4_stsd_audio(data, *stsd)
                            if parsed:
                                trak_channels, trak_sample_rate = parsed

                if trak_sample_rate:
                    # This is the audio track — use it and stop looking.
                    timescale, duration_units = trak_timescale, trak_duration
                    channels, sample_rate = trak_channels, trak_sample_rate
                    break
                elif timescale is None and trak_timescale:
                    # Keep the first track's duration as a fallback in case
                    # no track yields a readable stsd audio entry.
                    timescale, duration_units = trak_timescale, trak_duration

            if channels:
                properties["channels"] = channels
            if sample_rate:
                properties["sample_rate"] = sample_rate
            if timescale and duration_units is not None:
                duration = duration_units / timescale
                if duration > 0:
                    properties["duration"] = duration
                    # AAC is lossy/compressed; there's no cheap exact
                    # bitrate without decoding the esds descriptor, so
                    # approximate from file size like the FLAC handler does.
                    # bit_rate is stored/displayed in kbps throughout the app.
                    properties["bit_rate"] = int((len(data) * 8) / duration / 1000)

        except (IndexError, struct.error) as e:
            logger.warning(f"Error extracting MP4 properties: {e}")

        return properties

    def _parse_mp4_mdhd(self, data, start, end):
        """Parse an mdhd full-box for (timescale, duration_in_timescale_units)."""
        try:
            if end - start < 4:
                return None, None
            version = data[start]
            if version == 1:
                if end - start < 32:
                    return None, None
                timescale = struct.unpack(">I", data[start + 20 : start + 24])[0]
                duration = struct.unpack(">Q", data[start + 24 : start + 32])[0]
            else:
                if end - start < 20:
                    return None, None
                timescale = struct.unpack(">I", data[start + 12 : start + 16])[0]
                duration = struct.unpack(">I", data[start + 16 : start + 20])[0]
            return timescale, duration
        except struct.error:
            return None, None

    def _parse_mp4_stsd_audio(self, data, start, end):
        """Parse an stsd full-box's first entry for (channels, sample_rate)."""
        try:
            if end - start < 8:
                return None
            entry_count = struct.unpack(">I", data[start + 4 : start + 8])[0]
            if entry_count < 1:
                return None

            # Audio sample entry: size(4) format(4) reserved(6)
            # data_reference_index(2) version(2) revision(2) vendor(4)
            # channels(2) sample_size(2) compression_id(2) packet_size(2)
            # sample_rate(4, 16.16 fixed point)
            p = start + 8 + 8  # skip entry size+format
            p += 6 + 2  # reserved + data_reference_index
            p += 2 + 2 + 4  # version + revision + vendor

            if p + 2 > end:
                return None
            channels = struct.unpack(">H", data[p : p + 2])[0]
            p += 2

            p += 2  # sample_size
            p += 2 + 2  # compression_id + packet_size

            if p + 4 > end:
                return None
            sample_rate_fixed = struct.unpack(">I", data[p : p + 4])[0]
            sample_rate = sample_rate_fixed >> 16

            if not channels or not sample_rate:
                return None
            return channels, sample_rate
        except struct.error:
            return None

    # ------------------------------------------------------------------ Ogg

    def _extract_ogg_properties(self, data):
        """Extract audio technical properties from an Ogg container
        (Vorbis or Opus), by reading the identification header on the
        first page and the granule position of the last page."""
        properties = {}

        try:
            if data[0:4] != b"OggS":
                return properties

            channels = sample_rate = None
            pre_skip = 0
            last_granule = None
            first_page = True

            pos = 0
            while pos + 27 <= len(data) and data[pos : pos + 4] == b"OggS":
                granule_position = struct.unpack("<q", data[pos + 6 : pos + 14])[0]
                page_segments = data[pos + 26]
                segment_table_start = pos + 27
                if segment_table_start + page_segments > len(data):
                    break
                segment_table = data[
                    segment_table_start : segment_table_start + page_segments
                ]
                payload_size = sum(segment_table)
                payload_start = segment_table_start + page_segments
                payload_end = payload_start + payload_size

                if first_page:
                    payload = data[payload_start:payload_end]
                    if payload[0:7] == b"\x01vorbis" and len(payload) >= 16:
                        channels = payload[11]
                        sample_rate = struct.unpack("<I", payload[12:16])[0]
                    elif payload[0:8] == b"OpusHead" and len(payload) >= 12:
                        channels = payload[9]
                        pre_skip = struct.unpack("<H", payload[10:12])[0]
                        sample_rate = 48000  # Opus always decodes at 48kHz
                    first_page = False

                if granule_position >= 0:
                    last_granule = granule_position

                if payload_end <= pos:
                    break  # malformed/zero-progress page; avoid infinite loop
                pos = payload_end

            if channels:
                properties["channels"] = channels
            if sample_rate:
                properties["sample_rate"] = sample_rate
            if last_granule is not None and sample_rate:
                total_samples = max(last_granule - pre_skip, 0)
                duration = total_samples / sample_rate
                if duration > 0:
                    properties["duration"] = duration
                    # bit_rate is stored/displayed in kbps throughout the app.
                    properties["bit_rate"] = int((len(data) * 8) / duration / 1000)

        except (IndexError, struct.error) as e:
            logger.warning(f"Error extracting Ogg properties: {e}")

        return properties

    # -------------------------------------------------------------- MP3 helpers

    def _parse_mp3_bitrate(self, header):
        """Extract bitrate from MP3 frame header."""
        bitrate_table = [
            # MPEG Version 1
            [
                [0, 32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448],
                [0, 32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384],
                [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320],
            ],
            # MPEG Version 2/2.5
            [
                [0, 32, 48, 56, 64, 80, 96, 112, 128, 144, 160, 176, 192, 224, 256],
                [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160],
                [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160],
            ],
        ]

        try:
            version_bits = (header >> 19) & 0x3
            layer_bits = (header >> 17) & 0x3
            bitrate_index = (header >> 12) & 0xF

            version_index = 0 if version_bits == 3 else 1

            if layer_bits == 3:
                layer_index = 0
            elif layer_bits == 2:
                layer_index = 1
            elif layer_bits == 1:
                layer_index = 2
            else:
                return None

            if 1 <= bitrate_index <= 14:
                bitrate_kbps = bitrate_table[version_index][layer_index][bitrate_index]
                return bitrate_kbps * 1000  # Convert to bps

        except IndexError as e:
            logger.warning(f"Error parsing MP3 bitrate: {e}")

        return None

    def _parse_mp3_sample_rate(self, header):
        """Extract sample rate from MP3 frame header."""
        sample_rate_table = [
            [44100, 48000, 32000],  # MPEG-1
            [22050, 24000, 16000],  # MPEG-2
        ]

        version = (header >> 19) & 0x3
        sr_index = (header >> 10) & 0x3

        table = 0 if version == 3 else 1

        if 0 <= sr_index <= 2:
            return sample_rate_table[table][sr_index]

        return None

    def _parse_mp3_channels(self, header):
        """Extract channel mode from MP3 frame header."""
        mode = (header >> 6) & 0x3
        return 1 if mode == 3 else 2  # Mono = 1, Stereo/Joint/Dual = 2

    def _parse_mp3_frame_size(self, header, bitrate, sample_rate):
        """Calculate MP3 frame size."""
        if not bitrate or not sample_rate:
            return 0

        try:
            padding = (header >> 9) & 0x01
            return ((144000 * bitrate) // sample_rate) + padding
        except TypeError:
            return 0

    def _syncsafe_to_int(self, data):
        result = 0
        for byte in data:
            result = (result << 7) | (byte & 0x7F)
        return result
