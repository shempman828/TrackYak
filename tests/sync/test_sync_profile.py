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


def test_prune_untracked_new_profile_defaults_on():  # AC5
    assert SyncProfile(name="Phone", path="/x").prune_untracked is True


def test_prune_untracked_round_trips():  # AC5
    p = SyncProfile(name="Phone", path="/x", prune_untracked=False)
    assert SyncProfile.from_dict(p.to_dict()).prune_untracked is False
    p2 = SyncProfile(name="Phone", path="/x", prune_untracked=True)
    assert SyncProfile.from_dict(p2.to_dict()).prune_untracked is True


def test_legacy_dict_without_prune_key_loads_off():  # AC5
    legacy = {"name": "Old", "path": "/x", "playlist_ids": [1, 2]}
    assert SyncProfile.from_dict(legacy).prune_untracked is False
