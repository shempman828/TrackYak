"""Lifecycle management for files under the managed images directories.

Artist profile pictures (images/artist_images/) and publisher logos
(images/publisher_logos/) are copied in with deterministic
``{entity_id}_{sanitized_name}{suffix}`` names by
:mod:`src.artist.artist_image_manager` /
:mod:`src.publisher.publisher_image_manager`. Nothing else in the app ever
removed them, so deleting or merging the owning entity left the file behind
forever.

This module is the single place that unlinks or renames those files. It is
called from :meth:`DeleteDB.delete_entity` (row + file removed together) and
:meth:`MergeDB.merge_entities` (the surviving entity's picture is renamed to
its own id, the discarded one is unlinked). :func:`prune_orphaned_images`
does a one-time sweep of files that were already orphaned before any of this
existed.
"""

from pathlib import Path
import re

from src.core import asset_paths
from src.core.logger_config import logger

# Same set the image managers sanitize entity names against.
_INVALID_CHARS = re.compile(r'[<>:"/\\|?*]')

# model name -> (image-path column on the model, managed-dir attr on asset_paths)
IMAGE_PATH_COLUMNS: dict[str, tuple[str, str]] = {
    "Artist": ("profile_pic_path", "ARTIST_IMAGES_DIR"),
    "Publisher": ("logo_path", "PUBLISHER_LOGOS_DIR"),
}


def _managed_dirs() -> list[Path]:
    """Resolved paths of every directory this module is allowed to touch.

    Read at call time (not import time) so tests can monkeypatch the
    ``asset_paths`` constants.
    """
    out = []
    for _col, dir_attr in IMAGE_PATH_COLUMNS.values():
        try:
            out.append(Path(getattr(asset_paths, dir_attr)).resolve())
        except OSError:
            continue
    return out


def _is_managed(path: Path) -> bool:
    """True iff ``path`` sits directly inside one of the managed dirs."""
    try:
        resolved = path.resolve()
    except OSError:
        return False
    return resolved.parent in _managed_dirs()


def managed_image_name(entity_id, entity_name: str, suffix: str) -> str:
    """The deterministic filename the image managers would give this entity."""
    sanitized = _INVALID_CHARS.sub("_", entity_name or "")
    return f"{entity_id}_{sanitized}{suffix}"


def delete_managed_image(path: str | None) -> bool:
    """Unlink ``path`` iff it lives directly inside a managed images dir.

    No-ops (returning False) on an empty path, a path outside the managed
    dirs, or an already-missing file. Returns True only when a file was
    actually removed.
    """
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
    """Rename a managed image file to ``{entity_id}_{name}{suffix}``.

    Used after a merge, when the surviving entity inherited the merged-away
    entity's picture and the file is still named for the deleted id. Returns
    the new path, or None when nothing was renamed (path empty/unmanaged/
    missing, or the file is already named correctly).
    """
    if not old_path:
        return None
    src = Path(old_path)
    if not _is_managed(src) or not src.exists():
        return None
    dest = src.with_name(managed_image_name(entity_id, entity_name, src.suffix))
    if dest == src:
        return None
    try:
        if dest.exists():
            dest.unlink()
        src.rename(dest)
    except OSError as e:
        logger.error(f"rename_managed_image: {old_path!r} -> {dest}: {e}")
        return None
    logger.info(f"Renamed managed image {src.name} -> {dest.name}")
    return str(dest)


def discard_replaced_image(session, model_name: str, old_path: str | None, new_path) -> bool:
    """Unlink ``old_path`` after an entity's image column was changed to
    ``new_path`` (cleared, or re-picked with a different extension/name).

    No-ops -- returning False -- when the path is unchanged, empty,
    unmanaged, or still referenced by some other row. Returns True only when
    a file was actually removed.
    """
    if not old_path or old_path == new_path:
        return False
    col = IMAGE_PATH_COLUMNS.get(model_name)
    if not col:
        return False

    from src.db.db_tables.artist import Artist
    from src.db.db_tables.publisher import Publisher

    column = getattr({"Artist": Artist, "Publisher": Publisher}[model_name], col[0])
    if session.query(column).filter(column == old_path).first() is not None:
        return False
    return delete_managed_image(old_path)


def prune_orphaned_images(session, *, dry_run: bool = False) -> dict[str, list[str]]:
    """Delete every file in the managed image dirs that no row references.

    One-time / maintenance sweep for files orphaned before the delete- and
    merge-time hooks existed (e.g. a picture cleared or re-picked with a
    different extension in an editor). With ``dry_run=True`` nothing is
    unlinked -- the returned ``removed`` list is what *would* be removed.

    Guard: if a model's image column has zero non-empty values but its
    directory holds files, that directory is skipped -- a half-loaded or
    empty database must not be read as "every image is an orphan".

    Returns ``{"removed": [paths], "missing_refs": [filenames]}`` where
    ``missing_refs`` are files a row points at that are not on disk (logged,
    never mutated).
    """
    from src.db.db_tables.artist import Artist
    from src.db.db_tables.publisher import Publisher

    models = {"Artist": Artist, "Publisher": Publisher}
    removed: list[str] = []
    missing_refs: list[str] = []

    for model_name, (col, dir_attr) in IMAGE_PATH_COLUMNS.items():
        directory = Path(getattr(asset_paths, dir_attr))
        if not directory.is_dir():
            continue

        model = models[model_name]
        column = getattr(model, col)
        referenced = {
            Path(value).name
            for (value,) in session.query(column).filter(column.isnot(None), column != "")
        }

        files = [p for p in directory.iterdir() if p.is_file()]
        if not referenced and files:
            logger.warning(
                f"prune_orphaned_images: {model_name} has no referenced images but "
                f"{len(files)} file(s) in {directory}; skipping (partial DB load?)."
            )
            continue

        for f in files:
            if f.name in referenced:
                continue
            # Short-circuits before touching disk on a dry run.
            if dry_run or delete_managed_image(str(f)):
                removed.append(str(f))

        on_disk = {p.name for p in files}
        for name in sorted(referenced - on_disk):
            logger.warning(
                f"prune_orphaned_images: {model_name}.{col} references missing file {name!r}"
            )
            missing_refs.append(name)

    logger.info(
        f"prune_orphaned_images: {'would remove' if dry_run else 'removed'} "
        f"{len(removed)} orphan(s), {len(missing_refs)} dangling reference(s)."
    )
    return {"removed": removed, "missing_refs": missing_refs}
