"""Tests for docs/specs/genre_sort_by_count.md: the genre tree gains a
native, sortable "Tracks" column showing playlist-style "own · recursive"
track counts, loaded on a background GenreLoaderWorker thread. Each test
below maps to one numbered acceptance criterion in that spec.

tests/genre/conftest.py patches GenreLoaderWorker.start to run synchronously
(no real QThread), so GenreView(controller) can be used exactly as before
this feature -- see that file's docstring.
"""

import pytest
from PySide6.QtCore import Qt
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.db_helpers.delete import DeleteDB
from src.db.db_helpers.get import GetFromDB
from src.db.db_helpers.update import UpdateDB
from src.db.db_tables.associations import TrackGenre
from src.db.db_tables.base import Base
from src.db.db_tables.genre import Genre
from src.db.db_tables.track import Track
from src.genre.genre_view import GenreLoaderWorker, GenreView


class _Controller:
    def __init__(self, session):
        self.get = GetFromDB(session)
        self.update = UpdateDB(session)
        self.delete = DeleteDB(session)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


@pytest.fixture
def controller(session):
    return _Controller(session)


def _make_genre(session, name, parent=None):
    genre = Genre(genre_name=name, parent=parent)
    session.add(genre)
    session.commit()
    return genre


def _add_tracks_with_genre(session, genre, n):
    for i in range(n):
        track = Track(track_name=f"{genre.genre_name} track {i}")
        session.add(track)
        session.flush()
        session.add(TrackGenre(track_id=track.track_id, genre_id=genre.genre_id))
    session.commit()


def _top_level_names(tree):
    return [tree.topLevelItem(i).text(0) for i in range(tree.topLevelItemCount())]


