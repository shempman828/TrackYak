import struct

from logger_config import logger


class AudioPropertiesExtractor:
    """Dedicated audio technical properties extraction."""

    def __init__(self):
        self.format_handlers = {
            ".mp3": self._extract_mp3_properties,
            ".flac": self._extract_flac_properties,
            ".wav": self._extract_wav_properties,
            ".aiff": self._extract_aiff_properties,
        }

    def extract_audio_properties(self, file_path, file_ext):
        """
        Extract technical audio properties from file.

        Args:
            file_path: Path to the audio file
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

            with open(file_path, "rb") as f:
                data = f.read()

            format_properties = handler(data)
            properties.update(format_properties)

            # Add file size if not already present
            if "file_size" not in properties:
                properties["file_size"] = len(data)

            logger.debug(f"Extracted audio properties: {list(properties.keys())}")

        except Exception as e:
            logger.warning(f"Error extracting audio properties from {file_path}: {e}")

        return properties

    def _extract_mp3_properties(self, data):
        """Extract MP3 audio technical properties."""
        properties = {}
        frame_count = 0
        total_bitrate = 0
        total_samples = 0

        try:
            pos = 0
            while pos < len(data) - 3 and frame_count < 10:  # Analyze first 10 frames
                # Look for MP3 frame sync
                if (
                    pos + 4 <= len(data)
                    and data[pos] == 0xFF
                    and (data[pos + 1] & 0xE0) == 0xE0
                ):
                    header = struct.unpack(">I", data[pos : pos + 4])[0]

                    # Validate header
                    if (header & 0xFFE00000) != 0xFFE00000:
                        pos += 1
                        continue

                    # Extract frame properties
                    bitrate = self._parse_mp3_bitrate(header)
                    sample_rate = self._parse_mp3_sample_rate(header)
                    channels = self._parse_mp3_channels(header)
                    frame_size = self._parse_mp3_frame_size(
                        header, bitrate, sample_rate
                    )

                    if all([bitrate, sample_rate, frame_size]):
                        total_bitrate += bitrate
                        total_samples += 1152  # Samples per frame for Layer III
                        frame_count += 1

                        # Store properties from first valid frame
                        if frame_count == 1:
                            properties.update(
                                {
                                    "bit_rate": bitrate,
                                    "sample_rate": sample_rate,
                                    "channels": channels,
                                }
                            )

                    # Move to next frame
                    if frame_size > 0:
                        pos += frame_size
                    else:
                        pos += 1
                else:
                    pos += 1

            # Calculate averages if we found multiple frames
            if frame_count > 1:
                properties["bit_rate"] = total_bitrate // frame_count
                if properties.get("sample_rate") and total_samples > 0:
                    properties["duration"] = total_samples / properties["sample_rate"]

        except Exception as e:
            logger.warning(f"Error extracting MP3 properties: {e}")

        return properties

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

        except Exception as e:
            logger.warning(f"Error extracting FLAC properties: {e}")

        return properties

    def _extract_wav_properties(self, data):
        """Extract WAV audio technical properties."""
        properties = {}

        try:
            if data[0:4] != b"RIFF" or data[8:12] != b"WAVE":
                return properties

            pos = 12

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

                    # Bits per sample
                    if chunk_size >= 18:
                        bits_per_sample = struct.unpack("<H", fmt_data[14:16])[0]
                        properties["bit_depth"] = bits_per_sample

                    break

                pos += 8 + chunk_size

        except Exception as e:
            logger.warning(f"Error extracting WAV properties: {e}")

        return properties

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

                    # Sample rate (80-bit float)
                    sample_rate = self._parse_aiff_sample_rate(comm_data[8:18])

                    properties.update(
                        {
                            "channels": channels,
                            "bit_depth": bit_depth,
                            "sample_rate": sample_rate,
                        }
                    )

                    # Calculate duration if possible
                    if sample_rate > 0 and total_frames > 0:
                        properties["duration"] = total_frames / sample_rate

                    break

                pos += 8 + chunk_size

        except Exception as e:
            logger.warning(f"Error extracting AIFF properties: {e}")

        return properties

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

        except Exception as e:
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
        except:  # noqa: E722
            return 0

    def _parse_aiff_sample_rate(self, sample_rate_data):
        """Parse AIFF 80-bit extended float sample rate."""
        try:
            # Convert 80-bit extended float to Python float
            # This is a simplified version - actual conversion is more complex
            exponent = struct.unpack(">H", sample_rate_data[0:2])[0] - 16383
            mantissa_high = struct.unpack(">Q", sample_rate_data[2:10])[0]

            if exponent == 0 and mantissa_high == 0:
                return 0.0

            # Simplified conversion - for exact conversion, use proper 80-bit float logic
            sample_rate = float(mantissa_high) / (1 << 63) * (2**exponent)
            return int(sample_rate)

        except Exception as e:
            logger.warning(f"Error parsing AIFF sample rate: {e}")
            return None
