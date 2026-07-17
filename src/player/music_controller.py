"""This class grants access to database operations throughout the modules."""

from src.core.config_setup import Config
from src.core.logger_config import logger
from src.db.db_helpers import AddToDB, DeleteDB, GetFromDB, MergeDB, SplitDB, UpdateDB
from src.db.db_helpers.session import Session, engine
from src.db.db_tables import Base
from src.importing.library_import import TrackImporter
from src.player.player_util import MusicPlayer
from src.statistics.statistics_utility import MusicStatistics


class MusicController:
    """Mediates between modules and database"""

    def __init__(self):
        self.engine = engine
        Base.metadata.create_all(self.engine)

        # Shared scoped_session from db_helpers.session — the same engine/session
        # factory other modules (e.g. sync_view.py) use directly, so the whole
        # running app talks to one connection pool instead of several uncoordinated
        # ones pointed at the same SQLite file.
        self.SessionFactory = Session

        # Direct instances — no proxy needed. Most calls are on the main thread;
        # self.statistics is also called from a background QThread (scoped_session
        # gives that thread its own Session, and check_same_thread=False on the
        # shared engine lets pooled connections move between threads safely).
        self.get = GetFromDB(self.SessionFactory)
        self.add = AddToDB(self.SessionFactory)
        self.update = UpdateDB(self.SessionFactory)
        self.delete = DeleteDB(self.SessionFactory)
        self.split = SplitDB(self.SessionFactory)
        self.merge = MergeDB(self.SessionFactory)

        self.track_importer = TrackImporter(self)
        self.mediaplayer = MusicPlayer(self)
        self.config = Config()
        self.statistics = MusicStatistics(self.SessionFactory)

        logger.info("MusicController initialized")

    def close_session(self):
        """Ensure all sessions are properly closed."""
        self.SessionFactory.remove()
        logger.debug("Database session closed")
