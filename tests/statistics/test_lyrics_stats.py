"""Tests for LyricsStats' word_cloud vs. word_suggestions split
(src/statistics/stats/lyrics.py): the mood-tagging dialog's "Refresh
Suggestions" appeared to do nothing once a user worked through the word
list, because both the WordCloudWidget chart and the mood dialog's
suggestion feed shared the same WORD_CLOUD_TOP_N-capped list -- a fixed,
deterministic top-N that never grows no matter how many times it's
refetched. word_suggestions exposes the full ranked list instead, while
word_cloud stays capped for the chart.
"""

from src.statistics.stats.lyrics import WORD_CLOUD_TOP_N, LyricsStats


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *_args, **_kwargs):
        return self

    def all(self):
        return self._rows


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows

    def query(self, *_args, **_kwargs):
        return _FakeQuery(self._rows)

    def close(self):
        pass


def _distinct_word(i: int) -> str:
    """An all-alphabetic, guaranteed-distinct token -- _WORD_RE only matches
    [a-zA-Z'], so a numeric suffix (e.g. "lexeme0") would have its digits
    silently dropped by tokenization and collide with every other index."""
    letters = "abcdefghijklmnopqrstuvwxyz"
    suffix = "".join(letters[(i // (26**p)) % 26] for p in range(4))
    return f"lexeme{suffix}"


def _make_rows(word_count: int, tracks_per_word: int = 5):
    """`tracks_per_word` tracks, each one lyric line containing every one of
    `word_count` distinct words once -- so every word clears
    WORD_CLOUD_MIN_TRACKS (5) document frequency."""
    words = [_distinct_word(i) for i in range(word_count)]
    lyrics_line = " ".join(words)
    return [(lyrics_line, None) for _ in range(tracks_per_word)]


def test_word_cloud_is_unsliced_on_the_stats_object_directly():
    rows = _make_rows(WORD_CLOUD_TOP_N + 50)
    stats = LyricsStats(session_factory=None)

    full = stats._word_cloud(rows)

    assert len(full) == WORD_CLOUD_TOP_N + 50


def test_get_comprehensive_lyrics_stats_caps_word_cloud_but_not_suggestions():
    rows = _make_rows(WORD_CLOUD_TOP_N + 50)
    stats = LyricsStats(session_factory=lambda: _FakeSession(rows))

    result = stats.get_comprehensive_lyrics_stats()

    assert len(result["word_cloud"]) == WORD_CLOUD_TOP_N
    assert len(result["word_suggestions"]) == WORD_CLOUD_TOP_N + 50
    # word_cloud is a prefix of word_suggestions -- same ranking, just capped.
    assert result["word_suggestions"][:WORD_CLOUD_TOP_N] == result["word_cloud"]


def test_word_suggestions_matches_word_cloud_when_under_the_cap():
    rows = _make_rows(20)
    stats = LyricsStats(session_factory=lambda: _FakeSession(rows))

    result = stats.get_comprehensive_lyrics_stats()

    assert result["word_cloud"] == result["word_suggestions"]
    assert len(result["word_cloud"]) == 20


# Phrase (n-gram) detection ------------------------------------------------


def test_phrase_candidates_surfaces_a_repeated_trigram():
    lyric = "you broke my heart and walked away, you broke my heart again"
    rows = [(lyric, None)] * 5

    stats = LyricsStats(session_factory=None)
    phrases = dict(stats._phrase_candidates(rows))

    # "broke my" and "my heart" are each edge-anchored on "my", a stopword,
    # so only the full trigram (edges "broke"/"heart", both content words)
    # clears the boundary filter.
    assert "broke my heart" in phrases
    assert "broke my" not in phrases
    assert "my heart" not in phrases


def test_phrase_candidates_keeps_interior_stopword_phrases():
    # "state of mind" -- edges ("state"/"mind") are content words even
    # though the phrase's interior word ("of") is a stopword.
    lyric = "living in a state of mind tonight, dreaming in a state of mind"
    rows = [(lyric, None)] * 5

    stats = LyricsStats(session_factory=None)
    phrases = dict(stats._phrase_candidates(rows))

    assert "state of mind" in phrases
    assert "of mind" not in phrases  # edge-anchored on "of", a stopword


def test_phrase_candidates_drops_stopword_anchored_fragments():
    lyric = "and i walked away and i cried and i walked away and i cried"
    rows = [(lyric, None)] * 5

    stats = LyricsStats(session_factory=None)
    phrases = dict(stats._phrase_candidates(rows))

    # "and i", "i walked" (starts on "i", a stopword) shouldn't appear --
    # only content-anchored phrases like "walked away" should.
    assert "and i" not in phrases
    assert "i walked" not in phrases
    assert "walked away" in phrases


def test_phrase_candidates_respects_min_tracks_threshold():
    # Appears in only 4 tracks -- below PHRASE_MIN_TRACKS (5).
    rows = [("broke my heart tonight", None)] * 4

    stats = LyricsStats(session_factory=None)
    phrases = dict(stats._phrase_candidates(rows))

    assert "broke my" not in phrases


def test_get_comprehensive_lyrics_stats_includes_phrase_suggestions():
    lyric = "broke my heart again, broke my heart tonight"
    rows = [(lyric, None)] * 5
    stats = LyricsStats(session_factory=lambda: _FakeSession(rows))

    result = stats.get_comprehensive_lyrics_stats()

    assert "phrase_suggestions" in result
    assert dict(result["phrase_suggestions"])["broke my heart"] == 10
