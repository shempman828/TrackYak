"""
Unit tests for src/sync/transcode.py — the ffmpeg wrapper and the on-disk
transcode cache backing the "sync as MP3" option.

These shell out to a real ffmpeg/ffprobe (the same binary the app uses at
runtime); the module is skipped wholesale when ffmpeg is absent.
"""

from pathlib import Path
import shutil
import subprocess

import pytest

from src.sync import transcode
from src.sync.transcode import (
    LOSSLESS_EXTENSIONS,
    TranscodeCache,
    TranscodeError,
    ffmpeg_available,
    is_lossless_path,
    transcode_to_mp3,
)

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not installed")

_HAVE_FFPROBE = shutil.which("ffprobe") is not None


def _make_flac(path: Path, *, title: str = "Test Title", seconds: int = 1) -> Path:
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={seconds}",
            "-metadata",
            f"title={title}",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def _ffprobe(path: Path, entries: str) -> str:
    """`entries` is a raw -show_entries spec, e.g. 'stream=codec_name'."""
    sel = ["-select_streams", "a:0"] if entries.startswith("stream") else []
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            *sel,
            "-show_entries",
            entries,
            "-of",
            "default=nw=1:nk=1",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return out.stdout.strip()


# --- is_lossless_path ------------------------------------------------------- AC1


@pytest.mark.parametrize("name", ["a.flac", "a.FLAC", "a.wav", "a.WAV", "a.aiff", "a.aif"])
def test_is_lossless_path_true_for_lossless(name):
    assert is_lossless_path(name) is True


@pytest.mark.parametrize("name", ["a.mp3", "a.m4a", "a.m4b", "a.ogg", "a.opus", "a.aac", "a"])
def test_is_lossless_path_false_for_lossy(name):
    assert is_lossless_path(name) is False


def test_lossless_extensions_are_lowercase_with_dot():
    assert all(e.startswith(".") and e == e.lower() for e in LOSSLESS_EXTENSIONS)


# --- transcode_to_mp3 ----------------------------------------------------- AC2/3


@pytest.mark.skipif(not _HAVE_FFPROBE, reason="ffprobe not installed")
@pytest.mark.parametrize("bitrate", ["320k", "192k"])
def test_transcode_produces_cbr_mp3_with_tags(tmp_path, bitrate):
    src = _make_flac(tmp_path / "src.flac", title="Baked Title")
    dest = tmp_path / "out.mp3"

    transcode_to_mp3(str(src), dest, bitrate=bitrate)

    assert dest.exists()
    assert _ffprobe(dest, "stream=codec_name") == "mp3"
    got = int(_ffprobe(dest, "stream=bit_rate"))
    want = int(bitrate.rstrip("k")) * 1000
    assert abs(got - want) / want < 0.05
    assert _ffprobe(dest, "format_tags=title") == "Baked Title"


def test_transcode_bad_input_raises_and_leaves_nothing(tmp_path):
    src = tmp_path / "junk.flac"
    src.write_bytes(b"this is not audio")
    dest = tmp_path / "out.mp3"

    with pytest.raises(TranscodeError) as exc:
        transcode_to_mp3(str(src), dest)

    assert str(exc.value)  # carries ffmpeg's stderr tail
    assert not dest.exists()
    assert not dest.with_name(dest.name + ".part").exists()


def test_transcode_missing_ffmpeg_raises_transcode_error(tmp_path, monkeypatch):
    src = _make_flac(tmp_path / "src.flac")
    real_run = subprocess.run

    def fake_run(cmd, *a, **kw):
        if cmd and cmd[0] == "ffmpeg":
            raise FileNotFoundError("ffmpeg")
        return real_run(cmd, *a, **kw)

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(TranscodeError):
        transcode_to_mp3(str(src), tmp_path / "out.mp3")


# --- TranscodeCache ----------------------------------------------------- AC4/5


def test_cache_transcodes_once_then_reuses(tmp_path, monkeypatch):
    src = _make_flac(tmp_path / "src.flac")
    cache = TranscodeCache(cache_dir=tmp_path / "cache")

    calls: list = []
    real = transcode.transcode_to_mp3

    def counting(*args, **kwargs):
        calls.append(args)
        return real(*args, **kwargs)

    monkeypatch.setattr(transcode, "transcode_to_mp3", counting)

    first = cache.get_or_create(str(src))
    second = cache.get_or_create(str(src))

    assert first == second
    assert first.exists()
    assert len(calls) == 1  # second call was a cache hit


def test_cache_key_changes_when_source_changes(tmp_path):
    src = _make_flac(tmp_path / "src.flac")
    cache = TranscodeCache(cache_dir=tmp_path / "cache")

    before = cache.path_for(str(src))
    _make_flac(src, seconds=2)  # different bytes + mtime
    after = cache.path_for(str(src))

    assert before != after


def test_cache_key_changes_with_bitrate(tmp_path):
    src = _make_flac(tmp_path / "src.flac")
    cache = TranscodeCache(cache_dir=tmp_path / "cache")
    assert cache.path_for(str(src), "320k") != cache.path_for(str(src), "192k")


def test_cache_clear_and_size(tmp_path):
    src_a = _make_flac(tmp_path / "a.flac")
    src_b = _make_flac(tmp_path / "b.flac", seconds=2)
    cache = TranscodeCache(cache_dir=tmp_path / "cache")

    assert cache.size_bytes() == 0
    cache.get_or_create(str(src_a))
    cache.get_or_create(str(src_b))
    assert cache.size_bytes() > 0

    removed = cache.clear()
    assert removed == 2
    assert cache.size_bytes() == 0


def test_cache_clear_on_missing_dir_is_noop(tmp_path):
    cache = TranscodeCache(cache_dir=tmp_path / "nope")
    assert cache.clear() == 0
    assert cache.size_bytes() == 0


# --- asset_paths.CACHE_DIR --------------------------------------------------- AC16


def test_cache_dir_under_base_and_created():
    from src.foundation import asset_paths

    assert asset_paths.CACHE_DIR.parent == asset_paths.BASE_DIR
    asset_paths.ensure_directories_exist()
    assert asset_paths.CACHE_DIR.is_dir()
    # default TranscodeCache lands under it
    assert TranscodeCache().cache_dir.parent == asset_paths.CACHE_DIR
