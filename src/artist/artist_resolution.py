"""Alias-aware Artist resolution shared by every caller that turns a plain
artist name into an Artist row (file-tag import, manual track-role editing,
...). Centralizes the fix for a recurring gap where a name-only exact-match
lookup skipped ArtistAlias and created a duplicate for an already-merged or
aliased artist.
"""

from typing import Any


def resolve_or_create_artist(
    controller, artist_name: str, *, isgroup: int | None = None, commit: bool = True
) -> Any | None:
    """Resolve `artist_name` to an Artist, checking ArtistAlias before
    creating a new one -- so a name the user has aliased to a canonical
    artist (directly, or via a merge) resolves to it instead of spawning a
    duplicate. Creates a new Artist, with `isgroup` if given, if neither
    matches. Returns None for a blank name.
    """
    artist_name = (artist_name or "").strip()
    if not artist_name:
        return None

    artist = controller.get.resolve_entity_or_alias("Artist", "artist_name", artist_name)
    if artist:
        return artist

    create_kwargs = {"artist_name": artist_name}
    if isgroup is not None:
        create_kwargs["isgroup"] = isgroup

    return controller.add.add_entity("Artist", commit=commit, **create_kwargs)
