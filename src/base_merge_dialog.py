import re
from difflib import SequenceMatcher

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from src.logger_config import logger


class MergeDBDialog(QDialog):
    """Integrated dialog for searching, selecting, and resolving merge conflicts."""

    def __init__(self, controller, model_name, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.merge_helper = controller.merge
        self.model_name = model_name

        # Attribute naming conventions based on model name
        self.id_attr = f"{self.model_name.lower()}_id"
        self.name_attr = f"{self.model_name.lower()}_name"

        self.source_entity = None
        self.target_entity = None
        self.all_entities_cache = None

        self.setWindowTitle(f"Merge {model_name} Entries")
        self.resize(900, 700)  # Slightly larger for enhanced features

        # Use a StackedWidget to switch between 'Search' and 'Resolve' views
        self.stack = QStackedWidget(self)
        layout = QVBoxLayout(self)
        layout.addWidget(self.stack)

        self._init_search_ui()
        # The conflict UI is built dynamically after selection

    # --- SEARCH & SELECTION FEATURES (Enhanced from Publisher Dialog) ---

    def _init_search_ui(self):
        """Initialize the search and selection UI with enhanced features."""
        search_page = QWidget()
        main_layout = QVBoxLayout(search_page)

        # Instructions
        instructions = QLabel(
            f"Search and select two {self.model_name.lower()} entries to merge. "
            "The source will be merged into the target and then deleted. "
            "Use 'Find Similar' to quickly find potential matches."
        )
        instructions.setWordWrap(True)
        main_layout.addWidget(instructions)

        # Two-column layout
        cols = QHBoxLayout()

        # Source column with enhanced features
        source_col = QVBoxLayout()
        source_col.addWidget(
            QLabel(f"<b>Source {self.model_name}</b> (will be deleted):")
        )

        # Source info display — shown ABOVE the list so it's clearly the selected item
        self.source_info = QLabel("No entity selected")
        self.source_info.setWordWrap(True)
        self.source_info.setObjectName("entityInfoBox")
        self.source_info.setStyleSheet(
            "QLabel#entityInfoBox {"
            "  border: 1px solid palette(mid);"
            "  border-radius: 3px;"
            "  padding: 4px 6px;"
            "  background: palette(base);"
            "  min-height: 36px;"
            "}"
        )
        source_col.addWidget(self.source_info)

        # Source search with "Find Similar" button
        source_search_layout = QHBoxLayout()
        self.source_search = QLineEdit()
        self.source_search.setPlaceholderText(
            f"Search source {self.model_name.lower()}..."
        )
        self.source_search.textChanged.connect(lambda t: self._update_list(t, "source"))
        source_search_layout.addWidget(self.source_search)

        self.source_find_similar_btn = QPushButton("Find Similar")
        self.source_find_similar_btn.clicked.connect(
            lambda: self._find_similar("source")
        )
        self.source_find_similar_btn.setToolTip(
            f"Find {self.model_name.lower()}s similar to the selected target"
        )
        self.source_find_similar_btn.setEnabled(False)
        source_search_layout.addWidget(self.source_find_similar_btn)

        source_col.addLayout(source_search_layout)

        self.source_list = QListWidget()
        self.source_list.itemClicked.connect(lambda i: self._select_entity(i, "source"))
        source_col.addWidget(self.source_list)

        # Target column with enhanced features
        target_col = QVBoxLayout()
        target_col.addWidget(QLabel(f"<b>Target {self.model_name}</b> (will be kept):"))

        # Target info display — shown ABOVE the list so it's clearly the selected item
        self.target_info = QLabel("No entity selected")
        self.target_info.setWordWrap(True)
        self.target_info.setObjectName("entityInfoBox")
        self.target_info.setStyleSheet(
            "QLabel#entityInfoBox {"
            "  border: 1px solid palette(mid);"
            "  border-radius: 3px;"
            "  padding: 4px 6px;"
            "  background: palette(base);"
            "  min-height: 36px;"
            "}"
        )
        target_col.addWidget(self.target_info)

        # Target search with "Find Similar" button
        target_search_layout = QHBoxLayout()
        self.target_search = QLineEdit()
        self.target_search.setPlaceholderText(
            f"Search target {self.model_name.lower()}..."
        )
        self.target_search.textChanged.connect(lambda t: self._update_list(t, "target"))
        target_search_layout.addWidget(self.target_search)

        self.target_find_similar_btn = QPushButton("Find Similar")
        self.target_find_similar_btn.clicked.connect(
            lambda: self._find_similar("target")
        )
        self.target_find_similar_btn.setToolTip(
            f"Find {self.model_name.lower()}s similar to the selected source"
        )
        self.target_find_similar_btn.setEnabled(False)
        target_search_layout.addWidget(self.target_find_similar_btn)

        target_col.addLayout(target_search_layout)

        self.target_list = QListWidget()
        self.target_list.itemClicked.connect(lambda i: self._select_entity(i, "target"))
        target_col.addWidget(self.target_list)

        cols.addLayout(source_col)
        cols.addLayout(target_col)
        main_layout.addLayout(cols)

        # Control Row with enhanced buttons
        controls = QHBoxLayout()

        # Swap button
        self.swap_btn = QPushButton("↔ Swap Selection")
        self.swap_btn.clicked.connect(self._swap_selection)
        self.swap_btn.setToolTip("Swap source and target entities")
        self.swap_btn.setEnabled(False)
        controls.addWidget(self.swap_btn)

        controls.addStretch()

        # Next button
        self.next_btn = QPushButton("Next: Resolve Conflicts →")
        self.next_btn.setEnabled(False)
        self.next_btn.clicked.connect(self._build_conflict_ui)
        controls.addWidget(self.next_btn)

        main_layout.addLayout(controls)

        # Close button
        close_btn = QPushButton("Cancel")
        close_btn.clicked.connect(self.reject)
        main_layout.addWidget(close_btn)

        self.stack.addWidget(search_page)

    def _update_list(self, text, side):
        """Populate the list using a plain case-insensitive substring search.

        Fuzzy/similarity scoring is reserved for the 'Find Similar' button.
        Using it here caused short names (e.g. '3io') to score below threshold
        and disappear from the list entirely.
        """
        l_widget = self.source_list if side == "source" else self.target_list
        l_widget.clear()

        if self.all_entities_cache is None:
            try:
                self.all_entities_cache = self.controller.get.get_all_entities(
                    self.model_name
                )
            except Exception as e:
                logger.error(f"Error fetching all {self.model_name.lower()}s: {str(e)}")
                self.all_entities_cache = []

        text_lower = text.strip().lower()

        for e in self.all_entities_cache:
            name = getattr(e, self.name_attr, "Unknown") or ""
            # Show everything when the box is empty; otherwise do a substring match
            if not text_lower or text_lower in name.lower():
                item = QListWidgetItem(name)
                item.setData(Qt.UserRole, getattr(e, self.id_attr))
                l_widget.addItem(item)

    def _normalize_name(self, name):
        """Simple normalization - just lowercase and remove common punctuation."""
        if not name:
            return ""
        name = str(name).lower().strip()
        # Remove common punctuation but keep meaningful characters
        name = re.sub(r"[^\w\s&]", "", name)
        return name

    def _calculate_name_similarity(self, base_name, compare_name):
        """Calculate multiple similarity scores between two names."""
        if not base_name or not compare_name:
            return 0.0

        base_name_lower = str(base_name).lower()
        compare_name_lower = str(compare_name).lower()

        # If identical, return max score
        if base_name_lower == compare_name_lower:
            return 1.0

        norm1 = self._normalize_name(base_name)
        norm2 = self._normalize_name(compare_name)

        # If identical after normalization
        if norm1 == norm2:
            return 0.95

        scores = []

        # 1. Basic string similarity
        scores.append(SequenceMatcher(None, norm1, norm2).ratio() * 0.3)

        # 2. Token-based similarity
        tokens1 = set(norm1.split())
        tokens2 = set(norm2.split())

        if tokens1 and tokens2:
            # Check if one is subset of another
            if tokens1.issubset(tokens2) or tokens2.issubset(tokens1):
                scores.append(0.9 * 0.3)

            # Jaccard similarity
            intersection = len(tokens1.intersection(tokens2))
            union = len(tokens1.union(tokens2))
            jaccard = intersection / union if union > 0 else 0
            scores.append(jaccard * 0.3)

        # 3. Acronym/abbreviation matching
        acronym1 = "".join([t[0] for t in norm1.split() if t])
        acronym2 = "".join([t[0] for t in norm2.split() if t])

        if acronym1 and acronym2 and (acronym1 in acronym2 or acronym2 in acronym1):
            scores.append(0.8 * 0.2)

        # 4. Prefix matching
        if norm1.startswith(norm2) or norm2.startswith(norm1):
            prefix_score = min(
                len(norm2) / max(len(norm1), 1), len(norm1) / max(len(norm2), 1)
            )
            scores.append(prefix_score * 0.2)

        return min(sum(scores), 1.0)

    def _find_similar_entities_by_name(self, base_name, entities, limit=10):
        """Find entities similar to a given name using multiple strategies."""
        if not base_name:
            return []

        similarities = []
        base_name_lower = str(base_name).lower()

        for entity in entities:
            entity_name = getattr(entity, self.name_attr, "")
            selected_entity = (
                self.source_entity if hasattr(self, "source_entity") else None
            )
            if selected_entity is not None and getattr(
                entity, self.id_attr, None
            ) == getattr(selected_entity, self.id_attr, None):
                continue

            score = self._calculate_name_similarity(base_name, entity_name)

            # Apply heuristics for common patterns
            base_tokens = set(self._normalize_name(base_name).split())
            entity_tokens = set(self._normalize_name(entity_name).split())

            # Boost score if one name contains the other
            if (
                base_name_lower in str(entity_name).lower()
                or str(entity_name).lower() in base_name_lower
            ):
                score = min(score + 0.2, 1.0)

            # Boost score for shared significant tokens
            common_tokens = base_tokens.intersection(entity_tokens)
            if len(common_tokens) >= 1:
                # More weight for longer tokens (likely brand names)
                significant_common = sum(len(t) for t in common_tokens if len(t) > 3)
                if significant_common > 0:
                    score = min(score + 0.15, 1.0)

            if score > 0.4:  # Only include reasonably similar matches
                similarities.append((entity, score))

        # Sort by score
        similarities.sort(key=lambda x: x[1], reverse=True)

        return similarities[:limit]

    def _select_entity(self, item, side):
        """Select an entity with enhanced info display and auto-suggest."""
        eid = item.data(Qt.UserRole)
        try:
            entity = self.controller.get.get_entity_object(
                self.model_name, **{self.id_attr: eid}
            )

            if side == "source":
                self.source_entity = entity
                self.source_info.setText(self._build_entity_info(entity, "source"))
                # Enable target's "Find Similar" button
                self.target_find_similar_btn.setEnabled(True)
                # Auto-suggest similar targets
                self._auto_suggest_similar(entity, "target")
            else:
                self.target_entity = entity
                self.target_info.setText(self._build_entity_info(entity, "target"))
                # Enable source's "Find Similar" button
                self.source_find_similar_btn.setEnabled(True)
                # Auto-suggest similar sources
                self._auto_suggest_similar(entity, "source")

            self._update_action_buttons()
            self._highlight_selected_entities()

        except Exception as e:
            logger.error(f"Error selecting entity: {str(e)}")
            QMessageBox.warning(
                self, "Selection Error", f"Failed to select entity: {str(e)}"
            )

    def _build_entity_info(self, entity, side):
        """Build informational text for an entity."""
        if not entity:
            return "No entity selected"

        name = getattr(entity, self.name_attr, "Unknown")
        entity_id = getattr(entity, self.id_attr)

        info = f"<b>{name}</b><br>"

        # Add related entity count if available
        related_count = self._get_related_count(entity_id)
        if related_count > 0:
            info += f"Related items: {related_count}<br>"

        # Add status if available (like is_active field)
        if hasattr(entity, "is_active"):
            status = "Active" if getattr(entity, "is_active") == 1 else "Inactive"
            info += f"Status: {status}"

        return info

    def _get_related_count(self, entity_id):
        """Get count of related entities. Override in specialized dialogs."""
        # Base implementation returns 0
        # Subclasses can override to provide specific counts
        return 0

    def _auto_suggest_similar(self, selected_entity, target_list_type):
        """Automatically suggest similar entities in the opposite list."""
        if not selected_entity:
            return

        try:
            entities = self.all_entities_cache or []
            selected_name = getattr(selected_entity, self.name_attr, "")

            # Use our enhanced similarity finder
            similar_entities = self._find_similar_entities_by_name(
                selected_name, entities, limit=10
            )

            list_widget = (
                self.source_list if target_list_type == "source" else self.target_list
            )

            # Clear and show suggestions
            list_widget.clear()

            if similar_entities:
                # Update search field placeholder
                search_field = (
                    self.source_search
                    if target_list_type == "source"
                    else self.target_search
                )
                search_field.setPlaceholderText(f"Suggestions for '{selected_name}'")

                # Add suggestions with similarity score
                for entity, similarity in similar_entities:
                    name = getattr(entity, self.name_attr, "Unknown")
                    item = QListWidgetItem(f"{name} ({similarity:.0%})")
                    item.setData(Qt.UserRole, getattr(entity, self.id_attr))
                    item.setData(Qt.UserRole + 1, similarity)

                    # Color code based on similarity
                    if similarity > 0.8:
                        item.setForeground(Qt.darkGreen)
                    elif similarity > 0.6:
                        item.setForeground(Qt.darkBlue)

                    list_widget.addItem(item)
            else:
                # Clear search field placeholder
                search_field = (
                    self.source_search
                    if target_list_type == "source"
                    else self.target_search
                )
                search_field.setPlaceholderText(
                    f"Search {target_list_type} {self.model_name.lower()}..."
                )

        except Exception as e:
            logger.error(f"Error in auto-suggest: {str(e)}")

    def _find_similar(self, from_list_type):
        """Manually trigger finding similar entities."""
        try:
            if from_list_type == "source" and self.target_entity:
                # Find similar to target (for source list)
                self._auto_suggest_similar(self.target_entity, "source")
                # Clear source search to show suggestions
                self.source_search.clear()
            elif from_list_type == "target" and self.source_entity:
                # Find similar to source (for target list)
                self._auto_suggest_similar(self.source_entity, "target")
                # Clear target search to show suggestions
                self.target_search.clear()
        except Exception as e:
            logger.error(f"Error finding similar entities: {str(e)}")

    def _swap_selection(self):
        """Enhanced swap with highlighting and info updates."""
        if not self.source_entity or not self.target_entity:
            return

        # Swap the entities
        self.source_entity, self.target_entity = self.target_entity, self.source_entity

        try:
            # Update the info labels
            self.source_info.setText(
                self._build_entity_info(self.source_entity, "source")
            )
            self.target_info.setText(
                self._build_entity_info(self.target_entity, "target")
            )

            # Clear and refresh the lists
            self._update_list(self.source_search.text(), "source")
            self._update_list(self.target_search.text(), "target")

            # Highlight the current selections
            self._highlight_selected_entities()

            self._update_action_buttons()

        except Exception as e:
            logger.error(f"Error swapping selection: {str(e)}")

    def _highlight_selected_entities(self):
        """Highlight the currently selected entities in the lists."""
        if self.source_entity:
            self._highlight_item_in_list(
                self.source_list, getattr(self.source_entity, self.id_attr)
            )
        if self.target_entity:
            self._highlight_item_in_list(
                self.target_list, getattr(self.target_entity, self.id_attr)
            )

    def _highlight_item_in_list(self, list_widget, entity_id):
        """Highlight an item in a list widget by entity_id."""
        for i in range(list_widget.count()):
            item = list_widget.item(i)
            if item.data(Qt.UserRole) == entity_id:
                item.setSelected(True)
                list_widget.scrollToItem(item)
                break

    def _update_action_buttons(self):
        """Update enabled states of all action buttons."""
        has_source = self.source_entity is not None
        has_target = self.target_entity is not None
        different = (
            has_source and has_target and self.source_entity != self.target_entity
        )

        self.next_btn.setEnabled(different)
        self.swap_btn.setEnabled(has_source and has_target)

        # Update next button text with entity names
        if different:
            source_name = getattr(self.source_entity, self.name_attr, "Source")
            target_name = getattr(self.target_entity, self.name_attr, "Target")
            self.next_btn.setText(f"Next: Merge '{source_name}' into '{target_name}' →")
        else:
            self.next_btn.setText("Next: Resolve Conflicts →")

    # --- CONFLICT RESOLUTION FEATURES ---

    def _build_conflict_ui(self):
        """Conflict resolution UI with radio buttons."""
        # Confirm before proceeding to conflict resolution
        reply = QMessageBox.question(
            self,
            "Confirm Merge",
            f"Merge '{getattr(self.source_entity, self.name_attr)}' into "
            f"'{getattr(self.target_entity, self.name_attr)}'?\n\n"
            f"You will now resolve conflicts between the two entries.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )

        if reply != QMessageBox.Yes:
            return

        resolve_page = QWidget()
        layout = QVBoxLayout(resolve_page)

        # Back button
        back_layout = QHBoxLayout()
        back_btn = QPushButton("← Back to Selection")
        back_btn.clicked.connect(lambda: self.stack.setCurrentIndex(0))
        back_layout.addWidget(back_btn)
        back_layout.addStretch()
        layout.addLayout(back_layout)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        scroll_layout = QVBoxLayout(content)

        self.radio_groups = {}
        conflicts = self._get_conflicts()

        if not conflicts:
            # No conflicts found - show direct merge option
            scroll_layout.addWidget(
                QLabel(
                    "<h3>No conflicts detected!</h3>"
                    f"All fields are identical between the two {self.model_name.lower()}s.<br>"
                    "You can proceed with the merge directly."
                )
            )
        else:
            scroll_layout.addWidget(
                QLabel(
                    "<h3>Resolve Conflicts</h3>"
                    "Select which value to keep for each conflicting field:"
                )
            )
            scroll_layout.addSpacing(8)

            for field, (s_val, t_val) in conflicts.items():
                # --- Card widget: gives each field a visible box ---
                card = QWidget()
                card.setObjectName("conflictCard")
                card.setStyleSheet(
                    "QWidget#conflictCard {"
                    "  border: 1px solid palette(mid);"
                    "  border-radius: 4px;"
                    "  padding: 6px;"
                    "  margin-bottom: 6px;"
                    "}"
                )
                # Limit the card width so buttons don't stretch edge-to-edge
                card.setMaximumWidth(560)

                card_layout = QVBoxLayout(card)
                card_layout.setSpacing(4)
                card_layout.setContentsMargins(8, 6, 8, 6)

                # Field name label
                field_label = QLabel(f"<b>{field}</b>")
                card_layout.addWidget(field_label)

                group = QButtonGroup(self)

                s_display = self._format_value_for_display(s_val)
                t_display = self._format_value_for_display(t_val)

                source_name = getattr(self.source_entity, self.name_attr, "Source")
                target_name = getattr(self.target_entity, self.name_attr, "Target")

                # Radio buttons stacked vertically — much easier to read
                s_radio = QRadioButton(f"Keep Source ({source_name}):  {s_display}")
                t_radio = QRadioButton(f"Keep Target ({target_name}):  {t_display}")

                group.addButton(s_radio)
                group.addButton(t_radio)
                card_layout.addWidget(s_radio)
                card_layout.addWidget(t_radio)

                # Default to Target if source is empty, otherwise Source
                if s_val is None or s_val == "":
                    t_radio.setChecked(True)
                else:
                    s_radio.setChecked(True)

                self.radio_groups[field] = group

                # Left-align the card instead of stretching it
                row = QHBoxLayout()
                row.addWidget(card)
                row.addStretch()
                scroll_layout.addLayout(row)

        scroll_layout.addStretch()
        scroll.setWidget(content)
        layout.addWidget(scroll)

        # Footer: Cancel on left, Confirm Merge on right
        footer = QHBoxLayout()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        footer.addWidget(cancel_btn)
        footer.addStretch()
        merge_btn = QPushButton("Confirm Merge")
        merge_btn.clicked.connect(self._on_merge)
        footer.addWidget(merge_btn)
        layout.addLayout(footer)

        self.stack.addWidget(resolve_page)
        self.stack.setCurrentIndex(1)

    def _format_value_for_display(self, value):
        """Format a value for display in the conflict resolution UI."""
        if value is None:
            return "[Empty]"
        if value == "":
            return "[Blank]"
        if isinstance(value, str) and len(value) > 50:
            return value[:47] + "..."
        return str(value)

    def _is_skippable_field(self, attr, value):
        """Return True for fields that should never be shown as merge choices.

        Skipped categories:
        - Relationship fields: lists or ORM-mapped objects (not plain Python types)
        - Auto-generated IDs: any attribute ending in '_id'
        - Timestamps: any attribute ending in '_at' or named 'created_*' / 'updated_*'
        """
        # Skip ID columns (primary keys and foreign keys)
        if attr.endswith("_id"):
            return True

        # Skip timestamp columns
        if (
            attr.endswith("_at")
            or attr.startswith("created_")
            or attr.startswith("updated_")
        ):
            return True

        # Skip relationship fields — these are lists or mapped ORM objects,
        # not simple scalar values the user can meaningfully choose between.
        if isinstance(value, list):
            return True
        plain_types = (str, int, float, bool, type(None))
        if not isinstance(value, plain_types):
            return True

        return False

    def _get_conflicts(self):
        """Detect differences between source and target, excluding non-mergeable fields."""
        conflicts = {}
        for attr in vars(self.source_entity):
            if attr.startswith("_") or attr in ("metadata", self.id_attr):
                continue

            s_val = getattr(self.source_entity, attr)
            t_val = getattr(self.target_entity, attr)

            # Skip fields that should not be presented as merge choices
            if self._is_skippable_field(attr, s_val) or self._is_skippable_field(
                attr, t_val
            ):
                continue

            if s_val != t_val:
                conflicts[attr] = (s_val, t_val)
        return conflicts

    def _on_merge(self):
        """Final execution of the merge with confirmation."""
        # Final confirmation
        reply = QMessageBox.question(
            self,
            "Final Confirmation",
            f"Are you sure you want to merge '{getattr(self.source_entity, self.name_attr)}' "
            f"into '{getattr(self.target_entity, self.name_attr)}'?\n\n"
            f"This action cannot be undone.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )

        if reply != QMessageBox.Yes:
            return

        try:
            # Apply conflict resolutions if any
            if hasattr(self, "radio_groups"):
                for field, group in self.radio_groups.items():
                    if group.checkedButton():
                        if "Source" in group.checkedButton().text():
                            val = getattr(self.source_entity, field)
                        else:
                            val = getattr(self.target_entity, field)
                        setattr(self.target_entity, field, val)

            # Perform the merge
            success = self.merge_helper.merge_entities(
                self.model_name,
                getattr(self.source_entity, self.id_attr),
                getattr(self.target_entity, self.id_attr),
            )

            if success:
                self.accept()
            else:
                QMessageBox.warning(
                    self,
                    "Merge Failed",
                    f"Failed to merge {self.model_name.lower()}s. Please check the logs.",
                )

        except Exception as e:
            logger.error(f"Error during merge: {str(e)}")
            QMessageBox.critical(
                self, "Merge Error", f"An error occurred during merge: {str(e)}"
            )
