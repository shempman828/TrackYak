"""Round-trip tests for SyncProfile serialisation."""

from src.sync.sync_profile import SyncProfile


def test_transcode_fields_round_trip():  # AC6
    p = SyncProfile(name="Phone", path="/x", transcode_to_mp3=True, transcode_bitrate="192k")
    restored = SyncProfile.from_dict(p.to_dict())
    assert restored.transcode_to_mp3 is True
    assert restored.transcode_bitrate == "192k"


def test_legacy_dict_without_transcode_keys_defaults():  # AC6
    legacy = {"name": "Old", "path": "/x", "playlist_ids": [1, 2]}
    p = SyncProfile.from_dict(legacy)
    assert p.transcode_to_mp3 is False
    assert p.transcode_bitrate == "320k"
