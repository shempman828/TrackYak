import inspect
import os

from sqlalchemy import create_engine, select, update
from sqlalchemy import delete as sql_delete
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import scoped_session, sessionmaker

import src.db_tables
from src.logger_config import logger

# Iterate over everything in src.db_tables
for name, obj in inspect.getmembers(src.db_tables):
    # Only import classes (filter by type)
    if inspect.isclass(obj):
        globals()[name] = obj


# Initialize the database session
engine = create_engine(
    "sqlite:///music_library.db", connect_args={"check_same_thread": False}
)
Session = scoped_session(sessionmaker(bind=engine))


class BaseDBHelper:
    """Base class with common database operations"""

    def __init__(self, session):
        """Initialize with a database session."""
        self.session = session


class GetFromDB(BaseDBHelper):
    """Class for retrieving data from the database"""

    def query_entities(self, entity_class: str, multiple: bool = True, **filters):
        """Generic entity query supporting simple and advanced filtering."""
        logger.debug(
            f"Querying {entity_class} (multiple={multiple}) with filters: {filters}"
        )

        try:
            entity_class_obj = globals()[entity_class]
        except KeyError:
            logger.error(f"Entity class '{entity_class}' not found in globals()")
            return [] if multiple else None

        try:
            stmt = select(entity_class_obj)

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
                        stmt = stmt.where(
                            column.is_(None) if value else column.is_not(None)
                        )
                    case "notnull":
                        stmt = stmt.where(column.is_not(None))
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
                return self.session.scalars(stmt).all()
            else:
                return self.session.scalar(stmt)

        except SQLAlchemyError as e:
            logger.error(f"Database error querying {entity_class}: {e}")
            return [] if multiple else None

    def get_all_entities(self, model_name: str, **kwargs):
        return self.query_entities(model_name, multiple=True, **kwargs)

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
            Album = globals()["Album"]
        except KeyError:
            logger.error("Album class not found in globals()")
            return None

        try:
            # Find albums with matching name and year
            base_albums_stmt = select(Album.album_id, Album.album_name).where(
                Album.album_name == album_name,
                Album.release_year == release_year,
            )
            candidate_albums = self.session.execute(base_albums_stmt).all()

            if not candidate_albums:
                logger.debug("No albums found with matching name and year")
                return None

            # For each candidate album, check if it has exactly the expected album artists
            for album_id, album_name in candidate_albums:
                # Get all album artists (role_id=1) for this album
                # Use direct table reference to avoid relationship issues
                from src.db_tables import AlbumRoleAssociation

                artist_stmt = select(AlbumRoleAssociation.artist_id).where(
                    AlbumRoleAssociation.album_id == album_id,
                    AlbumRoleAssociation.role_id == 1,  # Album Artist role
                )
                album_artist_ids = sorted(
                    [row[0] for row in self.session.execute(artist_stmt).all()]
                )

                logger.debug(
                    f"Album '{album_name}' (ID: {album_id}) has album artists: {album_artist_ids}"
                )

                if album_artist_ids == expected_artist_ids:
                    # Found exact match, return the album
                    album = self.session.get(Album, album_id)
                    logger.debug(
                        f"Found matching album: {album.album_id} - {album.album_name}"
                    )
                    return album

        except Exception as e:
            logger.error(f"Error in album existence check: {e}")
            return None

        logger.debug("No matching album found.")
        return None

    def get_entity_links(self, link_type: str, **kwargs):
        return self.query_entities(link_type, multiple=True, **kwargs)


class AddToDB(BaseDBHelper):
    """Class for adding data to the database"""

    def add_entity(self, model_name: str, **kwargs):
        logger.debug(
            f"Adding new entity of type: {model_name} with attributes: {kwargs}"
        )

        try:
            entity_class = globals()[model_name]
        except KeyError:
            logger.error(f"Entity class {model_name} not found")
            return None

        new_entity = entity_class(**kwargs)
        self.session.add(new_entity)

        try:
            self.session.commit()

            # Safe refresh with error handling
            try:
                self.session.refresh(new_entity)
                logger.debug(f"New entity added and refreshed: {new_entity}")
            except SQLAlchemyError as refresh_error:
                logger.warning(
                    f"Could not refresh entity {model_name} after commit: {refresh_error}. "
                    f"Entity was still added successfully."
                )
                # The entity was committed, so we return it even if refresh fails

            return new_entity

        except Exception as e:
            self.session.rollback()
            logger.error(f"Failed to add entity: {e}")
            return None

    def add_entity_link(self, link_type: str, **kwargs):
        """Add a new link entity to the database.

        Args:
            link_type (str): The class name of the link entity (e.g., 'TrackArtistRole', 'AlbumArtistAssociation')
            **kwargs: Arbitrary keyword arguments representing attribute values.

        Returns:
            object: The newly created link entity instance.
        """
        logger.debug(
            f"Adding new link entity of type: {link_type} with attributes: {kwargs}"
        )
        try:
            link_class = globals()[link_type]
        except KeyError:
            return None

        new_link = link_class(**kwargs)
        self.session.add(new_link)
        self.session.commit()
        return new_link


