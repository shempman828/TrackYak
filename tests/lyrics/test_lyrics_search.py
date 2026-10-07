"""Tests for src/lyrics/lyrics_search.py: query snapshotting, fallback, error handling and the non-blocking thread wrapper."""

from http.client import IncompleteRead
from json import JSONDecodeError
import threading
from types import SimpleNamespace
from urllib.error import URLError

from lyriq import Lyrics
from PySide6.QtCore import QEventLoop, QTimer
import pytest

from src.lyrics import lyrics_search
from src.lyrics.lyrics_search import LyricQuery, LyricSearch, LyricSearchThread


def _lyrics(plain="la la la") -> Lyrics:
    return Lyrics.from_dict({"id": 1, "trackName": "T", "artistName": "A", "syncedLyrics": "", "plainLyrics": plain, "instrumental": False})


def _artist(name):
    return SimpleNamespace(artist_name=name)


def _track(**overrides):
    fields = {"track_name": "Song", "album_name": "Album", "duration": 215.7, "artists": [_artist("Producer")], "primary_artists": [_artist("Singer")]}
    fields.update(overrides)
    return SimpleNamespace(**fields)


class _FakeClient:
    """Stand-in for the lyriq module that records calls and replays scripted results."""

    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def get_lyrics(self, **kwargs):
        self.calls.append(kwargs)
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


def _search_with(client, track_or_query):
    searcher = LyricSearch(track_or_query)
    searcher.lyrics_client = client
    return searcher


# LyricQuery ------------------------------------------------------------------
def test_query_prefers_primary_artist_over_unordered_artists():
    assert LyricQuery.from_track(_track()).artist == "Singer"


def test_query_falls_back_to_first_artist_without_primary():
    assert LyricQuery.from_track(_track(primary_artists=[])).artist == "Producer"


def test_query_snapshots_album_and_integer_duration():
    query = LyricQuery.from_track(_track())
    assert (query.song, query.album, query.duration) == ("Song", "Album", 215)


def test_query_treats_missing_values_as_none():
    query = LyricQuery.from_track(_track(album_name=None, duration=None, artists=[], primary_artists=[]))
    assert (query.album, query.duration, query.artist) == (None, None, "")


# LyricSearch -----------------------------------------------------------------
def test_first_query_sends_album_and_duration():
    client = _FakeClient(_lyrics())
    assert _search_with(client, _track()).get_lyrics()
    assert client.calls[0]["album_name"] == "Album"
    assert client.calls[0]["duration"] == 215


def test_fallback_drops_album_and_duration():
    found = _lyrics()
    client = _FakeClient(None, found)
    assert _search_with(client, _track()).search_with_fallback() is found
    assert (client.calls[1]["album_name"], client.calls[1]["duration"]) == (None, None)


def test_fallback_skipped_when_first_query_had_no_extra_terms():
    client = _FakeClient(None)
    assert _search_with(client, LyricQuery("Song", "Singer")).search_with_fallback() is None
    assert len(client.calls) == 1


def test_empty_lyrics_object_counts_as_not_found():
    client = _FakeClient(_lyrics(plain=""))
    assert _search_with(client, LyricQuery("Song", "Singer")).get_lyrics() is None


def test_missing_song_or_artist_skips_network():
    client = _FakeClient()
    assert _search_with(client, LyricQuery("", "Singer")).get_lyrics() is None
    assert _search_with(client, LyricQuery("Song", "")).get_lyrics() is None
    assert client.calls == []


@pytest.mark.parametrize("exc", [URLError("down"), TimeoutError("slow"), ConnectionResetError(), IncompleteRead(b""), JSONDecodeError("x", "", 0)])
def test_network_and_parse_errors_fall_through_to_fallback(exc):
    found = _lyrics()
    client = _FakeClient(exc, found)
    assert _search_with(client, _track()).search_with_fallback() is found


# LyricSearchThread -----------------------------------------------------------
def _wait_for(predicate, timeout_ms=3000):
    loop = QEventLoop()
    timer = QTimer()
    timer.timeout.connect(lambda: loop.quit() if predicate() else None)
    timer.start(10)
    QTimer.singleShot(timeout_ms, loop.quit)
    loop.exec()
    timer.stop()
    return predicate()


def test_thread_emits_lyrics_ready(qapp, monkeypatch):
    found = _lyrics()
    monkeypatch.setattr(lyrics_search, "lyriq", _FakeClient(found))
    searcher = LyricSearchThread()
    results = []
    searcher.lyrics_ready.connect(results.append)

    searcher.search(LyricQuery("Song", "Singer"))

    assert _wait_for(lambda: results)
    assert results == [found]
    assert _wait_for(lambda: not lyrics_search._live_threads)


def test_thread_reports_empty_exception_text_by_type_name(qapp, monkeypatch):
    def _boom(self, none_char="♪"):
        raise ValueError  # str(ValueError()) is ""

    monkeypatch.setattr(LyricSearch, "search_with_fallback", _boom)
    searcher = LyricSearchThread()
    errors = []
    searcher.error_occurred.connect(errors.append)

    searcher.search(LyricQuery("Song", "Singer"))

    assert _wait_for(lambda: errors)
    assert errors == ["ValueError"]


def test_stop_does_not_block_and_discards_late_result(qapp, monkeypatch):
    release = threading.Event()

    class _BlockingClient:
        def get_lyrics(self, **_kwargs):
            release.wait(5)
            return _lyrics()

    monkeypatch.setattr(lyrics_search, "lyriq", _BlockingClient())
    searcher = LyricSearchThread()
    results = []
    searcher.lyrics_ready.connect(results.append)
    searcher.search(LyricQuery("Song", "Singer"))
    assert _wait_for(lambda: searcher.is_running)

    searcher.stop()  # must return at once although the worker is blocked

    assert not searcher.is_running
    assert lyrics_search._live_threads  # abandoned thread is kept alive, not destroyed mid-run
    release.set()
    assert _wait_for(lambda: not lyrics_search._live_threads)
    assert results == []
