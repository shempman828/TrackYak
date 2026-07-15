"""Class for splitting different database models."""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from src.core.logger_config import logger
from src.db.db_helpers.registry import BaseDBHelper
from src.db.db_tables import (
    AlbumPublisher,
    AlbumRoleAssociation,
    Artist,
    Genre,
    Mood,
    MoodTrackAssociation,
    Publisher,
    Role,
    TrackArtistRole,
    TrackGenre,
)


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

        original_publisher = self.session.get(Publisher, publisher_id)
        if not original_publisher:
            logger.error(f"Publisher with ID {publisher_id} not found")
            return False

        try:
            new_publishers = []

            for name in new_names:
                existing = self.session.scalar(
                    select(Publisher).where(Publisher.publisher_name == name)
                )
                if existing:
                    logger.debug(
                        f"Using existing publisher: {name} (ID: {existing.publisher_id})"
                    )
                    new_publishers.append(existing)
                else:
                    new_pub = Publisher(
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
                            select(Publisher).where(Publisher.publisher_name == name)
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
                            AlbumPublisher(
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

        original_artist = self.session.get(Artist, artist_id)
        if not original_artist:
            logger.error(f"Artist with ID {artist_id} not found")
            return False

        try:
            new_artists = []

            for name in new_names:
                existing = self.session.scalar(
                    select(Artist).where(Artist.artist_name == name)
                )
                if existing:
                    logger.debug(
                        f"Using existing artist: {name} (ID: {existing.artist_id})"
                    )
                    new_artists.append(existing)
                else:
                    new_artist = Artist(artist_name=name)
                    if self._safe_add(new_artist):
                        new_artists.append(new_artist)
                    else:
                        refetched = self.session.scalar(
                            select(Artist).where(Artist.artist_name == name)
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
                            TrackArtistRole(
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
                            AlbumRoleAssociation(
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

        original_genre = self.session.get(Genre, genre_id)
        if not original_genre:
            logger.error(f"Genre with ID {genre_id} not found")
            return False

        try:
            new_genres = []

            for name in new_names:
                existing = (
                    self.session.query(Genre).filter(Genre.genre_name == name).first()
                )
                if existing and existing.genre_id != genre_id:
                    logger.info(
                        f"Reusing existing genre '{name}' (ID: {existing.genre_id})"
                    )
                    new_genres.append(existing)
                else:
                    new_genre = Genre(
                        genre_name=name,
                        description=original_genre.description,
                        parent_id=original_genre.parent_id,
                    )
                    if self._safe_add(new_genre):
                        new_genres.append(new_genre)
                    else:
                        refetched = (
                            self.session.query(Genre)
                            .filter(Genre.genre_name == name)
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
                            TrackGenre(
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

        original_mood = self.session.get(Mood, mood_id)
        if not original_mood:
            logger.error(f"Mood with ID {mood_id} not found")
            return False

        try:
            new_moods = []

            for name in new_names:
                existing = (
                    self.session.query(Mood).filter(Mood.mood_name == name).first()
                )
                if existing and existing.mood_id != mood_id:
                    logger.info(
                        f"Reusing existing mood '{name}' (ID: {existing.mood_id})"
                    )
                    new_moods.append(existing)
                else:
                    new_mood = Mood(
                        mood_name=name,
                        mood_description=original_mood.mood_description,
                        parent_id=original_mood.parent_id,
                    )
                    if self._safe_add(new_mood):
                        new_moods.append(new_mood)
                    else:
                        refetched = (
                            self.session.query(Mood)
                            .filter(Mood.mood_name == name)
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
                            MoodTrackAssociation(
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

        original_role = self.session.get(Role, role_id)
        if not original_role:
            logger.error(f"Role with ID {role_id} not found")
            return False

        try:
            new_roles = []

            for name in new_names:
                existing = (
                    self.session.query(Role).filter(Role.role_name == name).first()
                )
                if existing and existing.role_id != role_id:
                    logger.info(
                        f"Reusing existing role '{name}' (ID: {existing.role_id})"
                    )
                    new_roles.append(existing)
                else:
                    new_role = Role(
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
                            self.session.query(Role)
                            .filter(Role.role_name == name)
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
                            TrackArtistRole(
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
                            AlbumRoleAssociation(
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
