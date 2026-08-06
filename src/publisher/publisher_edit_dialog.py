import os

from PySide6.QtCore import QSettings, QSize, Qt
from PySide6.QtGui import QIntValidator, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.exc import SQLAlchemyError

from src.common.entity_alias_tab import EntityAliasesTab
from src.common.entity_completer_edit import EntityCompleterEdit, find_or_create_by_name
from src.core.asset_paths import icon
from src.core.logger_config import logger
from src.place.place_association_types import (
    fetch_association_types,
    find_or_create_association_type,
)
from src.publisher.publisher_hierarchy import get_descendant_publisher_ids
from src.publisher.publisher_image_manager import move_to_publisher_logos_dir

_SETTINGS_LAST_LOGO_DIR = "publisher_editor/last_logo_dir"
_HEADQUARTERS_TYPE_NAME = "Headquarters"
LOGO_MAX_SIZE = QSize(100, 100)


class OptionalIntEdit(QLineEdit):
    """A QLineEdit that only accepts integers and returns None when empty."""

    def __init__(self, placeholder="", parent=None):
        super().__init__(parent)
        self.setPlaceholderText(placeholder)
        self.setFixedWidth(60)
        self.setAlignment(Qt.AlignCenter)
        self.setValidator(QIntValidator(0, 9999, self))

    def get_value_or_none(self):
        text = self.text().strip()
        return int(text) if text else None

    def set_from_db(self, val):
        self.setText(str(int(val)) if val is not None else "")


