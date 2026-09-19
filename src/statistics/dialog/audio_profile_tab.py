"""Audio Profile tab: BPM/key/gain/time-signature/DSP distributions and quality labels."""

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.statistics.dialog.shared import _recompute_bar
from src.statistics.stats.audio import DSP_COLUMNS
from src.statistics.widgets.bar_distribution_chart import BarDistributionChart
from src.statistics.widgets.histogram_chart import HistogramChart
from src.statistics.widgets.leaderboard_list import LeaderboardListWidget
from src.statistics.workers.audio_stats_worker import AudioStatsWorker


class AudioProfileTabMixin:
    def create_audio_profile_tab(self):
        widget = QScrollArea()
        content = QWidget()
        layout = QVBoxLayout(content)

        audio_recompute_bar, self.audio_recompute_button = _recompute_bar(
            self._recompute_audio_stats
        )
        layout.addLayout(audio_recompute_bar)

        quality_group = QGroupBox("Audio Quality")
        quality_layout = QVBoxLayout(quality_group)

        self.avg_bit_rate_label = self.create_stat_label("Average Bit Rate:")
        self.avg_bit_depth_label = self.create_stat_label("Average Bit Depth:")
        self.avg_file_size_label = self.create_stat_label("Average File Size:")
        self.total_track_length_label = self.create_stat_label("Total Track Length:")

        for lbl in [
            self.avg_bit_rate_label,
            self.avg_bit_depth_label,
            self.avg_file_size_label,
            self.total_track_length_label,
        ]:
            quality_layout.addWidget(lbl)

        layout.addWidget(quality_group)

        # BPM — with confidence toggle
        bpm_group = QGroupBox("BPM Distribution")
        bpm_layout = QVBoxLayout(bpm_group)
        self.bpm_confidence_checkbox = QCheckBox("Exclude confidence below 50%")
        self.bpm_confidence_checkbox.toggled.connect(self.load_audio_profile_data)
        bpm_layout.addWidget(self.bpm_confidence_checkbox)
        self.bpm_chart = HistogramChart(value_format="{:.0f}")
        bpm_layout.addWidget(self.bpm_chart)
        layout.addWidget(bpm_group)

        # Key — with confidence toggle
        key_group = QGroupBox("Key Distribution")
        key_layout = QVBoxLayout(key_group)
        self.key_confidence_checkbox = QCheckBox("Exclude confidence below 50%")
        self.key_confidence_checkbox.toggled.connect(self.load_audio_profile_data)
        key_layout.addWidget(self.key_confidence_checkbox)
        self.key_chart = BarDistributionChart()
        key_layout.addWidget(self.key_chart)
        layout.addWidget(key_group)

        # Track gain + quietest/loudest
        gain_group = QGroupBox("Track Gain")
        gain_layout = QVBoxLayout(gain_group)
        self.track_gain_chart = HistogramChart(unit=" dB", value_format="{:.1f}")
        gain_layout.addWidget(self.track_gain_chart)
        quiet_loud_layout = QHBoxLayout()
        quietest_box = QVBoxLayout()
        quietest_box.addWidget(QLabel("Quietest 10:"))
        self.quietest_list = LeaderboardListWidget(value_suffix=" dB")
        quietest_box.addWidget(self.quietest_list)
        loudest_box = QVBoxLayout()
        loudest_box.addWidget(QLabel("Loudest 10:"))
        self.loudest_list = LeaderboardListWidget(value_suffix=" dB")
        loudest_box.addWidget(self.loudest_list)
        quiet_loud_layout.addLayout(quietest_box)
        quiet_loud_layout.addLayout(loudest_box)
        gain_layout.addLayout(quiet_loud_layout)
        layout.addWidget(gain_group)

        # Time signature / file size
        misc_group = QGroupBox("Time Signature && File Size")
        misc_layout = QHBoxLayout(misc_group)
        time_sig_box = QVBoxLayout()
        time_sig_box.addWidget(QLabel("Time Signature:"))
        self.time_signature_confidence_checkbox = QCheckBox("Exclude confidence below 50%")
        self.time_signature_confidence_checkbox.toggled.connect(self.load_audio_profile_data)
        time_sig_box.addWidget(self.time_signature_confidence_checkbox)
        self.time_signature_chart = BarDistributionChart()
        time_sig_box.addWidget(self.time_signature_chart)
        file_size_box = QVBoxLayout()
        file_size_box.addWidget(QLabel("File Size (MB):"))
        self.file_size_chart = HistogramChart(unit=" MB", value_format="{:.1f}")
        file_size_box.addWidget(self.file_size_chart)
        misc_layout.addLayout(time_sig_box)
        misc_layout.addLayout(file_size_box)
        layout.addWidget(misc_group)

        # Instrumental / classical
        split_group = QGroupBox("Instrumental && Classical")
        split_layout = QHBoxLayout(split_group)
        instrumental_box = QVBoxLayout()
        instrumental_box.addWidget(QLabel("Instrumental:"))
        self.instrumental_chart = BarDistributionChart()
        instrumental_box.addWidget(self.instrumental_chart)
        classical_box = QVBoxLayout()
        classical_box.addWidget(QLabel("Classical:"))
        self.classical_chart = BarDistributionChart()
        classical_box.addWidget(self.classical_chart)
        split_layout.addLayout(instrumental_box)
        split_layout.addLayout(classical_box)
        layout.addWidget(split_group)

        # Advanced DSP properties — one metric at a time via selector, since
        # all 16 columns' distributions/top10/bottom10 are already computed
        # up front (see stats/audio.py), switching the selector is instant.
        dsp_group = QGroupBox("Advanced Audio Properties")
        dsp_layout = QVBoxLayout(dsp_group)
        self.dsp_metric_combo = QComboBox()
        for label, _attr in DSP_COLUMNS:
            self.dsp_metric_combo.addItem(label)
        self.dsp_metric_combo.currentTextChanged.connect(self.load_dsp_metric)
        dsp_layout.addWidget(self.dsp_metric_combo)
        self.dsp_chart = HistogramChart(value_format="{:.2f}")
        dsp_layout.addWidget(self.dsp_chart)
        dsp_lists_layout = QHBoxLayout()
        dsp_top_box = QVBoxLayout()
        dsp_top_box.addWidget(QLabel("Top 10:"))
        self.dsp_top_list = LeaderboardListWidget()
        dsp_top_box.addWidget(self.dsp_top_list)
        dsp_bottom_box = QVBoxLayout()
        dsp_bottom_box.addWidget(QLabel("Bottom 10:"))
        self.dsp_bottom_list = LeaderboardListWidget()
        dsp_bottom_box.addWidget(self.dsp_bottom_list)
        dsp_lists_layout.addLayout(dsp_top_box)
        dsp_lists_layout.addLayout(dsp_bottom_box)
        dsp_layout.addLayout(dsp_lists_layout)
        layout.addWidget(dsp_group)

        layout.addStretch()
        widget.setWidget(content)
        widget.setWidgetResizable(True)
        return widget

    def _load_audio_stats(self):
        """Lazy-load the Audio Profile tab's distributions/leaderboards, as
        a separate worker from load_data() -- see the module docstring in
        stats/audio.py for why it's heavier than the rest. Runs once per
        dialog session."""
        if self.audio_worker is not None and self.audio_worker.isRunning():
            return

        self.audio_worker = AudioStatsWorker(self.controller.statistics.audio)
        self.audio_worker.finished.connect(self.on_audio_stats_loaded)
        self.audio_worker.error.connect(self.on_audio_stats_error)
        self.audio_worker.start()

    def on_audio_stats_loaded(self, stats):
        self.audio_stats = stats
        self.load_audio_profile_data()
        self.audio_recompute_button.setEnabled(True)

    def on_audio_stats_error(self, message):
        self.audio_recompute_button.setEnabled(True)

    def _recompute_audio_stats(self):
        self.audio_recompute_button.setEnabled(False)
        self._load_audio_stats()

    def load_audio_quality_labels(self):
        """Populate the Audio Profile tab's "Audio Quality" scalar rows
        (bit rate / bit depth / file size / total length). These four labels
        sit on the Audio Profile tab but are fed by the main stats worker's
        comprehensive payload (self.stats), not the lazy AudioStatsWorker
        that drives that tab's charts -- so they're refreshed from here, with
        the rest of the on_stats_loaded() loaders, not load_audio_profile_data()."""
        audio_stats = self.stats.get("audio_quality_stats", {})

        avg_bit_rate = audio_stats.get("average_bit_rate")
        self.avg_bit_rate_label.setText(
            "Average Bit Rate: "
            + (f"{self.format_stat_value(avg_bit_rate, False)} kbps" if avg_bit_rate else "N/A")
        )

        avg_bit_depth = audio_stats.get("average_bit_depth")
        self.avg_bit_depth_label.setText(
            "Average Bit Depth: "
            + (f"{self.format_stat_value(avg_bit_depth, False)} bits" if avg_bit_depth else "N/A")
        )

        avg_file_size = audio_stats.get("average_file_size")
        self.avg_file_size_label.setText(
            "Average File Size: "
            + (
                self.format_stat_value(self.format_file_size(avg_file_size), False)
                if avg_file_size
                else "N/A"
            )
        )

        avg_duration = audio_stats.get("average_duration") or 0
        total_length = avg_duration * self.stats.get("total_tracks", 0)
        self.total_track_length_label.setText(
            "Total Track Length: "
            + self.format_stat_value(self.format_duration(total_length), False)
        )

    def load_audio_profile_data(self):
        """Load Audio Profile tab data (already fetched in self.audio_stats
        by the lazy AudioStatsWorker)."""
        stats = self.audio_stats
        if stats is None:
            return

        bpm = stats.get("bpm_distribution", {})
        bpm_key = "confident" if self.bpm_confidence_checkbox.isChecked() else "all"
        self.bpm_chart.set_data(bpm.get(bpm_key))

        key_dist = stats.get("key_distribution", {})
        key_key = "confident" if self.key_confidence_checkbox.isChecked() else "all"
        self.key_chart.set_data(key_dist.get(key_key))

        self.track_gain_chart.set_data(stats.get("track_gain_distribution"))

        quietest_loudest = stats.get("quietest_loudest", {})
        self.quietest_list.set_data(self._track_metric_rows(quietest_loudest.get("quietest", [])))
        self.loudest_list.set_data(self._track_metric_rows(quietest_loudest.get("loudest", [])))

        time_sig_dist = stats.get("time_signature_distribution", {})
        time_sig_key = "confident" if self.time_signature_confidence_checkbox.isChecked() else "all"
        self.time_signature_chart.set_data(time_sig_dist.get(time_sig_key))
        self.file_size_chart.set_data(stats.get("file_size_distribution"))

        self.instrumental_chart.set_data(stats.get("instrumental_distribution"))
        self.classical_chart.set_data(stats.get("classical_distribution"))

        self.load_dsp_metric(self.dsp_metric_combo.currentText())

    def load_dsp_metric(self, label: str):
        """Update the DSP histogram + top/bottom lists for the selected
        advanced-audio-property metric. All 16 metrics are already computed
        (see stats/audio.py), so switching the selector needs no new query."""
        stats = self.audio_stats
        if stats is None or not label:
            return

        distributions = stats.get("dsp_distributions", {})
        self.dsp_chart.set_data(distributions.get(label))

        top_bottom = stats.get("dsp_top_bottom", {}).get(label, {})
        self.dsp_top_list.set_data(self._track_metric_rows(top_bottom.get("top", [])))
        self.dsp_bottom_list.set_data(self._track_metric_rows(top_bottom.get("bottom", [])))
