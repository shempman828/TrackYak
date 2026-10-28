"""Entity completer: per-entity context hints and the completer edit widgets
(Enter picks, reset, context map, create-new row).
"""

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from src.common.widgets.entity_completer_context import album_context_map, artist_context_map, place_context_map, publisher_context_map, track_context_map
from src.common.widgets.entity_completer_edit import BoundedSearchEdit, ContextItemDelegate, EntityCompleterEdit, build_entity_search_widget, invalidate_entity_cache

# ---------------------------------------------------------------------------
# Unit tests for the entity-completer secondary-context builders.
# ---------------------------------------------------------------------------


class _Artist:
    def __init__(self, artist_id, artist_name, **kw):
        self.artist_id = artist_id
        self.artist_name = artist_name
        self.disambiguation = kw.get("disambiguation")
        self.isgroup = kw.get("isgroup")
        self._span = kw.get("career_span")

    @property
    def career_span(self):
        return self._span


class _Place:
    def __init__(self, place_id, place_name, place_type=None, parent=None):
        self.place_id = place_id
        self.place_name = place_name
        self.place_type = place_type
        self.parent = parent


class _ArtistName:
    def __init__(self, name):
        self.artist_name = name


class _Track:
    def __init__(self, track_id, primary_artists=(), album_name=None):
        self.track_id = track_id
        self.primary_artists = list(primary_artists)
        self.album_name = album_name


class _Album:
    def __init__(self, album_id, album_artists=(), release_year=None):
        self.album_id = album_id
        self.album_artists = list(album_artists)
        self.release_year = release_year


class _Publisher:
    def __init__(self, publisher_id, publisher_name, parent=None, begin_year=None, end_year=None):
        self.publisher_id = publisher_id
        self.publisher_name = publisher_name
        self.parent = parent
        self.begin_year = begin_year
        self.end_year = end_year


def test_artist_context_prefers_disambiguation():
    a = _Artist(
        1, "Nirvana", disambiguation="US grunge band", career_span="1987\u20131994", isgroup=1
    )
    assert artist_context_map([a]) == {1: "US grunge band"}


def test_artist_context_falls_back_to_career_span_then_group_person():
    span = _Artist(2, "Air", career_span="1995\u2013present", isgroup=1)
    group = _Artist(3, "Boston", isgroup=1)
    person = _Artist(4, "Prince", isgroup=0)
    bare = _Artist(5, "???", isgroup=None)
    out = artist_context_map([span, group, person, bare])
    assert out == {2: "1995\u2013present", 3: "Group", 4: "Person", 5: ""}


def test_place_context_type_and_country_from_parent_chain():
    country = _Place(10, "United Kingdom", "Country")
    region = _Place(11, "England", "Subdivision", parent=country)
    city = _Place(12, "Liverpool", "City", parent=region)
    assert place_context_map([city]) == {12: "City · England, United Kingdom"}


def test_place_context_includes_every_ancestor_level():
    country = _Place(13, "United States", "Country")
    subdivision = _Place(14, "Missouri", "Subdivision", parent=country)
    county = _Place(15, "Greene County", "County", parent=subdivision)
    city = _Place(16, "Springfield", "City", parent=county)
    assert place_context_map([city]) == {16: "City · Greene County, Missouri, United States"}


def test_place_context_degrades_to_type_or_country_or_empty():
    lonely = _Place(20, "Atlantis", "City")
    no_type = _Place(21, "Nowhere", None, parent=_Place(22, "Canada", "Country"))
    orphan = _Place(23, "Mystery", None)
    out = place_context_map([lonely, no_type, orphan])
    assert out == {20: "City", 21: "Canada", 23: ""}


def test_place_context_survives_parent_cycle():
    a = _Place(30, "A", "City")
    a.parent = a  # self-referential FK gone wrong -- must not hang
    # The lone chain entry is a's own name, filtered out, so the walk
    # degrades to just the type rather than spinning forever.
    assert place_context_map([a]) == {30: "City"}


