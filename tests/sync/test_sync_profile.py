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


# --- validation and safe persistence ------------------------------------------

import json  # noqa: E402

from src.sync.sync_profile import SyncProfileStore  # noqa: E402


def test_from_dict_replaces_invalid_values_with_defaults():
    prof = SyncProfile.from_dict({"name": 5, "path": None, "playlist_ids": [1, "2", True, 3], "mood_ids": "oops", "music_path": "../", "transcode_bitrate": "999k"})

    assert prof.name == "Unnamed"
    assert prof.path == ""
    assert prof.playlist_ids == [1, 3]
    assert prof.mood_ids == []
    assert prof.music_path == "Music"
    assert prof.transcode_bitrate == "320k"


def test_save_round_trips_and_leaves_no_temp_files(tmp_path):
    store = SyncProfileStore(str(tmp_path / "sync_profiles.json"))

    assert store.save([SyncProfile(name="A", path="/a", playlist_ids=[4])]) is True

    assert [p.playlist_ids for p in store.load()] == [[4]]
    assert sorted(f.name for f in tmp_path.iterdir()) == ["sync_profiles.json"]


def test_corrupt_file_is_backed_up_not_overwritten(tmp_path):
    path = tmp_path / "sync_profiles.json"
    path.write_text("{ not json")
    store = SyncProfileStore(str(path))

    assert store.load() == []
    store.save([])

    backups = [f for f in tmp_path.iterdir() if ".corrupt-" in f.name]
    assert len(backups) == 1
    assert backups[0].read_text() == "{ not json"


def test_non_list_json_is_backed_up_and_bad_entries_are_skipped(tmp_path):
    path = tmp_path / "sync_profiles.json"
    path.write_text(json.dumps({"name": "x"}))
    assert SyncProfileStore(str(path)).load() == []
    assert any(".corrupt-" in f.name for f in tmp_path.iterdir())

    path.write_text(json.dumps([{"name": "ok", "path": "/p"}, "junk", 3]))
    assert [p.name for p in SyncProfileStore(str(path)).load()] == ["ok"]
