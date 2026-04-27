"""This code searches Wikipedia for a given query and returns the choices for user selection.
Then, it fetches the summary and link of the selected choice."""

from typing import List, Optional, Tuple

import requests
import wikipedia

# Qt imports
from PySide6.QtCore import QObject, QThread
from PySide6.QtCore import Signal
from PySide6.QtCore import Signal as _Signal
from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.logger_config import logger
from src.wikipedia_image_dialog import WikipediaImageDialog

# Configure Wikipedia library
wikipedia.set_lang("en")  # Set default language to English
wikipedia.set_rate_limiting(
    True
)  # Enable rate limiting to be polite to Wikipedia's servers


class WikipediaSearch:
    """Performs a Wikipedia search and retrieves selected page details."""

    def __init__(self, query: str) -> None:
        self.query = query
        self.choices: List[wikipedia.WikipediaPage] = []
        self.selected_choice: Optional[wikipedia.WikipediaPage] = None
        self.summary: str = ""
        self.full_content: str = ""
        self.images: List[str] = []
        self.link: str = ""
        self.error: Optional[str] = None

    def search(self) -> List[str]:
        """
        Search Wikipedia for the query and store matching pages.

        Returns:
            A list of page titles for possible matches, or an empty list if none found.
        """
        try:
            search_results = wikipedia.search(self.query, results=10)
            self.choices.clear()

            for title in search_results:
                try:
                    page = wikipedia.page(title, auto_suggest=False)
                    self.choices.append(page)
                except wikipedia.exceptions.DisambiguationError as e:
                    # Attempt first disambiguation option
                    try:
                        page = wikipedia.page(e.options[0], auto_suggest=False)
                        self.choices.append(page)
                    except Exception:
                        logger.debug(f"Skipped disambiguation: {title}")
                except Exception:
                    logger.debug(f"Skipped result: {title}")

            return [p.title for p in self.choices]

        except wikipedia.exceptions.WikipediaException as e:
            self.error = f"Wikipedia search error: {e}"
            logger.exception(f"Wikipedia search error for '{self.query}': {e}")
        except Exception as e:
            self.error = f"Unexpected error: {e}"
            logger.exception(f"Unexpected error during search for '{self.query}': {e}")
        return []

    def select_choice(self, index: int) -> bool:
        """Select a result by index and load its summary, content, images, and URL."""
        if not (0 <= index < len(self.choices)):
            self.error = f"Invalid choice index: {index}"
            return False

        try:
            page = self.choices[index]
            self.selected_choice = page
            self.summary = page.summary
            self.full_content = page.content
            self.images = page.images

            # Filter images to only include common image formats
            self.images = [
                img
                for img in self.images
                if img.lower().endswith((".jpg", ".jpeg", ".png", ".gif", ".webp"))
            ]

            self.link = page.url
            return True
        except Exception as e:
            self.error = f"Error fetching page details: {e}"
            logger.exception(f"Error fetching page details for index {index}: {e}")
            return False


class WikipediaSearchWorker(QObject):
    """QThread worker that performs Wikipedia searches and emits results."""

    search_finished = Signal(list, str)  # results, error
    selection_finished = Signal(
        str, str, str, str, list
    )  # title, summary, content, link, images

    def __init__(self) -> None:
        super().__init__()
        self.current_search: Optional[WikipediaSearch] = None

    def perform_search(self, query: str) -> None:
        """Run a Wikipedia search and emit results."""
        try:
            search = WikipediaSearch(query)
            results = search.search()
            self.current_search = search
            self.search_finished.emit(results, search.error or "")
        except Exception as e:
            msg = f"Search error: {e}"
            logger.exception(msg)
            self.search_finished.emit([], msg)

    def select_result(self, index: int) -> None:
        """Select a specific result and emit detailed info."""
        if not self.current_search:
            self.selection_finished.emit("", "", "", "No active search", [])
            return

        if self.current_search.select_choice(index):
            choice = self.current_search.selected_choice
            self.selection_finished.emit(
                choice.title,
                self.current_search.summary,
                self.current_search.full_content,
                self.current_search.link,
                self.current_search.images,
            )
        else:
            self.selection_finished.emit(
                "", "", "", self.current_search.error or "Selection failed", []
            )


class _SelectRequester(QObject):
    """Tiny helper that lives on the worker thread and triggers select_result."""

    do_select = _Signal(int)


