"""Delete, rename and prune files in the managed image folders (artist pictures)."""

# Called from DeleteDB.delete_entity and MergeDB.merge_entities so files leave
# with their rows; prune_orphaned_images sweeps files orphaned before that.

from pathlib import Path
import re

from src.foundation import asset_paths
from src.foundation.logger_config import logger

# Characters not allowed in file names on Windows; artist_image_manager uses this too.
_INVALID_CHARS = re.compile(r'[<>:"/\\|?*]')

# model name -> (image-path column on the model, managed-dir attr on asset_paths)
IMAGE_PATH_COLUMNS: dict[str, tuple[str, str]] = {"Artist": ("profile_pic_path", "ARTIST_IMAGES_DIR")}


def _model_for(model_name: str):
    """Return the ORM model class that owns the image column for model_name."""
    from src.db.db_tables.artist import Artist

    return {"Artist": Artist}[model_name]


def _managed_dirs() -> list[Path]:
    """Return the resolved folders this module may touch."""
    # Read at call time so tests can monkeypatch the asset_paths constants.
    out = []
    for _col, dir_attr in IMAGE_PATH_COLUMNS.values():
        try:
            out.append(Path(getattr(asset_paths, dir_attr)).resolve())
        except OSError:
            continue
    return out


def _is_managed(path: Path) -> bool:
    """Return True if ``path`` sits directly inside one of the managed folders."""
    try:
        resolved = path.resolve()
    except OSError:
        return False
    return resolved.parent in _managed_dirs()


def managed_image_name(entity_id, entity_name: str, suffix: str) -> str:
    """Return the deterministic filename the image managers give this entity."""
    sanitized = _INVALID_CHARS.sub("_", entity_name or "")
    return f"{entity_id}_{sanitized}{suffix}"


def delete_managed_image(path: str | None) -> bool:
    """Unlink ``path`` if it is inside a managed folder; return True only if a file was removed."""
    if not path:
        return False
    p = Path(path)
    if not _is_managed(p):
        logger.debug(f"delete_managed_image: refusing unmanaged path {path!r}")
        return False
    try:
        p.unlink()
    except FileNotFoundError:
        return False
    except OSError as e:
        logger.error(f"delete_managed_image: could not remove {path!r}: {e}")
        return False
    logger.info(f"Removed managed image no longer referenced: {path}")
    return True


def rename_managed_image(old_path: str | None, entity_id, entity_name: str) -> str | None:
    """Rename a managed image to ``{entity_id}_{name}{suffix}`` after a merge; return the new path or None."""
    if not old_path:
        return None
    src = Path(old_path)
    if not _is_managed(src) or not src.exists():
        return None
    dest = src.with_name(managed_image_name(entity_id, entity_name, src.suffix))
    if dest == src:
        return None
    try:
        # Atomic overwrite: an unlink-then-rename loses dest if the rename fails.
        src.replace(dest)
    except OSError as e:
        logger.error(f"rename_managed_image: {old_path!r} -> {dest}: {e}")
        return None
    logger.info(f"Renamed managed image {src.name} -> {dest.name}")
    return str(dest)


def discard_replaced_image(session, model_name: str, old_path: str | None, new_path) -> bool:
    """Unlink ``old_path`` after an image column changed to ``new_path``, unless another row still uses it."""
    if not old_path or old_path == new_path:
        return False
    col = IMAGE_PATH_COLUMNS.get(model_name)
    if not col:
        return False

    column = getattr(_model_for(model_name), col[0])
    if session.query(column).filter(column == old_path).first() is not None:
        return False
    return delete_managed_image(old_path)


def prune_orphaned_images(session, *, dry_run: bool = False) -> dict[str, list[str]]:
    """Delete unreferenced files in the managed folders; return {"removed": paths, "missing_refs": names}."""
    # dry_run=True unlinks nothing; "removed" then lists what would go.
    removed: list[str] = []
    missing_refs: list[str] = []

    for model_name, (col, dir_attr) in IMAGE_PATH_COLUMNS.items():
        directory = Path(getattr(asset_paths, dir_attr))
        if not directory.is_dir():
            continue

        column = getattr(_model_for(model_name), col)
        referenced = {Path(value).name for (value,) in session.query(column).filter(column.isnot(None), column != "")}

        try:
            files = [p for p in directory.iterdir() if p.is_file()]
        except OSError as e:
            logger.error(f"prune_orphaned_images: cannot list {directory}: {e}")
            continue
        # A half-loaded or empty DB must not read as "every image is an orphan".
        if not referenced and files:
            logger.warning(f"prune_orphaned_images: {model_name} has no referenced images but {len(files)} file(s) in {directory}; skipping (partial DB load?).")
            continue

        for f in files:
            if f.name in referenced:
                continue
            # Short-circuits before touching disk on a dry run.
            if dry_run or delete_managed_image(str(f)):
                removed.append(str(f))

        on_disk = {p.name for p in files}
        for name in sorted(referenced - on_disk):
            logger.warning(f"prune_orphaned_images: {model_name}.{col} references missing file {name!r}")
            missing_refs.append(name)

    logger.info(f"prune_orphaned_images: {'would remove' if dry_run else 'removed'} {len(removed)} orphan(s), {len(missing_refs)} dangling reference(s).")
    return {"removed": removed, "missing_refs": missing_refs}
