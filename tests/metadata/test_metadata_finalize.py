"""Regression tests for the src/metadata finalize pass (tag build/parse round trips, merge, properties)."""

import struct
from types import SimpleNamespace

import pytest

from src.metadata.metadata_artwork import ArtworkExtractor
from src.metadata.metadata_ogg_pages import build_pages, iter_pages, replace_comment_packet
from src.metadata.metadata_properties import AudioPropertiesExtractor
from src.metadata.metadata_raw_tags import RawTagExtractor
from src.metadata.metadata_text import TextMetadataExtractor, flatten_text_metadata
from src.metadata.writers.metadata_flac_file_writer import FlacFileWriter
from src.metadata.writers.metadata_id3_frame_builder import ID3FrameBuilder
from src.metadata.writers.metadata_id3_writer import ID3TagWriter
from src.metadata.writers.metadata_mp3_file_writer import MP3FileWriter
from src.metadata.writers.metadata_vorbis_comment_builder import VorbisCommentBuilder
from src.metadata.writers.metadata_writer_merge import id3_frame_key, merge_id3_frames, merge_vorbis_comments
from src.metadata.writers.metadata_writer_types import WriteMode

_TRACK_FIELDS = [
    "play_count",
    "bpm",
    "track_copyright",
    "track_description",
    "key",
    "duration",
    "isrc",
    "work_name",
    "lyrics",
    "comment",
    "MBID",
    "recorded_year",
    "user_rating",
    "remaster_year",
    "first_performed_year",
    "side",
]


def _track(**fields):
    base = dict.fromkeys(_TRACK_FIELDS)
    base.update(track_id=1, track_number=1, track_name="Song")
    base.update(fields)
    return SimpleNamespace(**base)


def _album(**fields):
    base = {"album_name": "Album", "album_language": None, "MBID": None, "release_type": None, "status": None, "release_year": None}
    base.update(fields)
    return SimpleNamespace(**base)


def _data(track, album=None, **extra):
    data = {"track": track, "album": album, "disc": None, "artists_with_roles": [], "album_artists_with_roles": [], "genres": [], "moods": [], "publishers": [], "places": []}
    data.update(extra)
    return data


def _raw_id3(frames):
    return RawTagExtractor()._extract_id3_tags(ID3TagWriter().build_id3_tag(frames))


# --- B1: TXXX/UFID mapping keys become real frames -------------------------------


def test_mbid_mappings_build_valid_txxx_and_ufid_frames():
    frames = ID3FrameBuilder().build_frames(_data(_track(MBID="rec-1"), _album(MBID="alb-1")))

    assert all(frame[0:4].isalnum() for frame in frames)
    raw = _raw_id3(frames)
    assert raw["UFID:http://musicbrainz.org"] == ["rec-1"]
    assert raw["TXXX:MusicBrainz Album Id"] == ["alb-1"]
    assert raw["TALB"] == ["Album"]


# --- B2/B3: COMM/USLT layout --------------------------------------------------


def test_comment_and_lyrics_round_trip_without_language_prefix():
    raw = _raw_id3([ID3TagWriter().create_comment_frame("hello"), ID3TagWriter().create_lyrics_frame("la la")])

    assert raw["COMM"] == ["hello"]
    assert raw["USLT"] == ["la la"]


def test_comment_frame_has_encoding_byte_first():
    body = ID3TagWriter().create_comment_frame("x")[10:]
    assert body[0] == 1 and body[1:4] == b"eng"


def test_legacy_comment_layout_is_still_read():
    legacy = b"eng" + b"\x01" + "old".encode("utf-16")
    frame = b"COMM" + struct.pack(">I", len(legacy)) + b"\x00\x00" + legacy
    assert _raw_id3([frame])["COMM"] == ["old"]


def test_described_comment_is_kept_apart_from_user_comment():
    body = b"\x00eng" + b"iTunNORM\x00" + b"0000"
    frame = b"COMM" + struct.pack(">I", len(body)) + b"\x00\x00" + body
    raw = _raw_id3([frame])
    assert "COMM" not in raw
    assert raw["COMM:iTunNORM"] == ["0000"]


# --- B4/B5: merge keys and REPLACE_ALL artwork --------------------------------


