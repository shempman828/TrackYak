"""
Regression tests for album_merge.py.

Motivating bugs:
- _bucket_key tokenized the raw album_name (never stripping edition tags)
  and bucketed by the longest token, not the first significant one, so
  pairs like "Abbey Road" / "Abbey Road (Deluxe Edition)" landed in
  different buckets and were never compared even though _name_similarity
  scores them as a near-certain match.
- _DuplicateScanner.run() emitted finished(partial_results) even when
  cancelled mid-scan, and _start_scan didn't disconnect the outgoing
  scanner's signals before starting a new one -- a cancel-then-rescan
  could have the stale scan's results land after the new scan started.
- AlbumMergeList._run_queue opened AlbumMergeDialog for each checked pair
  without re-checking both albums still existed, risking a stale/deleted
  ORM object if an earlier merge in the same batch already removed one.
"""

from types import SimpleNamespace

from src.album.album_merge import AlbumMergeList, _AlbumSnapshot, _DuplicateScanner, score_pair


def test_bucket_key_groups_edition_variant_with_original():
    scanner = _DuplicateScanner([], threshold=0.7)
    key_plain = scanner._bucket_key("Abbey Road")
    key_deluxe = scanner._bucket_key("Abbey Road (Deluxe Edition)")
    assert key_plain == key_deluxe


def test_bucket_key_uses_first_significant_token_not_longest():
    scanner = _DuplicateScanner([], threshold=0.7)
    # "Wonderful" (9 chars) is longer than "The"/"Symphony", but "Symphony"
    # (the first non-stopword token) must be what determines the bucket.
    key = scanner._bucket_key("The Symphony of Wonderful Things")
    assert key == scanner._bucket_key("Symphony")


def test_abbey_road_deluxe_pair_scores_as_near_certain_match():
    a = SimpleNamespace(
        album_name="Abbey Road", album_artist_names="The Beatles", release_year=1969
    )
    b = SimpleNamespace(
        album_name="Abbey Road (Deluxe Edition)",
        album_artist_names="The Beatles",
        release_year=2019,
    )
    assert score_pair(a, b) > 0.9


def test_scanner_does_not_emit_when_cancelled_mid_scan():
    snapshots = [
        _AlbumSnapshot(1, "Alpha", "Artist", 2000),
        _AlbumSnapshot(2, "Alpha Deluxe", "Artist", 2001),
    ]
    scanner = _DuplicateScanner(snapshots, threshold=0.1)
    emitted = []
    scanner.finished.connect(emitted.append)
    scanner.request_cancel()

    scanner.run()

    assert emitted == []


def test_scanner_emits_results_when_not_cancelled():
    snapshots = [
        _AlbumSnapshot(1, "Alpha", "Artist", 2000),
        _AlbumSnapshot(2, "Alpha", "Artist", 2000),
    ]
    scanner = _DuplicateScanner(snapshots, threshold=0.1)
    emitted = []
    scanner.finished.connect(emitted.append)

    scanner.run()

    assert len(emitted) == 1
    assert len(emitted[0]) == 1  # one matching pair


class _FakeGet:
    def __init__(self, albums_by_id):
        self._albums_by_id = albums_by_id

    def get_entity_object(self, model_name, album_id=None, **kwargs):
        return self._albums_by_id.get(album_id)


class _FakeController:
    def __init__(self, albums_by_id):
        self.get = _FakeGet(albums_by_id)


def test_run_queue_skips_pair_when_an_album_was_already_deleted(qapp, monkeypatch):
    album_a = SimpleNamespace(album_id=1, album_name="A")
    album_b = SimpleNamespace(album_id=2, album_name="B")
    album_c = SimpleNamespace(album_id=3, album_name="C")

    # Album A gets deleted by the first pair's merge (simulated by it being
    # absent from albums_by_id for the second pair's re-check).
    albums_by_id = {2: album_b, 3: album_c}  # album 1 (A) no longer exists
    controller = _FakeController(albums_by_id)

    dialog_opens = []

    class _FakeMergeDialog:
        def __init__(self, controller, preload_source=None, preload_target=None, parent=None):
            dialog_opens.append((preload_source.album_id, preload_target.album_id))

        def setWindowTitle(self, title):
            pass

        def exec(self):
            return None

    merge_list = AlbumMergeList.__new__(AlbumMergeList)
    merge_list.controller = controller
    merge_list._status_label = SimpleNamespace(setText=lambda *a: None)
    merge_list.accept = lambda: None

    monkeypatch.setattr("src.album.album_merge.AlbumMergeDialog", _FakeMergeDialog)

    merge_list._run_queue([(album_a, album_b), (album_b, album_c)])

    # Only the second pair (both still existing) opens a dialog.
    assert dialog_opens == [(2, 3)]