def test_place_context_omits_country_equal_to_own_name():
    usa = _Place(40, "United States", "Country")
    usa.parent = _Place(41, "United States", "Country")
    assert place_context_map([usa]) == {40: "Country"}


def test_track_context_artist_and_album():
    t = _Track(1, primary_artists=[_ArtistName("Radiohead")], album_name="OK Computer")
    assert track_context_map([t]) == {1: "Radiohead · OK Computer"}


def test_track_context_degrades():
    only_artist = _Track(2, primary_artists=[_ArtistName("Aphex Twin")])
    only_album = _Track(3, album_name="Untitled")
    nothing = _Track(4)
    multi = _Track(
        5, primary_artists=[_ArtistName("Jay-Z"), _ArtistName("Kanye West")], album_name="WTT"
    )
    out = track_context_map([only_artist, only_album, nothing, multi])
    assert out == {2: "Aphex Twin", 3: "Untitled", 4: "", 5: "Jay-Z, Kanye West · WTT"}


def test_album_context_artist_and_year():
    a = _Album(1, album_artists=[_ArtistName("Radiohead")], release_year=1997)
    assert album_context_map([a]) == {1: "Radiohead · 1997"}


def test_album_context_degrades():
    only_artist = _Album(2, album_artists=[_ArtistName("Aphex Twin")])
    only_year = _Album(3, release_year=2001)
    nothing = _Album(4)
    multi = _Album(
        5, album_artists=[_ArtistName("Jay-Z"), _ArtistName("Kanye West")], release_year=2011
    )
    out = album_context_map([only_artist, only_year, nothing, multi])
    assert out == {2: "Aphex Twin", 3: "2001", 4: "", 5: "Jay-Z, Kanye West · 2011"}


def test_publisher_context_parent_and_lifespan():
    parent = _Publisher(1, "Universal Music Group")
    sub = _Publisher(2, "Interscope", parent=parent, begin_year=1990)
    assert publisher_context_map([sub]) == {2: "Universal Music Group · 1990\u2013present"}


def test_publisher_context_empty_when_nothing_known():
    assert publisher_context_map([_Publisher(3, "Indie")]) == {3: ""}


# ---------------------------------------------------------------------------
# Regression test: pressing Enter with a completer suggestion highlighted
# ---------------------------------------------------------------------------


class _StubEntity:
    def __init__(self, genre_id, genre_name):
        self.genre_id = genre_id
        self.genre_name = genre_name


class _StubGet:
    def get_all_entities(self, model_name, **kwargs):
        return [_StubEntity(1, "Rock"), _StubEntity(2, "Rockabilly")]


class _StubController:
    def __init__(self):
        self.get = _StubGet()


def _highlight_first_suggestion(widget) -> None:
    """Select the popup's first row the way arrowing down to it would --
    done directly on the popup's model rather than via a synthetic Down
    keypress because the popup's real-desktop keyboard grab (what makes
    Down move its own selection instead of the line edit's) doesn't
    establish under the headless/offscreen QPA platform tests run under."""
    popup = widget._completer.popup()
    popup.setCurrentIndex(popup.model().index(0, 0))


def test_entity_completer_edit_enter_picks_highlighted_suggestion(qapp):
    widget = EntityCompleterEdit()
    widget.set_index({"Rock": 1, "Rockabilly": 2, "Pop": 3})

    captured = {}
    widget.returnPressed.connect(
        lambda: captured.update(text=widget.text(), matched_id=widget.matched_id())
    )

    QTest.keyClicks(widget, "Roc")
    _highlight_first_suggestion(widget)
    QTest.keyClick(widget, Qt.Key_Return)

    assert captured == {"text": "Rock", "matched_id": 1}


def test_bounded_search_edit_enter_picks_highlighted_suggestion(qapp):
    widget = BoundedSearchEdit(_StubController(), "Genre", "genre_name", "genre_id")

    captured = {}
    widget.returnPressed.connect(
        lambda: captured.update(text=widget.text(), matched_id=widget.matched_id())
    )

    QTest.keyClicks(widget, "Roc")
    _highlight_first_suggestion(widget)
    QTest.keyClick(widget, Qt.Key_Return)

    assert captured == {"text": "Rock", "matched_id": 1}


