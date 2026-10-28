"""Chart playlist builder (root/decade/year tree, year gate, incremental
reruns, stale cleanup) and its worker signals.
"""

import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.charts.chart_playlist_builder import ChartPlaylistBuilder, ChartPlaylistStats
from src.charts.workers.chart_playlist_worker import ChartPlaylistWorker
from src.db.db_helpers.add import AddToDB
from src.db.db_helpers.delete import DeleteDB
from src.db.db_helpers.get import GetFromDB
from src.db.db_helpers.update import UpdateDB
from src.db.db_tables.album import Album
from src.db.db_tables.base import Base
from src.db.db_tables.chart import Chart, ChartEntry
from src.db.db_tables.playlist import Playlist, PlaylistTracks
from src.db.db_tables.track import Track

# ---------------------------------------------------------------------------
# Tests for ChartPlaylistBuilder (src/charts/chart_playlist_builder.py) against
# ---------------------------------------------------------------------------


class StubController:
    def __init__(self, session):
        self.get = GetFromDB(session)
        self.add = AddToDB(session)
        self.update = UpdateDB(session)
        self.delete = DeleteDB(session)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def controller(session):
    return StubController(session)


def _make_hot100(session) -> Chart:
    chart = Chart(
        chart_key="hot-100",
        chart_name="Billboard Hot 100",
        source_url="https://example.invalid/hot-100.csv",
        matched_entity_type="Track",
        last_synced_week=datetime.date(2023, 12, 30),
    )
    session.add(chart)
    session.commit()
    return chart


def _make_bb200(session) -> Chart:
    chart = Chart(
        chart_key="billboard-200",
        chart_name="Billboard 200",
        source_url="https://example.invalid/billboard-200.csv",
        matched_entity_type="Album",
        last_synced_week=datetime.date(2023, 12, 30),
    )
    session.add(chart)
    session.commit()
    return chart


def _make_track(session, name, year=None) -> Track:
    track = Track(track_name=name, recorded_year=year)
    session.add(track)
    session.commit()
    return track


def _entry(chart, week, position, track_id=None, entity_type=None, raw_title="X"):
    return ChartEntry(
        chart_id=chart.chart_id,
        chart_week=week,
        position=position,
        raw_title=raw_title,
        raw_performer="Someone",
        entity_type=entity_type,
        entity_id=track_id,
        match_score=1.0 if entity_type else None,
    )


def _playlist_by_marker(session, marker):
    return (
        session.query(Playlist).filter(Playlist.playlist_description == marker).one()
    )


def _track_ids_of(session, playlist_id):
    rows = (
        session.query(PlaylistTracks)
        .filter(PlaylistTracks.playlist_id == playlist_id)
        .all()
    )
    return {r.track_id for r in rows}


def test_creates_root_decade_year_tree_with_correct_contents(session, controller):
    chart = _make_hot100(session)
    t65 = _make_track(session, "1965 Song", 1965)
    t66 = _make_track(session, "1966 Song", 1966)
    session.add_all(
        [
            _entry(chart, datetime.date(1965, 6, 1), 1, t65.track_id, "Track"),
            _entry(chart, datetime.date(1966, 6, 1), 1, t66.track_id, "Track"),
        ]
    )
    session.commit()

    ChartPlaylistBuilder(controller).generate_or_update()

    root = _playlist_by_marker(session, "__chart_playlist__:root:hot-100")
    assert root.playlist_name == "Billboard Hot 100"
    assert root.parent_id is None

    decade = _playlist_by_marker(session, "__chart_playlist__:decade:hot-100:1960")
    assert decade.playlist_name == "1960s"
    assert decade.parent_id == root.playlist_id

    y65 = _playlist_by_marker(session, "__chart_playlist__:year:hot-100:1965")
    y66 = _playlist_by_marker(session, "__chart_playlist__:year:hot-100:1966")
    assert y65.parent_id == decade.playlist_id
    assert y66.parent_id == decade.playlist_id

    assert _track_ids_of(session, y65.playlist_id) == {t65.track_id}
    assert _track_ids_of(session, y66.playlist_id) == {t66.track_id}


