"""
One-time cleanup for FLAC files that carry more than one embedded PICTURE
block of the same picture type (almost always type 3 / front cover), left
behind by third-party taggers that appended a new cover without removing
the old one.

The app's reader (`ArtworkExtractor._extract_flac_artwork_all`) copes by
logging `Duplicate FLAC picture type N found; keeping first occurrence` and
taking the first - but "first" is arbitrary and is often the stale, smaller
cover. This script keeps the **largest** picture per type (by encoded byte
length) and strips the rest, so exactly one picture per type remains.

Block surgery reuses `FlacFileWriter`'s own primitives
(`_find_metadata_blocks` / `_serialize_blocks` / `atomic_write`), so a
leading ID3v2 tag, the audio frames, padding, seektable, and every
non-duplicated block pass through byte-for-byte unchanged - identical to
what a normal in-app artwork write does.

Dry run by default (reports what it would do, touches nothing). Pass
--apply to rewrite. Each rewritten file is backed up to a sibling
`<file>.bak` which is kept unless --discard-backups is given.

Run from the repo root:

    python scripts/dedupe_flac_duplicate_pictures.py            # dry run
    python scripts/dedupe_flac_duplicate_pictures.py --apply
"""

import argparse
from collections import defaultdict
from pathlib import Path
import sqlite3
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.foundation.logger_config import logger
from src.metadata.metadata_artwork import ArtworkExtractor
from src.metadata.metadata_flac_file_writer import FlacFileWriter
from src.metadata.metadata_writer_backup import atomic_write, backup_file, discard_backup

DB_PATH = "music_library.db"


def find_flac_paths(db_path: str = DB_PATH) -> list[str]:
    """Every FLAC track path the library knows about."""
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT track_file_path FROM tracks WHERE track_file_path LIKE '%.flac'"
        ).fetchall()
    finally:
        conn.close()
    return [r[0] for r in rows if r[0]]


def _picture_blocks(raw_blocks: list[tuple[int, bytes]]) -> list[tuple[int, int, int]]:
    """(index, picture_type, encoded_image_byte_length) for each structurally
    readable PICTURE block. Cheap header-only parse - no image decode - so a
    full-library scan stays fast; blocks whose length fields don't line up
    are skipped and thus never removed."""
    out = []
    for idx, (block_type, payload) in enumerate(raw_blocks):
        if block_type != 6:
            continue
        try:
            picture_type = struct.unpack(">I", payload[0:4])[0]
            mime_len = struct.unpack(">I", payload[4:8])[0]
            pos = 8 + mime_len
            desc_len = struct.unpack(">I", payload[pos : pos + 4])[0]
            pos += 4 + desc_len + 16  # skip description + width/height/depth/colors
            data_len = struct.unpack(">I", payload[pos : pos + 4])[0]
            pos += 4
        except struct.error:
            logger.warning(f"PICTURE block #{idx} header unreadable; leaving it in place")
            continue
        if pos + data_len != len(payload):
            logger.warning(f"PICTURE block #{idx} length fields inconsistent; leaving it in place")
            continue
        out.append((idx, picture_type, data_len))
    return out


def _decodes_as_image(payload: bytes) -> bool:
    """True if the PICTURE block's image data actually decodes - checked
    only for the block(s) a rewrite is about to keep, so we never strip a
    file's duplicates down to a single unreadable cover."""
    return ArtworkExtractor()._parse_flac_picture_block(payload) is not None


def _indices_to_drop(picture_blocks: list[tuple[int, int, int]]) -> list[int]:
    """Given (index, picture_type, size) tuples, return the indices to
    remove: for every picture type with more than one block, keep the
    largest (ties keep the earliest) and drop the rest."""
    by_type: dict[int, list[tuple[int, int, int]]] = defaultdict(list)
    for entry in picture_blocks:
        by_type[entry[1]].append(entry)

    drop = []
    for entries in by_type.values():
        if len(entries) < 2:
            continue
        # Largest size wins; on a tie the earliest index wins.
        keep = max(entries, key=lambda e: (e[2], -e[0]))
        drop.extend(e[0] for e in entries if e[0] != keep[0])
    return sorted(drop)


