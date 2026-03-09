import inspect
import os

from sqlalchemy import create_engine, select, update
from sqlalchemy.exc import SQLAlchemyError
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


class MergeDB(BaseDBHelper):
    """Class for merging database entries."""

    def merge_entities(self, model_name: str, source_id: int, target_id: int):
        """Merge two entities of the same type across all relationship tables."""
        logger.debug(f"Merging {model_name} ID {source_id} -> {target_id}")

        try:
            entity_class = globals()[model_name]
        except KeyError:
            logger.error(f"Entity '{model_name}' not found in globals()")
            return False

        source_entity = self.session.get(entity_class, source_id)
        target_entity = self.session.get(entity_class, target_id)
        if not source_entity or not target_entity:
            logger.error(f"Source or target {model_name} not found")
            return False

        try:
            # Get the primary key column name for the entity
            pk_columns = [
                col.name for col in entity_class.__table__.primary_key.columns
            ]
            if not pk_columns:
                logger.error(f"No primary key found for {model_name}")
                return False

            # Use the first primary key column (most common case)
            pk_column = pk_columns[0]

            # Get all tables that might reference this entity
            metadata = entity_class.metadata
            updated_tables = set()

            for table in metadata.tables.values():
                # Skip the entity's own table
                if table.name == entity_class.__table__.name:
                    continue

                # Check each column for foreign keys pointing to our entity table
                for column in table.columns:
                    for fk in column.foreign_keys:
                        # Check if this FK points to our entity's primary key
                        if (
                            fk.column.table.name == entity_class.__table__.name
                            and fk.column.name == pk_column
                        ):
                            logger.debug(
                                f"Found FK: {table.name}.{column.name} -> {entity_class.__table__.name}.{pk_column}"
                            )

                            # Update references from source to target
                            update_stmt = (
                                update(table)
                                .where(column == source_id)
                                .values({column.name: target_id})
                            )

                            # Check for unique constraint violations before updating
                            try:
                                result = self.session.execute(update_stmt)
                                if result.rowcount > 0:
                                    logger.info(
                                        f"Updated {result.rowcount} rows in {table.name}.{column.name}"
                                    )
                                    updated_tables.add(table.name)
                            except SQLAlchemyError as e:
                                logger.warning(
                                    f"Could not update {table.name}.{column.name}: {e}. "
                                    f"This may be due to unique constraints."
                                )
                                # Continue with other tables rather than failing completely

            # Delete old entity only if we're confident about the merge
            self.session.delete(source_entity)
            self.session.commit()

            logger.info(
                f"✅ Merge complete for {model_name} {source_id} -> {target_id}. "
                f"Updated {len(updated_tables)} tables: {sorted(updated_tables)}"
            )
            return True

        except SQLAlchemyError as e:
            logger.error(f"Error merging {model_name}: {e}")
            self.session.rollback()
            return False


