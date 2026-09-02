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
        self.tabs.addTab(self._create_skipped_roles_tab(), "Skipped Roles")
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
    # Skipped Genres / Skipped Roles -- parse-ignore lists. Genres/roles
    # on these lists are never attached to a track during import (see
    # docs/specs/split_and_merge_aliases.md and
    # docs/specs/role_parse_ignore_list.md). Both tabs are the same
    # list-editor over a pair of Config accessors, built by
    # _create_exclusion_tab(); the named genre-/role- methods stay as thin
    # wrappers so callers (and tests) have a stable per-noun surface.
    # ------------------------------------------------------------------

    def _create_exclusion_tab(
        self, *, blurb, placeholder, get_fn, set_fn, list_attr, edit_attr
    ) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel(blurb))

        list_widget = QListWidget()
        for name in get_fn():
            list_widget.addItem(name)
        layout.addWidget(list_widget)
        setattr(self, list_attr, list_widget)

        add_row = QHBoxLayout()
        edit = QLineEdit()
        edit.setPlaceholderText(placeholder)
        edit.returnPressed.connect(lambda: self._add_excluded(list_attr, edit_attr, set_fn))
        add_row.addWidget(edit, 1)
        setattr(self, edit_attr, edit)

        add_btn = QPushButton("+ Add")
        add_btn.clicked.connect(lambda: self._add_excluded(list_attr, edit_attr, set_fn))
        add_row.addWidget(add_btn)

        remove_btn = QPushButton("Remove Selected")
        remove_btn.clicked.connect(lambda: self._remove_excluded(list_attr, set_fn))
        add_row.addWidget(remove_btn)
        layout.addLayout(add_row)

        return page

    def _add_excluded(self, list_attr, edit_attr, set_fn):
        list_widget = getattr(self, list_attr)
        edit = getattr(self, edit_attr)
        name = edit.text().strip()
        if not name:
            return
        existing = {list_widget.item(i).text().lower() for i in range(list_widget.count())}
        if name.lower() in existing:
            edit.clear()
            return
        list_widget.addItem(name)
        edit.clear()
        self._save_exclusion(list_attr, set_fn)

    def _remove_excluded(self, list_attr, set_fn):
        list_widget = getattr(self, list_attr)
        for item in list_widget.selectedItems():
            list_widget.takeItem(list_widget.row(item))
        self._save_exclusion(list_attr, set_fn)

    def _save_exclusion(self, list_attr, set_fn):
        list_widget = getattr(self, list_attr)
        names = [list_widget.item(i).text() for i in range(list_widget.count())]
        set_fn(names)
        try:
            self.controller.config.save()
        except OSError:
            logger.exception("AliasManagementDialog: failed to save %s", list_attr)

    def _create_skipped_genres_tab(self) -> QWidget:
        return self._create_exclusion_tab(
            blurb=(
                "Genres in this list are never attached to a track during import, "
                "whether from file tags or MusicBrainz."
            ),
            placeholder="Genre name to skip…",
            get_fn=self.controller.config.get_excluded_genres,
            set_fn=self.controller.config.set_excluded_genres,
            list_attr="excluded_genres_list",
            edit_attr="excluded_genre_edit",
        )

    def _create_skipped_roles_tab(self) -> QWidget:
        return self._create_exclusion_tab(
            blurb=(
                "Credit roles in this list are never attached to a track during "
                "import, whether from file tags or MusicBrainz."
            ),
            placeholder="Role name to skip…",
            get_fn=self.controller.config.get_excluded_roles,
            set_fn=self.controller.config.set_excluded_roles,
            list_attr="excluded_roles_list",
            edit_attr="excluded_role_edit",
        )

    # Backwards-compatible thin wrappers (kept for existing callers/tests).
    def _add_excluded_genre(self):
        self._add_excluded(
            "excluded_genres_list",
            "excluded_genre_edit",
            self.controller.config.set_excluded_genres,
        )

    def _remove_excluded_genre(self):
        self._remove_excluded("excluded_genres_list", self.controller.config.set_excluded_genres)

    def _save_excluded_genres(self):
        self._save_exclusion("excluded_genres_list", self.controller.config.set_excluded_genres)