def test_multi_week_track_deduped_within_year(session, controller):
    chart = _make_hot100(session)
    track = _make_track(session, "Recurring Hit", 1965)
    session.add_all(
        [
            _entry(chart, datetime.date(1965, 1, 2), 1, track.track_id, "Track"),
            _entry(chart, datetime.date(1965, 1, 9), 1, track.track_id, "Track"),
            _entry(chart, datetime.date(1965, 1, 16), 2, track.track_id, "Track"),
        ]
    )
    session.commit()

    ChartPlaylistBuilder(controller).generate_or_update()

    year = _playlist_by_marker(session, "__chart_playlist__:year:hot-100:1965")
    assert _track_ids_of(session, year.playlist_id) == {track.track_id}


def test_decade_and_root_materialize_union_of_children(session, controller):
    chart = _make_hot100(session)
    t65 = _make_track(session, "A", 1965)
    t66 = _make_track(session, "B", 1966)
    t71 = _make_track(session, "C", 1971)
    session.add_all(
        [
            _entry(chart, datetime.date(1965, 6, 1), 1, t65.track_id, "Track"),
            _entry(chart, datetime.date(1966, 6, 1), 1, t66.track_id, "Track"),
            _entry(chart, datetime.date(1971, 6, 1), 1, t71.track_id, "Track"),
        ]
    )
    session.commit()

    ChartPlaylistBuilder(controller).generate_or_update()

    decade_60s = _playlist_by_marker(session, "__chart_playlist__:decade:hot-100:1960")
    decade_70s = _playlist_by_marker(session, "__chart_playlist__:decade:hot-100:1970")
    root = _playlist_by_marker(session, "__chart_playlist__:root:hot-100")

    assert _track_ids_of(session, decade_60s.playlist_id) == {t65.track_id, t66.track_id}
    assert _track_ids_of(session, decade_70s.playlist_id) == {t71.track_id}
    assert _track_ids_of(session, root.playlist_id) == {
        t65.track_id,
        t66.track_id,
        t71.track_id,
    }


def test_album_matched_entry_expands_to_its_tracks(session, controller):
    chart = _make_bb200(session)
    album = Album(album_name="Greatest Hits", release_year=1967)
    session.add(album)
    session.commit()

    t1 = Track(track_name="Side A", album_id=album.album_id)
    t2 = Track(track_name="Side B", album_id=album.album_id)
    other_album_track = Track(track_name="Unrelated")
    session.add_all([t1, t2, other_album_track])
    session.commit()

    session.add(_entry(chart, datetime.date(1967, 3, 1), 1, album.album_id, "Album"))
    session.commit()

    ChartPlaylistBuilder(controller).generate_or_update()

    year = _playlist_by_marker(session, "__chart_playlist__:year:billboard-200:1967")
    assert _track_ids_of(session, year.playlist_id) == {t1.track_id, t2.track_id}


def test_unmatched_entries_are_skipped(session, controller):
    chart = _make_hot100(session)
    session.add(_entry(chart, datetime.date(1965, 6, 1), 1))  # no entity_id/type
    session.commit()

    ChartPlaylistBuilder(controller).generate_or_update()

    root = _playlist_by_marker(session, "__chart_playlist__:root:hot-100")
    assert _track_ids_of(session, root.playlist_id) == set()
    assert (
        session.query(Playlist)
        .filter(
            Playlist.playlist_description
            == "__chart_playlist__:year:hot-100:1965"
        )
        .count()
        == 0
    )


def test_stale_track_match_does_not_poison_the_whole_sync(session, controller):
    chart = _make_hot100(session)
    live_track = _make_track(session, "Still Here", 1965)
    deleted_track = _make_track(session, "Will Be Deleted", 1965)
    deleted_track_id = deleted_track.track_id
    session.add_all(
        [
            _entry(chart, datetime.date(1965, 6, 1), 1, live_track.track_id, "Track"),
            _entry(chart, datetime.date(1965, 6, 8), 2, deleted_track_id, "Track"),
        ]
    )
    session.commit()
    session.delete(deleted_track)
    session.commit()

    # Must not raise, and must not drop the still-valid track along with
    # the stale one.
    ChartPlaylistBuilder(controller).generate_or_update()

    year = _playlist_by_marker(session, "__chart_playlist__:year:hot-100:1965")
    root = _playlist_by_marker(session, "__chart_playlist__:root:hot-100")
    assert _track_ids_of(session, year.playlist_id) == {live_track.track_id}
    assert _track_ids_of(session, root.playlist_id) == {live_track.track_id}