def test_entity_completer_edit_reset_clears_matched_id_before_signal(qapp):
    """reset()'s self.clear() is setText(""), which -- like the completion-
    pick path above -- emits textChanged synchronously. A textChanged
    listener (e.g. a dialog gating its OK button on matched_id()) must see
    matched_id() already cleared to None from inside that signal, not the
    stale id from before reset() was called."""
    widget = EntityCompleterEdit()
    widget.set_index({"Rock": 1, "Rockabilly": 2, "Pop": 3})
    widget._completer.activated.emit("Rock")
    assert widget.matched_id() == 1

    seen_during_signal = []
    widget.textChanged.connect(lambda _text: seen_during_signal.append(widget.matched_id()))

    widget.reset()

    assert seen_during_signal == [None]
    assert widget.matched_id() is None


def test_bounded_search_edit_reset_clears_matched_id_before_signal(qapp):
    widget = BoundedSearchEdit(_StubController(), "Genre", "genre_name", "genre_id")
    QTest.keyClicks(widget, "Roc")
    _highlight_first_suggestion(widget)
    widget._on_completion_picked("Rock")
    assert widget.matched_id() == 1

    seen_during_signal = []
    widget.textChanged.connect(lambda _text: seen_during_signal.append(widget.matched_id()))

    widget.reset()

    assert seen_during_signal == [None]
    assert widget.matched_id() is None


def test_set_index_without_context_map_is_unchanged(qapp):
    """AC1: set_index() with no context map leaves every row's context
    empty and behaves exactly as before."""
    widget = EntityCompleterEdit()
    widget.set_index({"Rock": 1, "Pop": 3})
    assert widget._display_to_context == {"Rock": "", "Pop": ""}


def test_set_index_stores_context_keyed_by_display(qapp):
    """AC1/AC2: context is handed in keyed by entity id but resolved to the
    display key -- including a disambiguated 'Name #id' key."""
    widget = EntityCompleterEdit()
    widget.set_index({"Foo #1": 1, "Foo #2": 2}, {1: "first band", 2: "second band"})
    assert widget._display_to_context == {"Foo #1": "first band", "Foo #2": "second band"}


def test_pick_sets_bare_name_not_context(qapp):
    """AC2: choosing a suggestion puts only the display key in the field --
    the context string never leaks into the line edit."""
    widget = EntityCompleterEdit()
    widget.set_index({"Wolves": 7}, {7: "Cornwall folk act"})
    widget._completer.activated.emit("Wolves")
    assert widget.text() == "Wolves"
    assert widget.matched_id() == 7


def test_delegate_reads_context_for_display_key(qapp):
    """AC2: the popup delegate resolves each row's dimmed context via the
    widget's live display->context lookup."""
    widget = EntityCompleterEdit()
    widget.set_index({"Rock": 1, "Pop": 3}, {1: "genre", 3: ""})
    delegate = widget._completer.popup().itemDelegate()
    assert isinstance(delegate, ContextItemDelegate)
    assert delegate._context_getter("Rock") == "genre"
    assert delegate._context_getter("Pop") == ""
    assert delegate._context_getter("unknown") == ""


def test_add_to_index_carries_context(qapp):
    widget = EntityCompleterEdit()
    widget.set_index({"Rock": 1}, {1: "genre"})
    widget.add_to_index("Techno", 9, context="electronic")
    assert widget._display_to_context["Techno"] == "electronic"
    assert widget._display_to_context["Rock"] == "genre"


class _ContextGet:
    def __init__(self, rows):
        self._rows = rows

    def count_entities(self, model_name):
        return len(self._rows)

    def get_all_entities(self, model_name, **kwargs):
        if not kwargs:
            return list(self._rows)
        # crude __contains emulation for the bounded path
        ((key, needle),) = kwargs.items()
        field = key.split("__")[0]
        return [r for r in self._rows if needle.lower() in getattr(r, field).lower()]