def dedupe_pictures_in_flac(path: str, *, apply: bool, keep_backup: bool = True) -> dict:
    """De-duplicate same-type PICTURE blocks in one FLAC file.

    Returns a result dict: {status, dropped, kept_by_type, [backup], [error]}.
    status is one of "clean" (nothing to do), "would-fix" (dry run),
    "fixed", or "error".
    """
    writer = FlacFileWriter()

    blocks = writer._find_metadata_blocks(path)
    if not blocks:
        return {"status": "error", "error": "no readable FLAC metadata blocks"}

    # Read only the metadata region for the scan - never the (huge) audio
    # frames - so a full-library pass isn't gated on disk throughput. The
    # apply branch re-reads the whole file once a rewrite is actually due.
    metadata_end = blocks[-1][1] + blocks[-1][2]
    with Path(path).open("rb") as f:
        header_data = f.read(metadata_end)

    raw_blocks = [(bt, header_data[pos : pos + size]) for bt, pos, size in blocks]
    picture_blocks = _picture_blocks(raw_blocks)
    drop = _indices_to_drop(picture_blocks)

    kept_by_type = {}
    for idx, ptype, size in picture_blocks:
        if idx not in drop:
            kept_by_type[ptype] = size

    if not drop:
        return {"status": "clean", "dropped": [], "kept_by_type": kept_by_type}

    dropped = [
        {"index": idx, "picture_type": ptype, "size": size}
        for idx, ptype, size in picture_blocks
        if idx in drop
    ]

    if not apply:
        return {"status": "would-fix", "dropped": dropped, "kept_by_type": kept_by_type}

    drop_set = set(drop)
    kept_picture_indices = {idx for idx, _, _ in picture_blocks} - drop_set
    unreadable = sorted(
        idx for idx in kept_picture_indices if not _decodes_as_image(raw_blocks[idx][1])
    )
    if unreadable:
        return {
            "status": "error",
            "error": f"picture block(s) {unreadable} we'd keep don't decode; not touching file",
        }

    with Path(path).open("rb") as f:
        file_data = f.read()
    audio_tail = writer._audio_tail(file_data, blocks)
    prefix = file_data[: writer._prefix_length(path)]
    new_blocks = [rb for i, rb in enumerate(raw_blocks) if i not in drop_set]
    new_data = writer._serialize_blocks(new_blocks, audio_tail, prefix)

    backup_path = backup_file(path)
    try:
        atomic_write(path, new_data)
    except (OSError, struct.error) as e:
        return {"status": "error", "error": f"write failed: {e}", "backup": backup_path}

    check = _verify(path, kept_by_type)
    if check is not None:
        return {"status": "error", "error": check, "backup": backup_path}

    if not keep_backup:
        discard_backup(backup_path)
        return {"status": "fixed", "dropped": dropped, "kept_by_type": kept_by_type}

    return {
        "status": "fixed",
        "dropped": dropped,
        "kept_by_type": kept_by_type,
        "backup": backup_path,
    }


def _verify(path: str, expected_kept_by_type: dict[int, int]) -> str | None:
    """Re-read the file; return an error string if the result isn't exactly
    one picture per expected type at the expected size, else None."""
    with Path(path).open("rb") as f:
        data = f.read()

    counts: dict[int, int] = defaultdict(int)
    sizes: dict[int, int] = {}
    for _, ptype, size in _picture_blocks(
        [(bt, data[pos : pos + sz]) for bt, pos, sz in FlacFileWriter()._find_metadata_blocks(path)]
    ):
        counts[ptype] += 1
        sizes[ptype] = size

    for ptype, expected_size in expected_kept_by_type.items():
        if counts.get(ptype) != 1:
            return f"post-write: expected 1 picture of type {ptype}, found {counts.get(ptype, 0)}"
        if sizes.get(ptype) != expected_size:
            return (
                f"post-write: type {ptype} size {sizes.get(ptype)} != "
                f"expected kept size {expected_size}"
            )
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="rewrite files (default: dry run)")
    parser.add_argument(
        "--discard-backups",
        action="store_true",
        help="delete each sibling .bak after a verified write",
    )
    parser.add_argument("--db", default=DB_PATH, help=f"library DB path (default: {DB_PATH})")
    args = parser.parse_args()

    paths = find_flac_paths(args.db)
    print(f"{len(paths)} FLAC track(s) in {args.db}")

    missing = fixed = clean = errors = 0
    affected = []

    for path in paths:
        if not Path(path).exists():
            missing += 1
            continue

        result = dedupe_pictures_in_flac(
            path, apply=args.apply, keep_backup=not args.discard_backups
        )
        status = result["status"]

        if status == "clean":
            clean += 1
        elif status == "error":
            errors += 1
            print(f"  ERROR  {path}\n         {result['error']}")
        else:  # would-fix / fixed
            affected.append((path, result))
            if status == "fixed":
                fixed += 1

    verb = "Fixed" if args.apply else "Would fix"
    print(f"\n{verb} {len(affected)} file(s):")
    for path, result in affected:
        drops = ", ".join(f"type {d['picture_type']} @ {d['size']}B" for d in result["dropped"])
        keeps = ", ".join(f"type {t} @ {s}B" for t, s in sorted(result["kept_by_type"].items()))
        print(f"  {path}")
        print(f"      drop: {drops}   keep: {keeps}")

    print(
        f"\n{clean} already clean, {missing} missing on disk, {errors} error(s)."
        + ("" if args.apply else "\n\nDry run - pass --apply to rewrite.")
    )


if __name__ == "__main__":
    main()
