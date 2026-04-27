"""
run_repair.py

Standalone script to run the one-time library repair pass without
starting the full application (no GUI, no media player, no config).

Usage:
    python run_repair.py
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import scoped_session, sessionmaker

from src.db_helpers import AddToDB, DeleteDB, GetFromDB, MergeDB, SplitDB, UpdateDB
from src.db_tables import Base
from src.library_rescan_repair import LibraryRepair
from src.logger_config import logger


class _MinimalController:
    """
    Boots only the database layer of MusicController.
    Skips MusicPlayer, TrackImporter, Config, and MusicStatistics
    so this script can run without any GUI or audio dependencies.
    """

    def __init__(self):
        self.engine = create_engine("sqlite:///music_library.db", echo=False)
        Base.metadata.create_all(self.engine)
        self.SessionFactory = scoped_session(sessionmaker(bind=self.engine))

        self.get = GetFromDB(self.SessionFactory)
        self.add = AddToDB(self.SessionFactory)
        self.update = UpdateDB(self.SessionFactory)
        self.delete = DeleteDB(self.SessionFactory)
        self.split = SplitDB(self.SessionFactory)
        self.merge = MergeDB(self.SessionFactory)

    def close_session(self):
        self.SessionFactory.remove()


def main():
    logger.info("=== Library Repair: starting ===")
    controller = _MinimalController()
    try:
        repair = LibraryRepair(controller)
        summary = repair.run()

        logger.info("=== Library Repair: finished ===")
        print("\nRepair complete. Summary:")
        for key, value in summary.items():
            print(f"  {key}: {value}")

    finally:
        controller.close_session()
        logger.info("Database session closed.")


if __name__ == "__main__":
    main()
    main()
