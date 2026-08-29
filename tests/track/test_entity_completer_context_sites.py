"""AC7 regression: the call sites that used to bake context into the
completion key (influences' "Name (Type)" and samples' "Name  [Album]")
now emit a bare name as the completion value, so a multi-entry add or a
name-based resolution can't turn the decorated string into a spurious new
entity. The context still reaches the popup via the dimmed secondary-text
channel.
"""

from src.artist.artist_edit_influences import _artist_display, _build_artist_index
from src.common.entity_completer_context import artist_context_map, track_context_map
from src.common.entity_completer_edit import EntityCompleterEdit
from src.track.track_edit_samples import _build_track_index, _track_display


class _Type:
    def __init__(self, type_name):
        self.type_name = type_name


class _Artist:
    def __init__(self, artist_id, artist_name, types=(), disambiguation=None):
        self.artist_id = artist_id
        self.artist_name = artist_name
        self.types = list(types)
        self.disambiguation = disambiguation
        self.isgroup = 1
        self.career_span = None


class _Track:
    def __init__(self, track_id, track_name, album_name=None, primary_artists=()):
        self.track_id = track_id
        self.track_name = track_name
        self.album_name = album_name
        self.primary_artists = list(primary_artists)


def test_influences_artist_display_is_bare_name():
    a = _Artist(1, "Portishead", types=[_Type("Group"), _Type("Trip Hop")])
    assert _artist_display(a) == "Portishead"
    assert _build_artist_index([a]) == {"Portishead": 1}


def test_samples_track_display_is_bare_name():
    t = _Track(5, "Glory Box", album_name="Dummy")
    assert _track_display(t) == "Glory Box"
    assert _build_track_index([t]) == {"Glory Box": 5}


def test_influences_pick_feeds_bare_name_into_field(qapp):
    a = _Artist(1, "Portishead", types=[_Type("Group")], disambiguation="Bristol trip hop")
    index = _build_artist_index([a])
    widget = EntityCompleterEdit()
    widget.set_index(index, artist_context_map([a]))

    widget._completer.activated.emit(next(iter(index)))

    assert widget.text() == "Portishead"
    assert widget.split_names() == ["Portishead"]
    assert widget.matched_id() == 1
    # context is carried, just not in the field text
    assert widget._display_to_context["Portishead"] == "Bristol trip hop"


def test_samples_context_map_has_artist_and_album():
    class _Name:
        def __init__(self, n):
            self.artist_name = n

    t = _Track(5, "Glory Box", album_name="Dummy", primary_artists=[_Name("Portishead")])
    assert track_context_map([t]) == {5: "Portishead · Dummy"}
