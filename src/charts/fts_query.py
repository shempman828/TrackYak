"""
fts_query.py

Shared FTS5 MATCH query-building helper, used by chart_search_tab.py's
title/artist search box and chart_matching.py's candidate shortlisting.

Filters out a short list of common English stopwords before building the
query. Two reasons, one for each caller:
  - It's what makes a leading-article mismatch ("The Radioactive" vs.
    "Radioactive") resolve to a single required term instead of two,
    so chart_matching.py's shortlist lookup still finds it.
  - Query cost against SQLite's FTS5 scales with how many rows contain the
    query's terms, not with how many rows come out after LIMIT --
    "ORDER BY rank LIMIT n" still has to score every matching row first.
    A term like "the" or "love" can appear in a large fraction of a real
    music library, making it expensive per lookup at chart-matching scale
    (hundreds of thousands of entries); dropping stopwords keeps queries
    anchored to the words that actually discriminate one title from
    another, which are also the rare (cheap) ones in the index.
If every token turns out to be a stopword, the original tokens are used
unfiltered rather than searching on nothing.

No Qt dependency here -- chart_matching.py deliberately has none either
(see its module docstring), so this can't pull any in.
"""

import re

_FTS_TOKEN_RE = re.compile(r"\w+")

_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
    "in", "into", "is", "it", "of", "on", "or", "that", "the", "to",
    "was", "with",
}


def fts_prefix_terms(text: str) -> list:
    """Tokenize free-typed text into quoted FTS5 prefix terms, stopwords
    dropped, e.g. "The Radioactive" -> ['"radioactive"*']. Empty for
    punctuation-only/empty input."""
    tokens = _FTS_TOKEN_RE.findall(text.lower())
    significant = [t for t in tokens if t not in _STOPWORDS]
    return [f'"{token}"*' for token in (significant or tokens)]


def build_and_query(text: str) -> str:
    """AND-joined MATCH query: every remaining word must appear (as a
    prefix) somewhere in the indexed row."""
    return " ".join(fts_prefix_terms(text))
