"""Qt-free FTS5 MATCH query builder shared by chart search and chart matching."""

import re

_FTS_TOKEN_RE = re.compile(r"\w+")

# Dropped so a leading-article mismatch ("The Radioactive" vs "Radioactive") still
# matches, and because FTS5 cost scales with how many rows hold a term -- common
# words are the expensive ones, rare words are the ones that discriminate.
_STOPWORDS = {"a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in", "into", "is", "it", "of", "on", "or", "that", "the", "to", "was", "with"}


def fts_prefix_terms(text: str) -> list:
    """Tokenize text into quoted FTS5 prefix terms with stopwords dropped."""
    tokens = _FTS_TOKEN_RE.findall(text.lower())
    significant = [t for t in tokens if t not in _STOPWORDS]
    # All-stopword input ("The The") falls back to the unfiltered tokens.
    return [f'"{token}"*' for token in (significant or tokens)]


def build_and_query(text: str) -> str:
    """Return an AND-joined MATCH query in which every remaining word must appear as a prefix."""
    return " ".join(fts_prefix_terms(text))
