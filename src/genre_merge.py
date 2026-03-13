from src.base_merge_dialog import MergeDBDialog
from src.logger_config import logger


class GenreMergeDialog(MergeDBDialog):
    """Specialized merge dialog for genres.

    Pre-populates the source side with the selected genre and automatically
    suggests similar-named genres as merge candidates on the target side.
    """

    def __init__(self, controller, parent=None, genre_obj=None):
        super().__init__(controller, "Genre", parent)

        if genre_obj is not None:
            self._prepopulate_source(genre_obj)

    def _prepopulate_source(self, genre_obj):
        """Fill the source side with the given genre and auto-suggest targets."""
        try:
            self.source_entity = genre_obj
            genre_name = getattr(genre_obj, self.name_attr, "")

            # Show the source entity details
            self.source_info.setText(self._build_entity_info(genre_obj, "source"))

            # Populate the source search field and list
            self.source_search.setText(genre_name)
            self._update_list(genre_name, "source")
            self._highlight_selected_entities()

            # Enable the "Find Similar" button on the target side
            self.target_find_similar_btn.setEnabled(True)

            # Auto-populate target list with similarity suggestions
            self._auto_suggest_similar(genre_obj, "target")

            # Refresh button states (e.g. enable Next if both sides are filled)
            self._update_action_buttons()

        except Exception as e:
            logger.error(f"Error pre-populating source genre: {str(e)}")

    def _get_related_count(self, genre_id):
        """Return the number of tracks associated with this genre."""
        try:
            track_genres = self.controller.get.get_entity_links(
                "TrackGenre", genre_id=genre_id
            )
            return len(track_genres)
        except Exception as e:
            logger.error(f"Error getting track count for genre {genre_id}: {str(e)}")
            return 0
