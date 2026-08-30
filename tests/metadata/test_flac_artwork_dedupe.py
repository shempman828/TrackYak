"""Regression: writing FLAC artwork must collapse duplicate same-type
PICTURE blocks, and the one-time cleanup script must keep the largest
picture per type.

Bug: some FLACs carry two type-3 (front cover) PICTURE blocks because a
third-party tagger appended a new cover without removing the old one.
`FlacFileWriter._find_picture_index_for_role` returned a single index, so
`write_artwork` stripped only the first duplicate and appended the new
picture -- leaving two type-3 blocks. The reader's "keep first occurrence"
then returned the stale one, and `verify_artwork_write` rolled the whole
write back: artwork writes to an already-duped FLAC failed outright.

  AC1  find_picture_indices_for_role returns *every* index for a role,
       and the singular helper still returns the first.
  AC2  write_artwork on a FLAC with two type-3 blocks succeeds and leaves
       exactly one type-3 block holding the newly written bytes.
  AC3  removing artwork (image_bytes=None) strips every type-3 block.
  AC4  the cleanup script keeps the largest picture per type, leaves
       other-type blocks untouched, and is idempotent.
"""

import io

from PIL import Image
import pytest

from scripts.dedupe_flac_duplicate_pictures import _indices_to_drop, dedupe_pictures_in_flac
from src.metadata.metadata_artwork import ArtworkExtractor
from src.metadata.metadata_flac_file_writer import FlacFileWriter
from src.metadata.metadata_image_utils import (
    find_picture_index_for_role,
    find_picture_indices_for_role,
)
from src.metadata.metadata_writer_flac_picture import FlacPictureWriter

_STREAMINFO = b"\x00" * 34  # contents irrelevant to the metadata surgery under test


def _png(size: int, colour: tuple[int, int, int]) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (size, size), colour).save(buf, "PNG")
    return buf.getvalue()


def _make_flac(path, picture_payloads: list[bytes]) -> None:
    """A minimal but structurally valid FLAC: fLaC + STREAMINFO + the given
    raw PICTURE payloads, no audio frames."""
    writer = FlacFileWriter()
    blocks = [(0, _STREAMINFO)] + [(6, payload) for payload in picture_payloads]
    path.write_bytes(writer._serialize_blocks(blocks, audio_tail=b"", prefix=b""))


def _front_block(size: int, colour=(10, 20, 30)) -> bytes:
    return FlacPictureWriter().build_picture_block("front", _png(size, colour))


def _unpack_type(item):
    """picture_type_for_item stand-in: items are (picture_type,) tuples."""
    return item[0]


# --- AC1 ---------------------------------------------------------------------


def test_indices_helper_returns_every_match_for_role():
    items = [(3,), (4,), (3,), (99,)]

    assert find_picture_indices_for_role(items, "front", _unpack_type) == [0, 2]
    assert find_picture_indices_for_role(items, "rear", _unpack_type) == [1]
    assert find_picture_indices_for_role(items, "liner", _unpack_type) == []
    # singular helper still yields the first match / None
    assert find_picture_index_for_role(items, "front", _unpack_type) == 0
    assert find_picture_index_for_role(items, "liner", _unpack_type) is None


def test_untyped_single_picture_still_maps_to_front():
    assert find_picture_indices_for_role([(0,)], "front", _unpack_type) == [0]
    # ambiguous once there's more than one untyped picture
    assert find_picture_indices_for_role([(0,), (0,)], "front", _unpack_type) == []


# --- AC2 / AC3 -------------------------------------------------------------


def test_write_artwork_collapses_duplicate_front_blocks(tmp_path):
    flac = tmp_path / "dup.flac"
    _make_flac(flac, [_front_block(60, (200, 0, 0)), _front_block(12, (0, 0, 200))])
    # sanity: the raw file really does carry two PICTURE blocks
    raw_pics = [b for b in FlacFileWriter()._find_metadata_blocks(str(flac)) if b[0] == 6]
    assert len(raw_pics) == 2

    new_cover = _png(40, (0, 200, 0))
    assert FlacFileWriter().write_artwork(str(flac), "front", new_cover) is True

    all_pictures = ArtworkExtractor()._extract_flac_artwork_all(flac.read_bytes())
    assert list(all_pictures) == [3]
    assert all_pictures[3]["data"] == new_cover


def test_write_artwork_removal_strips_all_duplicate_blocks(tmp_path):
    flac = tmp_path / "dup.flac"
    _make_flac(flac, [_front_block(60, (200, 0, 0)), _front_block(12, (0, 0, 200))])

    assert FlacFileWriter().write_artwork(str(flac), "front", None) is True

    raw_pics = [b for b in FlacFileWriter()._find_metadata_blocks(str(flac)) if b[0] == 6]
    assert raw_pics == []


# --- AC4 -----------------------------------------------------------------------


def test_indices_to_drop_keeps_largest_per_type():
    # (index, picture_type, size)
    blocks = [(0, 3, 900), (1, 3, 200), (2, 4, 50), (3, 3, 900), (4, 5, 10)]
    # type 3: keep index 0 (largest, tie broken by earliest), drop 1 and 3
    assert _indices_to_drop(blocks) == [1, 3]


def test_cleanup_script_keeps_largest_and_is_idempotent(tmp_path):
    flac = tmp_path / "dup.flac"
    big = FlacPictureWriter().build_picture_block("front", _png(80, (1, 2, 3)))
    small = FlacPictureWriter().build_picture_block("front", _png(10, (4, 5, 6)))
    rear = FlacPictureWriter().build_picture_block("rear", _png(20, (7, 8, 9)))
    _make_flac(flac, [small, big, rear])  # deliberately: smaller one first

    big_size = ArtworkExtractor()._parse_flac_picture_block(big)["size"]

    result = dedupe_pictures_in_flac(str(flac), apply=True)
    assert result["status"] == "fixed"
    assert [d["picture_type"] for d in result["dropped"]] == [3]
    assert (flac.parent / (flac.name + ".bak")).exists()

    all_pictures = ArtworkExtractor()._extract_flac_artwork_all(flac.read_bytes())
    assert sorted(all_pictures) == [3, 4]  # rear (type 4) untouched
    assert all_pictures[3]["size"] == big_size

    # nothing left to do on a second pass
    assert dedupe_pictures_in_flac(str(flac), apply=True)["status"] == "clean"


def test_cleanup_script_leaves_clean_file_untouched(tmp_path):
    flac = tmp_path / "clean.flac"
    _make_flac(flac, [_front_block(40)])
    before = flac.read_bytes()

    result = dedupe_pictures_in_flac(str(flac), apply=True)

    assert result["status"] == "clean"
    assert flac.read_bytes() == before
    assert not (flac.parent / (flac.name + ".bak")).exists()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
