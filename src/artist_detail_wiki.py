from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)


class WikipediaImportDialog(QDialog):
    """Dialog for selecting which Wikipedia information to import"""

    def __init__(self, title, summary, link, images, parent=None):
        super().__init__(parent)
        self.title = title
        self.summary = summary
        self.link = link
        self.images = images

        self.updates = {}
        self.init_ui()

    def init_ui(self):
        """Initialize dialog UI"""
        self.setWindowTitle("Import Wikipedia Information")
        self.setModal(True)
        layout = QVBoxLayout(self)

        # Title
        title_label = QLabel(f"<b>{self.title}</b>")
        title_label.setWordWrap(True)
        layout.addWidget(title_label)

        # Summary preview
        summary_label = QLabel("Summary preview:")
        layout.addWidget(summary_label)

        summary_text = QTextEdit()
        summary_text.setMaximumHeight(150)
        summary_text.setText(
            self.summary[:500] + "..." if len(self.summary) > 500 else self.summary
        )
        summary_text.setReadOnly(True)
        layout.addWidget(summary_text)

        # Import options
        options_group = QGroupBox("Import Options")
        options_layout = QVBoxLayout()

        # Biography checkbox
        self.bio_check = QLabel(
            f"<input type='checkbox' checked> Import biography (first {min(1000, len(self.summary))} characters)"
        )
        options_layout.addWidget(self.bio_check)

        # Wikipedia link checkbox
        self.link_check = QLabel(
            "<input type='checkbox' checked> Import Wikipedia link"
        )
        options_layout.addWidget(self.link_check)

        # Profile picture checkbox (if images available)
        self.image_check = None
        if self.images:
            self.image_check = QLabel(
                "<input type='checkbox'> Import profile picture from Wikipedia"
            )
            options_layout.addWidget(self.image_check)

        options_group.setLayout(options_layout)
        layout.addWidget(options_group)

        # Image selection button (if images available)
        self.image_select_btn = None
        self.selected_image_url = None
        if self.images:
            self.image_select_btn = QPushButton("Select Image...")
            self.image_select_btn.clicked.connect(self.select_image)
            layout.addWidget(self.image_select_btn)

        # Button box
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

        self.resize(500, 400)

    def select_image(self):
        """Open image selection dialog"""
        from wikipedia_seach import select_wikipedia_image

        selected_url = select_wikipedia_image(self.images, self)
        if selected_url:
            self.selected_image_url = selected_url
            # Show short preview of selected image URL
            filename = selected_url.split("/")[-1][:30]
            self.image_select_btn.setText(f"Selected: {filename}...")

    def get_updates(self):
        """Get updates to apply based on user selection"""
        updates = {}

        # Always import link if checked
        updates["wikipedia_link"] = self.link

        # Import biography if checked
        if True:  # We'll always import bio for now
            updates["biography"] = self.summary[:1000]  # Limit length

        # Import image if selected
        if self.selected_image_url:
            # Download and save image locally
            from wikipedia_seach import download_wikipedia_image

            image_bytes = download_wikipedia_image(self.selected_image_url)

            if image_bytes:
                # Save to local file (simplified - in practice would use proper path)
                import os
                from datetime import datetime

                # Create artist_images directory if it doesn't exist
                os.makedirs("artist_images", exist_ok=True)

                # Generate filename
                ext = self.selected_image_url.split(".")[-1].split("?")[0]
                if ext.lower() not in ["jpg", "jpeg", "png", "gif", "webp"]:
                    ext = "jpg"

                filename = f"artist_{int(datetime.now().timestamp())}.{ext}"
                filepath = os.path.join("artist_images", filename)

                # Save image
                with open(filepath, "wb") as f:
                    f.write(image_bytes)

                updates["profile_pic_path"] = filepath

        return updates
