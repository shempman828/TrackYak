"""Builds raw ID3v2.3 frames and the surrounding tag header."""

import struct

from src.foundation.logger_config import logger

# UTF-16 with BOM: the only Unicode encoding ID3v2.3 allows.
_UTF16 = 0x01


class ID3TagWriter:
    """Builds ID3v2.3 frames and tags as raw bytes."""

    @staticmethod
    def _frame(frame_id: str, body: bytes) -> bytes:
        """Wrap body in a v2.3 frame header (plain 32-bit size, no flags)."""
        return frame_id.encode("ascii") + struct.pack(">I", len(body)) + b"\x00\x00" + body

    def create_text_frame(self, frame_id: str, text: str) -> bytes:
        """Create a text information frame, or b"" for empty text."""
        if not text:
            return b""
        return self._frame(frame_id, bytes([_UTF16]) + text.encode("utf-16"))

    def _create_lang_frame(self, frame_id: str, text: str, language: str) -> bytes:
        """Create a COMM/USLT frame: encoding, language, empty description, then text."""
        if not text:
            return b""
        body = bytes([_UTF16]) + language.encode("latin-1")[:3].ljust(3, b" ") + "".encode("utf-16") + b"\x00\x00" + text.encode("utf-16")
        return self._frame(frame_id, body)

    def create_comment_frame(self, text: str, language: str = "eng") -> bytes:
        """Create a COMM frame with an empty description."""
        return self._create_lang_frame("COMM", text, language)

    def create_lyrics_frame(self, lyrics: str, language: str = "eng") -> bytes:
        """Create a USLT frame with an empty description."""
        return self._create_lang_frame("USLT", lyrics, language)

    def create_number_frame(self, frame_id: str, number: int) -> bytes:
        """Create a text frame holding an integer."""
        if number is None:
            return b""
        return self.create_text_frame(frame_id, str(number))

    def create_float_frame(self, frame_id: str, value: float) -> bytes:
        """Create a text frame holding a float."""
        if value is None:
            return b""
        return self.create_text_frame(frame_id, str(value))

    def create_counter_frame(self, count: int) -> bytes:
        """Create a PCNT play-counter frame (binary big-endian counter, at least 4 bytes)."""
        if count is None or count < 0:
            return b""
        length = max(4, (int(count).bit_length() + 7) // 8)
        return self._frame("PCNT", int(count).to_bytes(length, "big"))

    def create_ufid_frame(self, owner: str, identifier: str) -> bytes:
        """Create a UFID frame (owner URL, NUL, identifier; no encoding byte)."""
        if not owner or not identifier:
            return b""
        return self._frame("UFID", owner.encode("latin-1", errors="ignore") + b"\x00" + identifier.encode("ascii", errors="ignore")[:64])

    def create_txxx_frame(self, description: str, value: str) -> bytes:
        """Create a TXXX user-defined text frame keyed by description, or b"" if either is empty."""
        if not value or not description:
            return b""
        # Only one TXXX frame is allowed per description, so callers join multiple values (e.g. " ; ").
        return self._frame("TXXX", bytes([_UTF16]) + description.encode("utf-16") + b"\x00\x00" + value.encode("utf-16"))

    def sync_safe_int(self, value: int) -> bytes:
        """Encode a 28-bit integer as a 4-byte ID3 syncsafe integer."""
        return bytes([(value >> 21) & 0x7F, (value >> 14) & 0x7F, (value >> 7) & 0x7F, value & 0x7F])

    def build_id3_tag(self, frames: list[bytes]) -> bytes:
        """Build a complete ID3v2.3 tag from frames, or b"" when there are none."""
        if not frames:
            logger.debug("No frames provided to build_id3_tag; skipping tag creation")
            return b""

        tag_data = b"".join(frames)
        header = b"ID3" + struct.pack(">BB", 3, 0) + b"\x00" + self.sync_safe_int(len(tag_data))
        return header + tag_data
