import struct
from typing import List


class ID3TagWriter:
    """Handles writing ID3v2.3/2.4 tags to MP3 files."""

    def __init__(self, version: int = 4):
        self.version = version  # 3 for ID3v2.3, 4 for ID3v2.4
        self.encoding_byte = 0x03  # UTF-8 encoding for ID3v2.4

    def create_text_frame(self, frame_id: str, text: str) -> bytes:
        """Create a text information frame."""
        if not text:
            return b""

        # Encode text with BOM for unicode
        encoded_text = text.encode("utf-16be")
        frame_data = struct.pack(">B", 0x01) + encoded_text  # Unicode with BOM

        # Frame header: ID (4 bytes) + size (4 bytes) + flags (2 bytes)
        frame_size = len(frame_data)
        frame_header = (
            frame_id.encode("ascii") + struct.pack(">I", frame_size)[1:] + b"\x00\x00"
        )

        return frame_header + frame_data

    def create_comment_frame(self, text: str, language: str = "eng") -> bytes:
        """Create a comment frame."""
        if not text:
            return b""

        encoded_text = text.encode("utf-16be")
        frame_data = (
            language.encode("iso-8859-1") + struct.pack(">B", 0x01) + encoded_text
        )

        frame_size = len(frame_data)
        frame_header = b"COMM" + struct.pack(">I", frame_size)[1:] + b"\x00\x00"

        return frame_header + frame_data

    def create_number_frame(self, frame_id: str, number: int) -> bytes:
        """Create a numeric frame (track number, play count, etc.)."""
        if number is None:
            return b""

        text = str(number)
        return self.create_text_frame(frame_id, text)

    def create_float_frame(self, frame_id: str, value: float) -> bytes:
        """Create a float frame (BPM, etc.)."""
        if value is None:
            return b""

        text = str(value)
        return self.create_text_frame(frame_id, text)

    def create_lyrics_frame(self, lyrics: str, language: str = "eng") -> bytes:
        """Create a lyrics frame."""
        if not lyrics:
            return b""

        encoded_text = lyrics.encode("utf-16be")
        frame_data = (
            language.encode("iso-8859-1") + struct.pack(">B", 0x01) + encoded_text
        )

        frame_size = len(frame_data)
        frame_header = b"USLT" + struct.pack(">I", frame_size)[1:] + b"\x00\x00"

        return frame_header + frame_data

    def sync_safe_int(self, value: int) -> bytes:
        """Convert integer to sync-safe format (7 bits per byte)."""
        return struct.pack(">I", value)[1:]  # Simple sync-safe for ID3v2.3/2.4

    def build_id3_tag(self, frames: List[bytes]) -> bytes:
        """Build complete ID3 tag from frames."""
        if not frames:
            return b""

        tag_data = b"".join(frames)
        tag_size = len(tag_data)

        # ID3 header: "ID3" + version + flags + size
        header = b"ID3" + struct.pack(">BB", 3, 0) + self.sync_safe_int(tag_size)

        return header + tag_data
