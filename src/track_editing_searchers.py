from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from logger_config import logger


class SearchHandlers:
    def __init__(self, dialog, is_multi_track):
        self.dialog = dialog
        self.is_multi_track = is_multi_track

    @property
    def tracks(self):
        """Get tracks based on mode."""
        if self.is_multi_track:
            return self.dialog.tracks  # Multi-track case
        else:
            return [self.dialog.track]  # Single-track case as list

    @property
    def controller(self):
        return self.dialog.controller

    def _on_artist_search_changed(self, text):
        """Search for artists and show results in dropdown."""
        text = text.strip()
        if len(text) >= 2:
            artists = self.controller.get.get_entity_object("Artist", artist_name=text)
            self.dialog.artist_search_combo.clear()
            self.dialog.artist_search_combo.addItem(f"Create new: '{text}'", "new")

            if artists is None:
                pass
            elif not isinstance(artists, (list, tuple)):
                # FIX: Handle single artist object
                self.dialog.artist_search_combo.addItem(
                    artists.artist_name, artists.artist_id
                )
            else:
                for artist in artists:
                    self.dialog.artist_search_combo.addItem(
                        artist.artist_name, artist.artist_id
                    )

            self.dialog.artist_search_combo.setVisible(
                self.dialog.artist_search_combo.count() > 1
            )
        else:
            self.dialog.artist_search_combo.setVisible(False)

        role_text = self.dialog.role_edit.text().strip()
        self.dialog.add_artist_role_btn.setEnabled(
            len(text) >= 2 and len(role_text) >= 2
        )

    def _on_artist_selected(self, index):
        """When artist is selected from dropdown, update search field."""
        if index > 0:
            artist_name = self.dialog.artist_search_combo.currentText()
            self.dialog.artist_search_edit.setText(artist_name)

    def _add_artist_role(self):
        """Add artist role to all selected tracks."""
        artist_name = self.dialog.artist_search_edit.text().strip()
        role_name = self.dialog.role_edit.text().strip()

        if not artist_name or not role_name:
            return

        # Handle artist - create new or use existing
        if (
            self.dialog.artist_search_combo.isVisible()
            and self.dialog.artist_search_combo.currentData() == "new"
        ):
            artist = self.controller.add.add_entity("Artist", artist_name=artist_name)
        else:
            if (
                self.dialog.artist_search_combo.isVisible()
                and self.dialog.artist_search_combo.currentData() != "new"
            ):
                artist_id = self.dialog.artist_search_combo.currentData()
                artist = self.controller.get.get_entity_object(
                    "Artist", artist_id=artist_id
                )
            else:
                # FIX: Properly handle single vs multiple artist results
                artists = self.controller.get.get_entity_object(
                    "Artist", artist_name=artist_name
                )
                if artists:
                    if isinstance(artists, (list, tuple)):
                        artist = artists[0]  # Take first match from list
                    else:
                        artist = artists  # Single artist object
                else:
                    artist = self.controller.add.add_entity(
                        "Artist", artist_name=artist_name
                    )

        # Handle role - search for existing or create new
        roles = self.controller.get.get_entity_object("Role", role_name=role_name)
        if roles:
            if isinstance(roles, (list, tuple)):
                role_id = roles[0].role_id  # Take first match from list
            else:
                role_id = roles.role_id  # Single role object
        else:
            new_role = self.controller.add.add_entity("Role", role_name=role_name)
            role_id = new_role.role_id

        if artist and role_id:
            # Apply to ALL tracks
            success_count = 0
            for track in self.tracks:
                try:
                    self.controller.add.add_entity_link(
                        "TrackArtistRole",
                        track_id=track.track_id,
                        artist_id=artist.artist_id,
                        role_id=role_id,
                    )
                    success_count += 1
                except Exception as e:
                    logger.error(
                        f"Failed to add artist role to track {track.track_id}: {e}"
                    )

            # Show success message for multi-track
            if self.is_multi_track:
                QMessageBox.information(
                    self.dialog,
                    "Artist Role Added",
                    f"Added artist role to {success_count} out of {len(self.tracks)} tracks",
                )

            # Refresh the table
            self.dialog._load_artist_roles()
            self.dialog.artist_search_edit.clear()
            self.dialog.role_edit.clear()
            self.dialog.artist_search_combo.setVisible(False)

    def _remove_artist_role(self, row):
        """Remove artist role from all selected tracks."""
        artist_item = self.dialog.artist_roles_table.item(row, 0)
        role_item = self.dialog.artist_roles_table.item(row, 1)

        if artist_item and role_item:
            artist_id = artist_item.data(Qt.UserRole)
            role_id = role_item.data(Qt.UserRole)

            if artist_id and role_id:
                # Remove from ALL tracks
                success_count = 0
                for track in self.tracks:
                    try:
                        self.controller.delete.delete_entity(
                            "TrackArtistRole",
                            track_id=track.track_id,
                            artist_id=artist_id,
                            role_id=role_id,
                        )
                        success_count += 1
                    except Exception as e:
                        logger.error(
                            f"Failed to remove artist role from track {track.track_id}: {e}"
                        )

                # Show success message for multi-track
                if self.is_multi_track:
                    QMessageBox.information(
                        self.dialog,
                        "Artist Role Removed",
                        f"Removed artist role from {success_count} out of {len(self.tracks)} tracks",
                    )

                self.dialog.artist_roles_table.removeRow(row)

    def _on_role_changed(self, text):
        """Enable add button when both artist and role have text."""
        artist_text = self.dialog.artist_search_edit.text().strip()
        role_text = self.dialog.role_edit.text().strip()
        self.dialog.add_artist_role_btn.setEnabled(
            len(artist_text) >= 2 and len(role_text) >= 2
        )

    def _on_genre_search_changed(self, text):
        """Search for genres and show results in dropdown."""
        text = text.strip()
        if len(text) >= 2:
            genres = self.controller.get.get_entity_object("Genre", genre_name=text)
            self.dialog.genre_search_combo.clear()
            self.dialog.genre_search_combo.addItem(f"Create new: '{text}'", "new")

            if genres is None:
                pass
            elif not isinstance(genres, (list, tuple)):
                # Handle single genre object
                self.dialog.genre_search_combo.addItem(
                    genres.genre_name, genres.genre_id
                )
            else:
                for genre in genres:
                    self.dialog.genre_search_combo.addItem(
                        genre.genre_name, genre.genre_id
                    )

            self.dialog.genre_search_combo.setVisible(
                self.dialog.genre_search_combo.count() > 1
            )
        else:
            self.dialog.genre_search_combo.setVisible(False)

        self.dialog.add_genre_btn.setEnabled(len(text) >= 2)

    def _on_genre_selected(self, index):
        """When genre is selected from dropdown, update search field."""
        if index > 0:
            genre_name = self.dialog.genre_search_combo.currentText()
            self.dialog.genre_search_edit.setText(genre_name)

    def _add_genre(self):
        """Add genre to all selected tracks."""
        genre_name = self.dialog.genre_search_edit.text().strip()

        if not genre_name:
            return

        # Handle genre - create new or use existing
        if (
            self.dialog.genre_search_combo.isVisible()
            and self.dialog.genre_search_combo.currentData() == "new"
        ):
            genre = self.controller.add.add_entity("Genre", genre_name=genre_name)
        else:
            if (
                self.dialog.genre_search_combo.isVisible()
                and self.dialog.genre_search_combo.currentData() != "new"
            ):
                genre_id = self.dialog.genre_search_combo.currentData()
                genre = self.controller.get.get_entity_object(
                    "Genre", genre_id=genre_id
                )
            else:
                genres = self.controller.get.get_entity_object(
                    "Genre", genre_name=genre_name
                )
                if genres:
                    if isinstance(genres, (list, tuple)):
                        genre = genres[0]  # Take first match from list
                    else:
                        genre = genres  # Single genre object
                else:
                    genre = self.controller.add.add_entity(
                        "Genre", genre_name=genre_name
                    )

        if genre:
            # Apply to ALL tracks
            success_count = 0
            for track in self.tracks:
                try:
                    self.controller.add.add_entity_link(
                        "TrackGenre", track_id=track.track_id, genre_id=genre.genre_id
                    )
                    success_count += 1
                except Exception as e:
                    logger.error(f"Failed to add genre to track {track.track_id}: {e}")

            if self.is_multi_track:
                QMessageBox.information(
                    self.dialog,
                    "Genre Added",
                    f"Added genre to {success_count} out of {len(self.tracks)} tracks",
                )

            # Refresh the list
            self.dialog._load_genres()
            self.dialog.genre_search_edit.clear()
            self.dialog.genre_search_combo.setVisible(False)

    def _remove_genre(self, row):
        """Remove genre from all selected tracks."""
        genre_item = self.dialog.genres_list.item(row)
        if genre_item:
            genre_id = genre_item.data(Qt.UserRole)
            if genre_id:
                # Remove from ALL tracks
                success_count = 0
                for track in self.tracks:
                    try:
                        self.controller.delete.delete_entity(
                            "TrackGenre",
                            track_id=track.track_id,
                            genre_id=genre_id,
                        )
                        success_count += 1
                    except Exception as e:
                        logger.error(
                            f"Failed to remove genre from track {track.track_id}: {e}"
                        )

                if self.is_multi_track:
                    QMessageBox.information(
                        self.dialog,
                        "Genre Removed",
                        f"Removed genre from {success_count} out of {len(self.tracks)} tracks",
                    )

                self.dialog.genres_list.takeItem(row)

    def _on_place_search_changed(self, text):
        """Search for places and show results in dropdown."""
        text = text.strip()
        if len(text) >= 2:
            places = self.controller.get.get_entity_object("Place", place_name=text)
            self.dialog.place_search_combo.clear()
            self.dialog.place_search_combo.addItem(f"Create new: '{text}'", "new")

            if places is None:
                pass
            elif not isinstance(places, (list, tuple)):
                # Handle single place object
                self.dialog.place_search_combo.addItem(
                    places.place_name, places.place_id
                )
            else:
                for place in places:
                    self.dialog.place_search_combo.addItem(
                        place.place_name, place.place_id
                    )

            self.dialog.place_search_combo.setVisible(
                self.dialog.place_search_combo.count() > 1
            )
        else:
            self.dialog.place_search_combo.setVisible(False)

        self.dialog.add_place_btn.setEnabled(len(text) >= 2)

    def _on_place_selected(self, index):
        """When place is selected from dropdown, update search field."""
        if index > 0:
            place_name = self.dialog.place_search_combo.currentText()
            self.dialog.place_search_edit.setText(place_name)

    def _add_place_association(self):
        """Add place association to all selected tracks."""
        place_name = self.dialog.place_search_edit.text().strip()
        association_type = self.dialog.place_type_edit.text().strip()

        if not place_name:
            return

        if not association_type:
            association_type = "Associated"

        # Handle place - create new or use existing
        if (
            self.dialog.place_search_combo.isVisible()
            and self.dialog.place_search_combo.currentData() == "new"
        ):
            place = self.controller.add.add_entity("Place", place_name=place_name)
        else:
            if (
                self.dialog.place_search_combo.isVisible()
                and self.dialog.place_search_combo.currentData() != "new"
            ):
                place_id = self.dialog.place_search_combo.currentData()
                place = self.controller.get.get_entity_object(
                    "Place", place_id=place_id
                )
            else:
                places = self.controller.get.get_entity_object(
                    "Place", place_name=place_name
                )
                if places:
                    if isinstance(places, (list, tuple)):
                        place = places[0]  # Take first match from list
                    else:
                        place = places  # Single place object
                else:
                    place = self.controller.add.add_entity(
                        "Place", place_name=place_name
                    )

        if place:
            # Apply to ALL tracks
            success_count = 0
            for track in self.tracks:
                try:
                    self.controller.add.add_entity_link(
                        "PlaceAssociation",
                        entity_id=track.track_id,
                        entity_type="Track",
                        place_id=place.place_id,
                        association_type=association_type,
                    )
                    success_count += 1
                except Exception as e:
                    logger.error(
                        f"Failed to add place association to track {track.track_id}: {e}"
                    )

            if self.is_multi_track:
                QMessageBox.information(
                    self.dialog,
                    "Place Association Added",
                    f"Added place association to {success_count} out of {len(self.tracks)} tracks",
                )

            # Refresh the table
            self.dialog._load_place_associations()
            self.dialog.place_search_edit.clear()
            self.dialog.place_type_edit.clear()
            self.dialog.place_search_combo.setVisible(False)

    def _remove_place_association(self, row):
        """Remove place association from all selected tracks."""
        place_item = self.dialog.place_associations_table.item(row, 0)

        if place_item:
            place_id = place_item.data(Qt.UserRole)

            if place_id:
                # Remove from ALL tracks
                success_count = 0
                for track in self.tracks:
                    try:
                        self.controller.delete.delete_entity(
                            "PlaceAssociation",
                            entity_id=track.track_id,
                            entity_type="Track",
                            place_id=place_id,
                        )
                        success_count += 1
                    except Exception as e:
                        logger.error(
                            f"Failed to remove place association from track {track.track_id}: {e}"
                        )

                if self.is_multi_track:
                    QMessageBox.information(
                        self.dialog,
                        "Place Association Removed",
                        f"Removed place association from {success_count} out of {len(self.tracks)} tracks",
                    )

                self.dialog.place_associations_table.removeRow(row)

    def _on_mood_search_changed(self, text):
        """Search for moods and show results in dropdown."""
        text = text.strip()
        if len(text) >= 2:
            moods = self.controller.get.get_entity_object("Mood", mood_name=text)
            self.dialog.mood_search_combo.clear()
            self.dialog.mood_search_combo.addItem(f"Create new: '{text}'", "new")

            if moods is None:
                pass
            elif not isinstance(moods, (list, tuple)):
                # Handle single mood object
                self.dialog.mood_search_combo.addItem(moods.mood_name, moods.mood_id)
            else:
                for mood in moods:
                    self.dialog.mood_search_combo.addItem(mood.mood_name, mood.mood_id)

            self.dialog.mood_search_combo.setVisible(
                self.dialog.mood_search_combo.count() > 1
            )
        else:
            self.dialog.mood_search_combo.setVisible(False)

        self.dialog.add_mood_btn.setEnabled(len(text) >= 2)

    def _add_mood(self):
        """Add mood association to all selected tracks."""
        mood_name = self.dialog.mood_search_edit.text().strip()

        if not mood_name:
            return

        # Handle mood - create new or use existing
        if (
            self.dialog.mood_search_combo.isVisible()
            and self.dialog.mood_search_combo.currentData() == "new"
        ):
            mood = self.controller.add.add_entity("Mood", mood_name=mood_name)
        else:
            if (
                self.dialog.mood_search_combo.isVisible()
                and self.dialog.mood_search_combo.currentData() != "new"
            ):
                mood_id = self.dialog.mood_search_combo.currentData()
                mood = self.controller.get.get_entity_object("Mood", mood_id=mood_id)
            else:
                moods = self.controller.get.get_entity_object(
                    "Mood", mood_name=mood_name
                )
                if moods:
                    if isinstance(moods, (list, tuple)):
                        mood = moods[0]  # Take first match from list
                    else:
                        mood = moods  # Single mood object
                else:
                    mood = self.controller.add.add_entity("Mood", mood_name=mood_name)

        if mood:
            # Apply to ALL tracks
            success_count = 0
            for track in self.tracks:
                try:
                    self.controller.add.add_entity_link(
                        "MoodTrackAssociation",
                        track_id=track.track_id,
                        mood_id=mood.mood_id,
                    )
                    success_count += 1
                except Exception as e:
                    logger.error(f"Failed to add mood to track {track.track_id}: {e}")

            if self.is_multi_track:
                QMessageBox.information(
                    self.dialog,
                    "Mood Added",
                    f"Added mood to {success_count} out of {len(self.tracks)} tracks",
                )

            # Refresh the list
            self.dialog._load_moods()
            self.dialog.mood_search_edit.clear()
            self.dialog.mood_search_combo.setVisible(False)

    def _on_mood_selected(self, index):
        """When mood is selected from dropdown, update search field."""
        if index > 0:
            mood_name = self.dialog.mood_search_combo.currentText()
            self.dialog.mood_search_edit.setText(mood_name)

    def _remove_mood(self, row):
        """Remove mood association from all selected tracks."""
        mood_item = self.dialog.moods_list.item(row)
        if mood_item:
            mood_id = mood_item.data(Qt.UserRole)
            if mood_id:
                # Remove from ALL tracks
                success_count = 0
                for track in self.tracks:
                    try:
                        self.controller.delete.delete_entity(
                            "MoodTrackAssociation",
                            track_id=track.track_id,
                            mood_id=mood_id,
                        )
                        success_count += 1
                    except Exception as e:
                        logger.error(
                            f"Failed to remove mood from track {track.track_id}: {e}"
                        )

                if self.is_multi_track:
                    QMessageBox.information(
                        self.dialog,
                        "Mood Removed",
                        f"Removed mood from {success_count} out of {len(self.tracks)} tracks",
                    )

                self.dialog.moods_list.takeItem(row)

    def _on_award_search_changed(self, text):
        """Search for awards and show results in dropdown."""
        text = text.strip()
        if len(text) >= 2:
            awards = self.controller.get.get_entity_object("Award", award_name=text)
            self.dialog.award_search_combo.clear()
            self.dialog.award_search_combo.addItem(f"Create new: '{text}'", "new")

            if awards is None:
                pass
            elif not isinstance(awards, (list, tuple)):
                # Handle single award object
                self.dialog.award_search_combo.addItem(
                    awards.award_name, awards.award_id
                )
            else:
                for award in awards:
                    self.dialog.award_search_combo.addItem(
                        award.award_name, award.award_id
                    )

            self.dialog.award_search_combo.setVisible(
                self.dialog.award_search_combo.count() > 1
            )
        else:
            self.dialog.award_search_combo.setVisible(False)

        self.dialog.add_award_btn.setEnabled(len(text) >= 2)

    def _on_award_selected(self, index):
        """When award is selected from dropdown, update search field."""
        if index > 0:
            award_name = self.dialog.award_search_combo.currentText()
            self.dialog.award_search_edit.setText(award_name)

    def _add_award(self):
        """Add award association to all selected tracks."""
        award_name = self.dialog.award_search_edit.text().strip()
        category = self.dialog.award_category_edit.text().strip() or None
        year = self.dialog.award_year_edit.value() or None

        if not award_name:
            return

        # Handle award - create new or use existing
        if (
            self.dialog.award_search_combo.isVisible()
            and self.dialog.award_search_combo.currentData() == "new"
        ):
            award = self.controller.add.add_entity("Award", award_name=award_name)
        else:
            if (
                self.dialog.award_search_combo.isVisible()
                and self.dialog.award_search_combo.currentData() != "new"
            ):
                award_id = self.dialog.award_search_combo.currentData()
                award = self.controller.get.get_entity_object(
                    "Award", award_id=award_id
                )
            else:
                awards = self.controller.get.get_entity_object(
                    "Award", award_name=award_name
                )
                if awards:
                    if isinstance(awards, (list, tuple)):
                        award = awards[0]  # Take first match from list
                    else:
                        award = awards  # Single award object
                else:
                    award = self.controller.add.add_entity(
                        "Award", award_name=award_name
                    )

        if award:
            # Apply to ALL tracks
            success_count = 0
            for track in self.tracks:
                try:
                    self.controller.add.add_entity(
                        "AwardAssociation",
                        entity_id=track.track_id,
                        entity_type="Track",
                        award_id=award.award_id,
                        category=category,
                        year=year,
                    )
                    success_count += 1
                except Exception as e:
                    logger.error(f"Failed to add award to track {track.track_id}: {e}")

            if self.is_multi_track:
                QMessageBox.information(
                    self.dialog,
                    "Award Added",
                    f"Added award to {success_count} out of {len(self.tracks)} tracks",
                )

            # Refresh the table
            self.dialog._load_awards()
            self.dialog.award_search_edit.clear()
            self.dialog.award_category_edit.clear()
            self.dialog.award_year_edit.setValue(0)
            self.dialog.award_search_combo.setVisible(False)

    def _remove_award(self, row):
        """Remove award association from all selected tracks."""
        award_item = self.dialog.awards_table.item(row, 0)

        if award_item:
            award_id = award_item.data(Qt.UserRole)

            if award_id:
                # Remove from ALL tracks
                success_count = 0
                for track in self.tracks:
                    try:
                        self.controller.delete.delete_entity(
                            "AwardAssociation",
                            entity_id=track.track_id,
                            entity_type="Track",
                            award_id=award_id,
                        )
                        success_count += 1
                    except Exception as e:
                        logger.error(
                            f"Failed to remove award from track {track.track_id}: {e}"
                        )

                if self.is_multi_track:
                    QMessageBox.information(
                        self.dialog,
                        "Award Removed",
                        f"Removed award from {success_count} out of {len(self.tracks)} tracks",
                    )

                self.dialog.awards_table.removeRow(row)

    def _remove_award(self, row):
        """Remove award association from all selected tracks."""
        award_item = self.dialog.awards_table.item(row, 0)

        if award_item:
            award_id = award_item.data(Qt.UserRole)

            if award_id:
                # Remove from ALL tracks
                success_count = 0
                for track in self.tracks:
                    try:
                        self.controller.delete.delete_entity(
                            "AwardAssociation",
                            entity_id=track.track_id,
                            entity_type="Track",
                            award_id=award_id,
                        )

                        success_count += 1
                    except Exception as e:
                        logger.error(
                            f"Failed to remove award from track {track.track_id}: {e}"
                        )

                if self.is_multi_track:
                    QMessageBox.information(
                        self.dialog,
                        "Award Removed",
                        f"Removed award from {success_count} out of {len(self.tracks)} tracks",
                    )

                self.dialog.awards_table.removeRow(row)

    def _on_sample_search_changed(self, text):
        """Search for tracks and show results in dropdown."""
        text = text.strip()
        if len(text) >= 2:
            # Search for tracks by name (excluding current track)
            tracks = self.controller.get.get_entity_object("Track", track_name=text)
            self.dialog.sample_search_combo.clear()

            if tracks is None:
                pass
            elif not isinstance(tracks, (list, tuple)):
                # Handle single track object
                if tracks.track_id != self.tracks[0].track_id:
                    display_name = f"{tracks.track_name} (Album: {tracks.album_name if tracks.album_name else 'Unknown'})"
                    self.dialog.sample_search_combo.addItem(
                        display_name, tracks.track_id
                    )
            else:
                for track in tracks:
                    if (
                        track.track_id != self.tracks[0].track_id
                    ):  # Don't show current track
                        display_name = f"{track.track_name} (Album: {track.album_name if track.album_name else 'Unknown'})"
                        self.dialog.sample_search_combo.addItem(
                            display_name, track.track_id
                        )

            self.dialog.sample_search_combo.setVisible(
                self.dialog.sample_search_combo.count() > 0
            )
        else:
            self.dialog.sample_search_combo.setVisible(False)

        self.dialog.add_sample_btn.setEnabled(len(text) >= 2)

    def _on_sample_selected(self, index):
        """When sample track is selected from dropdown."""
        if index >= 0 and self.dialog.sample_search_combo.count() > 0:
            display_text = self.dialog.sample_search_combo.currentText()
            # Extract just the track name for the search field
            track_name = display_text.split(" (Album:")[0]
            self.dialog.sample_search_edit.setText(track_name)

    def _add_sample(self):
        """Add sample relationship to the current track."""
        search_text = self.dialog.sample_search_edit.text().strip()

        if not search_text:
            return

        # Get the selected track from combo box
        if (
            self.dialog.sample_search_combo.isVisible()
            and self.dialog.sample_search_combo.count() > 0
        ):
            sampled_id = self.dialog.sample_search_combo.currentData()
        else:
            # Search for the track
            tracks = self.controller.get.get_entity_object(
                "Track", track_name=search_text
            )
            if tracks:
                if isinstance(tracks, (list, tuple)):
                    # Take first match that's not the current track
                    for track in tracks:
                        if track.track_id != self.tracks[0].track_id:
                            sampled_id = track.track_id
                            break
                    else:
                        QMessageBox.warning(
                            self.dialog,
                            "Invalid Sample",
                            "Cannot sample the same track.",
                        )
                        return
                else:
                    if tracks.track_id == self.tracks[0].track_id:
                        QMessageBox.warning(
                            self.dialog,
                            "Invalid Sample",
                            "Cannot sample the same track.",
                        )
                        return
                    sampled_id = tracks.track_id
            else:
                QMessageBox.warning(
                    self.dialog,
                    "Track Not Found",
                    f"No track found with name: {search_text}",
                )
                return

        if sampled_id:
            # Apply to current track (or all tracks in multi-track mode)
            success_count = 0
            for track in self.tracks:
                try:
                    # Check if relationship already exists
                    existing = self.controller.get.get_entity_object(
                        "Samples", sampled_by_id=track.track_id, sampled_id=sampled_id
                    )

                    if existing:
                        QMessageBox.information(
                            self.dialog,
                            "Already Exists",
                            f"Sample relationship already exists for track: {track.track_name}",
                        )
                        continue

                    # Create the sample relationship
                    self.controller.add.add_entity_link(
                        "Samples", sampled_by_id=track.track_id, sampled_id=sampled_id
                    )
                    success_count += 1

                except Exception as e:
                    logger.error(f"Failed to add sample to track {track.track_id}: {e}")

            # Show success message
            if success_count > 0:
                if self.is_multi_track:
                    QMessageBox.information(
                        self.dialog,
                        "Sample Added",
                        f"Added sample relationship to {success_count} out of {len(self.tracks)} tracks",
                    )
                else:
                    QMessageBox.information(
                        self.dialog,
                        "Sample Added",
                        "Sample relationship added successfully.",
                    )

                # Refresh the lists
                self.dialog._load_samples()
                self.dialog.sample_search_edit.clear()
                self.dialog.sample_search_combo.setVisible(False)

    def _remove_sample(self, row):
        """Remove sample relationship from current track."""
        item = self.dialog.samples_used_list.item(row)
        if item:
            sampled_id = item.data(Qt.UserRole)
            if sampled_id:
                # Remove from ALL tracks (in multi-track mode)
                success_count = 0
                for track in self.tracks:
                    try:
                        self.controller.delete.delete_entity(
                            "Samples",
                            sampled_by_id=track.track_id,
                            sampled_id=sampled_id,
                        )
                        success_count += 1
                    except Exception as e:
                        logger.error(
                            f"Failed to remove sample from track {track.track_id}: {e}"
                        )

                if success_count > 0:
                    if self.is_multi_track:
                        QMessageBox.information(
                            self.dialog,
                            "Sample Removed",
                            f"Removed sample relationship from {success_count} out of {len(self.tracks)} tracks",
                        )
                    else:
                        QMessageBox.information(
                            self.dialog,
                            "Sample Removed",
                            "Sample relationship removed successfully.",
                        )

                    self.dialog.samples_used_list.takeItem(row)