class UpdateDB(BaseDBHelper):
    """Class for updating data in the database"""

    def update_entity(self, model_name: str, entity_id: int, **kwargs):
        """Update an existing entity in the database.

        Args:
            model_name (str): The class name of the entity (e.g., 'Artist', 'Playlist')
            entity_id (int): The ID of the entity to update.
            **kwargs: Arbitrary keyword arguments representing attribute values to update.

        Returns:
            bool: True if the update was successful, False otherwise.
        """
        logger.debug(
            f"Updating {model_name} with ID {entity_id} with attributes: {kwargs}"
        )
        try:
            entity_class = globals()[model_name]
        except KeyError:
            return False

        # Attempt to determine the primary key column name for this model
        pk_cols = list(entity_class.__table__.primary_key.columns)
        if pk_cols:
            pk_col = pk_cols[0].name
            stmt = (
                update(entity_class)
                .where(getattr(entity_class, pk_col) == entity_id)
                .values(**kwargs)
            )
        else:
            # fallback to generic 'id'
            stmt = (
                update(entity_class)
                .where(getattr(entity_class, "id") == entity_id)
                .values(**kwargs)
            )

        try:
            self.session.execute(stmt)
            self.session.commit()
            return True
        except SQLAlchemyError as e:
            logger.error(f"Error updating {model_name} with ID {entity_id}: {e}")
            self.session.rollback()
            return False


class DeleteDB(BaseDBHelper):
    """Minimal class for deleting database entities and associated files."""

    def delete_entity(
        self, model_name: str, entity_id: int = None, entity_ids: list = None, **filters
    ):
        """
        Delete one or many database entities.

        Three ways to call this:

            # Single item by primary key (original behaviour, unchanged)
            delete_entity("Track", entity_id=42)

            # Many items in one query -- new batch path
            delete_entity("Track", entity_ids=[1, 2, 3, 99])

            # Filter-based deletion (original behaviour, unchanged)
            delete_entity("Track", track_name="Unknown")
        """
        entity_class = globals().get(model_name)
        if not entity_class:
            logger.error(f"Entity type '{model_name}' not found")
            return False

        try:
            # ------------------------------------------------------------------
            # BATCH path -- new: delete many rows in a single WHERE id IN query
            # ------------------------------------------------------------------
            if entity_ids is not None:
                if not entity_ids:
                    logger.warning("delete_entity called with an empty entity_ids list")
                    return True  # Nothing to do -- not an error

                self.session.query(entity_class).filter(
                    entity_class.track_id.in_(
                        entity_ids
                    )  # <-- change track_id to match your model's actual PK field name
                ).delete(synchronize_session="fetch")
                # "fetch" tells SQLAlchemy to load the objects first so that
                # cascade rules (e.g. deleting related join rows) fire correctly.
                self.session.commit()
                logger.info(
                    f"Batch-deleted {len(entity_ids)} {model_name} row(s) "
                    f"(ids={entity_ids})"
                )
                return True

            # ------------------------------------------------------------------
            # SINGLE item path -- original behaviour, unchanged
            # ------------------------------------------------------------------
            elif entity_id is not None:
                entity = self.session.get(entity_class, entity_id)
                if not entity:
                    logger.warning(f"{model_name} with ID {entity_id} not found")
                    return False
                self.session.delete(entity)
                self.session.commit()
                logger.info(f"Deleted {model_name} with ID {entity_id}")
                return True

            # ------------------------------------------------------------------
            # FILTER path -- original behaviour, unchanged
            # ------------------------------------------------------------------
            elif filters:
                query = self.session.query(entity_class)
                for attr, value in filters.items():
                    if hasattr(entity_class, attr):
                        query = query.filter(getattr(entity_class, attr) == value)
                    else:
                        logger.warning(f"{model_name} has no attribute '{attr}'")
                        return False
                entities = query.all()
                for entity in entities:
                    self.session.delete(entity)
                self.session.commit()
                logger.info(
                    f"Deleted {len(entities)} {model_name} entities matching {filters}"
                )
                return True

            else:
                logger.error(
                    "Either entity_id, entity_ids, or filters must be provided"
                )
                return False

        except SQLAlchemyError as e:
            logger.error(f"Error deleting {model_name}: {e}")
            self.session.rollback()
            return False

    def delete_file(
        self,
        file_path: str = None,
        model_name: str = None,
        entity_id: int = None,
        **filters,
    ):
        """Delete a file from disk after deleting its database entry."""
        db_deleted = True
        if model_name:
            db_deleted = self.delete_entity(model_name, entity_id=entity_id, **filters)

        file_deleted = True
        if file_path:
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
                    logger.info(f"Deleted file: {file_path}")
                else:
                    logger.warning(f"File not found: {file_path}")
                    file_deleted = False
            except OSError as e:
                logger.error(f"Error deleting file {file_path}: {e}")
                file_deleted = False

        return db_deleted and file_deleted