class _ContextController:
    def __init__(self, rows):
        self.get = _ContextGet(rows)


def _genre_context_map(rows):
    return {r.genre_id: f"ctx-{r.genre_name}" for r in rows}


def test_build_entity_search_widget_applies_context_builder_preloaded(qapp):
    """AC3: context_builder runs against the preloaded list on the
    EntityCompleterEdit path."""
    rows = [_StubEntity(1, "Rock"), _StubEntity(2, "Jazz")]
    invalidate_entity_cache("CtxGenrePre")
    widget = build_entity_search_widget(
        _ContextController(rows),
        "CtxGenrePre",
        "genre_name",
        "genre_id",
        context_builder=_genre_context_map,
    )
    invalidate_entity_cache("CtxGenrePre")
    assert isinstance(widget, EntityCompleterEdit)
    assert widget._display_to_context == {"Rock": "ctx-Rock", "Jazz": "ctx-Jazz"}


def test_no_create_new_row_without_the_flag(qapp):
    widget = EntityCompleterEdit()
    widget.set_index({"Rock": 1, "Rockabilly": 2})
    QTest.keyClicks(widget, "Roc")
    assert not any(r.startswith("Create new ") for r in widget._completer.model().stringList())
    assert widget.wants_new_entity() is False


def test_entity_completer_edit_offers_and_flags_create_new(qapp):
    widget = EntityCompleterEdit(allow_create_new=True)
    widget.set_index({"Kind of Blue": 1})

    QTest.keyClicks(widget, "Kind of Blue")
    rows = widget._completer.model().stringList()
    assert rows[-1] == 'Create new "Kind of Blue"'  # always last, even on exact hit

    widget._completer.activated.emit('Create new "Kind of Blue"')
    assert widget.wants_new_entity() is True
    assert widget.matched_id() is None
    assert widget.text() == "Kind of Blue"  # typed name kept, sentinel not inserted


def test_create_new_flag_clears_on_further_edit_and_reset(qapp):
    widget = EntityCompleterEdit(allow_create_new=True)
    widget.set_index({"Foo": 1})
    QTest.keyClicks(widget, "Foo")
    widget._completer.activated.emit('Create new "Foo"')
    assert widget.wants_new_entity() is True

    QTest.keyClicks(widget, "!")  # any manual edit cancels the intent
    assert widget.wants_new_entity() is False

    widget._completer.activated.emit('Create new "Foo!"')
    assert widget.wants_new_entity() is True
    widget.reset()
    assert widget.wants_new_entity() is False


def test_bounded_search_edit_offers_and_flags_create_new(qapp):
    widget = BoundedSearchEdit(
        _ContextController([_StubEntity(1, "Greatest Hits")]),
        "Album",
        "genre_name",
        "genre_id",
        allow_create_new=True,
    )
    QTest.keyClicks(widget, "Greatest Hits")
    rows = widget._model.stringList()
    assert rows[-1] == 'Create new "Greatest Hits"'

    widget._on_completion_picked('Create new "Greatest Hits"')
    assert widget.wants_new_entity() is True
    assert widget.matched_id() is None
    assert widget.text() == "Greatest Hits"

    widget.reset()
    assert widget.wants_new_entity() is False


def test_bounded_search_edit_applies_context_builder(qapp):
    """AC8: context shows for on-demand results and known_matches() is
    unaffected."""
    rows = [_StubEntity(1, "Rock"), _StubEntity(2, "Rockabilly"), _StubEntity(3, "Pop")]
    widget = BoundedSearchEdit(
        _ContextController(rows),
        "Genre",
        "genre_name",
        "genre_id",
        context_builder=_genre_context_map,
    )
    QTest.keyClicks(widget, "Rock")
    assert widget._display_to_context == {"Rock": "ctx-Rock", "Rockabilly": "ctx-Rockabilly"}
    assert {e.genre_name for e in widget.known_matches()} == {"Rock", "Rockabilly"}
