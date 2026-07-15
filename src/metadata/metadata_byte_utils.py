"""Small binary-parsing helpers shared across metadata format readers."""


def syncsafe_to_int(data: bytes) -> int:
    """Decode an ID3v2 "syncsafe" integer: 7 usable bits per byte, used for
    ID3v2 tag/frame sizes so the size value itself can never contain a
    frame-sync byte pattern."""
    result = 0
    for byte in data:
        result = (result << 7) | (byte & 0x7F)
    return result