class WikipediaDialog(QDialog):
    """Dialog for searching and selecting Wikipedia articles."""

    _do_search = _Signal(str)

    def __init__(self, query: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.query = query
        self.selected_title = ""
        self.selected_summary = ""
        self.selected_full_content = ""
        self.selected_link = ""
        self.selected_images: List[str] = []

        # Single worker + thread that live for the dialog's lifetime
        self._thread = QThread(self)
        self._worker = WikipediaSearchWorker()
        self._worker.moveToThread(self._thread)

        # Connect worker signals → dialog slots (cross-thread, queued automatically)
        self._worker.search_finished.connect(self._on_search_finished)
        self._worker.selection_finished.connect(self._on_selection_finished)

        # Helper that lets us invoke select_result on the worker thread via signal
        self._requester = _SelectRequester()
        self._requester.moveToThread(self._thread)
        self._requester.do_select.connect(self._worker.select_result)

        # Clean up thread on dialog close
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._requester.deleteLater)
        self.finished.connect(self._stop_thread)

        self._thread.start()

        self._init_ui()
        self._start_search()

    def _stop_thread(self) -> None:
        self._thread.quit()
        self._thread.wait()

    def _init_ui(self) -> None:
        """Initialize dialog layout and widgets."""
        self.setWindowTitle(f"Wikipedia Search: {self.query}")
        self.resize(600, 400)

        layout = QVBoxLayout(self)

        self.title_label = QLabel(f"Searching for: {self.query}")
        layout.addWidget(self.title_label)

        self.results_list = QListWidget()
        self.results_list.itemDoubleClicked.connect(self._accept_selection)
        layout.addWidget(self.results_list)

        button_layout = QVBoxLayout()
        self.select_button = QPushButton("Select")
        self.select_button.setEnabled(False)
        self.select_button.clicked.connect(self._accept_selection)
        button_layout.addWidget(self.select_button)

        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)
        button_layout.addWidget(cancel_button)

        layout.addLayout(button_layout)

    def _start_search(self) -> None:
        """Invoke perform_search on the worker thread via a queued signal."""
        self.title_label.setText(f"Searching Wikipedia for: {self.query}...")
        self._do_search.connect(self._worker.perform_search)
        self._do_search.emit(self.query)

    def _on_search_finished(self, results: List[str], error: str) -> None:
        """Populate results or show error."""
        if error:
            QMessageBox.critical(
                self, "Search Error", f"Error searching Wikipedia:\n{error}"
            )
            self.reject()
            return

        if not results:
            self.title_label.setText("No results found.")
            return

        self.results_list.clear()
        self.results_list.addItems(results)
        self.title_label.setText(f"Found {len(results)} results for: {self.query}")
        self.select_button.setEnabled(True)

    def _on_selection_finished(
        self, title: str, summary: str, full_content: str, link: str, images: List[str]
    ) -> None:
        """Handle completion of a result selection."""
        self.setEnabled(True)

        if not title:
            QMessageBox.warning(
                self, "Selection Error", f"Error fetching details:\n{link}"
            )
            return

        self.selected_title = title
        self.selected_summary = summary
        self.selected_full_content = full_content
        self.selected_link = link
        self.selected_images = images
        self.accept()

    def _accept_selection(self) -> None:
        """Trigger selection of the current result on the worker thread."""
        current_row = self.results_list.currentRow()
        if current_row == -1:
            QMessageBox.warning(self, "Selection", "Please select a result first.")
            return

        self.title_label.setText("Fetching article details...")
        self.setEnabled(False)

        # Emit through the requester — it lives on the worker thread, so
        # select_result runs there without any moveToThread gymnastics.
        self._requester.do_select.emit(current_row)

    def get_selection(self) -> Tuple[str, str, str, str, List[str]]:
        """Return details of the selected Wikipedia page."""
        return (
            self.selected_title,
            self.selected_summary,
            self.selected_full_content,
            self.selected_link,
            self.selected_images,
        )


# Utility function for easy usage
def search_wikipedia(query: str, parent=None) -> Tuple[str, str, str, str, List[str]]:
    """
    Convenience function to search Wikipedia and return results
    Returns: (title, summary, full_content, link, images) or empty strings if cancelled/errored
    """
    dialog = WikipediaDialog(query, parent)
    result = dialog.exec_()

    if result == QDialog.Accepted:
        return dialog.get_selection()
    else:
        return "", "", "", "", []


def select_wikipedia_image(images: List[str], parent=None) -> str:
    """
    Open a dialog to select from Wikipedia images with previews.
    Returns the selected image URL or empty string if cancelled.
    """
    if not images:
        return ""

    dialog = WikipediaImageDialog(images, parent)
    if dialog.exec_() == QDialog.Accepted:
        return dialog.get_selected_image() or ""
    return ""


def download_wikipedia_image(image_url: str) -> Optional[bytes]:
    """
    Download a Wikipedia image with proper headers to avoid 403 errors.
    Returns image bytes or None if download fails.
    """
    try:
        # Use the same headers as in the preview dialog
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/91.0.4472.124 Safari/537.36"
            ),
            "Accept": "image/webp,image/*,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Accept-Encoding": "gzip, deflate, br",
            "DNT": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "image",
            "Sec-Fetch-Mode": "no-cors",
            "Sec-Fetch-Site": "cross-site",
        }

        # Some Wikipedia images may require using the original URL format
        # Try direct access first, then try with referrer if needed
        response = requests.get(image_url, timeout=30, headers=headers)
        response.raise_for_status()

        # If we get a 403, try with a referrer
        if response.status_code == 403:
            headers["Referer"] = "https://en.wikipedia.org/"
            response = requests.get(image_url, timeout=30, headers=headers)
            response.raise_for_status()

        return response.content

    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to download Wikipedia image {image_url}: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error downloading image {image_url}: {e}")
        return None


def search_and_select_wikipedia_image(
    query: str, parent=None
) -> Tuple[Optional[str], Optional[bytes]]:
    """
    Complete workflow: Search Wikipedia, select article, then select and download an image.
    Returns: (image_url, image_bytes) or (None, None) if cancelled/errored
    """
    # Step 1: Search Wikipedia
    title, summary, full_content, link, images = search_wikipedia(query, parent)

    if not link or not images:
        return None, None

    # Step 2: Let user select an image
    selected_url = select_wikipedia_image(images, parent)
    if not selected_url:
        return None, None

    # Step 3: Download the selected image
    image_bytes = download_wikipedia_image(selected_url)

    return selected_url, image_bytes
