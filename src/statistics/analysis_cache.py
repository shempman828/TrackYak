"""
Thin JSON wrapper at config/analysis_cache.json.

Tracks which track_ids are fully analysed so the analysis dialog doesn't
have to re-inspect every DB field on every open.
"""

import json
import threading
from pathlib import Path

from src.core.asset_paths import config
from src.core.logger_config import logger

CACHE_PATH = Path(config("analysis_cache.json"))
CACHE_SAVE_INTERVAL = 25  # Save the cache file every N completed tracks


class AnalysisCache:
    """
    Keeps a JSON set of track_ids that have been successfully analysed.

    Format of analysis_cache.json:
        { "analysed_ids": [1, 2, 3, ...] }

    Usage:
        cache = AnalysisCache()
        if cache.is_analysed(track_id):
            skip ...
        cache.mark_analysed(track_id)
        cache.save()                    # called automatically every 25 tracks
        cache.remove(track_id)          # right-click "force re-analyse"
    """

    def __init__(self):
        self._ids: set[int] = set()
        self._dirty_count = 0
        self._lock = threading.Lock()
        self._load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_analysed(self, track_id: int) -> bool:
        with self._lock:
            return track_id in self._ids

    def mark_analysed(self, track_id: int):
        """Record a track as done and save every CACHE_SAVE_INTERVAL tracks."""
        with self._lock:
            self._ids.add(track_id)
            self._dirty_count += 1
            if self._dirty_count >= CACHE_SAVE_INTERVAL:
                self._save_locked()
                self._dirty_count = 0

    def remove(self, track_id: int):
        """Remove a track from the cache so it will be re-analysed."""
        with self._lock:
            self._ids.discard(track_id)
            self._save_locked()

    def save(self):
        """Force an immediate save (call this on scheduler stop/finish)."""
        with self._lock:
            self._save_locked()
            self._dirty_count = 0

    def count(self) -> int:
        with self._lock:
            return len(self._ids)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load(self):
        try:
            if CACHE_PATH.exists():
                data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
                self._ids = set(data.get("analysed_ids", []))
                logger.info(f"AnalysisCache: loaded {len(self._ids)} cached IDs")
            else:
                self._ids = set()
        except (OSError, json.JSONDecodeError, AttributeError) as e:
            logger.error(f"AnalysisCache: failed to load cache — {e}")
            self._ids = set()

    def _save_locked(self):
        """Must be called while self._lock is already held."""
        try:
            CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            data = {"analysed_ids": list(self._ids)}
            CACHE_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError as e:
            logger.error(f"AnalysisCache: failed to save cache — {e}")


# Shared singleton so the dialog and scheduler always reference the same state
analysis_cache = AnalysisCache()


# Fields that must be present and non-zero/None for a track to be
# considered fully analysed.  Must track every field AudioCalculations.run_all()
# writes (audio_calculations.py) except acoustid_fingerprint/_duration, which
# can be legitimately and permanently None (e.g. no native chromaprint library
# available) — see AudioCalculations.calculate_fingerprint's docstring.
REQUIRED_ANALYSIS_FIELDS = [
    "bpm",
    "tempo_confidence",
    "key",
    "mode",
    "key_confidence",
    "track_gain",
    "track_peak",
    "crest_factor",
    "spectral_centroid",
    "spectral_rolloff",
    "spectral_flatness",
    "spectral_flux",
    "dynamic_range",
    "stereo_width",
    "ms_energy_ratio",
    "channel_coherence",
    "transient_strength",
    "energy",
    "danceability",
    "acousticness",
    "liveness",
    "valence",
    "audiophile_score",
]


def track_needs_analysis(track) -> bool:
    """Return True if any required audio field is missing or zero.

    The JSON cache alone (``analysis_cache.is_analysed``) is not a reliable
    signal that a track is analysed — it can drift from the DB (re-imports,
    restored backups, manual edits, reused track_ids). Callers that need to
    know whether a track is *actually* analysed should OR this with
    ``analysis_cache.is_analysed(track.track_id)``.
    """
    for field in REQUIRED_ANALYSIS_FIELDS:
        val = getattr(track, field, None)
        if val is None or val == 0 or val == 0.0:
            return True
    return False
