from sqlalchemy.exc import SQLAlchemyError

from src.common.base_merge_dialog import MergeDBDialog
from src.core.logger_config import logger


class RoleMergeDialog(MergeDBDialog):
    """Specialized merge dialog for roles.

    Pre-populates the source side with the selected role and automatically
    suggests similar-named roles as merge candidates on the target side
    (e.g. combining "Guitar" and "Guitarist").
    """

    def __init__(self, controller, parent=None, role_obj=None):
        super().__init__(controller, "Role", parent)

        if role_obj is not None:
            self._prepopulate_source(role_obj)

    def _prepopulate_source(self, role_obj):
        """Fill the source side with the given role and auto-suggest targets."""
        try:
            self.source_entity = role_obj
            role_name = getattr(role_obj, self.name_attr, "")

            self.source_info.setText(self._build_entity_info(role_obj, "source"))

            self.source_search.setText(role_name)
            self._update_list(role_name, "source")
            self._highlight_selected_entities()

            self.target_find_similar_btn.setEnabled(True)
            self._auto_suggest_similar(role_obj, "target")

            self._update_action_buttons()

        except RuntimeError as e:
            logger.error(f"Error pre-populating source role: {str(e)}")

    def _get_related_count(self, role_id):
        """Return the number of album and track assignments for this role."""
        try:
            album_links = self.controller.get.get_entity_links(
                "AlbumRoleAssociation", role_id=role_id
            )
            track_links = self.controller.get.get_entity_links(
                "TrackArtistRole", role_id=role_id
            )
            return len(album_links or []) + len(track_links or [])
        except SQLAlchemyError as e:
            logger.error(
                f"Error getting assignment count for role {role_id}: {str(e)}"
            )
            return 0