class PublisherEditDialog(QDialog):
    """Simple name/description dialog for creating a new publisher."""

    def __init__(self, controller, publisher=None, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.publisher = publisher
        # Populated after a successful save so callers (e.g. the "New Parent"/
        # "New Child" context menu actions) can link the resulting publisher
        # without re-querying the database.
        self.result_publisher = None
        self.tab_aliases = None
        self._logo_path = None
        self._hq_association_id = None
        # (artist_id, artist_name) pairs -- staged in memory and synced to
        # PublisherFounder rows on save (see _save_founders), the same
        # after-save-sync approach _save_headquarters uses so this also
        # works when creating a brand-new publisher.
        self._founder_ids = []
        self._settings = QSettings()
        self.setup_ui()
        self.load_data()

    def setup_ui(self):
        self.setWindowTitle("Edit Publisher" if self.publisher else "New Publisher")
        if self.publisher:
            self.setMinimumSize(480, 560)
            self.resize(520, 640)
        else:
            self.setMinimumWidth(420)

        root = QVBoxLayout(self)

        # All the group boxes below are stacked in a single column that can
        # get taller than the dialog (e.g. an existing publisher with many
        # founders/aliases). Wrapped in a scroll area so that content scrolls
        # instead of every group being compressed toward its minimum size --
        # same pattern used for artist_edit_basic.py.
        content = QVBoxLayout()

        # -- Basic information -------------------------------------------------
        basic_group = QGroupBox("Basic Information")
        basic_form = QFormLayout(basic_group)
        self.name_input = QLineEdit()
        basic_form.addRow("Publisher Name:", self.name_input)

        self.desc_input = QLineEdit()
        basic_form.addRow("Description:", self.desc_input)

        # Parent publisher picker. Excludes this publisher and its own
        # descendants from the index so a completion can never introduce a
        # cycle in the hierarchy.
        self.parent_edit = EntityCompleterEdit("Search parent publisher…")
        excluded_ids = set()
        if self.publisher:
            excluded_ids = set(
                get_descendant_publisher_ids(
                    self.controller, self.publisher.publisher_id
                )
            )
        publishers = self.controller.get.get_all_entities("Publisher")
        # Kept unfiltered (unlike parent_index below) so find-or-create in
        # validate() can still recognize an excluded descendant by name --
        # the cycle check there is what rejects it, not a missing lookup.
        self._known_publishers = publishers
        parent_index = {
            p.publisher_name: p.publisher_id
            for p in publishers
            if p.publisher_name and p.publisher_id not in excluded_ids
        }
        self.parent_edit.set_index(parent_index)
        self.parent_edit.returnPressed.connect(self.validate)
        basic_form.addRow("Parent Publisher:", self.parent_edit)

        # Headquarters place picker -- same find-or-create completer pattern
        # as the parent publisher field above, backed by a Place row linked
        # through the generic PlaceAssociation table (type "Headquarters"),
        # the same mechanism used for e.g. artist birthplaces.
        self.hq_edit = EntityCompleterEdit("Search place…")
        places = self.controller.get.get_all_entities("Place") or []
        self._known_places = places
        self.hq_edit.set_index(
            {p.place_name: p.place_id for p in places if p.place_name}
        )
        self.hq_edit.returnPressed.connect(self.validate)
        basic_form.addRow("Headquarters:", self.hq_edit)
        content.addWidget(basic_group)

        # -- Founders -----------------------------------------------------------
        # Artists credited with founding this record company. Staged in
        # self._founder_ids and synced to PublisherFounder rows on save (see
        # _save_founders) so it works for both new and existing publishers.
        founders_group = QGroupBox("Founded By")
        founders_layout = QVBoxLayout(founders_group)

        self.founders_list = QListWidget()
        self.founders_list.setMaximumHeight(90)
        founders_layout.addWidget(self.founders_list)

        founder_add_row = QHBoxLayout()
        self.founder_edit = EntityCompleterEdit("Search artist…")
        artists = self.controller.get.get_all_entities("Artist") or []
        self._known_artists = artists
        self.founder_edit.set_index(
            {a.artist_name: a.artist_id for a in artists if a.artist_name}
        )
        self.founder_edit.returnPressed.connect(self._add_founder)
        founder_add_btn = QPushButton("Add")
        founder_add_btn.clicked.connect(self._add_founder)
        founder_remove_btn = QPushButton("Remove Selected")
        founder_remove_btn.clicked.connect(self._remove_selected_founder)
        founder_add_row.addWidget(self.founder_edit, 1)
        founder_add_row.addWidget(founder_add_btn)
        founder_add_row.addWidget(founder_remove_btn)
        founders_layout.addLayout(founder_add_row)
        content.addWidget(founders_group)

        # -- Details: dates, status, links -------------------------------------
        details_group = QGroupBox("Details")
        details_grid = QGridLayout(details_group)

        self.begin_year_edit = OptionalIntEdit("YYYY")
        self.end_year_edit = OptionalIntEdit("YYYY")
        details_grid.addWidget(QLabel("Begin Year:"), 0, 0)
        details_grid.addWidget(self.begin_year_edit, 0, 1)
        details_grid.addWidget(QLabel("End Year:"), 0, 2)
        details_grid.addWidget(self.end_year_edit, 0, 3)

        self.is_active_check = QCheckBox("Active")
        self.first_pass_check = QCheckBox("First pass complete")
        details_grid.addWidget(self.is_active_check, 1, 0, 1, 2)
        details_grid.addWidget(self.first_pass_check, 1, 2, 1, 2)

        self.second_pass_check = QCheckBox("Second pass complete")
        details_grid.addWidget(self.second_pass_check, 2, 0, 1, 2)

        self.wiki_input = QLineEdit()
        self.wiki_input.setPlaceholderText("https://en.wikipedia.org/...")
        details_grid.addWidget(QLabel("Wikipedia:"), 3, 0)
        details_grid.addWidget(self.wiki_input, 3, 1, 1, 3)

        self.mbid_input = QLineEdit()
        self.mbid_input.setPlaceholderText("MusicBrainz label ID")
        details_grid.addWidget(QLabel("MBID:"), 4, 0)
        details_grid.addWidget(self.mbid_input, 4, 1, 1, 3)
        details_grid.setColumnStretch(1, 1)
        details_grid.setColumnStretch(3, 1)
        content.addWidget(details_group)

        # Logo picker only makes sense once the publisher exists -- like
        # the artist profile picture, the picked file is moved into the
        # managed images dir immediately using publisher_id in its
        # filename, so this section is edit-only.
        if self.publisher:
            logo_group = QGroupBox("Logo")
            logo_row = QHBoxLayout(logo_group)

            self.logo_label = QLabel()
            self.logo_label.setFixedSize(LOGO_MAX_SIZE)
            self.logo_label.setAlignment(Qt.AlignCenter)
            self.logo_label.setStyleSheet("border: 1px solid palette(mid);")
            logo_row.addWidget(self.logo_label)

            logo_buttons = QVBoxLayout()
            self.logo_browse_button = QPushButton("Browse...")
            self.logo_browse_button.clicked.connect(self._browse_logo)
            self.logo_clear_button = QPushButton("Clear")
            self.logo_clear_button.clicked.connect(self._clear_logo)
            logo_buttons.addWidget(self.logo_browse_button)
            logo_buttons.addWidget(self.logo_clear_button)
            logo_buttons.addStretch()
            logo_row.addLayout(logo_buttons)
            logo_row.addStretch()
            content.addWidget(logo_group)

        # Aliases only make sense once the publisher exists (they need a
        # publisher_id to point at), so this section is edit-only.
        if self.publisher:
            aliases_group = QGroupBox("Aliases")
            aliases_layout = QVBoxLayout(aliases_group)
            self.tab_aliases = EntityAliasesTab(
                self.controller,
                self.publisher,
                "Publisher",
                "publisher_id",
                placeholder="e.g. EMI Records",
                list_style=True,
            )
            aliases_layout.addWidget(self.tab_aliases)
            aliases_group.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            content.addWidget(aliases_group, 1)

        content.addStretch()

        content_container = QWidget()
        content_container.setLayout(content)

        content_scroll = QScrollArea()
        content_scroll.setWidgetResizable(True)
        content_scroll.setFrameShape(QFrame.NoFrame)
        content_scroll.setWidget(content_container)
        root.addWidget(content_scroll, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.validate)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def load_data(self):
        if self.publisher:
            self.name_input.setText(self.publisher.publisher_name or "")
            self.desc_input.setText(self.publisher.description or "")
            if self.publisher.parent_id:
                parent = self.controller.get.get_entity_object(
                    "Publisher", publisher_id=self.publisher.parent_id
                )
                self.parent_edit.setText(parent.publisher_name if parent else "")
            hq_assocs = self.controller.get.get_all_entities(
                "PlaceAssociation",
                entity_type="Publisher",
                entity_id=self.publisher.publisher_id,
            ) or []
            hq_assoc = next(
                (
                    a
                    for a in hq_assocs
                    if a.association_type
                    and a.association_type.type_name == _HEADQUARTERS_TYPE_NAME
                ),
                None,
            )
            if hq_assoc and hq_assoc.place:
                self.hq_edit.setText(hq_assoc.place.place_name)
                self._hq_association_id = hq_assoc.association_id
            founder_assocs = self.controller.get.get_all_entities(
                "PublisherFounder", publisher_id=self.publisher.publisher_id
            ) or []
            self._founder_ids = [
                (assoc.artist.artist_id, assoc.artist.artist_name)
                for assoc in founder_assocs
                if assoc.artist
            ]
            self._refresh_founders_list()
            self.begin_year_edit.set_from_db(self.publisher.begin_year)
            self.end_year_edit.set_from_db(self.publisher.end_year)
            self.is_active_check.setChecked(bool(self.publisher.is_active))
            self.wiki_input.setText(self.publisher.wikipedia_link or "")
            self.mbid_input.setText(self.publisher.MBID or "")
            self.first_pass_check.setChecked(bool(self.publisher.first_pass))
            self.second_pass_check.setChecked(bool(self.publisher.second_pass))
            self._set_logo_path(self.publisher.logo_path)
            if self.tab_aliases:
                self.tab_aliases.load(self.publisher)
        else:
            self.is_active_check.setChecked(True)

    def _refresh_logo_preview(self, path):
        if path and os.path.exists(path):
            pixmap = QPixmap(path)
        else:
            pixmap = icon("default_logo.svg").pixmap(LOGO_MAX_SIZE)
        scaled = pixmap.scaled(LOGO_MAX_SIZE, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.logo_label.setPixmap(scaled)

    def _set_logo_path(self, path):
        self._logo_path = path or None
        self._refresh_logo_preview(self._logo_path)

    def _browse_logo(self):
        start_dir = self._settings.value(_SETTINGS_LAST_LOGO_DIR, "", type=str)
        if not start_dir or not os.path.isdir(start_dir):
            start_dir = ""
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Publisher Logo",
            start_dir,
            "Images (*.png *.jpg *.jpeg *.webp *.bmp *.gif *.svg)",
        )
        if path:
            self._settings.setValue(_SETTINGS_LAST_LOGO_DIR, os.path.dirname(path))
            managed_path = move_to_publisher_logos_dir(
                self.publisher.publisher_id, self.publisher.publisher_name, path
            )
            self._set_logo_path(managed_path)

    def _clear_logo(self):
        self._set_logo_path(None)

    def validate(self):
        name = self.name_input.text().strip()
        description = self.desc_input.text().strip() or None

        if not name:
            QMessageBox.warning(self, "Validation", "Publisher name is required")
            return

        existing = self.controller.get.get_entity_object(
            "Publisher", publisher_name=name
        )
        if existing and (
            not self.publisher or existing.publisher_id != self.publisher.publisher_id
        ):
            QMessageBox.warning(self, "Validation", "Publisher name already exists")
            return

        parent_name = self.parent_edit.text().strip()
        parent_id = None
        if parent_name:
            # Prefer the id locked in when the user picked a completion --
            # falls back to a name lookup for the edit-mode prefill, which
            # sets the text directly and never locks an id.
            parent_id = self.parent_edit.matched_id()
            if parent_id is None:
                parent_obj = self.controller.get.get_entity_object(
                    "Publisher", publisher_name=parent_name
                )
                parent_id = parent_obj.publisher_id if parent_obj else None
            if parent_id is None:
                # No existing publisher matches what was typed -- create one
                # on the fly rather than blocking the save, mirroring the
                # find-or-create pattern used by the other completer fields.
                parent_obj = find_or_create_by_name(
                    self.controller,
                    "Publisher",
                    "publisher_name",
                    parent_name,
                    self._known_publishers,
                )
                parent_id = parent_obj.publisher_id if parent_obj else None
            if self.publisher and parent_id in get_descendant_publisher_ids(
                self.controller, self.publisher.publisher_id
            ):
                QMessageBox.warning(
                    self,
                    "Invalid Parent",
                    "Cannot set parent to this publisher itself or one of its own descendants.",
                )
                return

        hq_name = self.hq_edit.text().strip()
        hq_place_id = None
        if hq_name:
            # Same resolution order as the parent publisher field: prefer
            # the id locked in by picking a completion, else an exact-name
            # lookup, else create a new Place on the fly.
            hq_place_id = self.hq_edit.matched_id()
            if hq_place_id is None:
                place_obj = self.controller.get.get_entity_object(
                    "Place", place_name=hq_name
                )
                hq_place_id = place_obj.place_id if place_obj else None
            if hq_place_id is None:
                place_obj = find_or_create_by_name(
                    self.controller,
                    "Place",
                    "place_name",
                    hq_name,
                    self._known_places,
                )
                hq_place_id = place_obj.place_id if place_obj else None

        begin_year = self.begin_year_edit.get_value_or_none()
        end_year = self.end_year_edit.get_value_or_none()
        is_active = 1 if self.is_active_check.isChecked() else 0
        wikipedia_link = self.wiki_input.text().strip() or None
        mbid = self.mbid_input.text().strip() or None
        first_pass = 1 if self.first_pass_check.isChecked() else 0
        second_pass = 1 if self.second_pass_check.isChecked() else 0

        try:
            if self.publisher:  # Editing
                self.controller.update.update_entity(
                    "Publisher",
                    self.publisher.publisher_id,
                    publisher_name=name,
                    description=description,
                    parent_id=parent_id,
                    begin_year=begin_year,
                    end_year=end_year,
                    is_active=is_active,
                    wikipedia_link=wikipedia_link,
                    MBID=mbid,
                    first_pass=first_pass,
                    second_pass=second_pass,
                    logo_path=self._logo_path,
                )
                self.result_publisher = self.publisher
            else:  # Creating
                self.result_publisher = self.controller.add.add_entity(
                    "Publisher",
                    publisher_name=name,
                    description=description,
                    parent_id=parent_id,
                    begin_year=begin_year,
                    end_year=end_year,
                    is_active=is_active,
                    wikipedia_link=wikipedia_link,
                    MBID=mbid,
                    first_pass=first_pass,
                    second_pass=second_pass,
                )
            self._save_headquarters(self.result_publisher.publisher_id, hq_place_id)
            self._save_founders(self.result_publisher.publisher_id)
            self.accept()
        except SQLAlchemyError as e:
            logger.error(f"Error saving publisher: {str(e)}")
            QMessageBox.critical(self, "Error", f"Failed to save publisher: {str(e)}")

    def _save_headquarters(self, publisher_id, hq_place_id):
        """Sync this publisher's single "Headquarters" PlaceAssociation to hq_place_id."""
        if hq_place_id is None:
            if self._hq_association_id is not None:
                self.controller.delete.delete_entity(
                    "PlaceAssociation", self._hq_association_id
                )
                self._hq_association_id = None
            return

        if self._hq_association_id is not None:
            self.controller.update.update_entity(
                "PlaceAssociation", self._hq_association_id, place_id=hq_place_id
            )
        else:
            known_types = fetch_association_types(self.controller)
            hq_type = find_or_create_association_type(
                self.controller, _HEADQUARTERS_TYPE_NAME, known_types
            )
            assoc = self.controller.add.add_entity(
                "PlaceAssociation",
                entity_id=publisher_id,
                entity_type="Publisher",
                place_id=hq_place_id,
                association_type_id=hq_type.association_type_id if hq_type else None,
            )
            self._hq_association_id = assoc.association_id

    def _refresh_founders_list(self):
        self.founders_list.clear()
        for artist_id, artist_name in self._founder_ids:
            item = QListWidgetItem(artist_name)
            item.setData(Qt.UserRole, artist_id)
            self.founders_list.addItem(item)

    def _add_founder(self):
        names = self.founder_edit.split_names()
        if not names:
            return

        # matched_id only names a single typed founder -- with several typed
        # at once (e.g. "Ahmet Ertegun;Herb Abramson") each is resolved by
        # name instead of relying on that one-shot completer pick.
        single_matched_id = self.founder_edit.matched_id() if len(names) == 1 else None
        already_listed = []
        for name in names:
            # Same resolution order as the parent publisher/headquarters
            # fields: prefer the id locked in by picking a completion, else
            # an exact-name lookup, else create a new Artist on the fly.
            artist_id = single_matched_id
            artist_name = name
            if artist_id is None:
                artist_obj = self.controller.get.get_entity_object(
                    "Artist", artist_name=name
                )
                if artist_obj is None:
                    artist_obj = find_or_create_by_name(
                        self.controller, "Artist", "artist_name", name, self._known_artists
                    )
                if artist_obj is not None:
                    artist_id = artist_obj.artist_id
                    artist_name = artist_obj.artist_name

            if artist_id is None:
                continue

            if any(fid == artist_id for fid, _ in self._founder_ids):
                already_listed.append(artist_name)
            else:
                self._founder_ids.append((artist_id, artist_name))

        self._refresh_founders_list()
        if already_listed:
            QMessageBox.information(
                self,
                "Already Added",
                "Already listed as a founder:\n" + "\n".join(already_listed),
            )

        self.founder_edit.reset()

    def _remove_selected_founder(self):
        item = self.founders_list.currentItem()
        if not item:
            return
        artist_id = item.data(Qt.UserRole)
        self._founder_ids = [
            (fid, fname) for fid, fname in self._founder_ids if fid != artist_id
        ]
        self._refresh_founders_list()

    def _save_founders(self, publisher_id):
        """Sync this publisher's PublisherFounder rows to self._founder_ids."""
        existing = self.controller.get.get_all_entities(
            "PublisherFounder", publisher_id=publisher_id
        ) or []
        existing_ids = {assoc.artist_id for assoc in existing}
        target_ids = {artist_id for artist_id, _ in self._founder_ids}

        for assoc in existing:
            if assoc.artist_id not in target_ids:
                self.controller.delete.delete_entity(
                    "PublisherFounder",
                    publisher_id=publisher_id,
                    artist_id=assoc.artist_id,
                )
        for artist_id in target_ids - existing_ids:
            self.controller.add.add_entity(
                "PublisherFounder", publisher_id=publisher_id, artist_id=artist_id
            )
