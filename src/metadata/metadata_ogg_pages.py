"""Minimal Ogg page/packet reader and writer, shared by audio-properties
and tag extraction/writing for .ogg/.opus containers.

Reconstructs logical packets from physical pages by following each page's
lacing values (segment table), per the Ogg bitstream spec (RFC 3533):  a
255-byte segment means the packet continues into the next segment (and,
if it's the last segment on the page, into the next page); any shorter
segment ends the packet.
"""

from collections.abc import Iterator
import struct

from src.core.logger_config import logger


def iter_packets(data: bytes, max_packets: int = 8) -> Iterator[bytes]:
    """Yield up to max_packets reconstructed packets from the first
    logical stream at the start of data. Sufficient for header packets
    (identification, comment), which always come first, in order, before
    any audio packet."""
    pos = 0
    current = bytearray()
    packets_yielded = 0

    while (
        pos + 27 <= len(data) and data[pos : pos + 4] == b"OggS" and packets_yielded < max_packets
    ):
        page_segments = data[pos + 26]
        seg_table_start = pos + 27
        if seg_table_start + page_segments > len(data):
            break

        segment_table = data[seg_table_start : seg_table_start + page_segments]
        payload_start = seg_table_start + page_segments
        p = payload_start

        for seg_len in segment_table:
            current += data[p : p + seg_len]
            p += seg_len
            if seg_len < 255:
                yield bytes(current)
                packets_yielded += 1
                current = bytearray()
                if packets_yielded >= max_packets:
                    break

        page_end = payload_start + sum(segment_table)
        if page_end <= pos:
            break
        pos = page_end


# ---------------------------------------------------------------------
# Page-level reading and writing, used to rewrite the comment header
# packet in place (see replace_comment_packet below) without disturbing
# any audio page.
# ---------------------------------------------------------------------


def _make_crc_table() -> list[int]:
    table = []
    for i in range(256):
        r = i << 24
        for _ in range(8):
            if r & 0x80000000:
                r = ((r << 1) ^ 0x04C11DB7) & 0xFFFFFFFF
            else:
                r = (r << 1) & 0xFFFFFFFF
        table.append(r)
    return table


_CRC_TABLE = _make_crc_table()


def ogg_crc32(data: bytes) -> int:
    """Ogg's page checksum: CRC-32 with polynomial 0x04C11DB7, computed
    MSB-first with no reflection and no final XOR - distinct from the
    far more common zlib/CRC-32 variant. The page's own checksum field
    (bytes 22:26) must be zeroed in `data` before calling this."""
    crc = 0
    for byte in data:
        crc = ((crc << 8) & 0xFFFFFFFF) ^ _CRC_TABLE[((crc >> 24) & 0xFF) ^ byte]
    return crc & 0xFFFFFFFF


def iter_pages(data: bytes) -> Iterator[dict]:
    """Yield each Ogg page in data as a dict of its header fields plus
    payload bytes and its (start, end) byte span."""
    pos = 0
    while pos + 27 <= len(data) and data[pos : pos + 4] == b"OggS":
        header_type = data[pos + 5]
        granule_position = struct.unpack("<q", data[pos + 6 : pos + 14])[0]
        serial_number = struct.unpack("<I", data[pos + 14 : pos + 18])[0]
        sequence_number = struct.unpack("<I", data[pos + 18 : pos + 22])[0]
        page_segments = data[pos + 26]
        seg_table_start = pos + 27
        if seg_table_start + page_segments > len(data):
            break

        segment_table = data[seg_table_start : seg_table_start + page_segments]
        payload_start = seg_table_start + page_segments
        payload_end = payload_start + sum(segment_table)
        if payload_end > len(data):
            break

        yield {
            "start": pos,
            "end": payload_end,
            "header_type": header_type,
            "granule_position": granule_position,
            "serial_number": serial_number,
            "sequence_number": sequence_number,
            "segment_table": segment_table,
            "payload": data[payload_start:payload_end],
        }

        pos = payload_end


def _build_single_page(
    serial_number: int,
    sequence_number: int,
    granule_position: int,
    header_type: int,
    segment_table: bytes,
    payload: bytes,
) -> bytes:
    """Serialize one page from already-computed lacing values, filling in
    a correct checksum. Raises if segment_table needs more than the 255
    entries a page's one-byte segment count can hold."""
    if len(segment_table) > 255:
        logger.error(
            f"Cannot build Ogg page: segment table has {len(segment_table)} "
            "entries, exceeding the 255-entry limit"
        )
        raise ValueError("Ogg page cannot have more than 255 segments")

    header = bytearray()
    header += b"OggS"
    header.append(0)  # stream_structure_version
    header.append(header_type)
    header += struct.pack("<q", granule_position)
    header += struct.pack("<I", serial_number)
    header += struct.pack("<I", sequence_number)
    header += b"\x00\x00\x00\x00"  # checksum placeholder, filled in below
    header.append(len(segment_table))
    header += segment_table

    page = bytes(header) + payload
    crc = ogg_crc32(page)
    return page[:22] + struct.pack("<I", crc) + page[26:]


def _lace_payload(payload: bytes) -> list[int]:
    """Split one packet's length into Ogg lacing segment values: 255-byte
    chunks, terminated by a segment shorter than 255 (an explicit 0 if
    the packet's length is an exact multiple of 255)."""
    segments = []
    n = len(payload)
    i = 0
    while True:
        chunk = min(255, n - i)
        segments.append(chunk)
        i += chunk
        if chunk < 255:
            break
    return segments


