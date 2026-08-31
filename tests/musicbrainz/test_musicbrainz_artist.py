"""Tests for src/musicbrainz/musicbrainz_artist.py scalar enrichment parsing.

Currently focused on the sort_name field added per
docs/specs/artist_sort_name.md -- MB returns `sort-name` on both a
search-result list item and a full get_artist_by_id lookup, and
_extract_scalar_enrichment is the single mapper both paths share.
"""

from src.musicbrainz.musicbrainz_artist import _extract_scalar_enrichment


# AC3 -- MB enrichment parse ------------------------------------------------
def test_sort_name_is_parsed_from_mb_payload():
    enrichment = _extract_scalar_enrichment(
        {"id": "mbid-1", "name": "The Beatles", "sort-name": "Beatles, The"}
    )
    assert enrichment["sort_name"] == "Beatles, The"


def test_missing_sort_name_key_yields_no_entry():
    enrichment = _extract_scalar_enrichment({"id": "mbid-2", "name": "Aphex Twin"})
    assert "sort_name" not in enrichment


def test_blank_sort_name_yields_no_entry():
    enrichment = _extract_scalar_enrichment({"id": "mbid-3", "name": "x", "sort-name": ""})
    assert "sort_name" not in enrichment
