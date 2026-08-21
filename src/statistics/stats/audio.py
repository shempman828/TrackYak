"""
stats/audio.py

AudioStats: BPM/key/gain/time-signature/file-size distributions (with
confidence-based exclude toggles for BPM/key), quietest/loudest 10,
instrumental/classical distribution, and the 16 advanced-DSP-column
distributions + top10/bottom10.

Everything here is fetched via a separate worker (see AudioStatsWorker)
rather than folded into MusicStatistics.get_comprehensive_statistics() --
unlike the basic aggregates, several of these methods (the DSP
distributions, the DSP top/bottom lists, BPM/key/gain/file-size histograms)
each pull a full column scan or an unindexed sort across every track, and
there are enough of them that it's worth not holding up the rest of the
dialog on them. Computed once per dialog session, same as the
influence-graph tiles.
"""

from sqlalchemy import case, func

from src.db.db_tables import Track
from src.statistics.stats.helpers import distribution_stats, track_metric_top_bottom

# Confidence columns are stored on the same 0.0-1.0 scale as the rest of the
# analysis columns (danceability, energy, ...); "below 50%" means < 0.5.
CONFIDENCE_THRESHOLD = 0.5

# (label, Track column name) for the 16 advanced DSP/analysis properties.
DSP_COLUMNS = (
    ("Stereo Width", "stereo_width"),
    ("Crest Factor", "crest_factor"),
    ("MS Energy Ratio", "ms_energy_ratio"),
    ("Channel Coherence", "channel_coherence"),
    ("Transient Strength", "transient_strength"),
    ("Danceability", "danceability"),
    ("Energy", "energy"),
    ("Acousticness", "acousticness"),
    ("Liveness", "liveness"),
    ("Valence", "valence"),
    ("Audiophile Score", "audiophile_score"),
    ("Spectral Centroid", "spectral_centroid"),
    ("Spectral Rolloff", "spectral_rolloff"),
    ("Spectral Flatness", "spectral_flatness"),
    ("Spectral Flux", "spectral_flux"),
    ("Dynamic Range", "dynamic_range"),
)


class AudioStats:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    def get_comprehensive_audio_stats(self):
        """Everything on the Audio Profile tab, in one call, one session."""
        session = self.session_factory()
        try:
            return {
                "bpm_distribution": self._value_distribution(
                    session, Track.bpm, Track.tempo_confidence
                ),
                "key_distribution": self._key_distribution(session),
                "track_gain_distribution": distribution_stats(
                    self._column_values(session, Track.track_gain)
                ),
                "time_signature_distribution": self._time_signature_distribution(
                    session
                ),
                "file_size_distribution": distribution_stats(
                    [
                        v / (1024 * 1024)
                        for v in self._column_values(session, Track.file_size)
                    ]
                ),
                "quietest_loudest": self._quietest_loudest(session),
                "instrumental_distribution": self._boolean_distribution(
                    session, Track.is_instrumental, "Instrumental", "Vocal"
                ),
                "classical_distribution": self._boolean_distribution(
                    session, Track.is_classical, "Classical", "Non-Classical"
                ),
                "dsp_distributions": self._dsp_distributions(session),
                "dsp_top_bottom": self._dsp_top_bottom(session),
            }
        finally:
            session.close()

    # ------------------------------------------------------------------ #
    #  Shared query helpers                                                #
    # ------------------------------------------------------------------ #

    def _column_values(self, session, column, extra_filters=None):
        query = session.query(column).filter(column.isnot(None))
        if extra_filters:
            query = query.filter(*extra_filters)
        return [v for (v,) in query.all()]

    # ------------------------------------------------------------------ #
    #  BPM / Key -- confidence-toggle distributions                        #
    # ------------------------------------------------------------------ #

    def _value_distribution(self, session, value_column, confidence_column):
        """Numeric distribution plus a confidence-filtered variant, computed
        together so the UI's confidence toggle is instant (no re-query)."""
        all_values = self._column_values(session, value_column)
        confident_values = self._column_values(
            session,
            value_column,
            extra_filters=[confidence_column >= CONFIDENCE_THRESHOLD],
        )
        return {
            "all": distribution_stats(all_values),
            "confident": distribution_stats(confident_values),
        }

    def _key_distribution(self, session):
        """Categorical key distribution (e.g. "C Major"), plus a
        confidence-filtered variant."""

        def _fetch(extra_filters=None):
            query = session.query(
                Track.key, Track.mode, func.count(Track.track_id)
            ).filter(Track.key.isnot(None))
            if extra_filters:
                query = query.filter(*extra_filters)
            rows = query.group_by(Track.key, Track.mode).all()
            distribution = {}
            for key, mode, count in rows:
                label = f"{key} {mode}" if mode else key
                distribution[label] = distribution.get(label, 0) + count
            return distribution

        return {
            "all": _fetch(),
            "confident": _fetch([Track.key_confidence >= CONFIDENCE_THRESHOLD]),
        }

    def _time_signature_distribution(self, session):
        """Categorical time-signature distribution, plus a
        confidence-filtered variant (same shape as _key_distribution)."""

        def _fetch(extra_filters=None):
            query = session.query(
                Track.primary_time_signature, func.count(Track.track_id)
            ).filter(Track.primary_time_signature.isnot(None))
            if extra_filters:
                query = query.filter(*extra_filters)
            rows = query.group_by(Track.primary_time_signature).all()
            return {label: count for label, count in rows}

        return {
            "all": _fetch(),
            "confident": _fetch(
                [Track.time_signature_confidence >= CONFIDENCE_THRESHOLD]
            ),
        }

    # ------------------------------------------------------------------ #
    #  Quietest / loudest                                                  #
    # ------------------------------------------------------------------ #

    def _quietest_loudest(self, session, n=10):
        """Quietest/loudest 10 via ReplayGain-style track_gain: a positive
        gain means the track needed to be turned *up* to match the reference
        loudness (i.e. it's quiet), a negative gain means it needed to be
        turned *down* (i.e. it's loud)."""
        result = track_metric_top_bottom(session, Track.track_gain, n=n)
        return {"quietest": result["top"], "loudest": result["bottom"]}

    # ------------------------------------------------------------------ #
    #  Instrumental / classical                                            #
    # ------------------------------------------------------------------ #

    def _boolean_distribution(self, session, column, true_label, false_label):
        rows = (
            session.query(
                case(
                    (column == 1, true_label),
                    (column == 0, false_label),
                    else_="Unknown",
                ).label("bucket"),
                func.count(Track.track_id),
            )
            .group_by("bucket")
            .all()
        )
        return {label: count for label, count in rows}

    # ------------------------------------------------------------------ #
    #  16 advanced DSP columns                                             #
    # ------------------------------------------------------------------ #

    def _dsp_distributions(self, session):
        return {
            label: distribution_stats(
                self._column_values(session, getattr(Track, attr))
            )
            for label, attr in DSP_COLUMNS
        }

    def _dsp_top_bottom(self, session, n=10):
        return {
            label: track_metric_top_bottom(session, getattr(Track, attr), n=n)
            for label, attr in DSP_COLUMNS
        }
