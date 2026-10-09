"""Left dock with the app logo, a collapse toggle and the view navigation tree."""

from PySide6.QtCore import QEasingCurve, QEvent, QPropertyAnimation, QSize, Qt
from PySide6.QtWidgets import QDockWidget, QFrame, QHBoxLayout, QLabel, QMenu, QSizePolicy, QToolButton, QTreeWidget, QVBoxLayout, QWidget

from src.core.navigation_customization import NavigationCustomizationDialog
from src.foundation.asset_paths import icon
from src.foundation.logger_config import logger


class NavigationDock(QDockWidget):
    """Dock that holds the navigation tree and collapses to a narrow logo strip."""

    def __init__(self, gui_instance):
        super().__init__("Navigation", gui_instance)
        self.gui = gui_instance
        self.nav_collapsed = False
        self._nav_animation = None
        self._nav_animation_min = None
        self._init_ui()

    @property
    def nav_tree(self):
        """Return the navigation tree widget."""
        return self._nav_tree

    def _init_ui(self):
        """Build the header, logo toggle and navigation tree, and dock it on the left."""
        self.setObjectName("NavigationDock")
        self.setTitleBarWidget(QWidget())

        # Create all the UI components
        nav_container = QWidget()
        nav_container.setObjectName("NavContainer")
        nav_layout = QVBoxLayout(nav_container)
        nav_layout.setContentsMargins(0, 0, 0, 0)
        nav_layout.setSpacing(0)

        # Header container
        header_widget = QWidget()
        header_widget.setObjectName("NavHeader")
        header_layout = QVBoxLayout(header_widget)
        header_layout.setContentsMargins(4, 6, 4, 4)
        header_layout.setSpacing(4)

        # Logo area
        logo_container = QWidget()
        logo_layout = QHBoxLayout(logo_container)
        logo_layout.setContentsMargins(6, 6, 6, 6)
        logo_layout.setSpacing(0)
        logo_layout.setAlignment(Qt.AlignHCenter)

        # Logo button (collapse toggle)
        self.logo_button = QToolButton()
        self.logo_button.setObjectName("LogoButton")
        self.logo_button.setToolTip("Collapse / Expand Navigation")
        self.logo_button.setCursor(Qt.PointingHandCursor)
        self.logo_button.clicked.connect(self.toggle_navigation)

        # Make it truly square
        button_size = 40
        self.logo_button.setFixedSize(button_size, button_size)
        self.logo_button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.logo_button.setAutoRaise(True)

        # Load splash icon
        splash_icon = icon("splash.png")
        if not splash_icon.isNull():
            self.logo_button.setIcon(splash_icon)
            self.logo_button.setIconSize(QSize(button_size, button_size))

        # Add logo button to layout
        logo_layout.addWidget(self.logo_button, 0, Qt.AlignCenter)

        # App name label
        self.app_name_label = QLabel("TrackYak")
        self.app_name_label.setObjectName("NavAppName")
        self.app_name_label.setAlignment(Qt.AlignCenter)

        # Add widgets to header
        header_layout.addWidget(logo_container)
        header_layout.addWidget(self.app_name_label, 0, Qt.AlignHCenter)

        # Subtle border
        header_border = QFrame()
        header_border.setFrameShape(QFrame.HLine)
        header_border.setFrameShadow(QFrame.Plain)
        header_border.setFixedHeight(1)

        # Navigation tree
        self._nav_tree = QTreeWidget()
        self._nav_tree.setObjectName("NavTree")
        self._nav_tree.setHeaderHidden(True)
        # Tab focus (not click focus) avoids a focus frame on mouse use but keeps keyboard access.
        self._nav_tree.setFocusPolicy(Qt.TabFocus)
        self._nav_tree.installEventFilter(self)

        self._nav_tree.itemClicked.connect(self.gui._switch_view)
        self._nav_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self._nav_tree.customContextMenuRequested.connect(self._show_nav_context_menu)

        # Assemble everything
        nav_layout.addWidget(header_widget)
        nav_layout.addWidget(header_border)
        nav_layout.addWidget(self._nav_tree)

        # Set the main widget
        self.setWidget(nav_container)

        # Configure dock behavior
        self.setMinimumWidth(60)
        self.setMaximumWidth(400)
        self.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetClosable)

        self.gui.addDockWidget(Qt.LeftDockWidgetArea, self)

    def _show_nav_context_menu(self, pos):
        """Right-click menu on the nav tree, offering nav bar customization."""
        menu = QMenu(self)
        customize_action = menu.addAction("Customize Navigation…")
        customize_action.triggered.connect(self._show_navigation_customization_dialog)
        menu.exec_(self._nav_tree.mapToGlobal(pos))

    def _show_navigation_customization_dialog(self):
        """Open the navigation customization dialog."""
        dialog = NavigationCustomizationDialog(self.gui, self)
        dialog.exec_()

    def size_navigation_to_content(self):
        """Size the navigation dock to fit its content."""
        if self.nav_collapsed or self._nav_tree.topLevelItemCount() == 0:
            return

        # Calculate ideal width based on content
        self._nav_tree.resizeColumnToContents(0)
        content_width = self._nav_tree.sizeHintForColumn(0) + 40  # Padding

        # Constrain within reasonable limits
        ideal_width = max(200, min(350, content_width))

        self.resize(ideal_width, self.height())

    def toggle_navigation(self):
        """Toggle between collapsed and expanded navigation states."""
        if not self.isVisible():
            # The dock itself was closed/hidden (e.g. floated and closed),
            # not just collapsed - restore visibility before toggling width.
            self.setFloating(False)
            self.show()
        if self.nav_collapsed:
            self.expand_navigation()
        else:
            self.collapse_navigation()

    def collapse_navigation(self):
        """Collapse the dock to the logo strip with an animation."""
        if self.nav_collapsed:
            return

        self.nav_collapsed = True
        self.app_name_label.setVisible(False)
        self.nav_tree.setVisible(False)
        self.logo_button.setToolTip("Expand navigation")

        # Animate dock width (smooth collapse)
        current_width = self.width()
        target_width = 60

        self._animate_navigation_width(current_width, target_width)

        logger.debug("Navigation collapsed")

    def expand_navigation(self):
        """Expand the dock to show the navigation tree with an animation."""
        if not self.nav_collapsed:
            return

        self.nav_collapsed = False
        self.app_name_label.setVisible(True)
        self.nav_tree.setVisible(True)
        self.logo_button.setToolTip("Collapse navigation")

        # Animate dock width (smooth expand)
        current_width = self.width()
        target_width = 240  # feels good visually, not too wide

        self._animate_navigation_width(current_width, target_width)

        logger.debug("Navigation expanded")

    def eventFilter(self, obj, event):
        """Switch to the current nav item when Enter, Return or Space is pressed in the tree."""
        if obj is getattr(self, "_nav_tree", None) and event.type() == QEvent.KeyPress and event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            item = self._nav_tree.currentItem()
            if item is not None:
                self.gui._switch_view(item)
                return True
        return super().eventFilter(obj, event)

    def ensure_proper_navigation_size(self):
        """Ensure navigation dock has reasonable size after state restoration."""
        if self.nav_collapsed:
            # Ensure collapsed state is maintained
            self.resize(60, self.height())
        else:
            # Normal size constraints for expanded state
            current_width = self.width()
            if current_width > 500:
                self.resize(300, self.height())
            elif current_width < 150:
                self.resize(200, self.height())

    def _animate_navigation_width(self, start_width, end_width, duration=180):
        """Animate the dock width to end_width, then restore the normal width limits."""
        # A fast double toggle must not leave two animations fighting over the width.
        for running in (self._nav_animation, self._nav_animation_min):
            if running is not None:
                running.stop()

        animation = QPropertyAnimation(self, b"maximumWidth")
        animation.setStartValue(start_width)
        animation.setEndValue(end_width)
        animation.setDuration(duration)
        animation.setEasingCurve(QEasingCurve.InOutQuad)

        # Animate minimumWidth too for tighter layout binding
        min_anim = QPropertyAnimation(self, b"minimumWidth")
        min_anim.setStartValue(start_width)
        min_anim.setEndValue(end_width)
        min_anim.setDuration(duration)
        min_anim.setEasingCurve(QEasingCurve.InOutQuad)

        # When finished, restore proper min/max constraints rather than
        # pinning both to end_width (which would prevent manual resizing).
        def finalize_size():
            """Restore the min/max width limits and apply the final width."""
            if self.nav_collapsed:
                self.setMinimumWidth(60)
                self.setMaximumWidth(60)
            else:
                self.setMinimumWidth(60)
                self.setMaximumWidth(400)
            self.resize(end_width, self.height())

        animation.finished.connect(finalize_size)

        # Keep references so animations aren't GC'd
        self._nav_animation = animation
        self._nav_animation_min = min_anim

        animation.start()
        min_anim.start()