def build_pages(
    packets: list[bytes],
    serial_number: int,
    start_sequence_number: int,
    first_page_flag: bool = True,
) -> tuple[bytes, int]:
    """Serialize a list of complete packets into one or more Ogg pages,
    using standard lacing. A packet only spans multiple pages if its own
    lacing needs more than the 255 segments a single page can hold (the
    continued-packet header bit is set correctly in that case); this
    never happens for the small header packets this module writes in
    practice, but is handled rather than assumed away.

    Returns (page_bytes, next_sequence_number).
    """
    pages = bytearray()
    seq = start_sequence_number
    pending_segments: list[int] = []
    pending_payload = bytearray()
    first_page_emitted = False
    continues_from_prev = False

    def flush_page():
        nonlocal seq, pending_segments, pending_payload
        nonlocal first_page_emitted, continues_from_prev
        header_type = 0
        if not first_page_emitted and first_page_flag:
            header_type |= 0x02  # beginning of stream
        if continues_from_prev:
            header_type |= 0x01  # continued packet
        pages.extend(
            _build_single_page(
                serial_number,
                seq,
                0,  # header pages always carry granule_position 0
                header_type,
                bytes(pending_segments),
                bytes(pending_payload),
            )
        )
        seq += 1
        first_page_emitted = True
        continues_from_prev = False
        pending_segments = []
        pending_payload = bytearray()

    for packet in packets:
        seg_lengths = _lace_payload(packet)
        offset = 0
        while seg_lengths:
            room = 255 - len(pending_segments)
            if room <= 0:
                flush_page()
                continue
            take = seg_lengths[:room]
            seg_lengths = seg_lengths[room:]
            take_len = sum(take)
            pending_segments.extend(take)
            pending_payload += packet[offset : offset + take_len]
            offset += take_len
            if seg_lengths:
                # Packet didn't fully fit on this page - flush now, and
                # the next page continues this same packet.
                flush_page()
                continues_from_prev = True

    if pending_segments:
        flush_page()

    return bytes(pages), seq


def replace_comment_packet(data: bytes, new_comment_packet: bytes) -> bytes | None:
    """Rebuild an Ogg Vorbis file with its comment-header packet (the 2nd
    of the stream's 3 header packets) replaced.

    Every real encoder (libvorbis/oggenc, ffmpeg, ...) flushes the 3
    header packets onto their own page(s) before any audio packet
    begins - confirmed against real ffmpeg output, not assumed. That
    means only the page(s) holding those 3 packets ever need to be
    re-laid-out; every audio page's payload, granule position, and flags
    are carried through byte-for-byte, just renumbered and
    re-checksummed to stay contiguous with however many pages the new
    header now spans.

    Returns the new file bytes, or None if this file doesn't have that
    standard shape (e.g. a multiplexed multi-stream file, or a header
    packet sharing a page with the start of audio data) - callers should
    treat that as "can't safely rewrite this file" rather than guess.
    """
    pages = list(iter_pages(data))
    if len(pages) < 2:
        logger.warning(f"Cannot rewrite Ogg comment packet: file has only {len(pages)} page(s)")
        return None

    serial_number = pages[0]["serial_number"]

    packets: list[bytes] = []
    current = bytearray()
    header_pages_end = None

    for page in pages:
        if page["serial_number"] != serial_number:
            logger.warning(
                "Cannot rewrite Ogg comment packet: file is a multiplexed "
                "multi-stream file, which is out of scope"
            )
            return None  # multiplexed multi-stream file - out of scope

        seg_table = page["segment_table"]
        payload = page["payload"]
        p = 0
        for i, seg_len in enumerate(seg_table):
            current += payload[p : p + seg_len]
            p += seg_len
            if seg_len < 255:
                packets.append(bytes(current))
                current = bytearray()
                if len(packets) == 3:
                    # The 3rd header packet (setup) just completed -
                    # only safe to stop here if nothing else shares
                    # this page (i.e. no partial 4th packet trailing).
                    if i == len(seg_table) - 1:
                        header_pages_end = page["end"]
                    break
        if header_pages_end is not None or len(packets) >= 3:
            break

    if header_pages_end is None:
        logger.warning(
            "Cannot rewrite Ogg comment packet: could not locate the end of "
            "the header pages (unexpected file layout)"
        )
        return None

    # The identification header gets its own page, separate from the
    # comment+setup pages - not just because that's spec-legal lacing,
    # but because real-world readers (confirmed against mutagen) assume
    # it specifically: they parse the identification header as "read one
    # page", then start a fresh page-read loop for the comment header,
    # so any bytes sharing that first page beyond packet 1 are silently
    # never seen. This matches what every real encoder already does.
    id_header_bytes, seq_after_id = build_pages(
        [packets[0]], serial_number, pages[0]["sequence_number"], first_page_flag=True
    )
    comment_setup_bytes, next_sequence = build_pages(
        [new_comment_packet, packets[2]], serial_number, seq_after_id, first_page_flag=False
    )
    new_header_bytes = id_header_bytes + comment_setup_bytes

    tail = bytearray()
    seq = next_sequence
    for page in pages:
        if page["start"] < header_pages_end:
            continue
        tail.extend(
            _build_single_page(
                serial_number,
                seq,
                page["granule_position"],
                page["header_type"],
                page["segment_table"],
                page["payload"],
            )
        )
        seq += 1

    logger.debug(
        f"Rebuilt Ogg file with new comment packet: {len(new_header_bytes) + len(tail)} bytes total"
    )
    return new_header_bytes + bytes(tail)
