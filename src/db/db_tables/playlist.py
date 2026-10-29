"""Playlist ORM models: Playlist, SmartPlaylist, PlaylistTracks, SmartPlaylistCriteria."""

from datetime import datetime

from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Integer, PrimaryKeyConstraint, String
from sqlalchemy.orm import relationship

from src.db.db_tables.base import Base


class Playlist(Base):
    """User playlist (plain or smart), nested under a parent playlist folder."""

    __tablename__ = "playlists"

    playlist_id = Column(Integer, primary_key=True)
    playlist_name = Column(String, nullable=False)
    parent_id = Column(Integer, ForeignKey("playlists.playlist_id", ondelete="SET NULL"))
    playlist_description = Column(String)
    created_date = Column(DateTime, default=datetime.now)
    last_modified = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    is_smart = Column(Integer, CheckConstraint("is_smart IN (0, 1)"), default=0)

    tracks = relationship("PlaylistTracks", back_populates="playlist", cascade="all, delete-orphan", passive_deletes=True)
    parent = relationship("Playlist", remote_side=[playlist_id], backref="children")
    smart_playlist = relationship("SmartPlaylist", back_populates="playlist", uselist=False, cascade="all, delete-orphan")

    @property
    def track_count(self):
        """Number of tracks in the playlist."""
        return len(self.tracks)

    @property
    def playlist_size(self):
        """Total file size of all tracks in the playlist."""
        return sum(pt.track.file_size or 0 for pt in self.tracks if pt.track is not None)

    @property
    def recursive_track_count(self):
        """Count unique tracks in this playlist and all descendant playlists."""
        seen_playlists = set()
        track_ids = set()

        def walk(playlist):
            if playlist.playlist_id in seen_playlists:
                return
            seen_playlists.add(playlist.playlist_id)
            track_ids.update(pt.track_id for pt in playlist.tracks if pt.track_id)
            for child in playlist.children:
                walk(child)

        walk(self)
        return len(track_ids)


class SmartPlaylist(Base):
    """Rule-based settings for a Playlist with is_smart = 1."""

    __tablename__ = "smart_playlists"

    playlist_id = Column(Integer, ForeignKey("playlists.playlist_id"), primary_key=True)
    last_refreshed = Column(DateTime)
    auto_refresh = Column(Integer, default=0)  # Refresh on app start
    logic = Column(String, default="AND")

    playlist = relationship("Playlist", back_populates="smart_playlist")
    criteria = relationship("SmartPlaylistCriteria", back_populates="smart_playlist", cascade="all, delete-orphan", passive_deletes=True, order_by="SmartPlaylistCriteria.criterion_id")


class PlaylistTracks(Base):
    """One track's position in one playlist."""

    __tablename__ = "playlist_tracks"
    __table_args__ = (PrimaryKeyConstraint("playlist_id", "track_id"),)

    playlist_id = Column(Integer, ForeignKey("playlists.playlist_id", ondelete="CASCADE"), primary_key=True)
    track_id = Column(Integer, ForeignKey("tracks.track_id", ondelete="CASCADE"), primary_key=True)
    position = Column(Integer, nullable=False)
    date_added = Column(DateTime)

    playlist = relationship("Playlist", back_populates="tracks")
    track = relationship("Track", back_populates="playlists")


class SmartPlaylistCriteria(Base):
    """One filter condition of a smart playlist."""

    __tablename__ = "smart_playlist_criteria"

    criterion_id = Column(Integer, primary_key=True)
    smart_playlist_id = Column(Integer, ForeignKey("smart_playlists.playlist_id", ondelete="CASCADE"))
    field_name = Column(String)
    comparison = Column(String)
    value = Column(String)
    type = Column(String)

    smart_playlist = relationship("SmartPlaylist", back_populates="criteria")
