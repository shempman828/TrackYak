"""This class grants access to database operations throughout the modules."""

from concurrent.futures import ThreadPoolExecutor
from queue import Queue
from typing import Any, Dict

from sqlalchemy import create_engine
from sqlalchemy.orm import scoped_session, sessionmaker

from config_setup import Config
from db_helpers import AddToDB, DeleteDB, GetFromDB, MergeDB, SplitDB, UpdateDB
from db_tables import Base
from library_import import TrackImporter
from player_util import MusicPlayer
from statistics_utility import MusicStatistics


class ThreadedDBProxy:
    """proxy that runs DB operations in threads"""

    def __init__(self, db_instance: Any, executor: ThreadPoolExecutor):
        self._db_instance = db_instance
        self._executor = executor
        self._pending_results: Dict[int, Any] = {}
        self._result_id = 0
        self._result_queue = Queue()

    def __getattr__(self, name: str) -> Any:
        """Intercept ANY method call and run it in a thread"""
        method = getattr(self._db_instance, name)

        if not callable(method):
            return method

        # Create a wrapper that runs the method in a thread
        def threaded_wrapper(*args, **kwargs):
            # Submit to thread pool and wait for result
            future = self._executor.submit(method, *args, **kwargs)
            return future.result()  # This blocks until thread completes

        return threaded_wrapper


class MusicController:
    """Mediates between modules and database"""

    def __init__(self, max_workers: int = 4):
        self.engine = create_engine("sqlite:///music_library.db", echo=False)
        Base.metadata.create_all(self.engine)

        # Thread-safe session factory
        self.SessionFactory = scoped_session(sessionmaker(bind=self.engine))

        # Create thread pool FIRST
        self.executor = ThreadPoolExecutor(max_workers=max_workers)

        # Create real DB instances
        get_instance = GetFromDB(self.SessionFactory)
        add_instance = AddToDB(self.SessionFactory)
        update_instance = UpdateDB(self.SessionFactory)
        delete_instance = DeleteDB(self.SessionFactory)
        split_instance = SplitDB(self.SessionFactory)
        merge_instance = MergeDB(self.SessionFactory)

        # Wrap them with proxies
        self.get = ThreadedDBProxy(get_instance, self.executor)
        self.add = ThreadedDBProxy(add_instance, self.executor)
        self.update = ThreadedDBProxy(update_instance, self.executor)
        self.delete = ThreadedDBProxy(delete_instance, self.executor)
        self.split = ThreadedDBProxy(split_instance, self.executor)
        self.merge = ThreadedDBProxy(merge_instance, self.executor)

        # Instantiate importer after DB helpers exist
        self.track_importer = TrackImporter(self)

        # Initialize mediaplayer
        self.mediaplayer = MusicPlayer(self)

        self.config = Config()
        self.statistics = MusicStatistics(self.SessionFactory)

    def close_session(self):
        """Ensure all sessions are properly closed."""
        self.executor.shutdown(wait=True)  # Wait for all threads to complete
        self.SessionFactory.remove()
