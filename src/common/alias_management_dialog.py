"""
alias_management_dialog.py

Unified "Manage Aliases…" dialog (see docs/specs/split_and_merge_aliases.md):
one QTabWidget bringing together every merge-alias and split-alias table
(Genre/Artist/Publisher/Role) plus the skipped-genres list, instead of
scattering them across each entity's own edit dialog and ConfigDialog.
Mirrors ConfigDialog's structure -- one QTabWidget, each tab built by a
_create_X_tab() factory method.
"""

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from src.common.global_merge_alias_tab import GlobalMergeAliasTab
from src.common.global_split_alias_tab import GlobalSplitAliasTab
from src.core.logger_config import logger

# (tab label, model name, name field, id field)
_ENTITY_TYPES = [
    ("Genre", "Genre", "genre_name", "genre_id"),
    ("Artist", "Artist", "artist_name", "artist_id"),
    ("Publisher", "Publisher", "publisher_name", "publisher_id"),
    ("Role", "Role", "role_name", "role_id"),
]


class AliasManagementDialog(QDialog):
    """Non-modal dialog: skipped genres + one merge-alias tab and one
    split-alias tab per entity type."""

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.setWindowTitle("Manage Aliases")
        self.setMinimumSize(720, 480)

        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        self.tabs.addTab(self._create_skipped_genres_tab(), "Skipped Genres")
        for label, model_name, name_field, id_field in _ENTITY_TYPES:
            self.tabs.addTab(
                GlobalMergeAliasTab(controller, model_name, name_field, id_field),
                f"{label} Aliases",
            )
            self.tabs.addTab(
                GlobalSplitAliasTab(controller, model_name, name_field, id_field),
                f"{label} Split Aliases",
            )

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.close)
        buttons.button(QDialogButtonBox.Close).clicked.connect(self.close)
        layout.addWidget(buttons)

    # ------------------------------------------------------------------
    # Skipped Genres -- moved from ConfigDialog's Library tab; same
    # underlying `excluded_genres` config value, relocated here as the
    # single source of UI truth (see docs/specs/split_and_merge_aliases.md).
    # ------------------------------------------------------------------

    def _create_skipped_genres_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(
            QLabel(
                "Genres in this list are never attached to a track during import, "
                "whether from file tags or MusicBrainz."
            )
        )

        self.excluded_genres_list = QListWidget()
        app_config = self.controller.config
        for genre_name in app_config.get_excluded_genres():
            self.excluded_genres_list.addItem(genre_name)
        layout.addWidget(self.excluded_genres_list)

        add_row = QHBoxLayout()
        self.excluded_genre_edit = QLineEdit()
        self.excluded_genre_edit.setPlaceholderText("Genre name to skip…")
        self.excluded_genre_edit.returnPressed.connect(self._add_excluded_genre)
        add_row.addWidget(self.excluded_genre_edit, 1)

        add_btn = QPushButton("+ Add")
        add_btn.clicked.connect(self._add_excluded_genre)
        add_row.addWidget(add_btn)

        remove_btn = QPushButton("Remove Selected")
        remove_btn.clicked.connect(self._remove_excluded_genre)
        add_row.addWidget(remove_btn)
        layout.addLayout(add_row)

        return page

    def _add_excluded_genre(self):
        name = self.excluded_genre_edit.text().strip()
        if not name:
            return
        existing = {
            self.excluded_genres_list.item(i).text().lower()
            for i in range(self.excluded_genres_list.count())
        }
        if name.lower() in existing:
            self.excluded_genre_edit.clear()
            return
        self.excluded_genres_list.addItem(name)
        self.excluded_genre_edit.clear()
        self._save_excluded_genres()

    def _remove_excluded_genre(self):
        for item in self.excluded_genres_list.selectedItems():
            self.excluded_genres_list.takeItem(self.excluded_genres_list.row(item))
        self._save_excluded_genres()

    def _save_excluded_genres(self):
        names = [
            self.excluded_genres_list.item(i).text()
            for i in range(self.excluded_genres_list.count())
        ]
        self.controller.config.set_excluded_genres(names)
        try:
            self.controller.config.save()
        except OSError:
            logger.exception("AliasManagementDialog: failed to save excluded genres")