def test_update_existing_keeps_unrelated_txxx_frames():
    writer = ID3TagWriter()
    existing_gain = writer.create_txxx_frame("REPLAYGAIN_TRACK_GAIN", "-6 dB")
    existing_playlist = writer.create_txxx_frame("PLAYLIST", "Old")
    new_playlist = writer.create_txxx_frame("PLAYLIST", "New")

    merged = merge_id3_frames([("TXXX", existing_gain), ("TXXX", existing_playlist)], [new_playlist], WriteMode.UPDATE_EXISTING)

    assert existing_gain in merged
    assert existing_playlist not in merged
    assert new_playlist in merged


def test_add_only_adds_txxx_with_new_description():
    writer = ID3TagWriter()
    existing = writer.create_txxx_frame("OTHER", "x")
    new = writer.create_txxx_frame("PLAYLIST", "New")
    assert new in merge_id3_frames([("TXXX", existing)], [new], WriteMode.ADD_ONLY)


def test_frame_key_distinguishes_descriptions():
    writer = ID3TagWriter()
    assert id3_frame_key(writer.create_txxx_frame("A", "1")) == "TXXX:A"
    assert id3_frame_key(writer.create_comment_frame("c")) == "COMM:eng:"
    assert id3_frame_key(writer.create_text_frame("TIT2", "t")) == "TIT2"


def test_replace_all_keeps_artwork():
    apic = b"APIC" + struct.pack(">I", 3) + b"\x00\x00abc"
    title = ID3TagWriter().create_text_frame("TIT2", "old")
    merged = merge_id3_frames([("APIC", apic), ("TIT2", title)], [ID3TagWriter().create_text_frame("TIT2", "new")], WriteMode.REPLACE_ALL)
    assert apic in merged and title not in merged

    vorbis = merge_vorbis_comments({"METADATA_BLOCK_PICTURE": ["pic"], "TITLE": ["old"]}, {"TITLE": "new"}, WriteMode.REPLACE_ALL)
    assert vorbis == {"METADATA_BLOCK_PICTURE": ["pic"], "TITLE": "new"}


# --- B6/B7: MP3 frame size and sample rate -------------------------------------


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (0xFFFB9064, 417),  # MPEG-1 Layer III 128 kbps 44.1 kHz
        (0xFFF39064, 261),  # MPEG-2 Layer III 80 kbps 22.05 kHz -> 72 * 80000 // 22050
    ],
)
def test_mp3_frame_size(header, expected):
    extractor = AudioPropertiesExtractor()
    size = extractor._parse_mp3_frame_size(header, extractor._parse_mp3_bitrate(header), extractor._parse_mp3_sample_rate(header))
    assert size == expected


def test_mpeg25_sample_rate():
    assert AudioPropertiesExtractor()._parse_mp3_sample_rate(0xFFE30000) == 11025


def test_cbr_mp3_without_xing_header_gets_full_duration():
    header = struct.pack(">I", 0xFFFB9064)  # 128 kbps 44.1 kHz, 417-byte frames
    frame = header + b"\x00" * 413
    data = frame * 100

    props = AudioPropertiesExtractor()._extract_mp3_properties(data)

    assert props["duration"] == pytest.approx(100 * 1152 / 44100)
    assert props["bit_rate"] == 128


# --- B8: APIC with a UTF-16 description ----------------------------------------


def test_apic_with_utf16_description_is_read(tmp_path):
    from io import BytesIO

    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (4, 4), (1, 2, 3)).save(buf, "PNG")
    png = buf.getvalue()
    body = b"\x01image/png\x00" + bytes([3]) + "".encode("utf-16") + b"\x00\x00" + png
    frame = b"APIC" + struct.pack(">I", len(body)) + b"\x00\x00" + body

    picture = ArtworkExtractor()._extract_mp3_artwork(ID3TagWriter().build_id3_tag([frame]))

    assert picture is not None and picture["data"] == png


# --- B9/B10/B11/E1/E2/E5: ID3 text decoding ------------------------------------


def _text_frame(frame_id, encoding, payload):
    body = bytes([encoding]) + payload
    return frame_id.encode() + struct.pack(">I", len(body)) + b"\x00\x00" + body


