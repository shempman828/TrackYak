"""Minimal MP4/M4A atom (box) tree walker, shared by audio-properties and
tag extraction for .m4a/.mp4/.m4b files.

Only implements enough of ISO/IEC 14496-12 to locate specific atoms by
type and iterate direct children - not a general-purpose MP4 parser.
"""

import struct
from typing import Iterator, Optional, Tuple


def iter_atoms(data: bytes, start: int, end: int) -> Iterator[Tuple[bytes, int, int]]:
    """Yield (atom_type, payload_start, payload_end) for each direct child
    atom in data[start:end]."""
    pos = start
    while pos + 8 <= end:
        size = struct.unpack(">I", data[pos : pos + 4])[0]
        atom_type = data[pos + 4 : pos + 8]
        header_size = 8

        if size == 1:
            if pos + 16 > end:
                break
            size = struct.unpack(">Q", data[pos + 8 : pos + 16])[0]
            header_size = 16
        elif size == 0:
            size = end - pos

        if size < header_size or pos + size > end:
            break

        yield atom_type, pos + header_size, pos + size
        pos += size


def find_atom(
    data: bytes, atom_type: bytes, start: int, end: int
) -> Optional[Tuple[int, int]]:
    """Return (payload_start, payload_end) of the first direct child atom
    matching atom_type, or None if not found."""
    for child_type, child_start, child_end in iter_atoms(data, start, end):
        if child_type == atom_type:
            return child_start, child_end
    return None
