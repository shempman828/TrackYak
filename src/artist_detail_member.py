# file: artist_detail_membership.py
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)


class MembershipWidget(QWidget):
    """Membership section widget - shows members for groups or groups for individuals"""

    def __init__(self, artist: Any):
        super().__init__()
        self.artist = artist
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)

        # Get membership data based on artist type
        is_group = getattr(self.artist, "isgroup", 0) == 1

        if is_group:
            self._setup_group_ui(layout)
        else:
            self._setup_individual_ui(layout)

    def _setup_group_ui(self, layout: QVBoxLayout):
        """Setup UI for group artists - show members"""
        memberships = getattr(self.artist, "group_memberships", [])

        if not memberships:
            no_members_label = QLabel("No member information available.")
            no_members_label.setObjectName("NoMembersLabel")
            no_members_label.setAlignment(Qt.AlignCenter)
            layout.addWidget(no_members_label)
            return

        # Title
        title_label = QLabel("Group Members")
        title_label.setObjectName("SectionTitle")
        layout.addWidget(title_label)

        # Separator
        separator = QFrame()
        separator.setFrameShape(QFrame.HLine)
        separator.setFrameShadow(QFrame.Sunken)
        separator.setMaximumHeight(1)
        layout.addWidget(separator)

        # Create grid layout for members
        grid_layout = QGridLayout()
        grid_layout.setHorizontalSpacing(24)
        grid_layout.setVerticalSpacing(8)

        # Headers
        headers = ["Member", "Role", "Active Years", "Status"]
        for col, header in enumerate(headers):
            header_label = QLabel(header)
            header_label.setObjectName("TableHeader")
            header_label.setAlignment(Qt.AlignLeft)
            grid_layout.addWidget(header_label, 0, col)

        # Add members
        for row, membership in enumerate(memberships, start=1):
            member = membership.member

            # Member name with link functionality
            member_name = getattr(member, "artist_name", "Unknown")
            name_label = QLabel(member_name)
            name_label.setObjectName("MemberName")
            name_label.setCursor(Qt.PointingHandCursor)
            name_label.setToolTip(f"Click to view {member_name}")
            grid_layout.addWidget(name_label, row, 0)

            # Role
            role = getattr(membership, "role", "")
            role_label = QLabel(role if role else "-")
            role_label.setObjectName("MemberRole")
            grid_layout.addWidget(role_label, row, 1)

            # Active years
            start_year = getattr(membership, "active_start_year", "")
            end_year = getattr(membership, "active_end_year", "")

            if start_year and end_year:
                years_text = f"{start_year}–{end_year}"
            elif start_year:
                years_text = f"{start_year}–"
            elif end_year:
                years_text = f"–{end_year}"
            else:
                years_text = "-"

            years_label = QLabel(years_text)
            years_label.setObjectName("MemberYears")
            grid_layout.addWidget(years_label, row, 2)

            # Status
            is_current = getattr(membership, "is_current", 0) == 1
            status_text = "Current" if is_current else "Former"
            status_label = QLabel(status_text)
            status_label.setObjectName(
                f"MemberStatus{'Current' if is_current else 'Former'}"
            )
            grid_layout.addWidget(status_label, row, 3)

        layout.addLayout(grid_layout)

    def _setup_individual_ui(self, layout: QVBoxLayout):
        """Setup UI for individual artists - show groups they're part of"""
        memberships = getattr(self.artist, "member_memberships", [])

        if not memberships:
            no_groups_label = QLabel("No group membership information available.")
            no_groups_label.setObjectName("NoGroupsLabel")
            no_groups_label.setAlignment(Qt.AlignCenter)
            layout.addWidget(no_groups_label)
            return

        # Title
        title_label = QLabel("Group Memberships")
        title_label.setObjectName("SectionTitle")
        layout.addWidget(title_label)

        # Separator
        separator = QFrame()
        separator.setFrameShape(QFrame.HLine)
        separator.setFrameShadow(QFrame.Sunken)
        separator.setMaximumHeight(1)
        layout.addWidget(separator)

        # Create grid layout for groups
        grid_layout = QGridLayout()
        grid_layout.setHorizontalSpacing(24)
        grid_layout.setVerticalSpacing(8)

        # Headers
        headers = ["Group", "Role", "Active Years", "Status"]
        for col, header in enumerate(headers):
            header_label = QLabel(header)
            header_label.setObjectName("TableHeader")
            header_label.setAlignment(Qt.AlignLeft)
            grid_layout.addWidget(header_label, 0, col)

        # Add groups
        for row, membership in enumerate(memberships, start=1):
            group = membership.group

            # Group name with link functionality
            group_name = getattr(group, "artist_name", "Unknown")
            name_label = QLabel(group_name)
            name_label.setObjectName("GroupName")
            name_label.setCursor(Qt.PointingHandCursor)
            name_label.setToolTip(f"Click to view {group_name}")
            # Note: You'll need to connect this to an artist detail view
            # name_label.mousePressEvent = lambda e, artist=group: self.view_artist(artist)
            grid_layout.addWidget(name_label, row, 0)

            # Role
            role = getattr(membership, "role", "")
            role_label = QLabel(role if role else "-")
            role_label.setObjectName("GroupRole")
            grid_layout.addWidget(role_label, row, 1)

            # Active years
            start_year = getattr(membership, "active_start_year", "")
            end_year = getattr(membership, "active_end_year", "")

            if start_year and end_year:
                years_text = f"{start_year}–{end_year}"
            elif start_year:
                years_text = f"{start_year}–"
            elif end_year:
                years_text = f"–{end_year}"
            else:
                years_text = "-"

            years_label = QLabel(years_text)
            years_label.setObjectName("GroupYears")
            grid_layout.addWidget(years_label, row, 2)

            # Status
            is_current = getattr(membership, "is_current", 0) == 1
            status_text = "Current" if is_current else "Former"
            status_label = QLabel(status_text)
            status_label.setObjectName(
                f"GroupStatus{'Current' if is_current else 'Former'}"
            )
            grid_layout.addWidget(status_label, row, 3)

        layout.addLayout(grid_layout)
