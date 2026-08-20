"""
stats/lyrics.py

LyricsStats: corpus word cloud and high-vs-low-rated word weighting, built
from stdlib re/collections.Counter only (no NLP dependency).

Words are ranked by how many distinct tracks they appear in (document
frequency), not raw token count, so a single track repeating a word in its
chorus can't dominate the corpus-wide picture. The high/low-rated split
uses the same rating validity range as the rest of the ratings stats
(RATING_MIN/RATING_MAX) and splits tracks at the median rating among
lyricized, validly-rated tracks.
"""

import re
from collections import Counter

from src.db.db_tables import Track
from src.statistics.stats.helpers import RATING_MAX, RATING_MIN

WORD_CLOUD_MIN_TRACKS = 5
WORD_CLOUD_TOP_N = 150
WEIGHTED_WORDS_MIN_TRACKS = 5
WEIGHTED_WORDS_TOP_N = 25

# [Chorus], [Verse 1], [Bridge] etc. -- and any timestamp-shaped token
# (defensive -- this schema's lyrics field has no real LRC timestamps, but
# strip them if a source ever leaks one in).
_BRACKET_RE = re.compile(r"\[[^\]]*\]")
_TIMESTAMP_RE = re.compile(r"\b\d{1,2}:\d{2}(?:\.\d+)?\b")
_WORD_RE = re.compile(r"[a-zA-Z']+")

# Lyrics-oriented stopword list -- deliberately broader than
# fts_query.py's 21-word title-matching list, since lyrics are full
# natural-language text, not search titles.
STOPWORDS = frozenset(
    """
    a about above after again against ain all am an and any are aren aren't
    as at back be because been before being below between both but by
    came can can't cannot come could couldn't did didn't do does doesn't
    doing don don't down during each few for from further get gets go
    goes going gonna gotta had hadn't has hasn't have haven't having he
    he'd he'll he's her here here's hers herself him himself his how
    how's i i'd i'll i'm i've if in into is isn't it it's its itself
    just know let let's like ll ma me more most mustn't my myself never
    no nor not now of off on once only or other ought our ours ourselves
    out over own re s same shan't she she'd she'll she's should shouldn't
    so some such t than that that's the their theirs them themselves
    then there there's these they they'd they'll they're they've this
    those through to too under until up ve very was wasn't we we'd
    we'll we're we've were weren't what what's when when's where where's
    which while who who's whom why why's will with won't would wouldn't
    yeah yes you you'd you'll you're you've your yours yourself
    yourselves ooh oh uh la na whoa ah hey mm mmm gon wanna oohh ohh
    """.split()
)


def _tokenize(lyrics):
    cleaned = _BRACKET_RE.sub(" ", lyrics)
    cleaned = _TIMESTAMP_RE.sub(" ", cleaned)
    words = _WORD_RE.findall(cleaned.lower())
    return [w for w in words if w not in STOPWORDS and len(w) > 1]


class LyricsStats:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    def get_comprehensive_lyrics_stats(self):
        session = self.session_factory()
        try:
            rows = (
                session.query(Track.lyrics, Track.user_rating)
                .filter(Track.lyrics.isnot(None), Track.lyrics != "")
                .all()
            )
            return {
                "word_cloud": self._word_cloud(rows),
                "weighted_words": self._weighted_words(rows),
            }
        finally:
            session.close()

    def _word_cloud(self, rows):
        """[(word, count), ...] sorted by corpus-wide token count, limited
        to words appearing in at least WORD_CLOUD_MIN_TRACKS distinct
        tracks."""
        token_counts = Counter()
        doc_counts = Counter()
        for lyrics, _rating in rows:
            words = _tokenize(lyrics)
            if not words:
                continue
            token_counts.update(words)
            doc_counts.update(set(words))

        eligible = [
            word for word, n in doc_counts.items() if n >= WORD_CLOUD_MIN_TRACKS
        ]
        eligible.sort(key=lambda word: token_counts[word], reverse=True)
        top = eligible[:WORD_CLOUD_TOP_N]
        return [(word, token_counts[word]) for word in top]

    def _weighted_words(self, rows):
        """Words whose document-frequency skews most toward above-median-
        rated tracks ("high") or below-median-rated tracks ("low").

        Returns {"high": [(word, skew), ...], "low": [(word, skew), ...]},
        both sorted most-skewed first. skew is the difference between the
        word's fraction-of-tracks in each bucket (0..1 range), so it's
        comparable across words regardless of the bucket sizes.
        """
        rated = [
            (lyrics, rating)
            for lyrics, rating in rows
            if rating is not None and RATING_MIN <= rating <= RATING_MAX
        ]
        if len(rated) < 2:
            return {"high": [], "low": []}

        ratings = sorted(rating for _lyrics, rating in rated)
        mid = len(ratings) // 2
        median = (
            ratings[mid]
            if len(ratings) % 2
            else (ratings[mid - 1] + ratings[mid]) / 2
        )

        high_docs = Counter()
        low_docs = Counter()
        high_total = 0
        low_total = 0
        for lyrics, rating in rated:
            words = set(_tokenize(lyrics))
            if not words:
                continue
            if rating >= median:
                high_docs.update(words)
                high_total += 1
            else:
                low_docs.update(words)
                low_total += 1

        if not high_total or not low_total:
            return {"high": [], "low": []}

        skews = []
        for word in set(high_docs) | set(low_docs):
            total_docs = high_docs.get(word, 0) + low_docs.get(word, 0)
            if total_docs < WEIGHTED_WORDS_MIN_TRACKS:
                continue
            high_frac = high_docs.get(word, 0) / high_total
            low_frac = low_docs.get(word, 0) / low_total
            skews.append((word, high_frac - low_frac))

        skews.sort(key=lambda item: item[1], reverse=True)
        high = [(word, round(skew, 4)) for word, skew in skews if skew > 0][
            :WEIGHTED_WORDS_TOP_N
        ]
        low = [
            (word, round(-skew, 4))
            for word, skew in reversed(skews)
            if skew < 0
        ][:WEIGHTED_WORDS_TOP_N]
        return {"high": high, "low": low}
