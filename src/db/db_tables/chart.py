"""
Chart ORM models: Chart (one row per chart type, e.g. Hot 100 / Billboard 200)
and ChartEntry (one row per chart-week/position), imported from static
community-maintained CSV snapshots. See src/charts/ for the download/import/
matching workers that populate these tables.
"""

from sqlalchemy import (
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from src.db.db_tables.base import Base


class Chart(Base):
    __tablename__ = "charts"

    chart_id = Column(Integer, primary_key=True)
    chart_key = Column(String(20), unique=True, nullable=False)  # 'hot-100' | 'billboard-200'
    chart_name = Column(String(100), nullable=False)  # "Billboard Hot 100"
    source_url = Column(String, nullable=False)
    matched_entity_type = Column(
        String,
        CheckConstraint("matched_entity_type IN ('Track', 'Album')"),
        nullable=False,
    )
    last_downloaded_at = Column(DateTime)  # last successful CSV fetch
    last_synced_week = Column(Date)  # most recent chart_week imported; drives update diffing
    last_matched_at = Column(DateTime)  # last time the matching pass ran
    # Fingerprint of the library's titles/artists as of the last matching
    # pass (see chart_matching._rebuild_scratch_index). Lets a re-run skip
    # rescoring entries that were already attempted and failed, but only
    # while the library is provably unchanged -- any track/album rename,
    # addition, or removal changes this and forces those entries to be
    # retried, so a rename can never be silently missed.
    last_library_fingerprint = Column(String)

    entries = relationship(
        "ChartEntry",
        back_populates="chart",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class ChartEntry(Base):
    __tablename__ = "chart_entries"
    __table_args__ = (
        UniqueConstraint(
            "chart_id", "chart_week", "position", name="uq_chart_entry_week_position"
        ),
    )

    chart_entry_id = Column(Integer, primary_key=True)
    chart_id = Column(
        Integer, ForeignKey("charts.chart_id", ondelete="CASCADE"), nullable=False
    )
    chart_week = Column(Date, nullable=False)
    position = Column(Integer, nullable=False)
    last_week_position = Column(Integer)  # nullable: new entries have no prior week
    peak_position = Column(Integer)
    weeks_on_chart = Column(Integer)
    raw_title = Column(String, nullable=False)
    raw_performer = Column(String, nullable=False)

    # Populated by the matching pass (src/charts/chart_matching.py); NULL means unmatched.
    entity_type = Column(String, CheckConstraint("entity_type IN ('Track', 'Album')"))
    entity_id = Column(Integer)
    match_score = Column(Float)  # SequenceMatcher-derived confidence at match time
    # Set whenever this entry is scored (matched or not), regardless of
    # outcome. Combined with Chart.last_library_fingerprint, lets a re-run
    # tell "never attempted" (new chart week) apart from "attempted, no
    # match, library unchanged since" (safe to skip).
    last_match_attempt_at = Column(DateTime)

    chart = relationship("Chart", back_populates="entries")

    track = relationship(
        "Track",
        primaryjoin="and_(ChartEntry.entity_id == foreign(Track.track_id), "
        "ChartEntry.entity_type == 'Track')",
        viewonly=True,
    )
    album = relationship(
        "Album",
        primaryjoin="and_(ChartEntry.entity_id == foreign(Album.album_id), "
        "ChartEntry.entity_type == 'Album')",
        viewonly=True,
    )

    @property
    def entity(self):
        """Return the matched Track/Album object, or None if unmatched."""
        if self.entity_type == "Track":
            return self.track
        if self.entity_type == "Album":
            return self.album
        return None

    @property
    def is_matched(self) -> bool:
        return self.entity_id is not None