class SplitDB(BaseDBHelper):
    """Class for splitting different database models."""

    def split_publisher(self, publisher_id: int, new_names: list):
        """Split one publisher into N publishers, matching to existing when possible."""
        logger.debug(f"Splitting publisher {publisher_id} into publishers: {new_names}")

        original_publisher = self.session.get(src.db_tables.Publisher, publisher_id)
        if not original_publisher:
            logger.error(f"Publisher with ID {publisher_id} not found")
            return False

        try:
            new_publishers = []

            # Create or find publishers
            for name in new_names:
                # Check if publisher with this name already exists
                existing_publisher = self.session.scalar(
                    select(src.db_tables.Publisher).where(
                        src.db_tables.Publisher.publisher_name == name
                    )
                )

                if existing_publisher:
                    # Use existing publisher
                    logger.debug(
                        f"Using existing publisher: {name} (ID: {existing_publisher.publisher_id})"
                    )
                    new_publishers.append(existing_publisher)
                else:
                    # Create new publisher
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
                    self.session.add(new_pub)
                    new_publishers.append(new_pub)
                    logger.debug(f"Creating new publisher: {name}")

            self.session.flush()

            # Duplicate album associations only to NEW publishers (not existing ones)
            album_ids = [
                assoc.album_id for assoc in original_publisher.album_associations
            ]

            for new_pub in new_publishers:
                # Skip if this is an existing publisher that already has associations
                # (we don't want to duplicate existing associations)
                existing_album_ids = (
                    [assoc.album_id for assoc in new_pub.album_associations]
                    if hasattr(new_pub, "album_associations")
                    else []
                )

                for album_id in album_ids:
                    if album_id not in existing_album_ids:
                        # Create association only if it doesn't already exist
                        new_assoc = src.db_tables.AlbumPublisher(
                            album_id=album_id, publisher_id=new_pub.publisher_id
                        )
                        self.session.add(new_assoc)

            # Delete original publisher (but not if it's the same as one of the new ones)
            if original_publisher not in new_publishers:
                self.session.delete(original_publisher)

            self.session.commit()

            logger.info(
                f"Successfully split publisher {publisher_id}. "
                f"Created {len([p for p in new_publishers if p.publisher_id != publisher_id])} new publishers, "
                f"used {len([p for p in new_publishers if p.publisher_id == publisher_id])} existing publishers."
            )
            return True

        except SQLAlchemyError as e:
            logger.error(f"Error splitting publisher: {e}")
            self.session.rollback()
        return False

    def split_artist(self, artist_id: int, new_names: list):
        """Split one artist into N artists, duplicating relationships."""
        logger.debug(f"Splitting artist {artist_id} into {len(new_names)} artists")

        original_artist = self.session.get(src.db_tables.Artist, artist_id)
        if not original_artist:
            logger.error(f"Artist with ID {artist_id} not found")
            return False

        try:
            new_artists = []

            # Create or find artists for each name
            for name in new_names:
                # Check if an artist with this name already exists
                existing_artist = self.session.scalar(
                    select(src.db_tables.Artist).where(
                        src.db_tables.Artist.artist_name == name
                    )
                )

                if existing_artist:
                    # Use the existing artist instead of creating a duplicate
                    logger.debug(
                        f"Using existing artist: {name} (ID: {existing_artist.artist_id})"
                    )
                    new_artists.append(existing_artist)
                else:
                    # Create a brand new artist
                    new_artist = src.db_tables.Artist(
                        artist_name=name,
                    )
                    self.session.add(new_artist)
                    new_artists.append(new_artist)
                    logger.debug(f"Creating new artist: {name}")

            self.session.flush()

            # Collect existing track role combos to avoid duplicate primary key errors
            existing_track_roles = {
                (tr.track_id, tr.artist_id, tr.role_id)
                for new_artist in new_artists
                for tr in new_artist.track_roles
            }

            # Duplicate track artist roles
            for track_role in original_artist.track_roles:
                for new_artist in new_artists:
                    combo = (
                        track_role.track_id,
                        new_artist.artist_id,
                        track_role.role_id,
                    )
                    if combo not in existing_track_roles:
                        new_role = src.db_tables.TrackArtistRole(
                            track_id=track_role.track_id,
                            artist_id=new_artist.artist_id,
                            role_id=track_role.role_id,
                        )
                        self.session.add(new_role)
                        existing_track_roles.add(combo)

            # Duplicate album roles
            for album_role in original_artist.album_roles:
                for new_artist in new_artists:
                    new_album_role = src.db_tables.AlbumRoleAssociation(
                        album_id=album_role.album_id,
                        artist_id=new_artist.artist_id,
                        role_id=album_role.role_id,
                    )
                    self.session.add(new_album_role)

            # Delete the original artist - cascades handle relationship cleanup
            # (but only if it's not one of the artists we're keeping)
            if original_artist not in new_artists:
                self.session.delete(original_artist)

            self.session.commit()

            logger.info(
                f"Successfully split artist {artist_id} into {len(new_artists)} artists. "
                f"Created {len([a for a in new_artists if a.artist_id != artist_id])} new, "
                f"used {len([a for a in new_artists if a.artist_id == artist_id])} existing."
            )
            return True

        except SQLAlchemyError as e:
            logger.error(f"Error splitting artist: {e}")
            self.session.rollback()
            return False

    def split_genre(self, genre_id: int, new_names: list):
        """Split one genre into N genres, duplicating relationships."""
        logger.debug(f"Splitting genre {genre_id} into {len(new_names)} genres")

        original_genre = self.session.get(src.db_tables.Genre, genre_id)
        if not original_genre:
            logger.error(f"Genre with ID {genre_id} not found")
            return False

        try:
            new_genres = []

            # Create new genres, or reuse existing ones if the name already exists
            for name in new_names:
                existing_genre = (
                    self.session.query(src.db_tables.Genre)
                    .filter(src.db_tables.Genre.genre_name == name)
                    .first()
                )
                if existing_genre and existing_genre.genre_id != genre_id:
                    # A genre with this name already exists — reuse it
                    logger.info(
                        f"Reusing existing genre '{name}' (ID: {existing_genre.genre_id})"
                    )
                    new_genres.append(existing_genre)
                else:
                    # No match found — create a brand new genre
                    new_genre = src.db_tables.Genre(
                        genre_name=name,
                        description=original_genre.description,
                        parent_id=original_genre.parent_id,
                    )
                    self.session.add(new_genre)
                    new_genres.append(new_genre)

            self.session.flush()

            # Duplicate track genre associations, skipping any that already exist
            for track_genre in original_genre.tracks:
                for new_genre in new_genres:
                    already_exists = (
                        self.session.query(src.db_tables.TrackGenre)
                        .filter_by(
                            track_id=track_genre.track_id,
                            genre_id=new_genre.genre_id,
                        )
                        .first()
                    )
                    if already_exists:
                        logger.info(
                            f"Skipping duplicate TrackGenre: track {track_genre.track_id} "
                            f"already has genre '{new_genre.genre_name}'"
                        )
                        continue
                    assoc = src.db_tables.TrackGenre(
                        track_id=track_genre.track_id, genre_id=new_genre.genre_id
                    )
                    self.session.add(assoc)

            # Update children genres to point to first new genre as parent
            if original_genre.children:
                first_new_genre_id = new_genres[0].genre_id
                for child_genre in original_genre.children:
                    child_genre.parent_id = first_new_genre_id

            # Delete original genre (only if it isn't one of the reused targets)
            if original_genre not in new_genres:
                self.session.delete(original_genre)
            self.session.commit()

            logger.info(
                f"Successfully split genre {genre_id} into {len(new_genres)} genres"
            )
            return True

        except SQLAlchemyError as e:
            logger.error(f"Error splitting genre: {e}")
            self.session.rollback()
            return False

    def split_mood(self, mood_id: int, new_names: list):
        """Split one mood into N moods, duplicating relationships."""
        logger.debug(f"Splitting mood {mood_id} into {len(new_names)} moods")

        original_mood = self.session.get(src.db_tables.Mood, mood_id)
        if not original_mood:
            logger.error(f"Mood with ID {mood_id} not found")
            return False

        try:
            new_moods = []

            # Create new moods, or reuse existing ones if the name already exists
            for name in new_names:
                existing_mood = (
                    self.session.query(src.db_tables.Mood)
                    .filter(src.db_tables.Mood.mood_name == name)
                    .first()
                )
                if existing_mood and existing_mood.mood_id != mood_id:
                    logger.info(
                        f"Reusing existing mood '{name}' (ID: {existing_mood.mood_id})"
                    )
                    new_moods.append(existing_mood)
                else:
                    new_mood = src.db_tables.Mood(
                        mood_name=name,
                        mood_description=original_mood.mood_description,
                        parent_id=original_mood.parent_id,
                    )
                    self.session.add(new_mood)
                    new_moods.append(new_mood)

            self.session.flush()

            # Duplicate mood-track associations, skipping any that already exist
            for mood_track in original_mood.mood_tracks:
                for new_mood in new_moods:
                    already_exists = (
                        self.session.query(src.db_tables.MoodTrackAssociation)
                        .filter_by(
                            mood_id=new_mood.mood_id,
                            track_id=mood_track.track_id,
                        )
                        .first()
                    )
                    if already_exists:
                        logger.info(
                            f"Skipping duplicate MoodTrackAssociation: track "
                            f"{mood_track.track_id} already has mood '{new_mood.mood_name}'"
                        )
                        continue
                    new_assoc = src.db_tables.MoodTrackAssociation(
                        mood_id=new_mood.mood_id, track_id=mood_track.track_id
                    )
                    self.session.add(new_assoc)

            # Delete original mood (only if it isn't one of the reused targets)
            if original_mood not in new_moods:
                self.session.delete(original_mood)
            self.session.commit()

            logger.info(
                f"Successfully split mood {mood_id} into {len(new_moods)} moods"
            )
            return True

        except SQLAlchemyError as e:
            logger.error(f"Error splitting mood: {e}")
            self.session.rollback()
            return False

    def split_role(self, role_id: int, new_names: list):
        """Split one role into N roles, duplicating relationships."""
        logger.debug(f"Splitting role {role_id} into {len(new_names)} roles")

        original_role = self.session.get(src.db_tables.Role, role_id)
        if not original_role:
            logger.error(f"Role with ID {role_id} not found")
            return False

        try:
            new_roles = []

            # Create new roles, or reuse existing ones if the name already exists
            for name in new_names:
                existing_role = (
                    self.session.query(src.db_tables.Role)
                    .filter(src.db_tables.Role.role_name == name)
                    .first()
                )
                if existing_role and existing_role.role_id != role_id:
                    logger.info(
                        f"Reusing existing role '{name}' (ID: {existing_role.role_id})"
                    )
                    new_roles.append(existing_role)
                else:
                    new_role = src.db_tables.Role(
                        role_name=name,
                        role_description=original_role.role_description,
                        role_type=original_role.role_type,
                        parent_id=original_role.parent_id,
                        _artist_count=original_role._artist_count,
                    )
                    self.session.add(new_role)
                    new_roles.append(new_role)

            self.session.flush()

            # Collect existing track-role combos to avoid duplicate primary key errors
            existing_track_roles = {
                (tr.track_id, tr.artist_id, tr.role_id)
                for new_role in new_roles
                for tr in new_role.track_roles
            }

            # Duplicate track artist roles, skipping any that already exist
            for track_role in original_role.track_roles:
                for new_role in new_roles:
                    combo = (
                        track_role.track_id,
                        track_role.artist_id,
                        new_role.role_id,
                    )
                    if combo in existing_track_roles:
                        logger.info(
                            f"Skipping duplicate TrackArtistRole: track {track_role.track_id} "
                            f"artist {track_role.artist_id} already has role '{new_role.role_name}'"
                        )
                        continue
                    new_track_role = src.db_tables.TrackArtistRole(
                        track_id=track_role.track_id,
                        artist_id=track_role.artist_id,
                        role_id=new_role.role_id,
                    )
                    self.session.add(new_track_role)
                    existing_track_roles.add(combo)

            # Collect existing album-role combos to avoid duplicate primary key errors
            existing_album_roles = {
                (ar.album_id, ar.artist_id, ar.role_id)
                for new_role in new_roles
                for ar in new_role.album_roles
            }

            # Duplicate album role associations, skipping any that already exist
            for album_role in original_role.album_roles:
                for new_role in new_roles:
                    combo = (
                        album_role.album_id,
                        album_role.artist_id,
                        new_role.role_id,
                    )
                    if combo in existing_album_roles:
                        logger.info(
                            f"Skipping duplicate AlbumRoleAssociation: album {album_role.album_id} "
                            f"artist {album_role.artist_id} already has role '{new_role.role_name}'"
                        )
                        continue
                    new_album_role = src.db_tables.AlbumRoleAssociation(
                        album_id=album_role.album_id,
                        artist_id=album_role.artist_id,
                        role_id=new_role.role_id,
                    )
                    self.session.add(new_album_role)
                    existing_album_roles.add(combo)

            # Delete original role (only if it isn't one of the reused targets)
            if original_role not in new_roles:
                self.session.delete(original_role)
            self.session.commit()

            logger.info(
                f"Successfully split role {role_id} into {len(new_roles)} roles"
            )
            return True

        except SQLAlchemyError as e:
            logger.error(f"Error splitting role: {e}")
            self.session.rollback()
            return False

    # Generic method that routes to specific implementations
    def split_entity(self, model_name: str, entity_id: int, split_attributes: list):
        """Generic split method that routes to specific implementations."""
        # Extract names from split_attributes
        new_names = [
            attrs.get("name", "") for attrs in split_attributes if attrs.get("name")
        ]

        if not new_names:
            logger.error("No valid names provided for split")
            return False

        # Route to specific split method based on model_name
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
