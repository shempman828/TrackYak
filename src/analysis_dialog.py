from queue import Queue
import time

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
)

from src.analysis_utility import AudioAnalysis, AudioAnalysisWorker
from src.status_utility import StatusManager


class AudioAnalysisDialog(QDialog):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.tracks_to_analyze = []
        self.current_track_index = 0
        self.is_analyzing = False
        self.cancelled = False
        self.task_queue = Queue()
        self.setup_ui()
        self.load_tracks_missing_data()

    def setup_ui(self):
        self.setWindowTitle("Audio Analysis")
        self.setMinimumSize(600, 500)

        # Set size policy to allow expansion
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        layout = QVBoxLayout(self)

        # Make the layout expand
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        # Description
        desc_label = QLabel(
            "This will analyze audio files to extract musical properties like BPM, key, "
            "energy, danceability, and other audio features. Analysis may take several "
            "seconds per file."
        )
        desc_label.setWordWrap(True)
        desc_label.setStyleSheet("QLabel { padding: 10px; }")
        layout.addWidget(desc_label)

        # Tracks found section - FIXED: Make this expandable
        tracks_group = QGroupBox("Tracks Found for Analysis")
        tracks_layout = QVBoxLayout(tracks_group)

        # Allow the group box to expand
        tracks_group.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self.tracks_count_label = QLabel("Found 0 tracks missing audio analysis data")
        tracks_layout.addWidget(self.tracks_count_label)

        self.tracks_list = QListWidget()
        # FIX: Remove fixed maximum height and let it expand
        # self.tracks_list.setMaximumHeight(150)  # REMOVE THIS LINE
        self.tracks_list.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        tracks_layout.addWidget(self.tracks_list)

        # Set stretch factor to make the list widget take available space
        tracks_layout.setStretchFactor(self.tracks_list, 1)

        layout.addWidget(tracks_group)

        # FIX: Set stretch factors for the main layout to control expansion
        layout.setStretchFactor(tracks_group, 1)  # Tracks group should expand the most

        # Analysis options
        options_group = QGroupBox("Analysis Options")
        options_layout = QVBoxLayout(options_group)

        limit_layout = QHBoxLayout()
        limit_layout.addWidget(QLabel("Limit analysis to:"))
        self.limit_analysis_spinbox = QSpinBox()
        self.limit_analysis_spinbox.setMinimum(0)  # 0 means no limit
        self.limit_analysis_spinbox.setMaximum(10000)
        self.limit_analysis_spinbox.setValue(0)  # Default to no limit
        self.limit_analysis_spinbox.setSuffix(" tracks")
        self.limit_analysis_spinbox.valueChanged.connect(self.update_tracks_list)
        limit_layout.addWidget(self.limit_analysis_spinbox)
        limit_layout.addStretch()
        options_layout.addLayout(limit_layout)

        layout.addWidget(options_group)

        # Progress section
        progress_group = QGroupBox("Analysis Progress")
        progress_layout = QVBoxLayout(progress_group)

        self.current_file_label = QLabel("Ready to start analysis...")
        progress_layout.addWidget(self.current_file_label)

        self.worker_status_label = QLabel("Workers: Idle")
        progress_layout.addWidget(self.worker_status_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setMinimum(0)
        self.progress_bar.setValue(0)
        progress_layout.addWidget(self.progress_bar)

        self.progress_label = QLabel("0/0 tracks processed")
        progress_layout.addWidget(self.progress_label)

        layout.addWidget(progress_group)

        # Buttons - FIXED: Keep buttons at the bottom
        button_layout = QHBoxLayout()

        self.start_button = QPushButton("Start Analysis")
        self.start_button.clicked.connect(self.start_analysis)
        button_layout.addWidget(self.start_button)

        self.pause_button = QPushButton("Pause")
        self.pause_button.clicked.connect(self.toggle_pause)
        self.pause_button.setEnabled(False)
        button_layout.addWidget(self.pause_button)

        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.cancel_analysis)
        button_layout.addWidget(self.cancel_button)

        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(self.close)
        button_layout.addWidget(self.close_button)

        layout.addLayout(button_layout)

        # FIX: Set stretch factors to control which sections expand
        # 0 = doesn't expand, 1 = expands
        layout.setStretchFactor(tracks_group, 1)  # Tracks list expands the most
        layout.setStretchFactor(options_group, 0)  # Options stays fixed
        layout.setStretchFactor(progress_group, 0)  # Progress stays fixed
        # Buttons automatically stay at bottom

    def load_tracks_missing_data(self):
        """Load tracks that are missing any of the audio analysis data points"""
        try:
            # Get all tracks
            all_tracks = self.controller.get.get_all_entities("Track")

            # Filter tracks missing any audio analysis data
            self.tracks_to_analyze = []
            audio_data_fields = [
                "bpm",
                "key",
                "track_gain",
                "spectral_centroid",
                "dynamic_range",
                "danceability",
                "energy",
                "fidelity_score",
            ]

            for track in all_tracks:
                # Check if any of the key audio fields are missing or None
                missing_data = False
                for field in audio_data_fields:
                    if getattr(track, field, None) is None:
                        missing_data = True
                        break

                # Also check for default/placeholder values that might indicate missing analysis
                if not missing_data and hasattr(track, "bpm"):
                    if (
                        track.bpm == 120.0
                        and getattr(track, "tempo_confidence", 1.0) < 0.6
                    ):
                        missing_data = True

                if missing_data:
                    self.tracks_to_analyze.append(track)

            # Update UI with found tracks
            self.update_tracks_list()

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load tracks: {str(e)}")

    def update_tracks_list(self):
        """Update the tracks list display"""
        self.tracks_list.clear()

        limit = self.limit_analysis_spinbox.value()
        if limit > 0:
            display_tracks = self.tracks_to_analyze[:limit]
        else:
            display_tracks = self.tracks_to_analyze

        for track in display_tracks:
            track_name = getattr(track, "track_name", "Unknown Title")
            artist = track.artists[0].artist_name if track.artists else "Unknown Artist"
            self.tracks_list.addItem(f"{artist} - {track_name}")

        total_count = len(self.tracks_to_analyze)
        display_count = len(display_tracks)

        if limit > 0 and total_count > limit:
            self.tracks_count_label.setText(
                f"Found {total_count} tracks missing audio analysis data (showing first {display_count})"
            )
        else:
            self.tracks_count_label.setText(
                f"Found {total_count} tracks missing audio analysis data"
            )

        # Update progress bar maximum
        self.progress_bar.setMaximum(len(display_tracks))

    def start_analysis(self):
        """Start the audio analysis process using background workers"""
        if not self.tracks_to_analyze:
            QMessageBox.information(
                self, "No Tracks", "No tracks found that need analysis."
            )
            return

        # Get the tracks to analyze (limited if spinbox has value > 0)
        limit = self.limit_analysis_spinbox.value()
        if limit > 0:
            analysis_tracks = self.tracks_to_analyze[:limit]
        else:
            analysis_tracks = self.tracks_to_analyze

        if not analysis_tracks:
            return

        self.is_analyzing = True
        self.cancelled = False
        self.current_track_index = 0
        self.completed_tracks = 0

        # Update UI
        self.start_button.setEnabled(False)
        self.pause_button.setEnabled(True)
        self.pause_button.setText("Pause")
        self.close_button.setEnabled(True)  # Keep close enabled for background work

        self.progress_bar.setMaximum(len(analysis_tracks))
        self.progress_bar.setValue(0)
        self.progress_label.setText(f"0/{len(analysis_tracks)} tracks processed")
        self.worker_status_label.setText(f"Workers: Active (0/{len(analysis_tracks)})")

        # Create task queue if it doesn't exist
        if not hasattr(self, "task_queue"):
            from queue import Queue

            self.task_queue = Queue()

        # Clear the task queue and add all tracks
        while not self.task_queue.empty():
            try:
                self.task_queue.get_nowait()
                self.task_queue.task_done()
            except:  # noqa: E722
                pass

        for track in analysis_tracks:
            self.task_queue.put((self.controller, track))

        # Create threads spinbox if it doesn't exist (for backward compatibility)
        if not hasattr(self, "threads_spinbox"):
            # Default to 2 workers
            num_workers = 2
        else:
            num_workers = self.threads_spinbox.value()

        # Start worker threads
        self.workers = []

        for i in range(num_workers):
            worker = AudioAnalysisWorker(self.task_queue, self.on_track_analyzed)
            worker.start()
            self.workers.append(worker)

        # Start progress monitoring timer
        self.progress_timer = QTimer()
        self.progress_timer.timeout.connect(self.update_progress)
        self.progress_timer.start(500)  # Update every 500ms

        # Start analysis task in status manager
        StatusManager.start_task(f"Analyzing {len(analysis_tracks)} tracks...")

    def on_track_analyzed(self, track_id, metadata):
        """Callback when a track analysis is completed"""
        self.completed_tracks += 1

        # Update progress on the main thread - but only if dialog is visible
        if self.isVisible():
            QTimer.singleShot(0, self.update_progress_ui)

        # Always update status manager for background progress
        limit = self.limit_analysis_spinbox.value()
        if limit > 0:
            total_tracks = min(len(self.tracks_to_analyze), limit)
        else:
            total_tracks = len(self.tracks_to_analyze)

        if self.completed_tracks < total_tracks:
            StatusManager.show_message(
                f"Analyzing audio... ({self.completed_tracks}/{total_tracks} completed)",
                0,  # Persistent message
            )

    def update_progress(self):
        """Update progress display"""
        if not self.is_analyzing or self.cancelled:
            return

        limit = self.limit_analysis_spinbox.value()
        if limit > 0:
            total_tracks = min(len(self.tracks_to_analyze), limit)
        else:
            total_tracks = len(self.tracks_to_analyze)

        # Check if all tracks are processed
        if self.completed_tracks >= total_tracks:
            self.analysis_complete()
            return

        # Update UI only if dialog is visible
        if self.isVisible():
            self.update_progress_ui()

    def update_progress_ui(self):
        """Update progress UI elements"""
        limit = self.limit_analysis_spinbox.value()
        if limit > 0:
            total_tracks = min(len(self.tracks_to_analyze), limit)
        else:
            total_tracks = len(self.tracks_to_analyze)

        self.progress_bar.setValue(self.completed_tracks)
        self.progress_label.setText(
            f"{self.completed_tracks}/{total_tracks} tracks processed"
        )
        self.worker_status_label.setText(
            f"Workers: Active ({self.completed_tracks}/{total_tracks})"
        )

        # Update current file label to show overall progress
        if self.completed_tracks < total_tracks:
            self.current_file_label.setText(
                f"Processing... ({self.completed_tracks}/{total_tracks} completed)"
            )
        else:
            self.current_file_label.setText("Finalizing...")

    def analyze_next_track(self):
        """Analyze the next track in the queue"""
        if self.cancelled or not self.is_analyzing:
            return

        limit = self.limit_analysis_spinbox.value()
        if limit > 0:
            analysis_tracks = self.tracks_to_analyze[:limit]
        else:
            analysis_tracks = self.tracks_to_analyze

        if self.current_track_index >= len(analysis_tracks):
            self.analysis_complete()
            return

        current_track = analysis_tracks[self.current_track_index]
        track_name = getattr(current_track, "track_name", "Unknown Title")
        artist = getattr(current_track, "primary_artist_names", "Unknown Artist")

        # Update progress UI
        self.current_file_label.setText(f"Analyzing: {artist} - {track_name}")
        self.progress_bar.setValue(self.current_track_index)
        self.progress_label.setText(
            f"{self.current_track_index}/{len(analysis_tracks)} tracks processed"
        )

        # Update status manager with current progress
        progress_text = f"Analyzing {self.current_track_index + 1}/{len(analysis_tracks)}: {artist} - {track_name}"
        StatusManager.show_message(progress_text, 0)  # Persistent during analysis

        # Process events to update UI
        self.repaint()

        try:
            # Perform audio analysis
            analysis = AudioAnalysis(self.controller, current_track)
            analysis.update_track()

            # Small delay to allow UI updates and prevent freezing
            time.sleep(0.1)

        except Exception as e:
            print(f"Error analyzing track {track_name}: {e}")
            # Continue with next track even if this one fails

        # Move to next track
        self.current_track_index += 1

        # Schedule next analysis with a small delay to keep UI responsive
        if self.is_analyzing and not self.cancelled:
            QTimer.singleShot(50, self.analyze_next_track)
        else:
            self.analysis_complete()

    def toggle_pause(self):
        """Toggle pause/resume analysis"""
        if self.is_analyzing:
            # Pause - stop the progress timer but keep workers running
            self.is_analyzing = False
            self.pause_button.setText("Resume")
            self.current_file_label.setText("Analysis paused...")
            if hasattr(self, "progress_timer"):
                self.progress_timer.stop()
            StatusManager.show_message("Analysis paused", 0)
        else:
            # Resume
            self.is_analyzing = True
            self.pause_button.setText("Pause")
            if hasattr(self, "progress_timer"):
                self.progress_timer.start(500)
            StatusManager.show_message("Resuming analysis...", 2000)

    def cancel_analysis(self):
        """Cancel the ongoing analysis"""
        self.cancelled = True
        self.is_analyzing = False

        # Stop progress timer
        if hasattr(self, "progress_timer"):
            self.progress_timer.stop()

        # Stop worker threads
        if hasattr(self, "workers"):
            for worker in self.workers:
                worker.running = False
            self.workers.clear()

        # Clear the queue
        while not self.task_queue.empty():
            try:
                self.task_queue.get_nowait()
                self.task_queue.task_done()
            except:  # noqa: E722
                pass

        self.current_file_label.setText("Analysis cancelled")
        StatusManager.end_task("Analysis cancelled", 3000)
        self.reset_ui_state()

        # Show completion message for partial analysis
        if self.completed_tracks > 0:
            QMessageBox.information(
                self,
                "Analysis Cancelled",
                f"Analysis cancelled. Processed {self.completed_tracks} tracks.",
            )

    def analysis_complete(self):
        """Handle analysis completion"""
        self.is_analyzing = False
        self.cancelled = False

        # Stop progress timer
        if hasattr(self, "progress_timer"):
            self.progress_timer.stop()

        # Stop worker threads
        if hasattr(self, "workers"):
            for worker in self.workers:
                worker.running = False
            self.workers.clear()

        # Determine which tracks were analyzed
        limit = self.limit_analysis_spinbox.value()
        if limit > 0:
            analysis_tracks = self.tracks_to_analyze[:limit]
        else:
            analysis_tracks = self.tracks_to_analyze

        # StatusManager finish message
        StatusManager.end_task(
            f"Analysis complete: {len(analysis_tracks)} tracks processed", 5000
        )

        # UI updates only if dialog is visible
        if self.isVisible():
            self.current_file_label.setText("Analysis complete!")
            self.progress_bar.setValue(len(analysis_tracks))
            self.progress_label.setText(
                f"Complete: {len(analysis_tracks)}/{len(analysis_tracks)} tracks processed"
            )
            self.worker_status_label.setText("Workers: Idle")
            self.reset_ui_state()

            # Notify user
            QMessageBox.information(
                self,
                "Analysis Complete",
                f"Audio analysis completed for {len(analysis_tracks)} tracks.",
            )

            # Reload remaining tracks (if any)
            self.load_tracks_missing_data()
        else:
            # If dialog was closed, just reset the UI state for next time
            self.reset_ui_state()

    def reset_ui_state(self):
        """Reset UI to initial state"""
        self.start_button.setEnabled(True)
        self.pause_button.setEnabled(False)
        self.pause_button.setText("Pause")
        self.close_button.setEnabled(True)

    def closeEvent(self, event):
        """Handle dialog close event - allow background processing"""
        if self.is_analyzing:
            reply = QMessageBox.question(
                self,
                "Analysis in Progress",
                "Audio analysis is in progress. Do you want to:\n\n"
                "Yes - Close and continue analysis in background\n"
                "No - Stay open and monitor progress\n"
                "Cancel - Stop analysis and close",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
                QMessageBox.No,
            )

            if reply == QMessageBox.Yes:
                # Close dialog but keep analysis running in background
                event.accept()
            elif reply == QMessageBox.No:
                # Stay open
                event.ignore()
            else:  # Cancel
                # Stop analysis and close
                self.cancel_analysis()
                event.accept()
        else:
            event.accept()

    def showEvent(self, event):
        """Handle dialog show event - update UI if analysis is running"""
        super().showEvent(event)
        if self.is_analyzing:
            # Update UI to reflect current progress when dialog is shown
            self.update_progress_ui()
