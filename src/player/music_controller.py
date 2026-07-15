"""This class grants access to database operations throughout the modules."""

from sqlalchemy import create_engine
from sqlalchemy.orm import scoped_session, sessionmaker

from src.core.config_setup import Config
from src.db.db_helpers import AddToDB, DeleteDB, GetFromDB, MergeDB, SplitDB, UpdateDB
from src.db.db_tables import Base
from src.importing.library_import import TrackImporter
from src.player.player_util import MusicPlayer
from src.statistics.statistics_utility import MusicStatistics


class MusicController:
    """Mediates between modules and database"""

    def __init__(self):
        self.engine = create_engine(
            "sqlite:///music_library.db",
            echo=False,
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(self.engine)

        self.SessionFactory = scoped_session(sessionmaker(bind=self.engine))

        # Direct instances — no proxy needed. Most calls are on the main thread;
        # self.statistics is also called from a background QThread (scoped_session
        # gives that thread its own Session, and check_same_thread=False above lets
        # pooled connections move between threads safely).
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

    def close_session(self):
        """Ensure all sessions are properly closed."""
        self.SessionFactory.remove()
