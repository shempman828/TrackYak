"""This class grants access to database operations throughout the modules."""

from sqlalchemy import create_engine
from sqlalchemy.orm import scoped_session, sessionmaker

from src.config_setup import Config
from src.db_helpers import AddToDB, DeleteDB, GetFromDB, MergeDB, SplitDB, UpdateDB
from src.db_tables import Base
from src.library_import import TrackImporter
from src.player_util import MusicPlayer
from src.statistics_utility import MusicStatistics


class MusicController:
    """Mediates between modules and database"""

    def __init__(self):
        self.engine = create_engine("sqlite:///music_library.db", echo=False)
        Base.metadata.create_all(self.engine)

        self.SessionFactory = scoped_session(sessionmaker(bind=self.engine))

        # Direct instances — no proxy needed, all calls are on the main thread
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