# Model registry — safer than globals()
_MERGE_MODEL_REGISTRY: dict = {
    "Artist": src.db_tables.Artist,
    "Publisher": src.db_tables.Publisher,
    "Genre": src.db_tables.Genre,
    "Mood": src.db_tables.Mood,
    "Role": src.db_tables.Role,
    "Album": src.db_tables.Album,
}


class MergeDB(BaseDBHelper):
    """Class for merging database entries."""

    def merge_entities(
        self,
        model_name: str,
        source_id: int,
        target_id: int,
        resolved_fields: dict | None = None,
    ):
        """Merge two entities of the same type across all relationship tables."""
        logger.debug(f"Merging {model_name} ID {source_id} -> {target_id}")

        entity_class = _MERGE_MODEL_REGISTRY.get(model_name)
        if entity_class is None:
            logger.error(f"Entity '{model_name}' not found in merge registry.")
            return False

        source_entity = self.session.get(entity_class, source_id)
        target_entity = self.session.get(entity_class, target_id)
        if not source_entity or not target_entity:
            logger.error(f"Source or target {model_name} not found.")
            return False

        try:
            pk_columns = [
                col.name for col in entity_class.__table__.primary_key.columns
            ]
            if not pk_columns:
                logger.error(f"No primary key found for {model_name}.")
                return False

            pk_column = pk_columns[0]
            metadata = entity_class.metadata
            updated_tables = set()
            skipped_tables = set()

            for table in metadata.tables.values():
                if table.name == entity_class.__table__.name:
                    continue

                for column in table.columns:
                    for fk in column.foreign_keys:
                        if (
                            fk.column.table.name == entity_class.__table__.name
                            and fk.column.name == pk_column
                        ):
                            logger.debug(
                                f"Found FK: {table.name}.{column.name} -> "
                                f"{entity_class.__table__.name}.{pk_column}"
                            )

                            update_stmt = (
                                update(table)
                                .where(column == source_id)
                                .values({column.name: target_id})
                            )

                            rowcount = self._safe_execute(update_stmt)
                            if rowcount > 0:
                                logger.info(
                                    f"Updated {rowcount} rows in "
                                    f"{table.name}.{column.name}"
                                )
                                updated_tables.add(table.name)
                            elif rowcount == -1:
                                # Bulk UPDATE hit a unique constraint on at least one
                                # row, so the whole statement rolled back. Fall back
                                # to a row-by-row pass so only rows that truly
                                # collide with an existing target row get dropped —
                                # the rest are still migrated to the target.
                                moved, dropped = self._migrate_fk_rows_individually(
                                    table, column, source_id, target_id
                                )
                                if moved:
                                    logger.info(
                                        f"Row-by-row updated {moved} rows in "
                                        f"{table.name}.{column.name}"
                                    )
                                    updated_tables.add(table.name)
                                if dropped:
                                    logger.info(
                                        f"Constraint on {table.name}.{column.name}: "
                                        f"deleted {dropped} duplicate source rows "
                                        f"(target already has them)."
                                    )
                                    skipped_tables.add(table.name)

            # Delete via ORM so cascade rules are respected
            self.session.delete(source_entity)
            self.session.flush()
            # Apply resolved field values now that the source is scheduled for deletion.
            if resolved_fields:
                {col.name for col in entity_class.__table__.columns if col.unique}

                for field, value in resolved_fields.items():
                    if not hasattr(target_entity, field):
                        continue

                    # Skip unchanged values
                    if getattr(target_entity, field) == value:
                        continue

                    # Unique columns are now safe because the source row is being deleted.
                    setattr(target_entity, field, value)
            self.session.commit()

            logger.info(
                f"Merge complete: {model_name} {source_id} -> {target_id}. "
                f"Updated tables: {sorted(updated_tables)}. "
                f"Constraint-skipped tables: {sorted(skipped_tables)}."
            )
            return True

        except SQLAlchemyError as e:
            logger.error(f"Error merging {model_name}: {e}")
            self.session.rollback()
            return False

    def _safe_execute(self, stmt) -> int:
        """
        Execute a statement inside a savepoint.
        Returns rowcount on success, -1 if a unique/integrity constraint fired.
        Any other SQLAlchemyError is re-raised.
        """
        try:
            with self.session.begin_nested():
                result = self.session.execute(stmt)
                return result.rowcount
        except IntegrityError:
            logger.debug("Skipping statement due to unique constraint violation.")
            return -1

    def _migrate_fk_rows_individually(self, table, fk_column, source_id, target_id):
        """Move rows referencing source_id to target_id one row at a time.

        Used as a fallback when a bulk UPDATE fails because at least one row
        would collide with a unique constraint (the target already has an
        equivalent row). Handling rows individually ensures non-conflicting
        rows are still migrated instead of being dropped along with the
        genuine duplicates.
        """
        pk_columns = list(table.primary_key.columns)
        select_stmt = select(*pk_columns).where(fk_column == source_id)
        rows = self.session.execute(select_stmt).fetchall()

        moved = 0
        dropped = 0
        for row in rows:
            row_filter = [col == val for col, val in zip(pk_columns, row)]
            update_stmt = (
                update(table).where(*row_filter).values({fk_column.name: target_id})
            )
            rowcount = self._safe_execute(update_stmt)
            if rowcount > 0:
                moved += 1
            elif rowcount == -1:
                delete_stmt = sql_delete(table).where(*row_filter)
                self._safe_execute(delete_stmt)
                dropped += 1

        return moved, dropped