def test_rerun_with_no_new_data_is_idempotent(session, controller):
    chart = _make_hot100(session)
    track = _make_track(session, "Song", 1965)
    session.add(_entry(chart, datetime.date(1965, 6, 1), 1, track.track_id, "Track"))
    session.commit()

    builder = ChartPlaylistBuilder(controller)
    builder.generate_or_update()

    playlist_count_before = session.query(Playlist).count()
    track_rows_before = {
        (r.playlist_id, r.track_id) for r in session.query(PlaylistTracks).all()
    }

    stats = builder.generate_or_update()

    assert session.query(Playlist).count() == playlist_count_before
    track_rows_after = {
        (r.playlist_id, r.track_id) for r in session.query(PlaylistTracks).all()
    }
    assert track_rows_after == track_rows_before
    assert stats.playlists_created == 0
    assert stats.tracks_added == 0
    assert stats.tracks_removed == 0


def test_every_chart_row_processed_even_with_no_entries(session, controller):
    _make_hot100(session)
    _make_bb200(session)  # no ChartEntry rows for this one at all

    ChartPlaylistBuilder(controller).generate_or_update()

    hot100_root = _playlist_by_marker(session, "__chart_playlist__:root:hot-100")
    bb200_root = _playlist_by_marker(
        session, "__chart_playlist__:root:billboard-200"
    )
    assert hot100_root.playlist_name == "Billboard Hot 100"
    assert bb200_root.playlist_name == "Billboard 200"
    assert _track_ids_of(session, bb200_root.playlist_id) == set()


def test_incremental_rerun_only_affects_changed_year(session, controller):
    chart = _make_hot100(session)
    t65 = _make_track(session, "1965 Song", 1965)
    t71 = _make_track(session, "1971 Song", 1971)
    session.add_all(
        [
            _entry(chart, datetime.date(1965, 6, 1), 1, t65.track_id, "Track"),
            _entry(chart, datetime.date(1971, 6, 1), 1, t71.track_id, "Track"),
        ]
    )
    session.commit()

    builder = ChartPlaylistBuilder(controller)
    builder.generate_or_update()

    y71_before = _playlist_by_marker(session, "__chart_playlist__:year:hot-100:1971")
    y71_tracks_before = _track_ids_of(session, y71_before.playlist_id)

    # Simulate a later Match Now run finding a second match for 1965.
    t65b = _make_track(session, "1965 Song B", 1965)
    session.add(_entry(chart, datetime.date(1965, 6, 8), 2, t65b.track_id, "Track"))
    session.commit()

    builder.generate_or_update()

    y65 = _playlist_by_marker(session, "__chart_playlist__:year:hot-100:1965")
    y71_after = _playlist_by_marker(session, "__chart_playlist__:year:hot-100:1971")
    assert _track_ids_of(session, y65.playlist_id) == {t65.track_id, t65b.track_id}
    assert _track_ids_of(session, y71_after.playlist_id) == y71_tracks_before


def test_user_playlist_under_generated_root_is_untouched(session, controller):
    chart = _make_hot100(session)
    track = _make_track(session, "Song", 1965)
    session.add(_entry(chart, datetime.date(1965, 6, 1), 1, track.track_id, "Track"))
    session.commit()

    ChartPlaylistBuilder(controller).generate_or_update()
    root = _playlist_by_marker(session, "__chart_playlist__:root:hot-100")

    user_playlist = Playlist(
        playlist_name="My Own Mix", parent_id=root.playlist_id
    )
    session.add(user_playlist)
    session.commit()
    user_track = _make_track(session, "My own track")
    session.add(
        PlaylistTracks(
            playlist_id=user_playlist.playlist_id,
            track_id=user_track.track_id,
            position=1,
            date_added=datetime.datetime.now(),
        )
    )
    session.commit()
    user_playlist_id = user_playlist.playlist_id

    ChartPlaylistBuilder(controller).generate_or_update()

    still_there = session.get(Playlist, user_playlist_id)
    assert still_there is not None
    assert still_there.playlist_name == "My Own Mix"
    assert still_there.parent_id == root.playlist_id
    assert _track_ids_of(session, user_playlist_id) == {user_track.track_id}


