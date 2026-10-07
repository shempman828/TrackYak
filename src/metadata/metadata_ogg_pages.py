"""Minimal Ogg page/packet reader and writer (RFC 3533) for .ogg/.opus tag reads and writes."""

# Lacing: a 255-byte segment continues the packet (onto the next page if it is the last
# segment); any shorter segment ends it.

from collections.abc import Iterator
import struct

from src.foundation.logger_config import logger


def iter_packets(data: bytes, max_packets: int = 8) -> Iterator[bytes]:
    """Yield up to max_packets reconstructed packets from the start of data (enough for the header packets)."""
    pos = 0
    current = bytearray()
    packets_yielded = 0

    while pos + 27 <= len(data) and data[pos : pos + 4] == b"OggS" and packets_yielded < max_packets:
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
    """Build the lookup table for ogg_crc32."""
    table = []
    for i in range(256):
        r = i << 24
        for _ in range(8):
            r = ((r << 1) ^ 0x04C11DB7) & 0xFFFFFFFF if r & 0x80000000 else (r << 1) & 0xFFFFFFFF
        table.append(r)
    return table


_CRC_TABLE = _make_crc_table()


def ogg_crc32(data: bytes) -> int:
    """Compute the Ogg page checksum (MSB-first CRC-32, poly 0x04C11DB7; not zlib's CRC-32)."""
    # The page's own checksum field (bytes 22:26) must be zeroed in data first.
    crc = 0
    for byte in data:
        crc = ((crc << 8) & 0xFFFFFFFF) ^ _CRC_TABLE[((crc >> 24) & 0xFF) ^ byte]
    return crc & 0xFFFFFFFF


def iter_pages(data: bytes) -> Iterator[dict]:
    """Yield each Ogg page as a dict of header fields, payload, and its (start, end) span."""
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


def _build_single_page(serial_number: int, sequence_number: int, granule_position: int, header_type: int, segment_table: bytes, payload: bytes) -> bytes:
    """Serialize one page with a correct checksum; raise ValueError for more than 255 segments."""
    if len(segment_table) > 255:
        logger.error(f"Cannot build Ogg page: segment table has {len(segment_table)} entries, exceeding the 255-entry limit")
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
    """Split a packet length into lacing values: 255s, then one shorter value (0 for an exact multiple)."""
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


def build_pages(packets: list[bytes], serial_number: int, start_sequence_number: int, first_page_flag: bool = True) -> tuple[bytes, int]:
    """Serialize complete packets into Ogg pages, returning (page_bytes, next_sequence_number)."""
    # A packet spans pages only when it needs more than 255 segments; the continued bit is then set.
    pages = bytearray()
    seq = start_sequence_number
    pending_segments: list[int] = []
    pending_payload = bytearray()
    first_page_emitted = False
    continues_from_prev = False

    def flush_page():
        """Emit the pending segments as one page and reset the page state."""
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
    """Return an Ogg Vorbis file's bytes with the comment packet replaced, or None if the layout is not safe to rewrite."""
    # Encoders put the 3 Vorbis header packets on their own pages, so only those pages are rebuilt;
    # audio pages carry through, renumbered only when the header page count changes.
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
            logger.warning("Cannot rewrite Ogg comment packet: file is a multiplexed multi-stream file, which is out of scope")
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

    if not packets or not packets[0].startswith(b"\x01vorbis"):
        # Opus/Speex/FLAC-in-Ogg have a different header layout; a Vorbis rewrite would corrupt them.
        logger.warning("Cannot rewrite Ogg comment packet: stream is not Ogg Vorbis")
        return None

    if header_pages_end is None:
        logger.warning("Cannot rewrite Ogg comment packet: could not locate the end of the header pages (unexpected file layout)")
        return None

    # The identification header gets its own page: readers such as mutagen read it as "one page".
    id_header_bytes, seq_after_id = build_pages([packets[0]], serial_number, pages[0]["sequence_number"], first_page_flag=True)
    comment_setup_bytes, next_sequence = build_pages([new_comment_packet, packets[2]], serial_number, seq_after_id, first_page_flag=False)
    new_header_bytes = id_header_bytes + comment_setup_bytes

    audio_pages = [page for page in pages if page["start"] >= header_pages_end]
    if not audio_pages or audio_pages[0]["sequence_number"] == next_sequence:
        # Same page count as before: the audio pages are already numbered correctly, so copy them byte for byte.
        return new_header_bytes + data[header_pages_end:]

    tail = bytearray()
    seq = next_sequence
    for page in audio_pages:
        tail.extend(_build_single_page(serial_number, seq, page["granule_position"], page["header_type"], page["segment_table"], page["payload"]))
        seq += 1

    logger.debug(f"Rebuilt Ogg file with new comment packet: {len(new_header_bytes) + len(tail)} bytes total")
    return new_header_bytes + bytes(tail)