class SplitDB(BaseDBHelper):
    """Class for splitting different database models."""

    # ------------------------------------------------------------------
    # Internal helper
    # ------------------------------------------------------------------

    def _safe_add(self, obj) -> bool:
        """
        Add a single ORM object inside a savepoint.
        Returns True on success, False if a unique/integrity constraint fired.
        Any other exception is re-raised so the outer try/except still sees it.
        """
        try:
            with self.session.begin_nested():
                self.session.add(obj)
            return True
        except IntegrityError:
            # Constraint violation — row already exists or PK collision.
            # The savepoint is automatically rolled back; session stays usable.
            logger.debug(
                f"Skipping duplicate {type(obj).__name__}: constraint violation"
            )
            return False

    # ------------------------------------------------------------------
    # split_publisher
    # ------------------------------------------------------------------

    def split_publisher(self, publisher_id: int, new_names: list):
        """Split one publisher into N publishers, matching to existing when possible."""
        logger.debug(f"Splitting publisher {publisher_id} into publishers: {new_names}")

        original_publisher = self.session.get(src.db_tables.Publisher, publisher_id)
        if not original_publisher:
            logger.error(f"Publisher with ID {publisher_id} not found")
            return False

        try:
            new_publishers = []

            for name in new_names:
                existing = self.session.scalar(
                    select(src.db_tables.Publisher).where(
                        src.db_tables.Publisher.publisher_name == name
                    )
                )
                if existing:
                    logger.debug(
                        f"Using existing publisher: {name} (ID: {existing.publisher_id})"
                    )
                    new_publishers.append(existing)
                else:
                    new_pub = src.db_tables.Publisher(
                        publisher_name=name,
                        description=original_publisher.description,
                        logo_path=original_publisher.logo_path,
                        parent_id=original_publisher.parent_id,
                        begin_year=original_publisher.begin_year,
                        end_year=original_publisher.end_year,
                        is_active=original_publisher.is_active,
                        wikipedia_link=original_publisher.wikipedia_link,
                    )
                    if self._safe_add(new_pub):
                        new_publishers.append(new_pub)
                    else:
                        # Race condition: someone inserted the same name between
                        # our SELECT and our INSERT — re-fetch and reuse.
                        refetched = self.session.scalar(
                            select(src.db_tables.Publisher).where(
                                src.db_tables.Publisher.publisher_name == name
                            )
                        )
                        if refetched:
                            new_publishers.append(refetched)

            if not new_publishers:
                logger.error("No publishers were created or found; aborting split.")
                return False

            album_ids = [
                assoc.album_id for assoc in original_publisher.album_associations
            ]

            for new_pub in new_publishers:
                existing_album_ids = {
                    assoc.album_id for assoc in new_pub.album_associations
                }
                for album_id in album_ids:
                    if album_id not in existing_album_ids:
                        self._safe_add(
                            src.db_tables.AlbumPublisher(
                                album_id=album_id,
                                publisher_id=new_pub.publisher_id,
                            )
                        )

            if original_publisher not in new_publishers:
                self.session.delete(original_publisher)

            self.session.commit()
            logger.info(
                f"Successfully split publisher {publisher_id} into "
                f"{len(new_publishers)} publishers."
            )
            return True

        except SQLAlchemyError as e:
            logger.error(f"Error splitting publisher: {e}")
            self.session.rollback()
            return False

    # ------------------------------------------------------------------
    # split_artist
    # ------------------------------------------------------------------

    def split_artist(self, artist_id: int, new_names: list):
        """Split one artist into N artists, duplicating relationships."""
        logger.debug(f"Splitting artist {artist_id} into {len(new_names)} artists")

        original_artist = self.session.get(src.db_tables.Artist, artist_id)
        if not original_artist:
            logger.error(f"Artist with ID {artist_id} not found")
            return False

        try:
            new_artists = []

            for name in new_names:
                existing = self.session.scalar(
                    select(src.db_tables.Artist).where(
                        src.db_tables.Artist.artist_name == name
                    )
                )
                if existing:
                    logger.debug(
                        f"Using existing artist: {name} (ID: {existing.artist_id})"
                    )
                    new_artists.append(existing)
                else:
                    new_artist = src.db_tables.Artist(artist_name=name)
                    if self._safe_add(new_artist):
                        new_artists.append(new_artist)
                    else:
                        refetched = self.session.scalar(
                            select(src.db_tables.Artist).where(
                                src.db_tables.Artist.artist_name == name
                            )
                        )
                        if refetched:
                            new_artists.append(refetched)

            if not new_artists:
                logger.error("No artists were created or found; aborting split.")
                return False

            # Pre-collect existing combos across all new artists to skip dupes
            existing_track_roles = {
                (tr.track_id, tr.artist_id, tr.role_id)
                for a in new_artists
                for tr in a.track_roles
            }
            existing_album_roles = {
                (ar.album_id, ar.artist_id, ar.role_id)
                for a in new_artists
                for ar in a.album_roles
            }

            for track_role in original_artist.track_roles:
                for new_artist in new_artists:
                    combo = (
                        track_role.track_id,
                        new_artist.artist_id,
                        track_role.role_id,
                    )
                    if combo not in existing_track_roles:
                        added = self._safe_add(
                            src.db_tables.TrackArtistRole(
                                track_id=track_role.track_id,
                                artist_id=new_artist.artist_id,
                                role_id=track_role.role_id,
                            )
                        )
                        if added:
                            existing_track_roles.add(combo)

            for album_role in original_artist.album_roles:
                for new_artist in new_artists:
                    combo = (
                        album_role.album_id,
                        new_artist.artist_id,
                        album_role.role_id,
                    )
                    if combo not in existing_album_roles:
                        added = self._safe_add(
                            src.db_tables.AlbumRoleAssociation(
                                album_id=album_role.album_id,
                                artist_id=new_artist.artist_id,
                                role_id=album_role.role_id,
                            )
                        )
                        if added:
                            existing_album_roles.add(combo)

            if original_artist not in new_artists:
                self.session.delete(original_artist)

            self.session.commit()
            logger.info(
                f"Successfully split artist {artist_id} into {len(new_artists)} artists."
            )
            return True

        except SQLAlchemyError as e:
            logger.error(f"Error splitting artist: {e}")
            self.session.rollback()
            return False

    # ------------------------------------------------------------------
    # split_genre
    # ------------------------------------------------------------------

    def split_genre(self, genre_id: int, new_names: list):
        """Split one genre into N genres, duplicating relationships."""
        logger.debug(f"Splitting genre {genre_id} into {len(new_names)} genres")

        original_genre = self.session.get(src.db_tables.Genre, genre_id)
        if not original_genre:
            logger.error(f"Genre with ID {genre_id} not found")
            return False

        try:
            new_genres = []

            for name in new_names:
                existing = (
                    self.session.query(src.db_tables.Genre)
                    .filter(src.db_tables.Genre.genre_name == name)
                    .first()
                )
                if existing and existing.genre_id != genre_id:
                    logger.info(
                        f"Reusing existing genre '{name}' (ID: {existing.genre_id})"
                    )
                    new_genres.append(existing)
                else:
                    new_genre = src.db_tables.Genre(
                        genre_name=name,
                        description=original_genre.description,
                        parent_id=original_genre.parent_id,
                    )
                    if self._safe_add(new_genre):
                        new_genres.append(new_genre)
                    else:
                        refetched = (
                            self.session.query(src.db_tables.Genre)
                            .filter(src.db_tables.Genre.genre_name == name)
                            .first()
                        )
                        if refetched:
                            new_genres.append(refetched)

            if not new_genres:
                logger.error("No genres were created or found; aborting split.")
                return False

            existing_track_genres = {
                (tg.track_id, tg.genre_id) for g in new_genres for tg in g.tracks
            }

            for track_genre in original_genre.tracks:
                for new_genre in new_genres:
                    combo = (track_genre.track_id, new_genre.genre_id)
                    if combo not in existing_track_genres:
                        added = self._safe_add(
                            src.db_tables.TrackGenre(
                                track_id=track_genre.track_id,
                                genre_id=new_genre.genre_id,
                            )
                        )
                        if added:
                            existing_track_genres.add(combo)

            if original_genre.children:
                first_new_genre_id = new_genres[0].genre_id
                for child_genre in original_genre.children:
                    child_genre.parent_id = first_new_genre_id

            if original_genre not in new_genres:
                self.session.delete(original_genre)

            self.session.commit()
            logger.info(
                f"Successfully split genre {genre_id} into {len(new_genres)} genres."
            )
            return True

        except SQLAlchemyError as e:
            logger.error(f"Error splitting genre: {e}")
            self.session.rollback()
            return False

    # ------------------------------------------------------------------
    # split_mood
    # ------------------------------------------------------------------

    def split_mood(self, mood_id: int, new_names: list):
        """Split one mood into N moods, duplicating relationships."""
        logger.debug(f"Splitting mood {mood_id} into {len(new_names)} moods")

        original_mood = self.session.get(src.db_tables.Mood, mood_id)
        if not original_mood:
            logger.error(f"Mood with ID {mood_id} not found")
            return False

        try:
            new_moods = []

            for name in new_names:
                existing = (
                    self.session.query(src.db_tables.Mood)
                    .filter(src.db_tables.Mood.mood_name == name)
                    .first()
                )
                if existing and existing.mood_id != mood_id:
                    logger.info(
                        f"Reusing existing mood '{name}' (ID: {existing.mood_id})"
                    )
                    new_moods.append(existing)
                else:
                    new_mood = src.db_tables.Mood(
                        mood_name=name,
                        mood_description=original_mood.mood_description,
                        parent_id=original_mood.parent_id,
                    )
                    if self._safe_add(new_mood):
                        new_moods.append(new_mood)
                    else:
                        refetched = (
                            self.session.query(src.db_tables.Mood)
                            .filter(src.db_tables.Mood.mood_name == name)
                            .first()
                        )
                        if refetched:
                            new_moods.append(refetched)

            if not new_moods:
                logger.error("No moods were created or found; aborting split.")
                return False

            existing_mood_tracks = {
                (mt.mood_id, mt.track_id) for m in new_moods for mt in m.mood_tracks
            }

            for mood_track in original_mood.mood_tracks:
                for new_mood in new_moods:
                    combo = (new_mood.mood_id, mood_track.track_id)
                    if combo not in existing_mood_tracks:
                        added = self._safe_add(
                            src.db_tables.MoodTrackAssociation(
                                mood_id=new_mood.mood_id,
                                track_id=mood_track.track_id,
                            )
                        )
                        if added:
                            existing_mood_tracks.add(combo)

            if original_mood not in new_moods:
                self.session.delete(original_mood)

            self.session.commit()
            logger.info(
                f"Successfully split mood {mood_id} into {len(new_moods)} moods."
            )
            return True

        except SQLAlchemyError as e:
            logger.error(f"Error splitting mood: {e}")
            self.session.rollback()
            return False

    # ------------------------------------------------------------------
    # split_role
    # ------------------------------------------------------------------

    def split_role(self, role_id: int, new_names: list):
        """Split one role into N roles, duplicating relationships."""
        logger.debug(f"Splitting role {role_id} into {len(new_names)} roles")

        original_role = self.session.get(src.db_tables.Role, role_id)
        if not original_role:
            logger.error(f"Role with ID {role_id} not found")
            return False

        try:
            new_roles = []

            for name in new_names:
                existing = (
                    self.session.query(src.db_tables.Role)
                    .filter(src.db_tables.Role.role_name == name)
                    .first()
                )
                if existing and existing.role_id != role_id:
                    logger.info(
                        f"Reusing existing role '{name}' (ID: {existing.role_id})"
                    )
                    new_roles.append(existing)
                else:
                    new_role = src.db_tables.Role(
                        role_name=name,
                        role_description=original_role.role_description,
                        role_type=original_role.role_type,
                        parent_id=original_role.parent_id,
                        _artist_count=original_role._artist_count,
                    )
                    if self._safe_add(new_role):
                        new_roles.append(new_role)
                    else:
                        refetched = (
                            self.session.query(src.db_tables.Role)
                            .filter(src.db_tables.Role.role_name == name)
                            .first()
                        )
                        if refetched:
                            new_roles.append(refetched)

            if not new_roles:
                logger.error("No roles were created or found; aborting split.")
                return False

            existing_track_roles = {
                (tr.track_id, tr.artist_id, tr.role_id)
                for r in new_roles
                for tr in r.track_roles
            }
            existing_album_roles = {
                (ar.album_id, ar.artist_id, ar.role_id)
                for r in new_roles
                for ar in r.album_roles
            }

            for track_role in original_role.track_roles:
                for new_role in new_roles:
                    combo = (
                        track_role.track_id,
                        track_role.artist_id,
                        new_role.role_id,
                    )
                    if combo not in existing_track_roles:
                        added = self._safe_add(
                            src.db_tables.TrackArtistRole(
                                track_id=track_role.track_id,
                                artist_id=track_role.artist_id,
                                role_id=new_role.role_id,
                            )
                        )
                        if added:
                            existing_track_roles.add(combo)

            for album_role in original_role.album_roles:
                for new_role in new_roles:
                    combo = (
                        album_role.album_id,
                        album_role.artist_id,
                        new_role.role_id,
                    )
                    if combo not in existing_album_roles:
                        added = self._safe_add(
                            src.db_tables.AlbumRoleAssociation(
                                album_id=album_role.album_id,
                                artist_id=album_role.artist_id,
                                role_id=new_role.role_id,
                            )
                        )
                        if added:
                            existing_album_roles.add(combo)

            if original_role not in new_roles:
                self.session.delete(original_role)

            self.session.commit()
            logger.info(
                f"Successfully split role {role_id} into {len(new_roles)} roles."
            )
            return True

        except SQLAlchemyError as e:
            logger.error(f"Error splitting role: {e}")
            self.session.rollback()
            return False

    # ------------------------------------------------------------------
    # split_entity  (router — unchanged)
    # ------------------------------------------------------------------

    def split_entity(self, model_name: str, entity_id: int, split_attributes: list):
        """Generic split method that routes to specific implementations."""
        new_names = [
            attrs.get("name", "") for attrs in split_attributes if attrs.get("name")
        ]
        if not new_names:
            logger.error("No valid names provided for split")
            return False

        split_methods = {
            "Publisher": self.split_publisher,
            "Artist": self.split_artist,
            "Genre": self.split_genre,
            "Mood": self.split_mood,
            "Role": self.split_role,
        }

        if model_name in split_methods:
            return split_methods[model_name](entity_id, new_names)
        else:
            logger.error(f"Split not implemented for model: {model_name}")
            return False
            return False
