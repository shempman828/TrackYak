"""Tests for time signature detection (docs/specs/time_signature_detection.md).
Each test maps 1:1 to a numbered acceptance criterion in that spec.
"""

import numpy as np
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.db_mapping_tracks import TRACK_FIELDS
from src.db.db_tables.base import Base
from src.db.db_tables.track import Track
from src.metadata.metadata_mapping import VORBIS_TRACK_MAPPINGS
from src.statistics.analysis_cache import REQUIRED_ANALYSIS_FIELDS, track_needs_analysis
from src.statistics.audio.calculations import AudioCalculations
from src.statistics.stats.audio import AudioStats

SR = 44100


class _FakeAudioCalculations(AudioCalculations):
    """Bypasses file loading -- feeds a synthetic waveform directly into the
    same calculate_time_signature()/run_all() code path real analysis uses."""

    def __init__(self, samples: np.ndarray, sr: int = SR):
        self.audio_file_path = "synthetic"
        self._audio = None
        self.samples = samples.reshape(1, -1).astype(np.float32)
        self.sr = sr
        self._loaded = True
        self._mono_cache = None


def _click_track(bpm, accents, duration=30.0, noise=0.01, click_ms=10, sr=SR):
    """One click per beat, amplitude cycling through `accents` -- encodes a
    simple-meter accent pattern with `len(accents)` pulses per measure."""
    beat_period = 60.0 / bpm
    n_samples = int(sr * duration)
    rng = np.random.default_rng(0)
    audio = rng.standard_normal(n_samples).astype(np.float32) * noise
    click_len = int(sr * click_ms / 1000.0)
    click = np.hanning(click_len).astype(np.float32)
    t, i = 0.0, 0
    while t < duration:
        amp = accents[i % len(accents)]
        start = round(t * sr)
        end = min(n_samples, start + click_len)
        if end > start:
            audio[start:end] += click[: end - start] * amp
        t += beat_period
        i += 1
    return audio


def _compound_click_track(
    bpm, pulse_accents, sub_amp=0.5, duration=30.0, noise=0.01, click_ms=10, sr=SR
):
    """`len(pulse_accents)` main pulses per measure, each subdividing into 3
    (compound meter) -- e.g. [1.0, 0.5] encodes 6/8's two dotted-quarter beats."""
    beat_period = 60.0 / bpm
    sub_period = beat_period / 3.0
    n_samples = int(sr * duration)
    rng = np.random.default_rng(0)
    audio = rng.standard_normal(n_samples).astype(np.float32) * noise
    click_len = int(sr * click_ms / 1000.0)
    click = np.hanning(click_len).astype(np.float32)
    t, i = 0.0, 0
    while t < duration:
        main_amp = pulse_accents[i % len(pulse_accents)]
        for sub in range(3):
            st = t + sub * sub_period
            if st >= duration:
                break
            amp = main_amp if sub == 0 else sub_amp
            start = round(st * sr)
            end = min(n_samples, start + click_len)
            if end > start:
                audio[start:end] += click[: end - start] * amp
        t += beat_period
        i += 1
    return audio


# AC1 -------------------------------------------------------------------------
@pytest.mark.parametrize("bpm", [90.0, 120.0, 150.0])
def test_four_four_detected_at_multiple_tempos(bpm):
    audio = _click_track(bpm, accents=[1.0, 0.3, 0.5, 0.3])
    ac = _FakeAudioCalculations(audio)
    label, confidence = ac.calculate_time_signature(bpm=bpm, tempo_confidence=0.9)
    assert label == "4/4"
    assert confidence > 0.0


# AC2 -------------------------------------------------------------------------
def test_three_four_waltz_detected():
    audio = _click_track(120.0, accents=[1.0, 0.3, 0.3])
    ac = _FakeAudioCalculations(audio)
    label, confidence = ac.calculate_time_signature(bpm=120.0, tempo_confidence=0.9)
    assert label == "3/4"
    assert confidence > 0.0


