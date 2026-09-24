"""Regression: write_metadata_to_file/write_metadata_to_track must not
broadcast a per-file message to the global StatusManager.

Every caller (the batch write dialog, the dev-mode immediate-write commit
hook, multi-track advanced-tab writes) already loops over tracks and shows
its own aggregate status/log message once the loop finishes. Before this
fix, MetadataWriter also pushed its own "Writing metadata to <file>" /
"Updated <file>" message to the shared StatusManager on every single call.
At loop speed that overwrote the caller's message hundreds of times a
second - every track's filename flashing through the status bar too fast
to read, a blur instead of useful feedback.
"""

from unittest.mock import MagicMock

from src.metadata.metadata_writer import MetadataWriter, WriteMode


def _make_writer(tmp_path, monkeypatch, exists: bool = True):
    file_path = tmp_path / "song.mp3"
    if exists:
        file_path.write_bytes(b"ID3")

    controller = MagicMock()
    track = MagicMock(track_file_path=str(file_path))
    controller.get.get_entity_object.return_value = track

    writer = MetadataWriter(controller)
    monkeypatch.setattr(writer.track_data, "get_track_data", lambda track_id: {"track": {"track_id": track_id}})
    monkeypatch.setattr(writer, "_write_id3", lambda *a, **k: True)
    return writer, file_path


def test_write_metadata_to_track_does_not_broadcast_status(tmp_path, monkeypatch):
    writer, _ = _make_writer(tmp_path, monkeypatch)
    mock_status = MagicMock()
    monkeypatch.setattr("src.foundation.status_utility.StatusManager.show_message", mock_status)

    result = writer.write_metadata_to_track(1, WriteMode.UPDATE_EXISTING)

    assert result is True
    mock_status.assert_not_called()


def test_write_metadata_to_track_missing_file_does_not_broadcast_status(tmp_path, monkeypatch):
    writer, _ = _make_writer(tmp_path, monkeypatch, exists=False)
    mock_status = MagicMock()
    monkeypatch.setattr("src.foundation.status_utility.StatusManager.show_message", mock_status)

    result = writer.write_metadata_to_track(1, WriteMode.UPDATE_EXISTING)

    assert result is False
    mock_status.assert_not_called()
