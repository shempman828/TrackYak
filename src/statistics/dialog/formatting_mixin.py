"""Shared value-formatting and row-shaping helpers mixed into MusicStatsDialog."""

from PySide6.QtWidgets import QLabel

from src.statistics.dialog.shared import _HIGHLIGHT_COLOR


class FormattingMixin:
    def create_stat_label(self, text):
        """Create a consistent stat label."""
        label = QLabel(text)
        label.setObjectName("StatValueLabel")
        return label

    def format_stat_value(self, value, is_numeric=True):
        """Format a statistic value with colour styling."""
        if value is None or value == "N/A":
            formatted_value = "N/A"
        elif is_numeric and isinstance(value, (int, float)):
            formatted_value = f"{value:,}" if isinstance(value, int) else f"{value:.1f}"
        else:
            formatted_value = str(value)

        return (
            f'<span style="color: {_HIGHLIGHT_COLOR}; font-weight: bold;">{formatted_value}</span>'
        )

    def format_duration(self, seconds):
        """Convert a duration in seconds to a human-readable string.

        Scales automatically:
          - Under 1 minute  → "Xs"
          - Under 1 hour    → "Xm Ys"
          - Under 1 day     → "Xh Ym"
          - Under 1 year    → "Xd Yh"
          - 1 year or more  → "Xy Zd"
        """
        if not seconds:
            return "0s"

        seconds = int(seconds)

        MINUTE = 60
        HOUR = 3600
        DAY = 86400
        YEAR = 365 * DAY

        if seconds < MINUTE:
            return f"{seconds}s"
        if seconds < HOUR:
            m = seconds // MINUTE
            s = seconds % MINUTE
            return f"{m}m {s}s"
        if seconds < DAY:
            h = seconds // HOUR
            m = (seconds % HOUR) // MINUTE
            return f"{h}h {m}m"
        if seconds < YEAR:
            d = seconds // DAY
            h = (seconds % DAY) // HOUR
            return f"{d}d {h}h"
        y = seconds // YEAR
        d = (seconds % YEAR) // DAY
        return f"{y}y {d}d"

    def format_file_size(self, bytes_size):
        """Convert bytes to a human-readable string."""
        if not bytes_size:
            return "0 B"
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if bytes_size < 1024.0:
                return f"{bytes_size:.2f} {unit}"
            bytes_size /= 1024.0
        return f"{bytes_size:.2f} PB"

    def _rating_rows_with_n(self, rows):
        """Remap (name, value, n) rating rows into LeaderboardListWidget's
        (name, value, secondary_label) shape, with n as the secondary note."""
        return [(name, value, f"{n} tracks") for name, value, n in rows]

    def _rows_with_note(self, rows, note):
        """Like _rating_rows_with_n, but for leaderboards whose count isn't
        a track count (e.g. publishers counted by album)."""
        return [(name, value, f"{n} {note}") for name, value, n in rows]

    def _track_metric_rows(self, rows):
        """Remap track_metric_top_bottom's (track_name, artist, value) rows
        into LeaderboardListWidget's (name, value, secondary_label) shape."""
        return [(name, value, artist) for name, artist, value in rows]