def test_renamed_generated_playlist_is_found_by_marker_not_duplicated(
    session, controller
):
    chart = _make_hot100(session)
    track = _make_track(session, "Song", 1965)
    session.add(_entry(chart, datetime.date(1965, 6, 1), 1, track.track_id, "Track"))
    session.commit()

    ChartPlaylistBuilder(controller).generate_or_update()
    root = _playlist_by_marker(session, "__chart_playlist__:root:hot-100")
    root.playlist_name = "My Renamed Hot 100"
    session.commit()

    ChartPlaylistBuilder(controller).generate_or_update()

    matches = (
        session.query(Playlist)
        .filter(Playlist.playlist_description == "__chart_playlist__:root:hot-100")
        .all()
    )
    assert len(matches) == 1
    assert matches[0].playlist_name == "My Renamed Hot 100"


def test_track_charting_decades_after_release_is_excluded(session, controller):
    chart = _make_hot100(session)
    reissue_reentry = _make_track(session, "Let It Be", 1970)
    contemporary = _make_track(session, "Airplanes", 2010)
    session.add_all(
        [
            _entry(chart, datetime.date(2010, 10, 2), 1, reissue_reentry.track_id, "Track"),
            _entry(chart, datetime.date(2010, 10, 2), 2, contemporary.track_id, "Track"),
        ]
    )
    session.commit()

    ChartPlaylistBuilder(controller).generate_or_update()

    root = _playlist_by_marker(session, "__chart_playlist__:root:hot-100")
    assert _track_ids_of(session, root.playlist_id) == {contemporary.track_id}

    # The 1970s decade / any 1970 year node must not have been created for
    # this chart at all -- the only entry was in a 2010 week.
    assert (
        session.query(Playlist)
        .filter(
            Playlist.playlist_description == "__chart_playlist__:decade:hot-100:1970"
        )
        .count()
        == 0
    )
    y2010 = _playlist_by_marker(session, "__chart_playlist__:year:hot-100:2010")
    assert _track_ids_of(session, y2010.playlist_id) == {contemporary.track_id}


def test_year_gate_boundary_is_inclusive_at_two(session, controller):
    chart = _make_hot100(session)
    two_off = _make_track(session, "Late Peak", 1969)  # |1969 - 1971| == 2
    three_off = _make_track(session, "Too Old", 1968)  # |1968 - 1971| == 3
    session.add_all(
        [
            _entry(chart, datetime.date(1971, 6, 1), 1, two_off.track_id, "Track"),
            _entry(chart, datetime.date(1971, 6, 1), 2, three_off.track_id, "Track"),
        ]
    )
    session.commit()

    ChartPlaylistBuilder(controller).generate_or_update()

    y71 = _playlist_by_marker(session, "__chart_playlist__:year:hot-100:1971")
    assert _track_ids_of(session, y71.playlist_id) == {two_off.track_id}


def test_track_with_no_known_year_is_excluded(session, controller):
    chart = _make_hot100(session)
    undated = _make_track(session, "No Metadata")  # no recorded_year, no album
    session.add(_entry(chart, datetime.date(1965, 6, 1), 1, undated.track_id, "Track"))
    session.commit()

    ChartPlaylistBuilder(controller).generate_or_update()

    root = _playlist_by_marker(session, "__chart_playlist__:root:hot-100")
    assert _track_ids_of(session, root.playlist_id) == set()
    assert (
        session.query(Playlist)
        .filter(
            Playlist.playlist_description == "__chart_playlist__:year:hot-100:1965"
        )
        .count()
        == 0
    )


def test_album_entry_year_gate_uses_album_release_year(session, controller):
    chart = _make_bb200(session)
    old_album = Album(album_name="Abbey Road", release_year=1969)
    session.add(old_album)
    session.commit()
    t1 = Track(track_name="Come Together", album_id=old_album.album_id)
    session.add(t1)
    session.commit()

    # Album re-charts on a 2019 anniversary reissue week.
    session.add(_entry(chart, datetime.date(2019, 10, 5), 1, old_album.album_id, "Album"))
    session.commit()

    ChartPlaylistBuilder(controller).generate_or_update()

    root = _playlist_by_marker(session, "__chart_playlist__:root:billboard-200")
    assert _track_ids_of(session, root.playlist_id) == set()


