"""Regression: writing MP3 artwork must collapse duplicate same-type APIC
frames, and the one-time cleanup script must keep the largest picture per
type.

Bug: some MP3s carry two type-3 (front cover) APIC frames because a
third-party tagger appended a new cover without removing the old one.
`MP3FileWriter._find_picture_index_for_role` returned a single index, so
`write_artwork` stripped only the first duplicate and appended the new
picture -- leaving two type-3 frames. The reader's "keep first occurrence"
then returned the stale one, and `verify_artwork_write` rolled the whole
write back: artwork writes to an already-duped MP3 failed outright. This
mirrors the FLAC fix in commit 486773a.

  AC1  write_artwork on an MP3 with two type-3 APIC frames succeeds and
       leaves exactly one type-3 frame holding the newly written bytes.
  AC2  removing artwork (image_bytes=None) strips every type-3 frame.
  AC3  a non-picture frame (TIT2) is carried through the dedup write.
  AC4  the cleanup script keeps the largest picture per type, leaves
       other-type frames untouched, and is idempotent.
"""

import io
import struct

from PIL import Image
import pytest

from scripts.dedupe_mp3_duplicate_pictures import _indices_to_drop, dedupe_pictures_in_mp3
from src.metadata.metadata_artwork import ArtworkExtractor
from src.metadata.metadata_id3_writer import ID3TagWriter
from src.metadata.metadata_mp3_file_writer import MP3FileWriter
from src.metadata.metadata_writer_id3_picture import Id3PictureWriter


def _png(size: int, colour: tuple[int, int, int]) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (size, size), colour).save(buf, "PNG")
    return buf.getvalue()


def _tit2(text: str) -> bytes:
    body = b"\x00" + text.encode("iso-8859-1")  # encoding byte + ISO-8859-1 text
    return b"TIT2" + struct.pack(">I", len(body)) + b"\x00\x00" + body


def _apic(role: str, size: int, colour=(10, 20, 30)) -> bytes:
    return Id3PictureWriter().build_apic_frame(role, _png(size, colour))


def _make_mp3(path, frames: list[bytes]) -> None:
    """A minimal MP3: just an ID3v2.3 tag built from the given raw frames,
    no audio."""
    path.write_bytes(ID3TagWriter().build_id3_tag(frames))


def _apic_count(path) -> int:
    return sum(1 for fid, _, _ in MP3FileWriter()._find_frames(str(path)) if fid == "APIC")


# --- AC1 / AC2 -------------------------------------------------------------


def test_write_artwork_collapses_duplicate_front_frames(tmp_path):
    mp3 = tmp_path / "dup.mp3"
    _make_mp3(mp3, [_apic("front", 60, (200, 0, 0)), _apic("front", 12, (0, 0, 200))])
    assert _apic_count(mp3) == 2

    new_cover = _png(40, (0, 200, 0))
    assert MP3FileWriter().write_artwork(str(mp3), "front", new_cover) is True

    all_pictures = ArtworkExtractor()._extract_mp3_artwork_all(mp3.read_bytes())
    assert list(all_pictures) == [3]
    assert all_pictures[3]["data"] == new_cover
    assert _apic_count(mp3) == 1


def test_write_artwork_removal_strips_all_duplicate_frames(tmp_path):
    mp3 = tmp_path / "dup.mp3"
    _make_mp3(mp3, [_apic("front", 60, (200, 0, 0)), _apic("front", 12, (0, 0, 200))])

    assert MP3FileWriter().write_artwork(str(mp3), "front", None) is True

    assert _apic_count(mp3) == 0


# --- AC3 -----------------------------------------------------------------------


def test_non_picture_frame_survives_dedup_write(tmp_path):
    mp3 = tmp_path / "dup.mp3"
    _make_mp3(
        mp3, [_tit2("Keep Me"), _apic("front", 60, (200, 0, 0)), _apic("front", 12, (0, 0, 200))]
    )

    assert MP3FileWriter().write_artwork(str(mp3), "front", _png(40, (0, 200, 0))) is True

    frame_map = MP3FileWriter().get_existing_frame_map(str(mp3))
    assert b"Keep Me" in frame_map["TIT2"]
    assert _apic_count(mp3) == 1


# --- AC4 -----------------------------------------------------------------------


def test_indices_to_drop_keeps_largest_per_type():
    # (index, picture_type, size)
    frames = [(0, 3, 900), (1, 3, 200), (2, 4, 50), (3, 3, 900), (4, 5, 10)]
    # type 3: keep index 0 (largest, tie broken by earliest), drop 1 and 3
    assert _indices_to_drop(frames) == [1, 3]


def test_cleanup_script_keeps_largest_and_is_idempotent(tmp_path):
    mp3 = tmp_path / "dup.mp3"
    big = Id3PictureWriter().build_apic_frame("front", _png(80, (1, 2, 3)))
    small = Id3PictureWriter().build_apic_frame("front", _png(10, (4, 5, 6)))
    rear = Id3PictureWriter().build_apic_frame("rear", _png(20, (7, 8, 9)))
    _make_mp3(mp3, [small, big, rear])  # deliberately: smaller one first

    big_size = ArtworkExtractor()._parse_id3_apic_frame(big[10:], 3)["size"]

    result = dedupe_pictures_in_mp3(str(mp3), apply=True)
    assert result["status"] == "fixed"
    assert [d["picture_type"] for d in result["dropped"]] == [3]
    assert (mp3.parent / (mp3.name + ".bak")).exists()

    all_pictures = ArtworkExtractor()._extract_mp3_artwork_all(mp3.read_bytes())
    assert sorted(all_pictures) == [3, 4]  # rear (type 4) untouched
    assert all_pictures[3]["size"] == big_size

    # nothing left to do on a second pass
    assert dedupe_pictures_in_mp3(str(mp3), apply=True)["status"] == "clean"


def test_cleanup_script_leaves_clean_file_untouched(tmp_path):
    mp3 = tmp_path / "clean.mp3"
    _make_mp3(mp3, [_apic("front", 40)])
    before = mp3.read_bytes()

    result = dedupe_pictures_in_mp3(str(mp3), apply=True)

    assert result["status"] == "clean"
    assert mp3.read_bytes() == before
    assert not (mp3.parent / (mp3.name + ".bak")).exists()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
