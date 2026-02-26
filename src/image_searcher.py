"""
Image Search Tool
A plug-and-play solution for image search with reliable open-source methods.
"""

import io
import random
import time
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests
from PIL import Image
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QProgressDialog,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.logger_config import logger


class ImageSearch:
    """
    An image search tool focusing on reliable open-source image hosts.
    Returns actual image files instead of URLs.
    """

    def __init__(self, rate_limit_delay: float = 1.0, max_results: int = 50):
        self.rate_limit_delay = rate_limit_delay
        self.max_results = max_results
        self.user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        ]

    def get_headers(self) -> Dict[str, str]:
        """Return random user agent headers to avoid detection."""
        return {
            "User-Agent": random.choice(self.user_agents),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }

    def download_image(self, url: str) -> Optional[Image.Image]:
        """
        Download and validate an image from URL.
        Returns PIL Image object or None if download fails.
        """
        try:
            # Skip known non-image files before downloading
            parsed_url = urlparse(url)
            path = parsed_url.path.lower()

            # Skip PDFs, SVGs, and other non-image formats
            non_image_extensions = {".pdf", ".svg", ".doc", ".docx", ".txt", ".zip"}
            if any(path.endswith(ext) for ext in non_image_extensions):
                logger.debug(f"Skipping non-image file: {url}")
                return None

            session = requests.Session()
            session.headers.update(self.get_headers())

            response = session.get(url, timeout=10)
            response.raise_for_status()

            # Verify content type is an image
            content_type = response.headers.get("content-type", "").lower()
            if not content_type.startswith("image/"):
                logger.warning(
                    f"URL {url} is not an image (content-type: {content_type})"
                )
                return None

            # Skip SVG content types
            if "svg" in content_type:
                logger.debug(f"Skipping SVG image: {url}")
                return None

            # Open image with PIL
            image_data = io.BytesIO(response.content)
            image = Image.open(image_data)

            # Verify it's a valid image
            image.verify()

            # Reset the buffer and reopen (since verify() closes the image)
            image_data.seek(0)
            return Image.open(image_data)

        except Exception as e:
            logger.warning(f"Failed to download image from {url}: {e}")
            return None

    def pil_image_to_bytes(image: Image.Image, format: str = "PNG") -> bytes:
        """Convert PIL Image to bytes for saving."""
        img_byte_arr = io.BytesIO()
        image.save(img_byte_arr, format=format)
        return img_byte_arr.getvalue()

    def search_wikimedia(
        self, search_term: str, **kwargs
    ) -> List[Tuple[str, Image.Image]]:
        """
        Search Wikimedia Commons for images and download them.
        Returns list of (source_url, image) tuples.
        """
        try:
            url = "https://commons.wikimedia.org/w/api.php"

            params = {
                "action": "query",
                "format": "json",
                "generator": "search",
                "gsrnamespace": "6",  # File namespace
                "gsrsearch": search_term,
                "gsrlimit": min(self.max_results, 50),
                "prop": "imageinfo",
                "iiprop": "url|mime",
            }  # Added mime to filter by file type

            session = requests.Session()
            session.headers.update(self.get_headers())

            response = session.get(url, params=params, timeout=10)
            response.raise_for_status()

            data = response.json()
            image_results = []

            if "query" in data and "pages" in data["query"]:
                for page_id, page_data in data["query"]["pages"].items():
                    if "imageinfo" in page_data:
                        for info in page_data["imageinfo"]:
                            if "url" in info:
                                image_url = info["url"]

                                # Filter out non-image files using mime type
                                mime_type = info.get("mime", "").lower()
                                if not mime_type.startswith(
                                    (
                                        "image/jpeg",
                                        "image/png",
                                        "image/gif",
                                        "image/webp",
                                    )
                                ):
                                    logger.debug(
                                        f"Skipping non-image file: {image_url} (mime: {mime_type})"
                                    )
                                    continue

                                # Skip SVG files (PIL has limited SVG support)
                                if (
                                    mime_type == "image/svg+xml"
                                    or image_url.lower().endswith(".svg")
                                ):
                                    logger.debug(f"Skipping SVG file: {image_url}")
                                    continue

                                try:
                                    # Download and convert to PIL Image
                                    image = self.download_image(image_url)
                                    if image:
                                        image_results.append((image_url, image))

                                    # Stop if we have enough results
                                    if len(image_results) >= self.max_results:
                                        break

                                except Exception as e:
                                    logger.warning(
                                        f"Failed to download image from {image_url}: {e}"
                                    )
                                    continue

                    if len(image_results) >= self.max_results:
                        break

            logger.info(f"Wikimedia found {len(image_results)} images")
            return image_results

        except Exception as e:
            logger.warning(f"Wikimedia search failed: {e}")
            return []

    def _is_image_url(self, url: str) -> bool:
        """Check if URL likely points to an image file."""
        image_extensions = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff"}
        parsed_url = urlparse(url)
        path = parsed_url.path.lower()
        return any(path.endswith(ext) for ext in image_extensions)

    def search_images(
        self,
        search_term: str,
        search_types: Optional[List[str]] = None,
        **kwargs,
    ) -> List[Tuple[str, Image.Image]]:
        """
        Main search method that returns actual image files.
        Now searches ALL specified sources instead of stopping when enough results are found.
        """
        if search_types is None:
            search_types = ["wikimedia"]

        all_images = []
        source_counts = {}

        for search_type in search_types:
            logger.info(f"Trying {search_type} search for: {search_term}")

            try:
                if search_type == "wikimedia":
                    images = self.search_wikimedia(search_term, **kwargs)
                else:
                    continue

                source_counts[search_type] = len(images)
                all_images.extend(images)

                # Rate limiting between different search sources
                time.sleep(self.rate_limit_delay)

            except Exception as e:
                logger.error(f"Search type {search_type} failed: {e}")
                source_counts[search_type] = 0
                continue

        # Remove duplicates based on URL and limit results
        seen_urls = set()
        unique_images = []

        for url, image in all_images:
            if url not in seen_urls:
                seen_urls.add(url)
                unique_images.append((url, image))

        # Store source counts for display
        self.last_search_source_counts = source_counts

        return unique_images[: self.max_results]