def _clear_match(session, entry):
    entry.entity_type = None
    entry.entity_id = None
    entry.match_score = None
    session.commit()


def test_cleared_match_removed_from_year_that_still_has_other_tracks(
    session, controller
):
    chart = _make_hot100(session)
    keep = _make_track(session, "Keeper", 1964)
    drop = _make_track(session, "Dropped", 1964)
    keep_entry = _entry(chart, datetime.date(1964, 3, 7), 1, keep.track_id, "Track")
    drop_entry = _entry(chart, datetime.date(1964, 3, 7), 2, drop.track_id, "Track")
    session.add_all([keep_entry, drop_entry])
    session.commit()

    builder = ChartPlaylistBuilder(controller)
    builder.generate_or_update()

    y64 = _playlist_by_marker(session, "__chart_playlist__:year:hot-100:1964")
    assert _track_ids_of(session, y64.playlist_id) == {keep.track_id, drop.track_id}

    _clear_match(session, drop_entry)
    builder.generate_or_update()

    d60 = _playlist_by_marker(session, "__chart_playlist__:decade:hot-100:1960")
    root = _playlist_by_marker(session, "__chart_playlist__:root:hot-100")
    assert _track_ids_of(session, y64.playlist_id) == {keep.track_id}
    assert _track_ids_of(session, d60.playlist_id) == {keep.track_id}
    assert _track_ids_of(session, root.playlist_id) == {keep.track_id}


def test_cleared_match_empties_and_deletes_year_that_drops_to_zero(
    session, controller
):
    chart = _make_hot100(session)
    t64 = _make_track(session, "Only 64 Hit", 1964)
    t67 = _make_track(session, "A 67 Hit", 1967)
    e64 = _entry(chart, datetime.date(1964, 3, 7), 1, t64.track_id, "Track")
    session.add_all(
        [e64, _entry(chart, datetime.date(1967, 5, 6), 1, t67.track_id, "Track")]
    )
    session.commit()

    builder = ChartPlaylistBuilder(controller)
    builder.generate_or_update()

    y64_id = _playlist_by_marker(
        session, "__chart_playlist__:year:hot-100:1964"
    ).playlist_id

    _clear_match(session, e64)
    stats = builder.generate_or_update()

    # The 1964 node is gone entirely, and its track row with it.
    assert (
        session.query(Playlist)
        .filter(Playlist.playlist_description == "__chart_playlist__:year:hot-100:1964")
        .count()
        == 0
    )
    assert _track_ids_of(session, y64_id) == set()
    assert stats.playlists_removed == 1

    # The still-live siblings are untouched; the decade / root no longer
    # carry the cleared track.
    d60 = _playlist_by_marker(session, "__chart_playlist__:decade:hot-100:1960")
    root = _playlist_by_marker(session, "__chart_playlist__:root:hot-100")
    y67 = _playlist_by_marker(session, "__chart_playlist__:year:hot-100:1967")
    assert _track_ids_of(session, y67.playlist_id) == {t67.track_id}
    assert _track_ids_of(session, d60.playlist_id) == {t67.track_id}
    assert _track_ids_of(session, root.playlist_id) == {t67.track_id}


def test_whole_decade_dropping_to_zero_is_emptied_and_deleted(session, controller):
    chart = _make_hot100(session)
    t64 = _make_track(session, "60s Hit", 1964)
    t71 = _make_track(session, "70s Hit", 1971)
    e64 = _entry(chart, datetime.date(1964, 3, 7), 1, t64.track_id, "Track")
    session.add_all(
        [e64, _entry(chart, datetime.date(1971, 5, 6), 1, t71.track_id, "Track")]
    )
    session.commit()

    builder = ChartPlaylistBuilder(controller)
    builder.generate_or_update()

    _clear_match(session, e64)
    builder.generate_or_update()

    for marker in (
        "__chart_playlist__:year:hot-100:1964",
        "__chart_playlist__:decade:hot-100:1960",
    ):
        assert (
            session.query(Playlist)
            .filter(Playlist.playlist_description == marker)
            .count()
            == 0
        )
    root = _playlist_by_marker(session, "__chart_playlist__:root:hot-100")
    assert _track_ids_of(session, root.playlist_id) == {t71.track_id}


