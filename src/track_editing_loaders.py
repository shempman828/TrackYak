# track_editing_loaders.py (updated)
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QListWidgetItem, QPushButton, QTableWidgetItem

from src.logger_config import logger
from src.wikipedia_seach import search_wikipedia


class DataLoaders:
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

    def _load_artist_roles(self):
        """Load current artist roles into the table."""
        self.dialog.artist_roles_table.setRowCount(0)

        # For multi-track, show common artist roles or handle differently
        if self.is_multi_track:
            # Option 1: Show common roles across all tracks
            common_roles = self._get_common_artist_roles()
            for role_info in common_roles:
                row = self.dialog.artist_roles_table.rowCount()
                self.dialog.artist_roles_table.insertRow(row)

                self.dialog.artist_roles_table.setItem(
                    row, 0, QTableWidgetItem(role_info["artist_name"])
                )
                self.dialog.artist_roles_table.setItem(
                    row, 1, QTableWidgetItem(role_info["role_name"])
                )

                # Store IDs for removal
                self.dialog.artist_roles_table.item(row, 0).setData(
                    Qt.UserRole, role_info["artist_id"]
                )
                self.dialog.artist_roles_table.item(row, 1).setData(
                    Qt.UserRole, role_info["role_id"]
                )

                btn_remove = QPushButton("Remove")
                btn_remove.clicked.connect(
                    lambda checked, r=row: self.dialog._remove_artist_role(r)
                )
                self.dialog.artist_roles_table.setCellWidget(row, 2, btn_remove)
        else:
            # Single track case - original logic
            for role in self.tracks[0].artist_roles:
                row = self.dialog.artist_roles_table.rowCount()
                self.dialog.artist_roles_table.insertRow(row)

                artist_name = role.artist.artist_name if role.artist else "Unknown"
                role_name = role.role.role_name if role.role else "Unknown"

                self.dialog.artist_roles_table.setItem(
                    row, 0, QTableWidgetItem(artist_name)
                )
                self.dialog.artist_roles_table.setItem(
                    row, 1, QTableWidgetItem(role_name)
                )

                # Store IDs for removal
                if role.artist:
                    self.dialog.artist_roles_table.item(row, 0).setData(
                        Qt.UserRole, role.artist.artist_id
                    )
                if role.role:
                    self.dialog.artist_roles_table.item(row, 1).setData(
                        Qt.UserRole, role.role.role_id
                    )

                btn_remove = QPushButton("Remove")
                btn_remove.clicked.connect(
                    lambda checked, r=row: self.dialog._remove_artist_role(r)
                )
                self.dialog.artist_roles_table.setCellWidget(row, 2, btn_remove)

    def _get_common_artist_roles(self):
        """Get artist roles that are common across all selected tracks."""
        if not self.tracks:
            return []

        # Get all artist roles from all tracks
        all_roles = []
        for track in self.tracks:
            track_roles = set()
            for role in track.artist_roles:
                if role.artist and role.role:
                    key = (role.artist.artist_id, role.role.role_id)
                    track_roles.add((key, role.artist.artist_name, role.role.role_name))
            all_roles.append(track_roles)

        # Find intersection (common roles across all tracks)
        if not all_roles:
            return []

        common_roles = all_roles[0]
        for track_roles in all_roles[1:]:
            common_roles = common_roles.intersection(track_roles)

        # Convert to list of dictionaries
        result = []
        for role_key, artist_name, role_name in common_roles:
            artist_id, role_id = role_key
            result.append(
                {
                    "artist_id": artist_id,
                    "role_id": role_id,
                    "artist_name": artist_name,
                    "role_name": role_name,
                }
            )

        return result

    def _load_genres(self):
        """Load current genres into the list."""
        if not hasattr(self.dialog, "genres_list"):
            return

        self.dialog.genres_list.clear()

        if self.is_multi_track:
            # Show common genres across all tracks
            common_genres = self._get_common_genres()
            for genre_info in common_genres:
                item = QListWidgetItem(genre_info["genre_name"])
                item.setData(Qt.UserRole, genre_info["genre_id"])
                self.dialog.genres_list.addItem(item)
        else:
            # Single track case
            for genre in self.tracks[0].genres:
                item = QListWidgetItem(genre.genre_name)
                item.setData(Qt.UserRole, genre.genre_id)
                self.dialog.genres_list.addItem(item)

    def _get_common_genres(self):
        """Get genres that are common across all selected tracks."""
        if not self.tracks:
            return []

        # Get all genres from all tracks
        all_genres = []
        for track in self.tracks:
            track_genres = set()
            for genre in track.genres:
                track_genres.add((genre.genre_id, genre.genre_name))
            all_genres.append(track_genres)

        # Find intersection
        if not all_genres:
            return []

        common_genres = all_genres[0]
        for track_genres in all_genres[1:]:
            common_genres = common_genres.intersection(track_genres)

        # Convert to list of dictionaries
        result = []
        for genre_id, genre_name in common_genres:
            result.append({"genre_id": genre_id, "genre_name": genre_name})

        return result

    def _load_place_associations(self):
        """Load current place associations into the table."""
        if not hasattr(self.dialog, "place_associations_table"):
            return

        self.dialog.place_associations_table.setRowCount(0)

        if self.is_multi_track:
            # Show common place associations
            common_places = self._get_common_place_associations()
            for place_info in common_places:
                row = self.dialog.place_associations_table.rowCount()
                self.dialog.place_associations_table.insertRow(row)

                self.dialog.place_associations_table.setItem(
                    row, 0, QTableWidgetItem(place_info["place_name"])
                )
                self.dialog.place_associations_table.setItem(
                    row, 1, QTableWidgetItem(place_info["association_type"] or "")
                )

                # Store IDs for removal
                self.dialog.place_associations_table.item(row, 0).setData(
                    Qt.UserRole, place_info["place_id"]
                )

                btn_remove = QPushButton("Remove")
                btn_remove.clicked.connect(
                    lambda checked, r=row: self.dialog._remove_place_association(r)
                )
                self.dialog.place_associations_table.setCellWidget(row, 2, btn_remove)
        else:
            # Single track case
            place_associations = self.controller.get.get_entity_links(
                "PlaceAssociation",
                entity_id=self.tracks[0].track_id,
                entity_type="Track",
            )

            for pa in place_associations:
                place = self.controller.get.get_entity_object(
                    "Place", place_id=pa.place_id
                )
                row = self.dialog.place_associations_table.rowCount()
                self.dialog.place_associations_table.insertRow(row)

                place_name = place.place_name if place else "Unknown"
                self.dialog.place_associations_table.setItem(
                    row, 0, QTableWidgetItem(place_name)
                )
                self.dialog.place_associations_table.setItem(
                    row, 1, QTableWidgetItem(pa.association_type or "")
                )

                # Store IDs for removal
                if place:
                    self.dialog.place_associations_table.item(row, 0).setData(
                        Qt.UserRole, place.place_id
                    )

                btn_remove = QPushButton("Remove")
                btn_remove.clicked.connect(
                    lambda checked, r=row: self.dialog._remove_place_association(r)
                )
                self.dialog.place_associations_table.setCellWidget(row, 2, btn_remove)

    def _get_common_place_associations(self):
        """Get place associations that are common across all selected tracks."""
        if not self.tracks:
            return []

        all_places = []
        for track in self.tracks:
            track_places = set()
            place_associations = self.controller.get.get_entity_links(
                "PlaceAssociation",
                entity_id=track.track_id,
                entity_type="Track",
            )
            for pa in place_associations:
                place = self.controller.get.get_entity_object(
                    "Place", place_id=pa.place_id
                )
                if place:
                    key = (place.place_id, pa.association_type or "")
                    track_places.add((key, place.place_name, pa.association_type or ""))
            all_places.append(track_places)

        if not all_places:
            return []

        common_places = all_places[0]
        for track_places in all_places[1:]:
            common_places = common_places.intersection(track_places)

        result = []
        for place_key, place_name, association_type in common_places:
            place_id, assoc_type = place_key
            result.append(
                {
                    "place_id": place_id,
                    "place_name": place_name,
                    "association_type": association_type,
                }
            )

        return result

    def _load_moods(self):
        """Load current mood associations into the list."""
        if not hasattr(self.dialog, "moods_list"):
            return

        self.dialog.moods_list.clear()

        if self.is_multi_track:
            # Show common moods
            common_moods = self._get_common_moods()
            for mood_info in common_moods:
                item = QListWidgetItem(mood_info["mood_name"])
                item.setData(Qt.UserRole, mood_info["mood_id"])
                self.dialog.moods_list.addItem(item)
        else:
            # Single track case
            mood_associations = self.controller.get.get_entity_links(
                "MoodTrackAssociation", track_id=self.tracks[0].track_id
            )

            for ma in mood_associations:
                mood = self.controller.get.get_entity_object("Mood", mood_id=ma.mood_id)
                if mood:
                    item = QListWidgetItem(mood.mood_name)
                    item.setData(Qt.UserRole, mood.mood_id)
                    self.dialog.moods_list.addItem(item)

    def _get_common_moods(self):
        """Get moods that are common across all selected tracks."""
        if not self.tracks:
            return []

        all_moods = []
        for track in self.tracks:
            track_moods = set()
            mood_associations = self.controller.get.get_entity_links(
                "MoodTrackAssociation", track_id=track.track_id
            )
            for ma in mood_associations:
                mood = self.controller.get.get_entity_object("Mood", mood_id=ma.mood_id)
                if mood:
                    track_moods.add((mood.mood_id, mood.mood_name))
            all_moods.append(track_moods)

        if not all_moods:
            return []

        common_moods = all_moods[0]
        for track_moods in all_moods[1:]:
            common_moods = common_moods.intersection(track_moods)

        result = []
        for mood_id, mood_name in common_moods:
            result.append({"mood_id": mood_id, "mood_name": mood_name})

        return result

    def _load_awards(self):
        """Load current award associations into the table."""
        if not hasattr(self.dialog, "awards_table"):
            return

        self.dialog.awards_table.setRowCount(0)

        if self.is_multi_track:
            # Show common awards
            common_awards = self._get_common_awards()
            for award_info in common_awards:
                row = self.dialog.awards_table.rowCount()
                self.dialog.awards_table.insertRow(row)

                self.dialog.awards_table.setItem(
                    row, 0, QTableWidgetItem(award_info["award_name"])
                )
                self.dialog.awards_table.setItem(
                    row, 1, QTableWidgetItem(award_info["category"] or "")
                )
                self.dialog.awards_table.setItem(
                    row,
                    2,
                    QTableWidgetItem(
                        str(award_info["year"]) if award_info["year"] else ""
                    ),
                )

                # Store IDs for removal
                self.dialog.awards_table.item(row, 0).setData(
                    Qt.UserRole, award_info["award_id"]
                )

                btn_remove = QPushButton("Remove")
                btn_remove.clicked.connect(
                    lambda checked, r=row: self.dialog._remove_award(r)
                )
                self.dialog.awards_table.setCellWidget(row, 3, btn_remove)
        else:
            # Single track case
            award_associations = self.controller.get.get_entity_links(
                "AwardAssociation",
                entity_id=self.tracks[0].track_id,
                entity_type="Track",
            )

            for aa in award_associations:
                award = self.controller.get.get_entity_object(
                    "Award", award_id=aa.award_id
                )
                row = self.dialog.awards_table.rowCount()
                self.dialog.awards_table.insertRow(row)

                award_name = award.award_name if award else "Unknown"
                self.dialog.awards_table.setItem(row, 0, QTableWidgetItem(award_name))
                self.dialog.awards_table.setItem(
                    row, 1, QTableWidgetItem(aa.category or "")
                )
                self.dialog.awards_table.setItem(
                    row, 2, QTableWidgetItem(str(aa.year) if aa.year else "")
                )

                # Store IDs for removal
                if award:
                    self.dialog.awards_table.item(row, 0).setData(
                        Qt.UserRole, award.award_id
                    )

                btn_remove = QPushButton("Remove")
                btn_remove.clicked.connect(
                    lambda checked, r=row: self.dialog._remove_award(r)
                )
                self.dialog.awards_table.setCellWidget(row, 3, btn_remove)

    def _get_common_awards(self):
        """Get awards that are common across all selected tracks."""
        if not self.tracks:
            return []

        all_awards = []
        for track in self.tracks:
            track_awards = set()
            award_associations = self.controller.get.get_entity_links(
                "AwardAssociation",
                entity_id=track.track_id,
                entity_type="Track",
            )
            for aa in award_associations:
                award = self.controller.get.get_entity_object(
                    "Award", award_id=aa.award_id
                )
                if award:
                    key = (award.award_id, aa.category or "", aa.year or 0)
                    track_awards.add(
                        (key, award.award_name, aa.category or "", aa.year or 0)
                    )
            all_awards.append(track_awards)

        if not all_awards:
            return []

        common_awards = all_awards[0]
        for track_awards in all_awards[1:]:
            common_awards = common_awards.intersection(track_awards)

        result = []
        for award_key, award_name, category, year in common_awards:
            award_id, cat, yr = award_key
            result.append(
                {
                    "award_id": award_id,
                    "award_name": award_name,
                    "category": category,
                    "year": year if year != 0 else None,
                }
            )

        return result

    def _search_wikipedia(self):
        """Open Wikipedia search dialog and handle the selected result."""
        try:
            # For multi-track, use the first track's name or handle differently
            track_name = self.tracks[0].track_name if self.tracks else ""
            title, summary, link = search_wikipedia(track_name, self.dialog)

            if link:
                self.dialog.field_widgets["track_wikipedia_link"].setText(link)
                self.dialog._on_field_modified("track_wikipedia_link")

                if (
                    summary
                    and not self.dialog.field_widgets["track_description"]
                    .toPlainText()
                    .strip()
                ):
                    description_edit = self.dialog.field_widgets["track_description"]
                    description_edit.setPlainText(
                        summary[:500] + "..." if len(summary) > 500 else summary
                    )
                    self.dialog._on_field_modified("track_description")

        except Exception as e:
            logger.error(f"Error in Wikipedia search: {e}")

    def _search_lyrics(self, track=None):
        """Search for lyrics online."""
        try:
            from lyrics_search import search_lyrics_for_track

            # Use provided track or first track in multi-track scenario
            if track is None and self.tracks:
                track = self.tracks[0]

            if track:
                lyrics_obj = search_lyrics_for_track(track)
                if lyrics_obj:
                    formatted = self.format_lyrics(lyrics_obj)
                    self.dialog.lyrics_edit.setPlainText(formatted)
                    self.dialog._on_field_modified("lyrics")
                    logger.debug(f"Populated lyrics for {track.track_name}")
                else:
                    logger.info(f"No lyrics found for {track.track_name}")
        except Exception as e:
            logger.error(f"Error searching lyrics: {e}")

    def format_lyrics(self, lyrics_obj):
        """
        Convert lyrics dictionary into a nicely formatted string.
        """
        if hasattr(lyrics_obj, "lyrics"):
            lyrics_dict = lyrics_obj.lyrics
        else:
            lyrics_dict = lyrics_obj

        lines = []
        for ts in sorted(lyrics_dict.keys()):
            line = lyrics_dict[ts]
            if line.strip() == "♪":
                lines.append("")
            else:
                lines.append(f"[{ts}] {line}")
        return "\n".join(lines)

    def _load_samples(self):
        """Load current sample relationships."""
        if not hasattr(self.dialog, "samples_used_list"):
            return

        # Clear both lists
        self.dialog.samples_used_list.clear()
        self.dialog.sampled_by_list.clear()

        if self.is_multi_track:
            # For multi-track, we need special handling
            self._load_samples_multi_track()
        else:
            # Single track case
            track = self.tracks[0]

            # Load tracks that this track samples
            for sample in track.samples_used:
                sampled_track = sample.sampled
                if sampled_track:
                    display_text = f"{sampled_track.track_name}"
                    if sampled_track.album_name:
                        display_text += f" (Album: {sampled_track.album_name})"

                    item = QListWidgetItem(display_text)
                    item.setData(Qt.UserRole, sampled_track.track_id)
                    item.setToolTip("Double-click to open track")
                    self.dialog.samples_used_list.addItem(item)

            # Load tracks that sample this track
            for sample in track.sampled_by_tracks:
                sampling_track = sample.sampled_by
                if sampling_track:
                    display_text = f"{sampling_track.track_name}"
                    if sampling_track.album_name:
                        display_text += f" (Album: {sampling_track.album_name})"

                    item = QListWidgetItem(display_text)
                    item.setData(Qt.UserRole, sampling_track.track_id)
                    item.setToolTip("Double-click to open track")
                    self.dialog.sampled_by_list.addItem(item)

    def _load_samples_multi_track(self):
        """Load sample relationships for multi-track editing."""
        # For samples used, we can show common samples across all tracks
        common_samples_used = self._get_common_samples_used()
        for sample_info in common_samples_used:
            display_text = f"{sample_info['track_name']}"
            if sample_info.get("album_name"):
                display_text += f" (Album: {sample_info['album_name']})"

            item = QListWidgetItem(display_text)
            item.setData(Qt.UserRole, sample_info["track_id"])
            item.setToolTip("Double-click to open track")
            self.dialog.samples_used_list.addItem(item)

        # For sampled by, we need to find tracks that sample ALL selected tracks
        common_sampled_by = self._get_common_sampled_by()
        for sample_info in common_sampled_by:
            display_text = f"{sample_info['track_name']}"
            if sample_info.get("album_name"):
                display_text += f" (Album: {sample_info['album_name']})"

            item = QListWidgetItem(display_text)
            item.setData(Qt.UserRole, sample_info["track_id"])
            item.setToolTip("Double-click to open track")
            self.dialog.sampled_by_list.addItem(item)

    def _get_common_samples_used(self):
        """Get samples that are common across all selected tracks."""
        if not self.tracks:
            return []

        # Get all samples used from all tracks
        all_samples = []
        for track in self.tracks:
            track_samples = set()
            for sample in track.samples_used:
                sampled_track = sample.sampled
                if sampled_track:
                    track_samples.add(
                        (
                            sampled_track.track_id,
                            sampled_track.track_name,
                            sampled_track.album_name
                            if hasattr(sampled_track, "album_name")
                            else None,
                        )
                    )
            all_samples.append(track_samples)

        # Find intersection
        if not all_samples:
            return []

        common_samples = all_samples[0]
        for track_samples in all_samples[1:]:
            common_samples = common_samples.intersection(track_samples)

        # Convert to list of dictionaries
        result = []
        for track_id, track_name, album_name in common_samples:
            result.append(
                {
                    "track_id": track_id,
                    "track_name": track_name,
                    "album_name": album_name,
                }
            )

        return result

    def _get_common_sampled_by(self):
        """Get tracks that sample ALL selected tracks."""
        if not self.tracks:
            return []

        # Get all tracks that sample each track
        all_sampling_tracks = []
        for track in self.tracks:
            sampling_tracks = set()
            for sample in track.sampled_by_tracks:
                sampling_track = sample.sampled_by
                if sampling_track:
                    sampling_tracks.add(
                        (
                            sampling_track.track_id,
                            sampling_track.track_name,
                            sampling_track.album_name
                            if hasattr(sampling_track, "album_name")
                            else None,
                        )
                    )
            all_sampling_tracks.append(sampling_tracks)

        # Find intersection - tracks that sample ALL selected tracks
        if not all_sampling_tracks:
            return []

        common_sampling_tracks = all_sampling_tracks[0]
        for sampling_tracks in all_sampling_tracks[1:]:
            common_sampling_tracks = common_sampling_tracks.intersection(
                sampling_tracks
            )

        # Convert to list of dictionaries
        result = []
        for track_id, track_name, album_name in common_sampling_tracks:
            result.append(
                {
                    "track_id": track_id,
                    "track_name": track_name,
                    "album_name": album_name,
                }
            )

        return result
