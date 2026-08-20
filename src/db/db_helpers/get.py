"""Class for retrieving data from the database."""

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from src.core.logger_config import logger
from src.db.db_helpers.registry import MODEL_REGISTRY, BaseDBHelper
from src.db.db_tables import Album, AlbumRoleAssociation


class GetFromDB(BaseDBHelper):
    """Class for retrieving data from the database"""

    def query_entities(
        self, entity_class: str, multiple: bool = True, load_options=None, **filters
    ):
        """Generic entity query supporting simple and advanced filtering.

        ``load_options`` accepts a list of SQLAlchemy loader options (e.g.
        ``selectinload(...)``) so callers that need related data can eager-load
        it in one extra query instead of triggering a lazy-load per row.
        """
        logger.debug(
            f"Querying {entity_class} (multiple={multiple}) with filters: {filters}"
        )

        try:
            entity_class_obj = MODEL_REGISTRY[entity_class]
        except KeyError:
            logger.error(f"Entity class '{entity_class}' not found in globals()")
            return [] if multiple else None

        try:
            stmt = select(entity_class_obj)

            if load_options:
                stmt = stmt.options(*load_options)

            # Check for direct filter_expression
            filter_expression = filters.pop("filter_expression", None)
            if filter_expression is not None:
                stmt = stmt.where(filter_expression)

            # Process other filters
            for key, value in filters.items():
                if "__" in key:
                    field, op = key.split("__", 1)
                else:
                    field, op = key, "eq"

                # Validate that the field exists on the entity class
                if not hasattr(entity_class_obj, field):
                    logger.error(f"Field '{field}' not found on entity {entity_class}")
                    continue

                column = getattr(entity_class_obj, field)

                match op:
                    case "eq":
                        stmt = stmt.where(column == value)
                    case "not":
                        stmt = stmt.where(
                            ~column.in_(value)
                            if isinstance(value, (list, tuple, set))
                            else column != value
                        )
                    case "in":
                        if not isinstance(value, (list, tuple, set)):
                            logger.warning(
                                f"Filter 'in' requires iterable, got {type(value)}"
                            )
                            continue
                        stmt = stmt.where(column.in_(value))
                    case "not_in":
                        if not isinstance(value, (list, tuple, set)):
                            logger.warning(
                                f"Filter 'not_in' requires iterable, got {type(value)}"
                            )
                            continue
                        stmt = stmt.where(~column.in_(value))
                    case "contains":
                        stmt = stmt.where(column.contains(value))
                    case "startswith":
                        stmt = stmt.where(column.startswith(value))
                    case "endswith":
                        stmt = stmt.where(column.endswith(value))
                    case "gt":
                        stmt = stmt.where(column > value)
                    case "lt":
                        stmt = stmt.where(column < value)
                    case "gte":
                        stmt = stmt.where(column >= value)
                    case "isnull":
                        stmt = stmt.where(column == None if value else column != None)
                    case "notnull":
                        stmt = stmt.where(column != None)
                    case "lte":
                        stmt = stmt.where(column <= value)
                    case "range":
                        if isinstance(value, (list, tuple)) and len(value) == 2:
                            stmt = stmt.where(column.between(value[0], value[1]))
                        else:
                            logger.warning(
                                f"Filter 'range' requires tuple/list of length 2, got {value}"
                            )
                    case _:
                        logger.error(f"Unsupported filter operation: {op}")
                        continue

            if multiple:
                result = self.session.scalars(stmt).all()
            else:
                result = self.session.scalar(stmt)

            # Close out the read transaction this query just opened -- left
            # open (SQLAlchemy autobegin never ends it on its own), a
            # long-lived caller's session (MainThread's, above all: it's
            # never torn down for the app's whole lifetime) pins a pooled
            # connection indefinitely. See project memory on the "database
            # is locked" investigation. Session is configured with
            # expire_on_commit=False so this doesn't invalidate the
            # attributes on the objects being returned.
            self.session.commit()
            return result

        except SQLAlchemyError as e:
            logger.error(f"Database error querying {entity_class}: {e}")
            return [] if multiple else None

    def get_all_entities(self, model_name: str, load_options=None, **kwargs):
        return self.query_entities(
            model_name, multiple=True, load_options=load_options, **kwargs
        )

    def get_entity_object(self, model_name: str, **kwargs):
        return self.query_entities(model_name, multiple=False, **kwargs)

    def get_album_exists(self, album_name, release_year, artist_ids):
        """
        Check whether an album exists with the given title, release year,
        and exact set of artist IDs (for Album Artist role only).
        """
        if not artist_ids:
            logger.debug(
                f"No artist IDs provided for album check: '{album_name}' ({release_year})"
            )
            return None

        logger.debug(
            f"Checking if album exists: '{album_name}' ({release_year}), Album Artists: {artist_ids}"
        )
        expected_artist_ids = sorted(artist_ids)

        try:
            # Find albums with matching name and year
            base_albums_stmt = select(Album.album_id, Album.album_name).where(
                Album.album_name == album_name,
                Album.release_year == release_year,
            )
            candidate_albums = self.session.execute(base_albums_stmt).all()

            if not candidate_albums:
                logger.debug("No albums found with matching name and year")
                self.session.commit()
                return None

            # For each candidate album, check if it has exactly the expected album artists
            for album_id, candidate_album_name in candidate_albums:
                # Get all album artists (role_id=1) for this album
                artist_stmt = select(AlbumRoleAssociation.artist_id).where(
                    AlbumRoleAssociation.album_id == album_id,
                    AlbumRoleAssociation.role_id == 1,  # Album Artist role
                )
                album_artist_ids = sorted([
                    row[0] for row in self.session.execute(artist_stmt).all()
                ])

                logger.debug(
                    f"Album '{candidate_album_name}' (ID: {album_id}) has album artists: {album_artist_ids}"
                )

                if album_artist_ids == expected_artist_ids:
                    # Found exact match, return the album
                    album = self.session.get(Album, album_id)
                    logger.debug(
                        f"Found matching album: {album.album_id} - {album.album_name}"
                    )
                    self.session.commit()
                    return album

        except SQLAlchemyError as e:
            logger.error(f"Error in album existence check: {e}")
            self.session.rollback()
            return None

        logger.debug("No matching album found.")
        self.session.commit()
        return None

    def get_entity_links(self, link_type: str, **kwargs):
        return self.query_entities(link_type, multiple=True, **kwargs)

    def count_entities(self, model_name: str) -> int:
        """Cheap SELECT count(*) -- used to decide whether a table is small
        enough to safely preload in full (e.g. into a completer index)."""
        try:
            entity_class_obj = MODEL_REGISTRY[model_name]
        except KeyError:
            logger.error(f"Entity class '{model_name}' not found in globals()")
            return 0
        try:
            stmt = select(func.count()).select_from(entity_class_obj)
            count = self.session.scalar(stmt) or 0
            self.session.commit()
            return count
        except SQLAlchemyError as e:
            logger.error(f"Database error counting {model_name}: {e}")
            self.session.rollback()
            return 0

    def resolve_entity_or_alias(self, model_name: str, name_field: str, name: str):
        """Resolve `name` to an entity of `model_name` by its own name field,
        falling back to a `<model_name>Alias` table's `alias_name` (e.g.
        PublisherAlias, GenreAlias). Lets a name the user has aliased to a
        canonical entity -- directly, or via a merge -- resolve to it
        instead of the caller creating a duplicate. Returns None if neither
        matches.
        """
        entity = self.get_entity_object(model_name, **{name_field: name})
        if entity:
            return entity

        alias = self.get_entity_object(f"{model_name}Alias", alias_name=name)
        return getattr(alias, model_name.lower()) if alias else None

    def resolve_split_alias(self, model_name: str, name: str) -> list | None:
        """Resolve `name` against a `<model_name>SplitAlias` table (e.g.
        RoleSplitAlias, GenreSplitAlias) -- a name that was previously split
        into 2+ entities (see SplitDB._record_split_alias) resolves to that
        same ordered list of entities instead of the caller creating/
        reusing one combined entity. Returns None if no rule matches (as
        opposed to an empty list, which would mean "matched but every
        target entity is gone" -- callers should treat both as "no split
        happened" and fall back to normal single-entity resolution).
        """
        alias_class = MODEL_REGISTRY.get(f"{model_name}SplitAlias")
        if alias_class is None:
            return None
        try:
            rows = self.session.scalars(
                select(alias_class)
                .where(alias_class.alias_name == name)
                .order_by(alias_class.sort_order)
            ).all()
        except SQLAlchemyError as e:
            logger.error(f"Database error resolving split alias for {model_name}: {e}")
            return None
        if not rows:
            return None
        targets = [getattr(row, model_name.lower()) for row in rows]
        return [t for t in targets if t is not None] or None
