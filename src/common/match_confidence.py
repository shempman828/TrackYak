"""Shared color/label convention for 0.0-1.0 fuzzy-match confidence scores."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor

HIGH_CONFIDENCE = 0.8
MEDIUM_CONFIDENCE = 0.6


def confidence_color(score: float) -> QColor:
    """Color for a match score: green (strong), blue (possible), red (weak)."""
    if score > HIGH_CONFIDENCE:
        return QColor(Qt.darkGreen)
    if score > MEDIUM_CONFIDENCE:
        return QColor(Qt.darkBlue)
    return QColor(Qt.darkRed)


def confidence_label(score: float) -> str:
    """Short human-readable label for a match score."""
    if score > HIGH_CONFIDENCE:
        return "Strong match"
    if score > MEDIUM_CONFIDENCE:
        return "Possible match"
    return "Weak match — verify"
