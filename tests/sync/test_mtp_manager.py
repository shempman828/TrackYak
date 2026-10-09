"""
Regression tests for MtpManager._run_bounded.

Motivating bug: opening SyncView could hang the GUI thread. list_devices()
shells out to `gio`, and subprocess.run(timeout=...) — after a timeout —
kills the child and then does an UNBOUNDED wait() (Popen.__exit__ waits
again). A `gio` call stuck on a wedged MTP backend never dies, so that wait
never returns. _run_bounded kills + waits with a hard cap and abandons the
child rather than blocking forever.
"""

import time

from src.sync.mtp_manager import _run_bounded


def test_run_bounded_returns_completed_process_for_quick_command():
    result = _run_bounded(["printf", "hello"], timeout=5)
    assert result is not None
    assert result.returncode == 0
    assert result.stdout == "hello"


def test_run_bounded_returns_none_on_timeout_without_blocking():
    start = time.monotonic()
    result = _run_bounded(["sleep", "30"], timeout=1)
    elapsed = time.monotonic() - start

    assert result is None
    # timeout (1s) + kill grace (2s) + slack — nowhere near `sleep`'s 30s.
    assert elapsed < 10, f"_run_bounded blocked for {elapsed:.1f}s"


def test_run_bounded_returns_none_when_binary_missing():
    assert _run_bounded(["this-binary-does-not-exist-xyz"], timeout=5) is None


# ---------------------------------------------------------------------------
# Music path normalization, storage resolution, URI consistency, recursive remove
# ---------------------------------------------------------------------------

import shutil  # noqa: E402

import pytest  # noqa: E402

from src.sync.mtp_manager import MtpDevice, MtpManager, normalize_music_path  # noqa: E402


@pytest.mark.parametrize(("raw", "clean"), [("", "Music"), ("   ", "Music"), ("/Music/", "Music"), ("a//b", "a/b"), ("../../x", "x"), ("a\\b", "a/b"), ("./Music/.", "Music")])
def test_normalize_music_path(raw, clean):
    assert normalize_music_path(raw) == clean


def _mtp_manager_with_roots(monkeypatch, roots):
    mgr = MtpManager()
    calls = []

    def fake_roots(device):
        calls.append(device.uri)
        return roots

    monkeypatch.setattr(mgr, "_list_root_names", fake_roots)
    return mgr, calls


_PHONE = MtpDevice(uri="mtp://PHONE_123/", name="Phone", backend="gio")


def test_plain_path_goes_into_internal_storage_and_is_cached(monkeypatch):
    mgr, calls = _mtp_manager_with_roots(monkeypatch, ["SD card", "Internal shared storage"])

    assert mgr.resolve_music_dir(_PHONE, "Music") == "Internal shared storage/Music"
    assert mgr.resolve_music_dir(_PHONE, "Music") == "Internal shared storage/Music"
    assert len(calls) == 1


def test_path_naming_a_storage_volume_is_used_as_given(monkeypatch):
    mgr, _ = _mtp_manager_with_roots(monkeypatch, ["Internal shared storage", "SD card"])

    assert mgr.resolve_music_dir(_PHONE, "sd card/Music") == "sd card/Music"


def test_unlistable_root_is_not_cached(monkeypatch):
    mgr, calls = _mtp_manager_with_roots(monkeypatch, None)

    assert mgr.resolve_music_dir(_PHONE, "Music") == "Music"
    mgr.resolve_music_dir(_PHONE, "Music")
    assert len(calls) == 2


def test_all_uris_share_the_resolved_storage(monkeypatch):
    mgr, _ = _mtp_manager_with_roots(monkeypatch, ["Internal shared storage"])
    base = "mtp://PHONE_123/Internal shared storage"

    assert mgr.build_music_uri(_PHONE, "Music") == f"{base}/Music/"
    assert mgr.build_file_uri(_PHONE, "Music", "A - B.mp3") == f"{base}/Music/A - B.mp3"
    assert mgr.build_playlists_dir_uri(_PHONE, "Music") == f"{base}/Playlists/"
    assert mgr.build_playlist_uri(_PHONE, "Music", "PL") == f"{base}/Playlists/PL.m3u"


def test_non_mtp_uris_skip_storage_resolution(monkeypatch):
    mgr, calls = _mtp_manager_with_roots(monkeypatch, ["Internal shared storage"])
    stand_in = MtpDevice(uri="file:///tmp/dev/", name="d", backend="gio")

    assert mgr.build_music_uri(stand_in, "Music") == "file:///tmp/dev/Music/"
    assert calls == []


@pytest.mark.parametrize(("path", "sub"), [("Music", "../Music"), ("a/b/Songs", "../Songs"), ("", "../Music")])
def test_m3u_music_subpath_is_always_the_sibling_folder(path, sub):
    assert MtpManager.m3u_music_subpath(path) == sub


def test_copy_text_as_file_always_removes_its_temp_file(monkeypatch):
    mgr = MtpManager()
    seen = []

    def fake_copy(device, local_path, remote_uri):
        seen.append(local_path)
        return False

    monkeypatch.setattr(mgr, "copy_file", fake_copy)

    assert mgr.copy_text_as_file(_PHONE, "#EXTM3U\n", "mtp://x/PL.m3u") is False
    from pathlib import Path

    assert not Path(seen[0]).exists()


@pytest.mark.skipif(shutil.which("gio") is None, reason="gio not installed")
def test_remove_remote_dir_empties_a_non_empty_folder(tmp_path):
    target = tmp_path / "Music"
    (target / "sub").mkdir(parents=True)
    (target / "A - B.mp3").write_bytes(b"x")
    (target / "sub" / "c.txt").write_bytes(b"y")
    device = MtpDevice(uri=f"file://{tmp_path}/", name="d", backend="gio")

    assert MtpManager().remove_remote_dir(device, f"file://{target}/") is True
    assert not target.exists()