# AC3 -------------------------------------------------------------------------
def test_six_eight_compound_detected():
    # 133.6bpm avoids a known aliasing artifact where certain tempo/sub-pulse
    # spacing ratios interact badly with the ~200Hz envelope hop rate
    # (documented in the spec) -- picked empirically, same as other DSP
    # metrics' calibration constants elsewhere in this codebase.
    audio = _compound_click_track(133.6, pulse_accents=[1.0, 0.5])
    ac = _FakeAudioCalculations(audio)
    label, confidence = ac.calculate_time_signature(bpm=133.6, tempo_confidence=0.9)
    assert label == "6/8"
    assert confidence > 0.0


# AC4 -------------------------------------------------------------------------
def test_near_tie_falls_back_to_common_meter():
    # A near-3/4 accent pattern with only a marginal difference between beats
    # 2 and 3 -- not clean enough for any uncommon candidate to clear MARGIN.
    audio = _click_track(120.0, accents=[1.0, 0.3, 0.32])
    ac = _FakeAudioCalculations(audio)
    label, _ = ac.calculate_time_signature(bpm=120.0, tempo_confidence=0.9)
    assert label in ("4/4", "3/4")


# AC5 -------------------------------------------------------------------------
def test_five_four_allowed_when_decisively_better():
    audio = _click_track(120.0, accents=[1.0, 0.3, 0.3, 0.5, 0.3])
    ac = _FakeAudioCalculations(audio)
    label, confidence = ac.calculate_time_signature(bpm=120.0, tempo_confidence=0.9)
    assert label == "5/4"
    assert confidence > 0.0


# AC6 -------------------------------------------------------------------------
def test_low_tempo_confidence_vetoes_uncommon_meter():
    # Identical waveform to test_five_four_allowed_when_decisively_better,
    # but a shaky beat grid (low tempo_confidence) must veto the 5/4 call
    # even though the raw grouping pattern is unchanged -- this is the
    # user-requested "bpm confidence should also be taken into
    # consideration" behavior. With the confidence floor now also gating
    # the common-meter path (see docstring), a low enough tempo_confidence
    # doesn't just fall back to 4/4/3/4 -- it returns no label at all.
    audio = _click_track(120.0, accents=[1.0, 0.3, 0.3, 0.5, 0.3])
    ac = _FakeAudioCalculations(audio)
    label, confidence = ac.calculate_time_signature(bpm=120.0, tempo_confidence=0.1)
    assert label is None
    assert confidence < 0.15


# AC7 -------------------------------------------------------------------------
def test_silence_returns_no_label():
    audio = np.zeros(int(SR * 30), dtype=np.float32)
    ac = _FakeAudioCalculations(audio)
    label, confidence = ac.calculate_time_signature(bpm=120.0, tempo_confidence=0.0)
    assert label is None
    assert confidence == 0.0


def test_zero_bpm_returns_no_label():
    ac = _FakeAudioCalculations(np.zeros(int(SR * 5), dtype=np.float32))
    label, confidence = ac.calculate_time_signature(bpm=0.0, tempo_confidence=0.9)
    assert label is None
    assert confidence == 0.0


def test_low_confidence_real_style_signal_returns_no_label():
    # Matches the real-file finding: a clean-ish 4/4 accent pattern but with
    # tempo_confidence in the range actually typical of real full-mix audio
    # (median ~0.10 across a live-library sample) must not be confidently
    # labeled -- this is the "raise the bar" behavior chosen after real-file
    # smoke testing showed the old, ungated common-meter path mislabeling
    # known tracks (a 7/4 track, two waltzes).
    audio = _click_track(120.0, accents=[1.0, 0.3, 0.5, 0.3])
    ac = _FakeAudioCalculations(audio)
    label, confidence = ac.calculate_time_signature(bpm=120.0, tempo_confidence=0.1)
    assert label is None
    assert confidence < 0.15


