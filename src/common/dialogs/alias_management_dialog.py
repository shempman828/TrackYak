"""Unified "Manage Aliases…" dialog: skipped genres/roles plus every merge- and split-alias table."""

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QPushButton, QTabWidget, QVBoxLayout, QWidget

from src.common.alias.global_merge_alias_tab import GlobalMergeAliasTab
from src.common.alias.global_split_alias_tab import GlobalSplitAliasTab
from src.core.status_widget import show_status_message
from src.foundation.logger_config import logger

# (tab label, model name, name field, id field)
_ENTITY_TYPES = [
    ("Genre", "Genre", "genre_name", "genre_id"),
    ("Artist", "Artist", "artist_name", "artist_id"),
    ("Publisher", "Publisher", "publisher_name", "publisher_id"),
    ("Role", "Role", "role_name", "role_id"),
]


class AliasManagementDialog(QDialog):
    """Non-modal dialog: skipped genres/roles plus a merge- and split-alias tab per entity type."""

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
        # Alias tabs are built on first view: each one loads its table and a
        # completer preload, which made opening the dialog slow.
        self._lazy_tabs: dict[QWidget, tuple] = {}
        for label, model_name, name_field, id_field in _ENTITY_TYPES:
            for tab_class, suffix in ((GlobalMergeAliasTab, "Aliases"), (GlobalSplitAliasTab, "Split Aliases")):
                holder = QWidget()
                holder_layout = QVBoxLayout(holder)
                holder_layout.setContentsMargins(0, 0, 0, 0)
                self._lazy_tabs[holder] = (tab_class, model_name, name_field, id_field)
                self.tabs.addTab(holder, f"{label} {suffix}")
        self.tabs.currentChanged.connect(self._build_lazy_tab)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)

    def _build_lazy_tab(self, index: int) -> None:
        holder = self.tabs.widget(index)
        spec = self._lazy_tabs.pop(holder, None)
        if spec is None:
            return
        tab_class, model_name, name_field, id_field = spec
        holder.layout().addWidget(tab_class(self.controller, model_name, name_field, id_field))

    # -- Skipped Genres / Skipped Roles: import parse-ignore lists over Config accessors --

    def _create_exclusion_tab(self, *, blurb, placeholder, get_fn, set_fn, list_attr, edit_attr) -> QWidget:
        """One list-editor page over a Config get/set pair; widgets are stored on `list_attr`/`edit_attr`."""
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
            show_status_message(edit, f"'{name}' is already in the list.")
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
            show_status_message(list_widget, "Could not save the list to the config file.")

    def _create_skipped_genres_tab(self) -> QWidget:
        return self._create_exclusion_tab(
            blurb=("Genres in this list are never attached to a track during import, whether from file tags or MusicBrainz."),
            placeholder="Genre name to skip…",
            get_fn=self.controller.config.get_excluded_genres,
            set_fn=self.controller.config.set_excluded_genres,
            list_attr="excluded_genres_list",
            edit_attr="excluded_genre_edit",
        )

    def _create_skipped_roles_tab(self) -> QWidget:
        return self._create_exclusion_tab(
            blurb=("Credit roles in this list are never attached to a track during import, whether from file tags or MusicBrainz."),
            placeholder="Role name to skip…",
            get_fn=self.controller.config.get_excluded_roles,
            set_fn=self.controller.config.set_excluded_roles,
            list_attr="excluded_roles_list",
            edit_attr="excluded_role_edit",
        )

    # Per-noun genre wrappers, used by tests/genre/test_genre_view.py.
    def _add_excluded_genre(self):
        self._add_excluded("excluded_genres_list", "excluded_genre_edit", self.controller.config.set_excluded_genres)

    def _remove_excluded_genre(self):
        self._remove_excluded("excluded_genres_list", self.controller.config.set_excluded_genres)

    def _save_excluded_genres(self):
        self._save_exclusion("excluded_genres_list", self.controller.config.set_excluded_genres)
