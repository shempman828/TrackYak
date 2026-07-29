"""
match_confidence.py

Shared color/label convention for fuzzy string-match confidence scores
(0.0-1.0, typically a SequenceMatcher ratio) used wherever the UI
auto-suggests an entity match a human should confirm -- e.g. MusicBrainz
track matching. Centralized so every such view reads the same score the
same way instead of each picking its own thresholds.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor

HIGH_CONFIDENCE = 0.8
MEDIUM_CONFIDENCE = 0.6


def confidence_color(score: float) -> QColor:
    if score > HIGH_CONFIDENCE:
        return QColor(Qt.darkGreen)
    if score > MEDIUM_CONFIDENCE:
        return QColor(Qt.darkBlue)
    return QColor(Qt.darkRed)


def confidence_label(score: float) -> str:
    if score > HIGH_CONFIDENCE:
        return "Strong match"
    if score > MEDIUM_CONFIDENCE:
        return "Possible match"
    return "Weak match — verify"
