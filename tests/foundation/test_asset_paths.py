"""Coverage for src/foundation/asset_paths.py.

Focus: the one-time relocate of regenerable caches (analysis_cache.json,
images/imagecache/) into the top-level cache/ dir, plus the cache() helper
and the IMAGECACHE_DIR home. The relocate must be best-effort — a failure
or an already-migrated target must never raise, since the caches rebuild.
"""

from pathlib import Path

from src.foundation import asset_paths


def _wire_dirs(monkeypatch, root: Path):
    """Point every dir constant the migration reads at a scratch tree."""
    config_dir = root / "config"
    images_dir = root / "images"
    cache_dir = root / "cache"
    imagecache_dir = cache_dir / "imagecache"
    for d in (config_dir, images_dir):
        d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(asset_paths, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(asset_paths, "IMAGES_DIR", images_dir)
    monkeypatch.setattr(asset_paths, "CACHE_DIR", cache_dir)
    monkeypatch.setattr(asset_paths, "IMAGECACHE_DIR", imagecache_dir)
    return config_dir, images_dir, cache_dir, imagecache_dir


def test_imagecache_dir_lives_under_cache_dir():
    assert asset_paths.IMAGECACHE_DIR.parent == asset_paths.CACHE_DIR


def test_cache_helper_points_into_cache_dir():
    assert Path(asset_paths.cache("x.json")) == asset_paths.CACHE_DIR / "x.json"


def test_relocates_legacy_analysis_cache_and_imagecache(monkeypatch, tmp_path):
    config_dir, images_dir, cache_dir, imagecache_dir = _wire_dirs(monkeypatch, tmp_path)
    (config_dir / "analysis_cache.json").write_text('{"analysed_ids": [1, 2]}')
    legacy_imagecache = images_dir / "imagecache"
    legacy_imagecache.mkdir()
    (legacy_imagecache / "artwork_cache.db").write_bytes(b"sqlite-ish")

    asset_paths._migrate_legacy_cache_locations()

    assert not (config_dir / "analysis_cache.json").exists()
    assert (cache_dir / "analysis_cache.json").read_text() == '{"analysed_ids": [1, 2]}'
    assert not legacy_imagecache.exists()
    assert (imagecache_dir / "artwork_cache.db").read_bytes() == b"sqlite-ish"


def test_relocate_is_noop_when_nothing_legacy_present(monkeypatch, tmp_path):
    _wire_dirs(monkeypatch, tmp_path)
    asset_paths._migrate_legacy_cache_locations()  # must not raise


def test_relocate_keeps_new_location_when_both_exist(monkeypatch, tmp_path):
    config_dir, _, cache_dir, _ = _wire_dirs(monkeypatch, tmp_path)
    (config_dir / "analysis_cache.json").write_text("OLD")
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "analysis_cache.json").write_text("NEW")

    asset_paths._migrate_legacy_cache_locations()

    assert (cache_dir / "analysis_cache.json").read_text() == "NEW"
    assert (config_dir / "analysis_cache.json").read_text() == "OLD"


def test_relocate_swallows_move_errors(monkeypatch, tmp_path):
    config_dir, _, _, _ = _wire_dirs(monkeypatch, tmp_path)
    (config_dir / "analysis_cache.json").write_text("{}")

    def boom(*_a, **_k):
        raise OSError("cross-device link")

    monkeypatch.setattr(asset_paths.shutil, "move", boom)
    asset_paths._migrate_legacy_cache_locations()  # logged, not raised
