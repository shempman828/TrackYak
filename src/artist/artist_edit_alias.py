# ══════════════════════════════════════════════════════════════════════════════
# Tab: Aliases  (embedded, no separate dialog window)
# ══════════════════════════════════════════════════════════════════════════════
from PySide6.QtWidgets import QDialog, QMessageBox
from sqlalchemy.exc import SQLAlchemyError

from src.artist.artist_alias_dialog import SUGGESTED_ALIAS_TYPES
from src.common.base_merge_dialog import MergeDBDialog
from src.common.entity_alias_tab import EntityAliasesTab
from src.core.logger_config import logger
from src.core.status_utility import show_status_message


class AliasesTab(EntityAliasesTab):
    """
    Alias management embedded directly in the artist-edit tab.

    Adds an alias "Type" column (autocompleting from SUGGESTED_ALIAS_TYPES
    plus any custom types already in use) and a "Use as Name" action that
    promotes an alias to the artist's primary name, on top of the generic
    EntityAliasesTab.
    """

    def __init__(self, controller, artist, parent=None):
        super().__init__(
            controller,
            artist,
            model_name="Artist",
            id_field="artist_id",
            placeholder="e.g. Marshall Mathers",
            extra_field="alias_type",
            extra_field_label="Type",
            extra_field_placeholder="e.g. Birth Name  (or type your own)",
            extra_field_hint=(
                "Choose a suggestion or type a custom type — "
                "leave blank if unspecified."
            ),
            on_swap=self._perform_swap,
            on_before_add=self._check_existing_artist,
            parent=parent,
        )

    @property
    def artist(self):
        return self.entity

    def load(self, artist):
        logger.debug(
            "AliasesTab.load: artist_id=%s name=%r",
            artist.artist_id,
            artist.artist_name,
        )
        super().load(artist)

    def _extra_completions(self) -> list[str]:
        return list(dict.fromkeys(SUGGESTED_ALIAS_TYPES + self._existing_extra_values()))

    def _check_existing_artist(self, alias_name: str) -> bool:
        """
        Before saving a new alias, check whether `alias_name` already
        belongs to a *different* artist (as their primary name or one of
        their own aliases). If so, offer to merge that artist into this
        one instead of creating a duplicate/aliased name split across two
        records.

        Returns True to let the caller proceed with the normal add-alias
        flow, or False if a merge was offered (accepted or declined) and
        no further action should be taken here.
        """
        match = self.controller.get.resolve_entity_or_alias(
            "Artist", "artist_name", alias_name
        )
        if not match or match.artist_id == self.artist.artist_id:
            return True

        reply = QMessageBox.question(
            self,
            "Artist Already Exists",
            f"An artist named <b>{match.artist_name}</b> already exists.<br>"
            f"Merge it into <b>{self.artist.artist_name}</b> instead of adding "
            "this as a separate alias?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if reply != QMessageBox.Yes:
            logger.debug(
                "AliasesTab._check_existing_artist: user declined merge of "
                "artist_id=%s into artist_id=%s",
                match.artist_id,
                self.artist.artist_id,
            )
            show_status_message(
                self,
                f"Alias not added — '{alias_name}' already belongs to another artist.",
            )
            return False

        dialog = MergeDBDialog(
            self.controller,
            "Artist",
            self,
            preload_source=match,
            preload_target=self.artist,
        )
        if dialog.exec() == QDialog.Accepted:
            logger.info(
                "AliasesTab._check_existing_artist: merged artist_id=%s into artist_id=%s",
                match.artist_id,
                self.artist.artist_id,
            )
            show_status_message(
                self, f"Merged '{match.artist_name}' into '{self.artist.artist_name}'."
            )
        return False

    def _perform_swap(self, alias_id: int, new_primary: str, alias_type: str) -> bool:
        old_primary = self.artist.artist_name
        logger.debug(
            "AliasesTab._perform_swap: alias_id=%s old=%r new=%r",
            alias_id,
            old_primary,
            new_primary,
        )

        if new_primary == old_primary:
            logger.debug(
                "AliasesTab._perform_swap: no-op, alias already matches primary name"
            )
            show_status_message(self, "That alias is already the primary name.")
            return False

        reply = QMessageBox.question(
            self,
            "Use as Primary Name",
            f"Set primary name to <b>{new_primary}</b>?<br>"
            f"Current name <b>{old_primary}</b> will be saved as an alias.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            logger.debug("AliasesTab._perform_swap: cancelled")
            return False

        try:
            # Attempt the rename first, before touching the alias row: if the
            # new name collides with another artist we want to fail loudly
            # and leave both the alias and the primary name untouched.
            success = self.controller.update.update_entity(
                "Artist", self.artist.artist_id, artist_name=new_primary
            )
            if not success:
                logger.warning(
                    "AliasesTab._perform_swap: rename rejected for artist_id=%s -> %r "
                    "(likely a duplicate artist name)",
                    self.artist.artist_id,
                    new_primary,
                )
                QMessageBox.warning(
                    self,
                    "Could Not Swap Name",
                    f"Could not set primary name to '{new_primary}'. "
                    "Another artist may already have that name.",
                )
                return False

            self.controller.delete.delete_entity("ArtistAlias", alias_id)
            self.artist.artist_name = new_primary
            save_type = alias_type or "Former Name"
            self.controller.add.add_entity(
                "ArtistAlias",
                artist_id=self.artist.artist_id,
                alias_name=old_primary,
                alias_type=save_type,
            )
            logger.info(
                "AliasesTab._perform_swap: swapped artist_id=%s primary %r -> %r (old saved as type=%r)",
                self.artist.artist_id,
                old_primary,
                new_primary,
                save_type,
            )
        except SQLAlchemyError as exc:
            logger.exception(
                "AliasesTab._perform_swap: failed during swap for artist_id=%s",
                self.artist.artist_id,
            )
            QMessageBox.critical(self, "Error", f"Could not swap name:\n{exc}")
            return False

        return True
