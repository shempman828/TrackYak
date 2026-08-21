from PySide6.QtCore import QEasingCurve, QEvent, QPropertyAnimation, QSize, Qt
from PySide6.QtWidgets import (
    QApplication,
    QDockWidget,
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QToolButton,
    QTreeWidget,
    QVBoxLayout,
    QWidget,
)

from src.core.asset_paths import icon
from src.core.logger_config import logger


class NavigationDock(QDockWidget):
    """navigation dock widget"""

    def __init__(self, gui_instance):
        super().__init__("", gui_instance)
        self.gui = gui_instance
        self.nav_collapsed = False
        self.nav_auto_collapse = False
        self._init_ui()

    @property
    def nav_tree(self):
        """Provide access to the navigation tree from the GUI"""
        return self._nav_tree

    def _init_ui(self):
        """Initialize the navigation dock UI"""
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
        self.nav_tree.setFocusPolicy(Qt.NoFocus)  # removes annoying focus styling

        self._nav_tree.itemClicked.connect(self.gui._switch_view)

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

        # Add to main window
        self.gui.addDockWidget(Qt.LeftDockWidgetArea, self)
        # Watch the main window's own resize, not this dock's -- a sibling
        # dock claiming/releasing space resizes us too, which isn't the same
        # signal as the user actually shrinking the window.
        self.gui.installEventFilter(self)

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

    def _set_initial_navigation_size(self):
        """Set initial navigation size based on screen size.
        Call this after the main window is shown, not during construction."""
        screen = QApplication.primaryScreen()
        screen_width = screen.availableGeometry().width()

        # Auto-collapse on small screens
        if screen_width < 1366:  # HD ready or smaller
            self.nav_auto_collapse = True
            self.collapse_navigation()
        else:
            self.nav_auto_collapse = False
            self.expand_navigation()

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
        """Collapse the navigation to icon-only mode with animation."""
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
        """Expand the navigation to show full content with animation."""
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
        """Handle resize events for responsive navigation.

        Bound to the main window's resize event (not this dock's own) so
        that a sibling dock's visibility change can never be mistaken for
        the user shrinking the window -- see _set_initial_navigation_size
        for the matching 1366px "small screen" breakpoint.
        """
        if obj is not None and obj == getattr(self, "gui", None) and event.type() == QEvent.Resize:
            # Auto-collapse/expand based on available width
            if not self.nav_auto_collapse:
                return False

            new_width = event.size().width()
            if new_width < 1366 and not self.nav_collapsed:
                self.collapse_navigation()
            elif new_width >= 1416 and self.nav_collapsed:
                self.expand_navigation()

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
        """Animate dock width smoothly and ensure it resizes to final width."""
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
            if self.nav_collapsed:
                self.setMinimumWidth(60)
                self.setMaximumWidth(60)
            else:
                self.setMinimumWidth(60)
                self.setMaximumWidth(400)
            self.resize(end_width, self.height())

        animation.finished.connect(finalize_size)

        # Keep references so animations aren’t GC’d
        self._nav_animation = animation
        self._nav_animation_min = min_anim

        animation.start()
        min_anim.start()