# Convenience function
def image_search(
    search_term: str,
    max_results: int = 50,
    **kwargs,
) -> List[Tuple[str, Image.Image]]:
    """
    Convenience function for quick image searches.

    Args:
        search_term: Term to search for
        max_results: Maximum number of results to return (increased to 50)
        music_context: Whether this is music-related content
        search_types: Which search methods to use
        **kwargs: Additional parameters

    Returns:
        List of (source_url, image) tuples where image is a PIL Image object
    """
    searcher = ImageSearch(max_results=max_results)
    return searcher.search_images(
        search_term=search_term,
        **kwargs,
    )


def open_image_selection_dialog(
    controller, entity_type, entity_id, entity_name, search_term, parent=None
):
    """
    Convenience function to open image search and selection dialog.
    """
    # Create and start worker thread for initial search
    search_worker = SearchWorker(
        search_term=search_term,
        search_types=["wikimedia"],
        max_results=50,
        music_context=(entity_type != "publisher"),
    )

    progress_dialog = QProgressDialog("Searching for images...", "Cancel", 0, 0, parent)
    progress_dialog.setWindowTitle("Image Search")
    progress_dialog.setModal(True)
    progress_dialog.setMinimumDuration(0)  # Show immediately

    result_container = {"result": None, "selected_image": None}

    def on_search_finished(results, source_counts):
        """Handle completed search."""
        progress_dialog.close()

        if not results:
            QMessageBox.information(
                parent, "No Results", "No images found for the search term."
            )
            result_container["result"] = QDialog.Rejected
            return

        # Create and show dialog
        dialog = ImageSelectionDialog(
            controller, entity_name, entity_id, results, source_counts, parent
        )
        result = dialog.exec_()

        result_container["result"] = result
        result_container["selected_image"] = dialog.selected_image

    def on_search_error(error_message):
        """Handle search error."""
        progress_dialog.close()
        QMessageBox.warning(parent, "Search Error", f"Search failed: {error_message}")
        result_container["result"] = QDialog.Rejected

    def on_cancel():
        """Handle cancel."""
        search_worker.stop()  # Use safe stop instead of terminate
        result_container["result"] = QDialog.Rejected

    # Connect signals
    search_worker.search_finished.connect(on_search_finished)
    search_worker.search_error.connect(on_search_error)
    progress_dialog.canceled.connect(on_cancel)

    # Start the search
    search_worker.start()

    # Show progress dialog and wait
    progress_dialog.exec_()

    return result_container["result"], result_container["selected_image"]