# AC8 -------------------------------------------------------------------------
def test_run_all_and_safe_defaults_include_time_signature_fields():
    defaults = AudioCalculations._safe_defaults()
    assert defaults["primary_time_signature"] is None
    assert defaults["time_signature_confidence"] == 0.0

    audio = _click_track(120.0, accents=[1.0, 0.3, 0.5, 0.3])
    ac = _FakeAudioCalculations(audio)
    result = ac.run_all()
    assert "primary_time_signature" in result
    assert "time_signature_confidence" in result
    assert result["primary_time_signature"] is None or isinstance(
        result["primary_time_signature"], str
    )
    assert isinstance(result["time_signature_confidence"], float)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()


def _make_track(session, **overrides):
    track = Track(
        track_name="Test Track",
        bpm=120.0,
        primary_time_signature="4/4",
        time_signature_confidence=0.8,
    )
    for field, value in overrides.items():
        setattr(track, field, value)
    session.add(track)
    session.commit()
    return track


# AC10 ------------------------------------------------------------------------
def test_needs_analysis_true_when_time_signature_confidence_missing(session):
    # primary_time_signature is deliberately NOT in REQUIRED_ANALYSIS_FIELDS
    # (it can be a real, permanent None on a fully-analysed track -- see
    # calculate_time_signature's docstring) -- only time_signature_confidence
    # is the "was this analysed" signal.
    assert "primary_time_signature" not in REQUIRED_ANALYSIS_FIELDS
    assert "time_signature_confidence" in REQUIRED_ANALYSIS_FIELDS

    track = _make_track(
        session, primary_time_signature=None, time_signature_confidence=None
    )
    for field in REQUIRED_ANALYSIS_FIELDS:
        if field != "time_signature_confidence":
            setattr(track, field, 1.0)
    session.commit()
    assert track_needs_analysis(track) is True


def test_needs_analysis_false_once_confidently_unconfident(session):
    # A track can be fully, genuinely analysed and still end up with
    # primary_time_signature=None (not confident enough to guess) -- that
    # must not perpetually re-queue it, as long as time_signature_confidence
    # itself is set (the real, low-but-nonzero confidence value).
    track = _make_track(
        session, primary_time_signature=None, time_signature_confidence=0.08
    )
    for field in REQUIRED_ANALYSIS_FIELDS:
        if field != "time_signature_confidence":
            setattr(track, field, 1.0)
    session.commit()
    assert track_needs_analysis(track) is False


# AC11 ------------------------------------------------------------------------
def test_manual_override_of_time_signature_confidence_clears_needs_analysis(session):
    track = _make_track(
        session, primary_time_signature=None, time_signature_confidence=None
    )
    for field in REQUIRED_ANALYSIS_FIELDS:
        if field != "time_signature_confidence":
            setattr(track, field, 1.0)
    session.commit()
    assert track_needs_analysis(track) is True

    # Simulates a user manually correcting the field and bumping confidence,
    # same mechanism key_confidence already uses.
    track.primary_time_signature = "5/4"
    track.time_signature_confidence = 1.0
    session.commit()
    assert track_needs_analysis(track) is False


def test_time_signature_confidence_field_is_editable():
    spec = TRACK_FIELDS["time_signature_confidence"]
    assert spec.editable is True


# AC12 ------------------------------------------------------------------------
def test_confident_only_toggle_excludes_low_confidence_tracks(session):
    _make_track(session, primary_time_signature="7/8", time_signature_confidence=0.2)
    _make_track(session, primary_time_signature="4/4", time_signature_confidence=0.9)
    stats = AudioStats(lambda: session)
    dist = stats._time_signature_distribution(session)
    assert dist["all"] == {"7/8": 1, "4/4": 1}
    assert dist["confident"] == {"4/4": 1}


# AC13 ------------------------------------------------------------------------
def test_metadata_tag_mapping_covers_time_signature_confidence():
    assert VORBIS_TRACK_MAPPINGS["TIMESIGNATURECONFIDENCE"] == {
        "field": "time_signature_confidence",
        "type": float,
        "entity": "Track",
    }