def test_utf16be_and_multi_value_text():
    raw = _raw_id3([_text_frame("TIT2", 2, "Hé".encode("utf-16-be")), _text_frame("TPE1", 3, b"A\x00B")])
    assert raw["TIT2"] == ["Hé"]
    assert raw["TPE1"] == ["A", "B"]


def test_txxx_utf16_little_endian_bom():
    payload = "Desc".encode("utf-16-le")
    body = b"\x01" + b"\xff\xfe" + payload + b"\x00\x00" + b"\xff\xfe" + "Val".encode("utf-16-le")
    frame = b"TXXX" + struct.pack(">I", len(body)) + b"\x00\x00" + body
    assert _raw_id3([frame])["TXXX:Desc"] == ["Val"]


def test_id3v1_does_not_override_v2():
    tag = ID3TagWriter().build_id3_tag([ID3TagWriter().create_text_frame("TIT2", "A long title beyond thirty characters")])
    v1 = b"TAG" + b"Short".ljust(30, b"\x00") + b"Artist".ljust(30, b"\x00") + b"\x00" * 30 + b"\x00" * 4 + b"\x00" * 30 + b"\x00"
    raw = RawTagExtractor()._extract_id3_tags(tag + b"\x00" * 10 + v1)
    assert raw["TIT2"] == ["A long title beyond thirty characters"]
    assert raw["TPE1"] == ["Artist"]


def test_id3v22_frames_map_to_v23_ids():
    body = b"\x00Title"
    frame = b"TT2" + len(body).to_bytes(3, "big") + body
    data = b"ID3\x02\x00\x00" + ID3TagWriter().sync_safe_int(len(frame)) + frame
    assert RawTagExtractor()._extract_id3_tags(data)["TIT2"] == ["Title"]


def test_extended_header_is_skipped():
    frame = ID3TagWriter().create_text_frame("TIT2", "T")
    ext = struct.pack(">I", 6) + b"\x00" * 6  # v2.3: size excludes itself
    body = ext + frame
    data = b"ID3\x03\x00\x40" + ID3TagWriter().sync_safe_int(len(body)) + body
    assert RawTagExtractor()._extract_id3_tags(data)["TIT2"] == ["T"]


def test_tipl_nul_separated_pairs():
    metadata = TextMetadataExtractor("x.mp3", "mp3", {"TIPL": ["Producer\x00Ann\x00Mixer\x00Bob"]}).extract_metadata()
    pairs = {(f["value"], f["role"]) for f in metadata["Artist"]}
    assert pairs == {("Ann", "Producer"), ("Bob", "Mixer")}


# --- B12-B16, B17, B18, B20: text mapping -------------------------------------


def test_performer_is_parsed_once_and_keeps_hyphenated_names():
    metadata = TextMetadataExtractor("x.flac", "flac", {"PERFORMER": ["Jay-Z", "Bob (voc)", "Ann - Guitar"]}).extract_metadata()
    artists = [(f["value"], f["role"]) for f in metadata["Artist"]]
    assert artists == [("Jay-Z", "Performer"), ("Bob", "Vocalist"), ("Ann", "Guitar")]


def test_european_decimal_comma():
    extractor = TextMetadataExtractor("x", "flac", {})
    assert extractor._safe_float("3,5") == 3.5
    assert extractor._safe_float("1,234.5") == 1234.5


def test_location_is_read():
    flat = flatten_text_metadata(TextMetadataExtractor("x.flac", "flac", {"LOCATION": ["Abbey Road"]}).extract_metadata())
    assert flat["place_name"] == ["Abbey Road"]


def test_rating_round_trip_scales_back():
    comments = VorbisCommentBuilder().build_comments(_data(_track(user_rating=8.0)))
    assert comments["RATING"] == "80"
    flat = flatten_text_metadata(TextMetadataExtractor("x.flac", "flac", {"RATING": [comments["RATING"]]}).extract_metadata())
    assert flat["user_rating"] == 8.0