class ImageSelectionDialog(QDialog):
    """Generic dialog for selecting images from search results with pagination."""

    def __init__(
        self,
        controller,
        entity_name,
        entity_id,
        search_results,
        source_counts,
        parent=None,
    ):
        super().__init__(parent)
        self.controller = controller
        self.entity_name = entity_name
        self.entity_id = entity_id
        self.all_search_results = search_results  # All results
        self.source_counts = source_counts  # Results count by source
        self.selected_image = None
        self.current_page = 0
        self.results_per_page = 10
        self.init_ui()

    def init_ui(self):
        """Initialize the image selection dialog UI."""
        self.setWindowTitle(f"Select Image for {self.entity_name}")
        self.setModal(True)
        self.setMinimumSize(800, 600)

        layout = QVBoxLayout(self)

        # Search bar for refining search
        search_layout = QHBoxLayout()
        search_layout.addWidget(QLabel("Search:"))
        self.search_edit = QLineEdit(self.entity_name)
        self.search_edit.returnPressed.connect(self.search_again)
        search_layout.addWidget(self.search_edit)

        self.search_btn = QPushButton("Search Again")
        self.search_btn.clicked.connect(self.search_again)
        search_layout.addWidget(self.search_btn)

        layout.addLayout(search_layout)

        # Results info and pagination controls
        info_layout = QHBoxLayout()

        # Results count by source
        self.results_info = QLabel("")
        info_layout.addWidget(self.results_info)

        info_layout.addStretch()

        # Pagination controls
        self.prev_btn = QPushButton("◀ Previous")
        self.prev_btn.clicked.connect(self.previous_page)
        self.prev_btn.setEnabled(False)
        info_layout.addWidget(self.prev_btn)

        self.page_info = QLabel("Page 1")
        info_layout.addWidget(self.page_info)

        self.next_btn = QPushButton("Next ▶")
        self.next_btn.clicked.connect(self.next_page)
        self.next_btn.setEnabled(len(self.all_search_results) > self.results_per_page)
        info_layout.addWidget(self.next_btn)

        layout.addLayout(info_layout)

        # Progress bar (initially hidden)
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        # Results grid
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.grid_widget = QWidget()
        self.grid_layout = QGridLayout(self.grid_widget)
        self.scroll_area.setWidget(self.grid_widget)
        layout.addWidget(self.scroll_area)

        # Buttons
        button_layout = QHBoxLayout()
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(self.cancel_btn)

        button_layout.addStretch()

        layout.addLayout(button_layout)

        self.update_results_info()
        self.display_current_page()

    def update_results_info(self):
        """Update the results information display."""
        total_results = len(self.all_search_results)

        # Build source counts string
        source_info = []
        for source, count in self.source_counts.items():
            source_info.append(f"{source}: {count}")

        if source_info:
            sources_text = " | ".join(source_info)
            self.results_info.setText(
                f"Total: {total_results} results ({sources_text})"
            )
        else:
            self.results_info.setText(f"Total: {total_results} results")

    def display_current_page(self):
        """Display results for the current page."""
        # Clear previous results
        for i in reversed(range(self.grid_layout.count())):
            widget = self.grid_layout.itemAt(i).widget()
            if widget:
                widget.setParent(None)

        if not self.all_search_results:
            no_results = QLabel("No images found. Try a different search term.")
            no_results.setAlignment(Qt.AlignCenter)
            self.grid_layout.addWidget(no_results, 0, 0)
            return

        # Get results for current page
        start_idx = self.current_page * self.results_per_page
        end_idx = start_idx + self.results_per_page
        page_results = self.all_search_results[start_idx:end_idx]

        # Calculate columns based on available width
        columns = max(3, self.width() // 180)

        for idx, (url, image_bytes) in enumerate(page_results):
            row = idx // columns
            col = idx % columns

            # Create thumbnail widget
            thumbnail = self.create_thumbnail(url, image_bytes, idx)
            self.grid_layout.addWidget(thumbnail, row, col, Qt.AlignTop)

        # Update page info
        total_pages = (
            len(self.all_search_results) + self.results_per_page - 1
        ) // self.results_per_page
        self.page_info.setText(f"Page {self.current_page + 1} of {total_pages}")

        # Update pagination buttons
        self.prev_btn.setEnabled(self.current_page > 0)
        self.next_btn.setEnabled(
            (self.current_page + 1) * self.results_per_page
            < len(self.all_search_results)
        )

    def create_thumbnail(self, url: str, image_data, index: int) -> QWidget:
        """Create a thumbnail widget for an image."""
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(5, 5, 5, 5)

        # Convert PIL Image to bytes if needed
        try:
            if isinstance(image_data, Image.Image):
                # Convert PIL Image to bytes
                img_byte_arr = io.BytesIO()
                image_data.save(img_byte_arr, format="PNG")
                img_byte_arr = img_byte_arr.getvalue()
                # Store the bytes for later use
                self.all_search_results[index] = (url, img_byte_arr)
            else:
                img_byte_arr = image_data

            pixmap = QPixmap()
            pixmap.loadFromData(img_byte_arr)

            # Scale to thumbnail size
            scaled_pixmap = pixmap.scaled(
                150, 150, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )

            image_label = QLabel()
            image_label.setPixmap(scaled_pixmap)
            image_label.setAlignment(Qt.AlignCenter)
            image_label.setToolTip(f"Source: {url}")

            # Make clickable
            image_label.mousePressEvent = lambda event, idx=index: self.select_image(
                idx
            )

            layout.addWidget(image_label)

        except Exception as e:
            error_label = QLabel("Error loading image")
            error_label.setAlignment(Qt.AlignCenter)
            layout.addWidget(error_label)
            logger.warning(f"Failed to create thumbnail for image {index}: {e}")

        return container

    def select_image(self, index: int):
        """Handle image selection."""
        start_idx = self.current_page * self.results_per_page
        actual_index = start_idx + index

        if 0 <= actual_index < len(self.all_search_results):
            url, image_data = self.all_search_results[actual_index]

            # Ensure image data is in bytes format
            if isinstance(image_data, Image.Image):
                img_byte_arr = io.BytesIO()
                image_data.save(img_byte_arr, format="PNG")
                image_data_bytes = img_byte_arr.getvalue()
                self.selected_image = (url, image_data_bytes)
            else:
                self.selected_image = (url, image_data)

            self.accept()

    def update_pagination_buttons(self):
        """Update pagination buttons state."""
        self.prev_btn.setEnabled(self.current_page > 0)
        self.next_btn.setEnabled(
            (self.current_page + 1) * self.results_per_page
            < len(self.all_search_results)
        )

    def previous_page(self):
        """Go to previous page of results."""
        if self.current_page > 0:
            self.current_page -= 1
            self.display_current_page()

    def next_page(self):
        """Go to next page of results."""
        if (self.current_page + 1) * self.results_per_page < len(
            self.all_search_results
        ):
            self.current_page += 1
            self.display_current_page()

    def search_again(self):
        """Perform a new search with the updated search term using background thread."""
        search_term = self.search_edit.text().strip()
        if not search_term:
            return

        # Stop any existing search worker
        if hasattr(self, "search_worker") and self.search_worker.isRunning():
            self.search_worker.stop()

        # Show progress bar
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 100)  # Set to 0-100 for percentage
        self.progress_bar.setValue(0)
        self.search_btn.setEnabled(False)
        self.search_btn.setText("Searching...")

        # Disable pagination during search
        self.prev_btn.setEnabled(False)
        self.next_btn.setEnabled(False)

        # Clear current results
        for i in reversed(range(self.grid_layout.count())):
            widget = self.grid_layout.itemAt(i).widget()
            if widget:
                widget.setParent(None)

        # Show searching message
        searching_label = QLabel("Searching... Please wait.")
        searching_label.setAlignment(Qt.AlignCenter)
        self.grid_layout.addWidget(searching_label, 0, 0)

        # Create and start worker thread
        self.search_worker = SearchWorker(
            search_term=search_term,
            search_types=["wikimedia"],
            max_results=50,
            music_context=True,
        )
        self.search_worker.search_finished.connect(self.on_search_finished)
        self.search_worker.search_error.connect(self.on_search_error)
        self.search_worker.progress_update.connect(self.on_search_progress)
        self.search_worker.start()

    def on_search_progress(self, message, progress):
        """Update progress during search."""
        self.progress_bar.setValue(progress)

    def on_search_finished(self, results, source_counts):
        """Handle completed search."""
        self.all_search_results = results
        self.source_counts = source_counts
        self.current_page = 0  # Reset to first page

        self.progress_bar.setVisible(False)
        self.search_btn.setEnabled(True)
        self.search_btn.setText("Search Again")

        self.update_results_info()
        self.display_current_page()

    def on_search_error(self, error_message):
        """Handle search error."""
        self.progress_bar.setVisible(False)
        self.search_btn.setEnabled(True)
        self.search_btn.setText("Search Again")

        # Show error message in results area
        for i in reversed(range(self.grid_layout.count())):
            widget = self.grid_layout.itemAt(i).widget()
            if widget:
                widget.setParent(None)

        error_label = QLabel(f"Search failed: {error_message}")
        error_label.setAlignment(Qt.AlignCenter)
        self.grid_layout.addWidget(error_label, 0, 0)

        QMessageBox.warning(self, "Search Error", f"Search failed: {error_message}")

        # Re-enable pagination for previous results if any
        if self.all_search_results:
            self.update_pagination_buttons()