def test_stale_year_with_user_playlist_child_is_emptied_but_kept(session, controller):
    chart = _make_hot100(session)
    t64 = _make_track(session, "Only 64 Hit", 1964)
    t67 = _make_track(session, "A 67 Hit", 1967)
    e64 = _entry(chart, datetime.date(1964, 3, 7), 1, t64.track_id, "Track")
    session.add_all(
        [e64, _entry(chart, datetime.date(1967, 5, 6), 1, t67.track_id, "Track")]
    )
    session.commit()

    builder = ChartPlaylistBuilder(controller)
    builder.generate_or_update()

    y64 = _playlist_by_marker(session, "__chart_playlist__:year:hot-100:1964")
    mine = Playlist(playlist_name="My 64 Mix", parent_id=y64.playlist_id)
    session.add(mine)
    session.commit()
    my_track = _make_track(session, "My pick")
    session.add(
        PlaylistTracks(
            playlist_id=mine.playlist_id, track_id=my_track.track_id, position=1
        )
    )
    session.commit()
    mine_id = mine.playlist_id

    _clear_match(session, e64)
    builder.generate_or_update()

    # The generated 1964 node stays (it still has a child), but its own
    # chart tracks are stripped; the user's nested playlist is untouched.
    kept = _playlist_by_marker(session, "__chart_playlist__:year:hot-100:1964")
    assert _track_ids_of(session, kept.playlist_id) == set()
    assert session.get(Playlist, mine_id) is not None
    assert _track_ids_of(session, mine_id) == {my_track.track_id}


# ---------------------------------------------------------------------------
# Tests for ChartPlaylistWorker (src/charts/workers/chart_playlist_worker.py) against
# ---------------------------------------------------------------------------


class StubControllerWorker:
    def __init__(self, session_worker):
        self.get = GetFromDB(session_worker)
        self.add = AddToDB(session_worker)
        self.update = UpdateDB(session_worker)


@pytest.fixture
def session_worker():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session_worker = Session()
    yield session_worker
    session_worker.close()


@pytest.fixture
def controller_worker(session_worker):
    return StubControllerWorker(session_worker)


def _seed_one_matched_entry(session_worker):
    chart = Chart(
        chart_key="hot-100",
        chart_name="Billboard Hot 100",
        source_url="https://example.invalid/hot-100.csv",
        matched_entity_type="Track",
        last_synced_week=datetime.date(2023, 12, 30),
    )
    track = Track(track_name="Song", recorded_year=1965)
    session_worker.add_all([chart, track])
    session_worker.commit()
    session_worker.add(
        ChartEntry(
            chart_id=chart.chart_id,
            chart_week=datetime.date(1965, 6, 1),
            position=1,
            raw_title="Song",
            raw_performer="Someone",
            entity_type="Track",
            entity_id=track.track_id,
            match_score=1.0,
        )
    )
    session_worker.commit()


def test_finished_signal_emits_stats(session_worker, controller_worker):
    _seed_one_matched_entry(session_worker)

    worker = ChartPlaylistWorker(controller_worker)
    results = []
    errors = []
    worker.finished.connect(results.append)
    worker.error.connect(errors.append)

    worker.run()

    assert errors == []
    assert len(results) == 1
    stats = results[0]
    assert isinstance(stats, ChartPlaylistStats)
    assert stats.playlists_created > 0
    assert stats.tracks_added > 0


def test_progress_signal_emits_at_least_once(session_worker, controller_worker):
    _seed_one_matched_entry(session_worker)

    worker = ChartPlaylistWorker(controller_worker)
    progress_calls = []
    worker.progress.connect(lambda done, total: progress_calls.append((done, total)))

    worker.run()

    assert progress_calls
    last_done, last_total = progress_calls[-1]
    assert last_done == last_total


def test_run_releases_db_session_without_error(session_worker, controller_worker, monkeypatch):
    """AC14: the worker must release its pooled DB session_worker on completion.
    Patch Session.remove so we can assert it was actually called, without
    the real music_library.db engine being touched."""
    calls = []
    monkeypatch.setattr(
        "src.db.db_engine.Session.remove", lambda: calls.append(True)
    )
    _seed_one_matched_entry(session_worker)

    ChartPlaylistWorker(controller_worker).run()

    assert calls == [True]
