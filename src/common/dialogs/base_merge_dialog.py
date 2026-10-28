"""Two-step dialog: search and pick a source/target pair, then resolve field conflicts and merge."""

from difflib import SequenceMatcher
import html
import re

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QScrollArea, QStackedWidget, QVBoxLayout, QWidget
from sqlalchemy.exc import SQLAlchemyError

from src.common.dialogs.conflict_resolution import ConflictGridWidget
from src.common.match_confidence import MEDIUM_CONFIDENCE, confidence_color
from src.common.widgets.item_foreground_delegate import ItemForegroundDelegate
from src.common.widgets.qt_text import esc_amp as _esc_amp
from src.foundation.logger_config import logger

# Rows shown per list; a broad search over a large table (e.g. Artist) would
# otherwise rebuild thousands of items on every keystroke.
_MAX_LIST_ROWS = 500


class MergeDBDialog(QDialog):
    """Integrated dialog for searching, selecting, and resolving merge conflicts."""

    def __init__(self, controller, model_name, parent=None, preload_source=None, preload_target=None):
        super().__init__(parent)
        self.controller = controller
        self.merge_helper = controller.merge
        self.model_name = model_name

        # Attribute naming conventions based on model name
        self.id_attr = f"{self.model_name.lower()}_id"
        self.name_attr = f"{self.model_name.lower()}_name"

        self.source_entity = preload_source
        self.target_entity = preload_target
        self.all_entities_cache = None

        self.setWindowTitle(f"Merge {model_name} Entries")
        self.resize(900, 700)

        # Use a StackedWidget to switch between 'Search' and 'Resolve' views
        self.stack = QStackedWidget(self)
        layout = QVBoxLayout(self)
        layout.addWidget(self.stack)

        self._init_search_ui()
        if preload_source and preload_target:
            # Both entities known: open straight on the conflict page (the
            # search page stays at index 0 for "Back").
            self._build_conflict_ui()

    # --- SEARCH & SELECTION FEATURES ---

    def _init_search_ui(self):
        """Build the search-and-select page (stack index 0)."""
        search_page = QWidget()
        main_layout = QVBoxLayout(search_page)

        # Instructions
        instructions = QLabel(
            f"Search and select two {self.model_name.lower()} entries to merge. The source will be merged into the target and then deleted. Use 'Find Similar' to quickly find potential matches."
        )
        instructions.setWordWrap(True)
        main_layout.addWidget(instructions)

        # Two-column layout
        cols = QHBoxLayout()

        # Source column with enhanced features
        source_col = QVBoxLayout()
        source_col.addWidget(QLabel(f"<b>Source {self.model_name}</b> (will be deleted):"))

        # Source info display — shown ABOVE the list so it's clearly the selected item
        self.source_info = QLabel("No entity selected")
        self.source_info.setWordWrap(True)
        self.source_info.setObjectName("entityInfoBox")
        source_col.addWidget(self.source_info)

        # Source search with "Find Similar" button
        source_search_layout = QHBoxLayout()
        self.source_search = QLineEdit()
        self.source_search.setPlaceholderText(f"Search source {self.model_name.lower()}...")
        self.source_search.textChanged.connect(lambda t: self._update_list(t, "source"))
        source_search_layout.addWidget(self.source_search)

        self.source_find_similar_btn = QPushButton("Find Similar")
        self.source_find_similar_btn.clicked.connect(lambda: self._find_similar("source"))
        self.source_find_similar_btn.setToolTip(f"Find {self.model_name.lower()}s similar to the selected target")
        self.source_find_similar_btn.setEnabled(False)
        source_search_layout.addWidget(self.source_find_similar_btn)

        source_col.addLayout(source_search_layout)

        self.source_list = QListWidget()
        self.source_list.setItemDelegate(ItemForegroundDelegate(self.source_list))  # QSS would hide the score colours
        self.source_list.itemClicked.connect(lambda i: self._select_entity(i, "source"))
        source_col.addWidget(self.source_list)

        # Target column with enhanced features
        target_col = QVBoxLayout()
        target_col.addWidget(QLabel(f"<b>Target {self.model_name}</b> (will be kept):"))

        # Target info display — shown ABOVE the list so it's clearly the selected item
        self.target_info = QLabel("No entity selected")
        self.target_info.setWordWrap(True)
        self.target_info.setObjectName("entityInfoBox")
        target_col.addWidget(self.target_info)

        # Target search with "Find Similar" button
        target_search_layout = QHBoxLayout()
        self.target_search = QLineEdit()
        self.target_search.setPlaceholderText(f"Search target {self.model_name.lower()}...")
        self.target_search.textChanged.connect(lambda t: self._update_list(t, "target"))
        target_search_layout.addWidget(self.target_search)

        self.target_find_similar_btn = QPushButton("Find Similar")
        self.target_find_similar_btn.clicked.connect(lambda: self._find_similar("target"))
        self.target_find_similar_btn.setToolTip(f"Find {self.model_name.lower()}s similar to the selected source")
        self.target_find_similar_btn.setEnabled(False)
        target_search_layout.addWidget(self.target_find_similar_btn)

        target_col.addLayout(target_search_layout)

        self.target_list = QListWidget()
        self.target_list.setItemDelegate(ItemForegroundDelegate(self.target_list))
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
        """Fill one side's list with a case-insensitive substring match, capped at _MAX_LIST_ROWS."""
        # Plain substring on purpose: fuzzy scoring dropped short names (e.g. '3io').
        l_widget = self.source_list if side == "source" else self.target_list
        l_widget.clear()

        if self.all_entities_cache is None:
            try:
                self.all_entities_cache = self.controller.get.get_all_entities(self.model_name)
            except SQLAlchemyError as e:
                logger.error(f"Error fetching all {self.model_name.lower()}s: {e!s}")
                self.all_entities_cache = []

        text_lower = text.strip().lower()

        shown = 0
        for e in self.all_entities_cache:
            name = getattr(e, self.name_attr, "Unknown") or ""
            # Show everything when the box is empty; otherwise do a substring match
            if text_lower and text_lower not in name.lower():
                continue
            if shown == _MAX_LIST_ROWS:
                more = QListWidgetItem(f"More than {_MAX_LIST_ROWS} matches. Type more to refine the search.")
                more.setFlags(Qt.NoItemFlags)
                l_widget.addItem(more)
                break
            item = QListWidgetItem(name)
            item.setData(Qt.UserRole, getattr(e, self.id_attr))
            l_widget.addItem(item)
            shown += 1

    def _normalize_name(self, name):
        """Simple normalization - just lowercase and remove common punctuation."""
        if not name:
            return ""
        name = str(name).lower().strip()
        # Remove common punctuation but keep meaningful characters
        return re.sub(r"[^\w\s&]", "", name)

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
            prefix_score = min(len(norm2) / max(len(norm1), 1), len(norm1) / max(len(norm2), 1))
            scores.append(prefix_score * 0.2)

        return min(sum(scores), 1.0)

    def _find_similar_entities_by_name(self, base_name, entities, limit=10, exclude_id=None):
        """Return up to `limit` (entity, score) pairs similar to `base_name`, skipping `exclude_id`."""
        if not base_name:
            return []

        similarities = []
        base_name_lower = str(base_name).lower()
        base_tokens = set(self._normalize_name(base_name).split())

        for entity in entities:
            if exclude_id is not None and getattr(entity, self.id_attr, None) == exclude_id:
                continue
            entity_name = getattr(entity, self.name_attr, "")

            score = self._calculate_name_similarity(base_name, entity_name)

            # Apply heuristics for common patterns
            entity_tokens = set(self._normalize_name(entity_name).split())

            # Boost score if one name contains the other
            if base_name_lower in str(entity_name).lower() or str(entity_name).lower() in base_name_lower:
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
        """Select a list item's entity for `side` and auto-suggest matches on the other side."""
        eid = item.data(Qt.UserRole)
        try:
            entity = self.controller.get.get_entity_object(self.model_name, **{self.id_attr: eid})

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

        except (SQLAlchemyError, RuntimeError) as e:
            logger.error(f"Error selecting entity: {e!s}")
            QMessageBox.warning(self, "Selection Error", f"Failed to select entity: {e!s}")

    def _build_entity_info(self, entity, side):
        """Build informational text for an entity."""
        if not entity:
            return "No entity selected"

        name = getattr(entity, self.name_attr, "Unknown")
        entity_id = getattr(entity, self.id_attr)

        info = f"<b>{html.escape(str(name))}</b><br>"

        # Add related entity count if available
        related_count = self._get_related_count(entity_id)
        if related_count > 0:
            info += f"Related items: {related_count}<br>"

        # Add status if available (like is_active field)
        if hasattr(entity, "is_active"):
            status = "Active" if entity.is_active == 1 else "Inactive"
            info += f"Status: {status}"

        return info

    def _get_related_count(self, entity_id):
        """Get count of related entities. Override in specialized dialogs."""
        # Base implementation returns 0
        # Subclasses can override to provide specific counts
        return 0

    def _auto_suggest_similar(self, selected_entity, target_list_type):
        """Fill `target_list_type`'s list with entities similar to `selected_entity`."""
        if not selected_entity:
            return

        try:
            entities = self.all_entities_cache or []
            selected_name = getattr(selected_entity, self.name_attr, "")

            # Use our enhanced similarity finder
            similar_entities = self._find_similar_entities_by_name(selected_name, entities, limit=10, exclude_id=getattr(selected_entity, self.id_attr, None))

            list_widget = self.source_list if target_list_type == "source" else self.target_list

            # Clear and show suggestions
            list_widget.clear()

            if similar_entities:
                # Update search field placeholder
                search_field = self.source_search if target_list_type == "source" else self.target_search
                search_field.setPlaceholderText(f"Suggestions for '{selected_name}'")

                # Add suggestions with similarity score
                for entity, similarity in similar_entities:
                    name = getattr(entity, self.name_attr, "Unknown")
                    item = QListWidgetItem(f"{name} ({similarity:.0%})")
                    item.setData(Qt.UserRole, getattr(entity, self.id_attr))
                    item.setData(Qt.UserRole + 1, similarity)

                    if similarity > MEDIUM_CONFIDENCE:
                        item.setForeground(confidence_color(similarity))

                    list_widget.addItem(item)
            else:
                # Clear search field placeholder
                search_field = self.source_search if target_list_type == "source" else self.target_search
                search_field.setPlaceholderText(f"Search {target_list_type} {self.model_name.lower()}...")

        except (AttributeError, RuntimeError) as e:
            logger.error(f"Error in auto-suggest: {e!s}")

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
        except RuntimeError as e:
            logger.error(f"Error finding similar entities: {e!s}")

    def _swap_selection(self):
        """Swap the source and target entities and refresh both sides."""
        if not self.source_entity or not self.target_entity:
            return

        # Swap the entities
        self.source_entity, self.target_entity = self.target_entity, self.source_entity

        try:
            # Update the info labels
            self.source_info.setText(self._build_entity_info(self.source_entity, "source"))
            self.target_info.setText(self._build_entity_info(self.target_entity, "target"))

            # Clear and refresh the lists
            self._update_list(self.source_search.text(), "source")
            self._update_list(self.target_search.text(), "target")

            # Highlight the current selections
            self._highlight_selected_entities()

            self._update_action_buttons()

        except RuntimeError as e:
            logger.error(f"Error swapping selection: {e!s}")

    def _highlight_selected_entities(self):
        """Highlight the currently selected entities in the lists."""
        if self.source_entity:
            self._highlight_item_in_list(self.source_list, getattr(self.source_entity, self.id_attr))
        if self.target_entity:
            self._highlight_item_in_list(self.target_list, getattr(self.target_entity, self.id_attr))

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
        different = has_source and has_target and self.source_entity != self.target_entity

        self.next_btn.setEnabled(different)
        self.swap_btn.setEnabled(has_source and has_target)

        # Update next button text with entity names
        if different:
            source_name = getattr(self.source_entity, self.name_attr, "Source")
            target_name = getattr(self.target_entity, self.name_attr, "Target")
            self.next_btn.setText(_esc_amp(f"Next: Merge '{source_name}' into '{target_name}' →"))
        else:
            self.next_btn.setText("Next: Resolve Conflicts →")

    # --- CONFLICT RESOLUTION FEATURES ---

    def _build_conflict_ui(self):
        """Build (or rebuild) the conflict page at stack index 1 for the current pair."""
        # Drop the previous pair's page: index 1 must always be the current grid.
        while self.stack.count() > 1:
            old_page = self.stack.widget(1)
            self.stack.removeWidget(old_page)
            old_page.deleteLater()
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

        source_name = getattr(self.source_entity, self.name_attr, "Source")
        target_name = getattr(self.target_entity, self.name_attr, "Target")
        self._conflict_grid = ConflictGridWidget(self.source_entity, self.target_entity, self.id_attr, source_name, target_name)

        if not self._conflict_grid.has_conflicts():
            # No conflicts found - show direct merge option
            scroll_layout.addWidget(QLabel(f"<h3>No conflicts detected!</h3>All fields are identical between the two {self.model_name.lower()}s.<br>You can proceed with the merge directly."))
        else:
            scroll_layout.addWidget(QLabel("<h3>Resolve Conflicts</h3>"))
            scroll_layout.addSpacing(8)
            scroll_layout.addWidget(self._conflict_grid)

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

    def _on_merge(self):
        """Confirm, then execute the merge (own-prompt subclasses call `_execute_merge()` directly)."""
        reply = QMessageBox.question(
            self,
            "Final Confirmation",
            (f"Are you sure you want to merge '{getattr(self.source_entity, self.name_attr)}' into '{getattr(self.target_entity, self.name_attr)}'?\n\nThis action cannot be undone."),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )

        if reply != QMessageBox.Yes:
            return

        self._execute_merge()

    def _execute_merge(self):
        """Run the merge without any confirmation prompt of its own."""
        try:
            # Build a dictionary of the user's chosen values.
            # Do NOT modify the ORM objects here.
            resolved_fields = self._conflict_grid.get_resolved_fields() if hasattr(self, "_conflict_grid") else {}

            success = self.merge_helper.merge_entities(self.model_name, getattr(self.source_entity, self.id_attr), getattr(self.target_entity, self.id_attr), resolved_fields)

            if success:
                self.accept()
            else:
                QMessageBox.warning(self, "Merge Failed", f"Failed to merge {self.model_name.lower()}s. Please check the logs.")

        except (AttributeError, SQLAlchemyError) as e:
            logger.exception("Error during merge")

            QMessageBox.critical(self, "Merge Error", f"An error occurred during merge:\n\n{e}")