class SearchWorker(QThread):
    """Worker thread for performing image searches."""

    search_finished = Signal(list, dict)  # (results, source_counts)
    search_error = Signal(str)
    progress_update = Signal(str, int)  # (source_name, progress_percentage)

    def __init__(self, search_term, search_types, max_results=50, music_context=False):
        super().__init__()
        self.search_term = search_term
        self.search_types = search_types
        self.max_results = max_results
        self.music_context = music_context
        self.searcher = ImageSearch(max_results=max_results)
        self._is_running = True  # Add flag to control thread execution

    def run(self):
        try:
            all_images = []
            source_counts = {}
            total_sources = len(self.search_types)

            for idx, search_type in enumerate(self.search_types):
                # Check if thread should stop
                if not self._is_running:
                    return

                # Emit progress update
                progress = int((idx / total_sources) * 100)
                self.progress_update.emit(f"Searching {search_type}...", progress)

                logger.info(f"Trying {search_type} search for: {self.search_term}")

                try:
                    if search_type == "wikimedia":
                        images = self.searcher.search_wikimedia(self.search_term)
                    else:
                        continue

                    source_counts[search_type] = len(images)

                    # Convert PIL Images to bytes before storing
                    for url, pil_image in images:
                        if isinstance(pil_image, Image.Image):
                            img_byte_arr = io.BytesIO()
                            pil_image.save(img_byte_arr, format="PNG")
                            image_bytes = img_byte_arr.getvalue()
                            all_images.append((url, image_bytes))
                        else:
                            all_images.append((url, pil_image))

                    # Rate limiting between different search sources
                    if self._is_running:  # Only sleep if still running
                        time.sleep(self.searcher.rate_limit_delay)

                except Exception as e:
                    logger.error(f"Search type {search_type} failed: {e}")
                    source_counts[search_type] = 0
                    continue

            # Check if thread should stop before processing results
            if not self._is_running:
                return

            # Remove duplicates based on URL and limit results
            seen_urls = set()
            unique_images = []

            for url, image_bytes in all_images:
                if url not in seen_urls:
                    seen_urls.add(url)
                    unique_images.append((url, image_bytes))

            # Final progress update
            self.progress_update.emit("Processing results...", 100)

            # Store source counts for display
            self.searcher.last_search_source_counts = source_counts

            self.search_finished.emit(unique_images[: self.max_results], source_counts)

        except Exception as e:
            self.search_error.emit(str(e))

    def stop(self):
        """Safely stop the thread."""
        self._is_running = False
        self.quit()
        self.wait(1000)  # Wait up to 1 second for thread to finish