def test_tlen_written_and_read_in_milliseconds():
    frames = ID3FrameBuilder().build_frames(_data(_track(duration=215.5)))
    raw = _raw_id3(frames)
    assert raw["TLEN"] == ["215500"]
    flat = flatten_text_metadata(TextMetadataExtractor("x.mp3", "mp3", raw).extract_metadata())
    assert flat["duration"] == 215.5


def test_play_count_is_binary_counter():
    frames = ID3FrameBuilder().build_frames(_data(_track(play_count=300)))
    pcnt = next(f for f in frames if f.startswith(b"PCNT"))
    assert pcnt[10:] == (300).to_bytes(4, "big")
    assert _raw_id3(frames)["PCNT"] == ["300"]


def test_side_prefixed_track_number_round_trips():
    flat = flatten_text_metadata(TextMetadataExtractor("x.flac", "flac", {"TRACKNUMBER": ["B1"]}).extract_metadata())
    assert flat["track_number"] == 1
    assert flat["side"] == "B"


# --- B21: read-only aliases are not written -------------------------------------


def test_albumsort_not_written():
    comments = VorbisCommentBuilder().build_comments(_data(_track(), _album()))
    assert comments["ALBUM"] == "Album"
    assert "ALBUMSORT" not in comments


# --- B23/P1: Ogg rewrite ---------------------------------------------------------


def _ogg(first_packet):
    header, seq = build_pages([first_packet], 7, 0, first_page_flag=True)
    rest, seq = build_pages([b"\x03vorbis" + b"\x00" * 8 + b"\x01", b"\x05vorbis-setup"], 7, seq, first_page_flag=False)
    audio = b"OggS\x00\x00" + struct.pack("<q", 4410) + struct.pack("<I", 7) + struct.pack("<I", seq) + b"\x00\x00\x00\x00" + b"\x01\x05" + b"audio"
    return header + rest + audio


def test_non_vorbis_ogg_is_refused():
    assert replace_comment_packet(_ogg(b"OpusHead" + b"\x01" * 11), b"\x03vorbis\x01") is None


def test_vorbis_rewrite_copies_audio_pages_when_page_count_is_unchanged():
    data = _ogg(b"\x01vorbis" + b"\x00" * 22)
    new = replace_comment_packet(data, b"\x03vorbis" + b"\x00" * 20 + b"\x01")
    assert new is not None
    assert new.endswith(data[-(27 + 1 + 5) :])  # audio page carried through byte for byte
    assert [p["sequence_number"] for p in iter_pages(new)] == [0, 1, 2]


# --- P2/E4/M2: file writers ---------------------------------------------------


def test_flac_block_over_16_mib_is_refused():
    with pytest.raises(ValueError, match="16 MiB"):
        FlacFileWriter()._serialize_blocks([(4, b"\x00" * (1 << 24))], b"")


def test_mp3_write_tags_keeps_audio_and_unrelated_frames(tmp_path):
    path = tmp_path / "a.mp3"
    writer = ID3TagWriter()
    audio = b"\xff\xfb\x90\x64" + b"\x00" * 100
    path.write_bytes(writer.build_id3_tag([writer.create_txxx_frame("REPLAYGAIN_TRACK_GAIN", "-1 dB"), writer.create_text_frame("TIT2", "old")]) + audio)

    assert MP3FileWriter().write_tags(str(path), [writer.create_text_frame("TIT2", "new")], WriteMode.UPDATE_EXISTING)

    data = path.read_bytes()
    assert data.endswith(audio)
    raw = RawTagExtractor()._extract_id3_tags(data)
    assert raw["TIT2"] == ["new"]
    assert raw["TXXX:REPLAYGAIN_TRACK_GAIN"] == ["-1 dB"]


def test_mp3_artwork_write_creates_tag_when_missing(tmp_path):
    from io import BytesIO

    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (4, 4), (9, 9, 9)).save(buf, "PNG")
    path = tmp_path / "bare.mp3"
    audio = b"\xff\xfb\x90\x64" + b"\x00" * 100
    path.write_bytes(audio)

    assert MP3FileWriter().write_artwork(str(path), "front", buf.getvalue())

    assert path.read_bytes().endswith(audio)
    assert ArtworkExtractor().extract_artwork_by_role(str(path), ".mp3")["front"]["data"] == buf.getvalue()
