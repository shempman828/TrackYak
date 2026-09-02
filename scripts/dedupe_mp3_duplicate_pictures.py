"""
One-time cleanup for MP3 files that carry more than one embedded APIC
frame of the same picture type (almost always type 3 / front cover), left
behind by third-party taggers that appended a new cover without removing
the old one.

The app's reader (`ArtworkExtractor._extract_mp3_artwork_all`) copes by
logging `Duplicate ID3 picture type N found; keeping first occurrence` and
taking the first - but "first" is arbitrary and is often the stale, smaller
cover. This script keeps the **largest** picture per type (by embedded
image byte length) and strips the rest, so exactly one picture per type
remains.

Frame surgery reuses `MP3FileWriter`'s own primitives (`_find_frames` /
`_rewrap_frame_as_v3` / `_find_audio_start` / `build_id3_tag` /
`atomic_write`), so the whole tag is rewritten as ID3v2.3 with every
non-duplicated frame carried through body-for-body unchanged and the audio
frames untouched - identical to what a normal in-app artwork write does.

Dry run by default (reports what it would do, touches nothing). Pass
--apply to rewrite. Each rewritten file is backed up to a sibling
`<file>.bak` which is kept unless --discard-backups is given.

Run from the repo root:

    python scripts/dedupe_mp3_duplicate_pictures.py            # dry run
    python scripts/dedupe_mp3_duplicate_pictures.py --apply
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
from src.metadata.metadata_mp3_file_writer import MP3FileWriter
from src.metadata.metadata_writer_backup import atomic_write, backup_file, discard_backup

DB_PATH = "music_library.db"


def find_mp3_paths(db_path: str = DB_PATH) -> list[str]:
    """Every MP3 track path the library knows about."""
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT track_file_path FROM tracks WHERE track_file_path LIKE '%.mp3'"
        ).fetchall()
    finally:
        conn.close()
    return [r[0] for r in rows if r[0]]


def _id3_version_major(path: str) -> int | None:
    """ID3v2 major version of the file's leading tag, or None if there's no
    ID3v2.3/2.4 tag this writer can rewrite."""
    try:
        with Path(path).open("rb") as f:
            header = f.read(10)
    except OSError:
        return None
    if len(header) < 10 or header[0:3] != b"ID3" or header[3] not in (3, 4):
        return None
    return header[3]


def _picture_frames(
    raw_frames: list[tuple[str, bytes]], version_major: int
) -> list[tuple[int, int, int]]:
    """(index, picture_type, embedded_image_byte_length) for each APIC/PIC
    frame whose body parses. Frames that don't parse are skipped and thus
    never removed."""
    out = []
    extractor = ArtworkExtractor()
    for idx, (frame_id, frame_bytes) in enumerate(raw_frames):
        if frame_id not in ("APIC", "PIC"):
            continue
        parsed = extractor._parse_id3_apic_frame(frame_bytes[10:], version_major)
        if parsed is None:
            logger.warning(f"APIC frame #{idx} body unreadable; leaving it in place")
            continue
        out.append((idx, parsed["picture_type"], parsed["size"]))
    return out


def _decodes_as_image(frame_bytes: bytes, version_major: int) -> bool:
    """True if the APIC frame's image data actually decodes - checked only
    for the frame(s) a rewrite is about to keep, so we never strip a file's
    duplicates down to a single unreadable cover."""
    return ArtworkExtractor()._parse_id3_apic_frame(frame_bytes[10:], version_major) is not None


def _indices_to_drop(picture_frames: list[tuple[int, int, int]]) -> list[int]:
    """Given (index, picture_type, size) tuples, return the indices to
    remove: for every picture type with more than one frame, keep the
    largest (ties keep the earliest) and drop the rest."""
    by_type: dict[int, list[tuple[int, int, int]]] = defaultdict(list)
    for entry in picture_frames:
        by_type[entry[1]].append(entry)

    drop = []
    for entries in by_type.values():
        if len(entries) < 2:
            continue
        # Largest size wins; on a tie the earliest index wins.
        keep = max(entries, key=lambda e: (e[2], -e[0]))
        drop.extend(e[0] for e in entries if e[0] != keep[0])
    return sorted(drop)


def dedupe_pictures_in_mp3(path: str, *, apply: bool, keep_backup: bool = True) -> dict:
    """De-duplicate same-type APIC frames in one MP3 file.

    Returns a result dict: {status, dropped, kept_by_type, [backup], [error]}.
    status is one of "clean" (nothing to do), "would-fix" (dry run),
    "fixed", or "error".
    """
    writer = MP3FileWriter()

    version_major = _id3_version_major(path)
    if version_major is None:
        return {"status": "error", "error": "no writable ID3v2.3/2.4 tag"}

    frames = writer._find_frames(path)
    if not frames:
        return {"status": "error", "error": "no readable ID3 frames"}

    # The ID3 tag sits at the front of the file, so read only up to the
    # audio start for the scan - never the (huge) audio frames. The apply
    # branch re-reads the audio once a rewrite is actually due.
    audio_start = writer._find_audio_start(path)
    with Path(path).open("rb") as f:
        header_data = f.read(audio_start)

    raw_frames = [(fid, header_data[pos : pos + size]) for fid, pos, size in frames]
    picture_frames = _picture_frames(raw_frames, version_major)
    drop = _indices_to_drop(picture_frames)

    kept_by_type = {}
    for idx, ptype, size in picture_frames:
        if idx not in drop:
            kept_by_type[ptype] = size

    if not drop:
        return {"status": "clean", "dropped": [], "kept_by_type": kept_by_type}

    dropped = [
        {"index": idx, "picture_type": ptype, "size": size}
        for idx, ptype, size in picture_frames
        if idx in drop
    ]

    if not apply:
        return {"status": "would-fix", "dropped": dropped, "kept_by_type": kept_by_type}

    drop_set = set(drop)
    kept_picture_indices = {idx for idx, _, _ in picture_frames} - drop_set
    unreadable = sorted(
        idx
        for idx in kept_picture_indices
        if not _decodes_as_image(raw_frames[idx][1], version_major)
    )
    if unreadable:
        return {
            "status": "error",
            "error": f"APIC frame(s) {unreadable} we'd keep don't decode; not touching file",
        }

    # Mirror MP3FileWriter.write_artwork's mutate(): re-header every kept
    # frame as v2.3 and rebuild the whole tag, then carry the audio through.
    kept_frame_bytes = [
        writer._rewrap_frame_as_v3(fid, frame_bytes[10:])
        for i, (fid, frame_bytes) in enumerate(raw_frames)
        if i not in drop_set
    ]
    new_tag = writer.id3_writer.build_id3_tag(kept_frame_bytes)

    with Path(path).open("rb") as f:
        f.seek(audio_start)
        audio_data = f.read()

    backup_path = backup_file(path)
    try:
        atomic_write(path, new_tag + audio_data)
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
    version_major = _id3_version_major(path)
    if version_major is None:
        return "post-write: no readable ID3v2.3/2.4 tag"

    writer = MP3FileWriter()
    frames = writer._find_frames(path)
    audio_start = writer._find_audio_start(path)
    with Path(path).open("rb") as f:
        header_data = f.read(audio_start)
    raw_frames = [(fid, header_data[pos : pos + size]) for fid, pos, size in frames]

    counts: dict[int, int] = defaultdict(int)
    sizes: dict[int, int] = {}
    for _, ptype, size in _picture_frames(raw_frames, version_major):
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

    paths = find_mp3_paths(args.db)
    print(f"{len(paths)} MP3 track(s) in {args.db}")

    missing = fixed = clean = errors = 0
    affected = []

    for path in paths:
        if not Path(path).exists():
            missing += 1
            continue

        result = dedupe_pictures_in_mp3(
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
