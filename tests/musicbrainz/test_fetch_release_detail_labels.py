"""Tests for fetch_release_detail's label (record company/publisher)
parsing -- the release-embedded label-info-list entry is only a stub
(name, catalog number), so each unique label gets its own get_label_by_id
follow-up (see _fetch_label_by_id/_parse_label) for life-span, annotation,
headquarters area, and founder relations.
"""

from unittest.mock import patch

from src.musicbrainz import musicbrainz_release as mc

_LABEL_ID = "5a5c8d97-9d47-49d8-9958-4b06e6e0f81a"
_NYC_ID = "5099dc37-eab7-3c96-a20d-6f0b3c5f9601"
_USA_ID = "489ce91b-6658-3307-9877-795b68554c98"


def _release():
    return {
        "release": {
            "id": "faada5d1-971b-499c-b902-5bab9e03bc1b",
            "release-group": {"id": "gggggggg-gggg-gggg-gggg-gggggggggggg"},
            "label-info-list": [
                {
                    "catalog-number": "ATL-1",
                    "label": {"id": _LABEL_ID, "name": "Atlantic Records"},
                }
            ],
            "medium-list": [],
        }
    }


def _get_label_by_id(label_mbid, includes=None):
    assert label_mbid == _LABEL_ID
    return {
        "label": {
            "id": _LABEL_ID,
            "name": "Atlantic Records",
            "disambiguation": "US label",
            "life-span": {"begin": "1947-10", "end": None},
            "area": {"id": _NYC_ID, "name": "New York City"},
            "annotation": {"text": "Founded by Ahmet Ertegun and Herb Abramson."},
            "artist-relation-list": [
                {
                    "type": "founder",
                    "artist": {"id": "aaaaaaaa-1111-1111-1111-111111111111", "name": "Ahmet Ertegun"},
                },
                {
                    "type": "founder",
                    "artist": {"id": "bbbbbbbb-2222-2222-2222-222222222222", "name": "Herb Abramson"},
                },
                # Not a founder relation -- must be filtered out.
                {
                    "type": "legal representation",
                    "artist": {"id": "cccccccc-3333-3333-3333-333333333333", "name": "Some Lawyer"},
                },
            ],
        }
    }


def _get_area_by_id(area_mbid, includes=None):
    if area_mbid == _NYC_ID:
        return {
            "area": {
                "name": "New York City",
                "type": "City",
                "area-relation-list": [
                    {
                        "type": "part of",
                        "direction": "backward",
                        "area": {"id": _USA_ID, "name": "United States", "type": "Country"},
                    }
                ],
            }
        }
    if area_mbid == _USA_ID:
        return {"area": {"name": "United States", "type": "Country", "area-relation-list": []}}
    raise AssertionError(f"unexpected area_mbid lookup: {area_mbid}")


class TestFetchReleaseDetailLabels:
    def test_parses_label_scalars_annotation_and_founders(self):
        with (
            patch.object(mc.musicbrainzngs, "get_release_by_id", return_value=_release()),
            patch.object(
                mc.musicbrainzngs, "get_label_by_id", side_effect=_get_label_by_id
            ),
            patch.object(mc.musicbrainzngs, "get_area_by_id", side_effect=_get_area_by_id),
        ):
            detail = mc.fetch_release_detail("faada5d1-971b-499c-b902-5bab9e03bc1b")

        assert len(detail.labels) == 1
        label = detail.labels[0]
        assert label.mbid == _LABEL_ID
        assert label.name == "Atlantic Records"
        assert label.catalog_number == "ATL-1"
        assert label.disambiguation == "US label"
        assert label.annotation == "Founded by Ahmet Ertegun and Herb Abramson."
        assert label.begin_year == 1947
        assert label.begin_month == 10
        assert label.end_year is None

        founder_names = {f.name for f in label.founders}
        assert founder_names == {"Ahmet Ertegun", "Herb Abramson"}

    def test_resolves_headquarters_area_chain(self):
        with (
            patch.object(mc.musicbrainzngs, "get_release_by_id", return_value=_release()),
            patch.object(
                mc.musicbrainzngs, "get_label_by_id", side_effect=_get_label_by_id
            ),
            patch.object(mc.musicbrainzngs, "get_area_by_id", side_effect=_get_area_by_id),
        ):
            detail = mc.fetch_release_detail("faada5d1-971b-499c-b902-5bab9e03bc1b")

        chain_names = [node["name"] for node in detail.labels[0].area_chain]
        assert chain_names == ["New York City", "United States"]

    def test_missing_label_id_is_skipped_without_error(self):
        release = _release()
        release["release"]["label-info-list"] = [{"catalog-number": "NO-ID", "label": {}}]
        with (
            patch.object(mc.musicbrainzngs, "get_release_by_id", return_value=release),
            patch.object(mc.musicbrainzngs, "get_label_by_id") as get_label,
        ):
            detail = mc.fetch_release_detail("faada5d1-971b-499c-b902-5bab9e03bc1b")

        get_label.assert_not_called()
        assert detail.labels == []

    def test_label_lookup_failure_is_best_effort(self):
        with (
            patch.object(mc.musicbrainzngs, "get_release_by_id", return_value=_release()),
            patch.object(
                mc.musicbrainzngs, "get_label_by_id", side_effect=RuntimeError("boom")
            ),
        ):
            detail = mc.fetch_release_detail("faada5d1-971b-499c-b902-5bab9e03bc1b")

        assert detail.labels == []