def _count_execute_calls(session, monkeypatch):
    """Wrap session.execute to count calls made during the `with` block."""
    calls = {"n": 0}
    original = session.execute

    def counting_execute(*args, **kwargs):
        calls["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(session, "execute", counting_execute)
    return calls


# ---------------------------------------------------------------------------
# AC1 -- GenreLoaderWorker computes correct direct + recursive counts
# ---------------------------------------------------------------------------


def test_worker_computes_direct_and_recursive_counts(session, controller):
    rock = _make_genre(session, "Rock")
    punk = _make_genre(session, "Punk", parent=rock)
    alt = _make_genre(session, "Alt", parent=rock)
    jazz = _make_genre(session, "Jazz")
    _add_tracks_with_genre(session, rock, 1)
    _add_tracks_with_genre(session, punk, 2)
    _add_tracks_with_genre(session, jazz, 3)

    worker = GenreLoaderWorker(controller)
    result = {}
    worker.finished.connect(
        lambda genres, direct, recursive: result.update(
            genres=genres, direct=direct, recursive=recursive
        )
    )
    worker.run()

    assert result["direct"].get(rock.genre_id, 0) == 1
    assert result["direct"].get(punk.genre_id, 0) == 2
    assert result["direct"].get(alt.genre_id, 0) == 0
    assert result["direct"].get(jazz.genre_id, 0) == 3
    assert result["recursive"][rock.genre_id] == 3  # own(1) + punk(2) + alt(0)
    assert result["recursive"][punk.genre_id] == 2
    assert result["recursive"][alt.genre_id] == 0
    assert result["recursive"][jazz.genre_id] == 3
    assert {g.genre_id for g in result["genres"]} == {
        rock.genre_id,
        punk.genre_id,
        alt.genre_id,
        jazz.genre_id,
    }


# ---------------------------------------------------------------------------
# Regression: a track tagged with both a genre and one of its descendants
# must only count once toward the ancestor's recursive total -- summing
# direct_counts + child totals as plain integers double-counts it.
# ---------------------------------------------------------------------------


def test_recursive_count_dedupes_track_tagged_at_multiple_levels(session, controller):
    rock = _make_genre(session, "Rock")
    punk = _make_genre(session, "Punk", parent=rock)

    track = Track(track_name="Overlap track")
    session.add(track)
    session.flush()
    # Tagged with both Rock (parent) and Punk (child) -- one physical track.
    session.add(TrackGenre(track_id=track.track_id, genre_id=rock.genre_id))
    session.add(TrackGenre(track_id=track.track_id, genre_id=punk.genre_id))
    session.commit()

    worker = GenreLoaderWorker(controller)
    result = {}
    worker.finished.connect(
        lambda genres, direct, recursive: result.update(direct=direct, recursive=recursive)
    )
    worker.run()

    assert result["direct"].get(rock.genre_id, 0) == 1
    assert result["direct"].get(punk.genre_id, 0) == 1
    # Naive summation would give 2 (1 own + 1 from Punk); the correct
    # recursive count is 1 unique track.
    assert result["recursive"][rock.genre_id] == 1
    assert result["recursive"][punk.genre_id] == 1


# ---------------------------------------------------------------------------
# AC2 -- worker releases its DB session and emits `error` on failure
# ---------------------------------------------------------------------------


def test_worker_releases_session_and_emits_error_on_exception(
    session, controller, monkeypatch
):
    _make_genre(session, "Rock")

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated query failure")

    monkeypatch.setattr(session, "execute", _boom)

    released = {"called": False}
    monkeypatch.setattr(
        GenreLoaderWorker,
        "_release_db_session",
        staticmethod(lambda: released.__setitem__("called", True)),
    )

    worker = GenreLoaderWorker(controller)
    outcomes = {}
    worker.finished.connect(lambda *a: outcomes.update(finished=a))
    worker.error.connect(lambda msg: outcomes.update(error=msg))
    worker.run()

    assert "finished" not in outcomes
    assert "simulated query failure" in outcomes["error"]
    assert released["called"] is True


# ---------------------------------------------------------------------------
# AC3 -- two-column header with native sorting enabled
# ---------------------------------------------------------------------------


def test_tree_has_genre_and_tracks_columns_with_sorting_enabled(qapp, controller):
    view = GenreView(controller)

    assert view.tree.columnCount() == 2
    assert view.tree.headerItem().text(0) == "Genre"
    assert view.tree.headerItem().text(1) == "Tracks"
    assert view.tree.isSortingEnabled() is True
    assert view.tree.isHeaderHidden() is False


# ---------------------------------------------------------------------------
# AC4 -- default load order is unchanged: case-insensitive alphabetical asc
# ---------------------------------------------------------------------------


def test_initial_load_is_alphabetical_ascending(session, qapp, controller):
    _make_genre(session, "zydeco")
    _make_genre(session, "Blues")
    _make_genre(session, "Jazz")

    view = GenreView(controller)

    assert _top_level_names(view.tree) == ["Blues", "Jazz", "zydeco"]


# ---------------------------------------------------------------------------
# AC5 -- clicking the Tracks column sorts each hierarchy level by recursive
# track count, ascending or descending
# ---------------------------------------------------------------------------


def test_sorting_by_tracks_column_orders_by_recursive_count(session, qapp, controller):
    rock = _make_genre(session, "Rock")
    punk = _make_genre(session, "Punk", parent=rock)
    _make_genre(session, "Alt", parent=rock)
    jazz = _make_genre(session, "Jazz")
    _add_tracks_with_genre(session, rock, 1)
    _add_tracks_with_genre(session, punk, 5)  # rock recursive = 6
    _add_tracks_with_genre(session, jazz, 2)

    view = GenreView(controller)

    view.tree.sortByColumn(1, Qt.SortOrder.DescendingOrder)
    assert _top_level_names(view.tree) == ["Rock", "Jazz"]  # 6 desc 2

    rock_item = view.tree.topLevelItem(0)
    assert [rock_item.child(i).text(0) for i in range(rock_item.childCount())] == [
        "Punk",
        "Alt",
    ]  # 5 desc 0

    view.tree.sortByColumn(1, Qt.SortOrder.AscendingOrder)
    assert _top_level_names(view.tree) == ["Jazz", "Rock"]  # 2 asc 6


# ---------------------------------------------------------------------------
# AC6 -- sorting by the Tracks column never re-queries the database
# ---------------------------------------------------------------------------


def test_sorting_by_tracks_column_issues_no_database_queries(session, qapp, controller, monkeypatch):
    rock = _make_genre(session, "Rock")
    jazz = _make_genre(session, "Jazz")
    _add_tracks_with_genre(session, rock, 1)
    _add_tracks_with_genre(session, jazz, 2)

    view = GenreView(controller)

    calls = _count_execute_calls(session, monkeypatch)
    view.tree.sortByColumn(1, Qt.SortOrder.DescendingOrder)
    assert calls["n"] == 0


# ---------------------------------------------------------------------------
# AC7 / AC8 / AC9 -- Tracks column text: plain count, "own · recursive", or
# collapsed to a single number when recursive == own
# ---------------------------------------------------------------------------


def test_genre_without_subgenres_shows_plain_count(session, qapp, controller):
    jazz = _make_genre(session, "Jazz")
    _add_tracks_with_genre(session, jazz, 3)

    view = GenreView(controller)

    item = view.tree.topLevelItem(0)
    assert item.text(1) == "3"


def test_genre_with_subgenres_shows_own_and_recursive_when_they_differ(
    session, qapp, controller
):
    rock = _make_genre(session, "Rock")
    punk = _make_genre(session, "Punk", parent=rock)
    _add_tracks_with_genre(session, rock, 12)
    _add_tracks_with_genre(session, punk, 30)

    view = GenreView(controller)

    rock_item = view.tree.topLevelItem(0)
    assert rock_item.text(1) == "12 · 42"


def test_genre_with_zero_track_subgenres_collapses_to_single_number(
    session, qapp, controller
):
    rock = _make_genre(session, "Rock")
    _make_genre(session, "Silent Subgenre", parent=rock)  # no tracks
    _add_tracks_with_genre(session, rock, 7)

    view = GenreView(controller)

    rock_item = view.tree.topLevelItem(0)
    assert rock_item.text(1) == "7"  # recursive == own, no "·"


# ---------------------------------------------------------------------------
# AC10 -- Tracks column styling: right-aligned, italic, tooltip when "·"
# ---------------------------------------------------------------------------


def test_tracks_column_is_right_aligned_italic_with_tooltip(session, qapp, controller):
    rock = _make_genre(session, "Rock")
    punk = _make_genre(session, "Punk", parent=rock)
    _add_tracks_with_genre(session, rock, 1)
    _add_tracks_with_genre(session, punk, 2)
    jazz = _make_genre(session, "Jazz")
    _add_tracks_with_genre(session, jazz, 5)

    view = GenreView(controller)

    names = _top_level_names(view.tree)
    rock_item = view.tree.topLevelItem(names.index("Rock"))
    jazz_item = view.tree.topLevelItem(names.index("Jazz"))

    assert rock_item.textAlignment(1) == (Qt.AlignRight | Qt.AlignVCenter)
    assert rock_item.font(1).italic() is True
    assert rock_item.toolTip(1) != ""  # "1 · 3" has a "·" -> tooltip set

    assert jazz_item.text(1) == "5"
    assert jazz_item.toolTip(1) == ""  # no "·" -> no tooltip


# ---------------------------------------------------------------------------
# AC11 -- Tracks column sorts numerically, not lexicographically
# ---------------------------------------------------------------------------


def test_tracks_column_sorts_numerically_not_lexicographically(session, qapp, controller):
    nine = _make_genre(session, "Nine")
    twelve = _make_genre(session, "Twelve")
    _add_tracks_with_genre(session, nine, 9)
    _add_tracks_with_genre(session, twelve, 12)

    view = GenreView(controller)
    view.tree.sortByColumn(1, Qt.SortOrder.AscendingOrder)

    # Lexicographic string sort would put "12" before "9"; numeric sort
    # must put 9 before 12.
    assert _top_level_names(view.tree) == ["Nine", "Twelve"]


# ---------------------------------------------------------------------------
# AC12 -- Flat View sorted by Tracks column: unnested, ordered by count
# ---------------------------------------------------------------------------


def test_flat_view_sorted_by_tracks_column_is_unnested_and_ordered(
    session, qapp, controller
):
    rock = _make_genre(session, "Rock")
    punk = _make_genre(session, "Punk", parent=rock)
    _add_tracks_with_genre(session, rock, 1)
    _add_tracks_with_genre(session, punk, 5)
    jazz = _make_genre(session, "Jazz")
    _add_tracks_with_genre(session, jazz, 2)

    view = GenreView(controller)
    view.flat_view_button.setChecked(True)
    view.toggle_flat_view()
    view.tree.sortByColumn(1, Qt.SortOrder.DescendingOrder)

    assert view.tree.topLevelItemCount() == 3
    for i in range(view.tree.topLevelItemCount()):
        assert view.tree.topLevelItem(i).childCount() == 0
    # Punk(5) > Jazz(2) > Rock(1, recursive incl. Punk = 6... but Punk is
    # itself a flat top-level row here, so Rock's own count in Flat View
    # is still shown as its recursive total, 6)
    assert _top_level_names(view.tree) == ["Rock", "Punk", "Jazz"]


# ---------------------------------------------------------------------------
# AC13 -- sort column/order is preserved across Flat View toggle and reload
# ---------------------------------------------------------------------------


def test_sort_state_preserved_across_flat_view_toggle_and_reload(
    session, qapp, controller
):
    rock = _make_genre(session, "Rock")
    _add_tracks_with_genre(session, rock, 1)
    jazz = _make_genre(session, "Jazz")
    _add_tracks_with_genre(session, jazz, 9)

    view = GenreView(controller)
    view.tree.sortByColumn(1, Qt.SortOrder.DescendingOrder)
    assert _top_level_names(view.tree) == ["Jazz", "Rock"]

    view.flat_view_button.setChecked(True)
    view.toggle_flat_view()
    assert _top_level_names(view.tree) == ["Jazz", "Rock"]  # still count-desc

    view.flat_view_button.setChecked(False)
    view.toggle_flat_view()
    assert _top_level_names(view.tree) == ["Jazz", "Rock"]  # still count-desc

    # Reload (e.g. after an edit) also preserves it, since load_genres()
    # re-triggers _on_genres_loaded -> _rebuild_tree without resetting the
    # header's sort indicator.
    view.load_genres()
    assert _top_level_names(view.tree) == ["Jazz", "Rock"]


# ---------------------------------------------------------------------------
# AC14 -- selection and expansion survive a re-sort / Flat View toggle
# ---------------------------------------------------------------------------


def test_selection_and_expansion_preserved_across_resort(session, qapp, controller):
    rock = _make_genre(session, "Rock")
    punk = _make_genre(session, "Punk", parent=rock)
    _add_tracks_with_genre(session, rock, 1)
    _add_tracks_with_genre(session, punk, 2)

    view = GenreView(controller)
    rock_item = view.tree.topLevelItem(0)
    rock_item.setExpanded(True)
    view.tree.setCurrentItem(rock_item)

    view.tree.sortByColumn(1, Qt.SortOrder.DescendingOrder)

    current = view.tree.currentItem()
    assert current is not None
    assert current.data(0, Qt.UserRole) == rock.genre_id

    view._rebuild_tree()
    names = _top_level_names(view.tree)
    rebuilt_rock = view.tree.topLevelItem(names.index("Rock"))
    assert rebuilt_rock.isExpanded() is True


# ---------------------------------------------------------------------------
# AC15 -- renaming works via column 0 only; the Tracks column is a no-op
# ---------------------------------------------------------------------------


def test_rename_via_name_column_updates_the_genre(session, qapp, controller):
    rock = _make_genre(session, "Rock")

    view = GenreView(controller)
    item = view.tree.topLevelItem(0)
    item.setText(0, "Classic Rock")
    view.on_item_edited(item, 0)

    session.expire(rock)
    assert rock.genre_name == "Classic Rock"


def test_editing_tracks_column_is_a_no_op(session, qapp, controller):
    rock = _make_genre(session, "Rock")
    _add_tracks_with_genre(session, rock, 4)

    view = GenreView(controller)
    item = view.tree.topLevelItem(0)
    original_count_text = item.text(1)

    item.setText(1, "garbage")
    view.on_item_edited(item, 1)

    session.expire(rock)
    assert rock.genre_name == "Rock"  # untouched
    assert item.text(0) == "Rock"
    item.setText(1, original_count_text)  # cosmetic cleanup, not asserted


# ---------------------------------------------------------------------------
# AC16 -- loading indicator + duplicate-load guard
# ---------------------------------------------------------------------------


def test_loading_state_shown_and_duplicate_load_is_guarded(session, qapp, controller):
    _make_genre(session, "Rock")

    view = GenreView(controller)
    # After the (synchronous, per conftest) worker finishes, the loading
    # state must be cleared and the tree usable again.
    assert view.loading_label.isVisible() is False
    assert view.tree.isEnabled() is True

    # Simulate "still running" and confirm a second load_genres() call
    # doesn't spawn a second worker while one is (nominally) in flight.
    class _FakeRunningThread:
        def isRunning(self):
            return True

    view._loader_thread = _FakeRunningThread()
    previous_thread = view._loader_thread
    view.load_genres()
    assert view._loader_thread is previous_thread  # no new worker started


# ---------------------------------------------------------------------------
# AC17 -- cached counts mean Flat View / re-sort never touch the database
# ---------------------------------------------------------------------------


def test_flat_view_toggle_uses_cached_counts_no_database_calls(
    session, qapp, controller, monkeypatch
):
    rock = _make_genre(session, "Rock")
    punk = _make_genre(session, "Punk", parent=rock)
    _add_tracks_with_genre(session, rock, 1)
    _add_tracks_with_genre(session, punk, 2)

    view = GenreView(controller)

    calls = _count_execute_calls(session, monkeypatch)
    view.flat_view_button.setChecked(True)
    view.toggle_flat_view()
    view.flat_view_button.setChecked(False)
    view.toggle_flat_view()
    assert calls["n"] == 0


# ---------------------------------------------------------------------------
# Regression: genres arriving from GenreLoaderWorker are detached from their
# originating (background-thread, since-released) session by the time
# _rebuild_tree()/_make_genre_item() run on the main thread. Building a
# tooltip must not touch the ORM `.parent` relationship (a lazy load, which
# raises DetachedInstanceError once the session is gone) -- only caught by
# a real cross-thread run against a real DB (see the live smoke test in
# docs/specs/genre_sort_by_count.md's verification notes); this test
# reproduces it directly by expunging the genres from the session before
# handing them to _on_genres_loaded, exactly as a real detach would leave
# them.
# ---------------------------------------------------------------------------


def test_tooltip_build_does_not_touch_orm_parent_relationship_when_detached(
    session, qapp, controller
):
    rock = _make_genre(session, "Rock")
    punk = _make_genre(session, "Punk", parent=rock)
    _add_tracks_with_genre(session, rock, 1)
    _add_tracks_with_genre(session, punk, 2)

    view = GenreView(controller)  # initial load, still session-bound: fine

    # Now simulate what a real background-thread load hands back: genres
    # whose session has already been released.
    genres = list(session.query(Genre))
    for genre in genres:
        session.expunge(genre)

    direct = {rock.genre_id: 1, punk.genre_id: 2}
    recursive = {rock.genre_id: 3, punk.genre_id: 2}

    # Must not raise sqlalchemy.orm.exc.DetachedInstanceError.
    view._on_genres_loaded(genres, direct, recursive)

    names = _top_level_names(view.tree)
    rock_item = view.tree.topLevelItem(names.index("Rock"))
    punk_item = rock_item.child(0)
    assert punk_item.text(0) == "Punk"
    assert "Parent: Rock" in punk_item.toolTip(0)


# ---------------------------------------------------------------------------
# AC18 -- pre-existing genre tree tests are covered by running
# test_genre_view_delete_exclusion.py itself (see tests/genre/conftest.py),
# not duplicated here.
# ---------------------------------------------------------------------------
