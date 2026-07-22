"""
One-off backfill: set Artist.religion_id for artists marked metadata-complete
(is_fixed=1) whose religious affiliation is reliably documented in public
sources (Wikipedia "Personal life" sections, reputable interviews/biographies).

Only assignments with clear sourcing are included -- artists with no public
record, or only contested/unconfirmed claims, are deliberately left unset
rather than guessed.

Run once, manually, from the repo root:

    python scripts/backfill_artist_religion.py
"""

import shutil
from datetime import datetime

from sqlalchemy.orm import scoped_session, sessionmaker

from src.core.logger_config import logger
from src.db.db_helpers import AddToDB, DeleteDB, GetFromDB, MergeDB, SplitDB, UpdateDB
from src.db.db_tables.database import MusicDatabase

DB_PATH = "music_library.db"

# (religion_name, parent_name or None, description)
RELIGIONS = [
    ("Christianity", None, None),
    ("Episcopalian", "Christianity", None),
    ("Pentecostal", "Christianity", None),
    ("Eastern Orthodoxy", "Christianity", None),
    ("Estonian Orthodox Church", "Eastern Orthodoxy", None),
    ("Southern Baptist", "Christianity", None),
    ("Jehovah's Witness", "Christianity", None),
    ("Judaism", None, None),
    ("Buddhism", None, None),
    ("Non-religious / Spiritual", None, "Self-described as spiritual/non-religious rather than following an organized faith"),
]

# artist_name -> religion_name. Keyed by name (not id) since artist_id is
# only stable within this user's own database; name is what's verifiable
# against the research below.
ASSIGNMENTS = {
    "Andy Samberg": ("Judaism", "Jewish (cultural); Wikipedia, Hey Alma interview"),
    "Eddie Kramer": ("Judaism", "Jewish (bar mitzvahed); Jewish Rhode Island profile"),
    "Russ Titelman": ("Judaism", "Wikipedia"),
    "Doris Troy": ("Pentecostal", "Raised Pentecostal; father was a Pentecostal minister -- udiscovermusic biography"),
    "John Philip Sousa": ("Episcopalian", "BrainyQuote/biography sources"),
    "Michael Giles": ("Buddhism", "Ordained Buddhist monk after leaving King Crimson, 1972 -- Wikipedia"),
    "Tönu Kaljuste": ("Estonian Orthodox Church", "Received into the Orthodox Church, 1972 -- EMIC bio"),
    "Ray Manzarek": ("Non-religious / Spiritual", "Raised Catholic; later described self as 'a oneness man' -- National Catholic Reporter obituary"),
    "Flea": ("Non-religious / Spiritual", "No organized religion; belief in God as 'divine energy' -- Fox News/Christian Post interview 2019"),
    "Adele": ("Non-religious / Spiritual", "Describes self as spiritual, not religious -- interviews"),
    "Doja Cat": ("Non-religious / Spiritual", "Jewish maternal heritage; describes self as spiritual, not religious -- Wikipedia, Moment Magazine"),
    "Bryan Garris": ("Non-religious / Spiritual", "Raised Christian; states he does not follow organized religion -- Kerrang/Rock Sound interviews"),
    "Maynard James Keenan": ("Southern Baptist", "Raised Southern Baptist -- Wikipedia, Phoenix New Times interview"),
    "Janet Jackson": ("Jehovah's Witness", "Raised Jehovah's Witness -- Hollowverse and other entertainment-press sources"),
}


class _MinimalController:
    """Boots only the database layer, same pattern as run_repair.py's
    _MinimalController -- no GUI/audio deps needed for a data backfill.

    Goes through MusicDatabase (rather than a bare create_engine +
    create_all like run_repair.py) so its integrity check runs and
    auto-adds the new religion_id column to the existing artists table --
    create_all() alone only creates missing tables, it doesn't add columns
    to ones that already exist.
    """

    def __init__(self):
        self.db = MusicDatabase(f"sqlite:///{DB_PATH}")
        self.engine = self.db.engine
        self.SessionFactory = scoped_session(sessionmaker(bind=self.engine))

        self.get = GetFromDB(self.SessionFactory)
        self.add = AddToDB(self.SessionFactory)
        self.update = UpdateDB(self.SessionFactory)
        self.delete = DeleteDB(self.SessionFactory)
        self.split = SplitDB(self.SessionFactory)
        self.merge = MergeDB(self.SessionFactory)

    def close_session(self):
        self.SessionFactory.remove()


def _backup(db_path: str) -> str:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = f"{db_path}.{timestamp}.bak"
    shutil.copy2(db_path, backup_path)
    return backup_path


def _find_or_create_religion(controller, name: str, parent_id, description):
    existing = controller.get.get_entity_object("Religion", religion_name=name)
    if existing:
        return existing
    return controller.add.add_entity(
        "Religion", religion_name=name, parent_id=parent_id, description=description
    )


def main():
    backup_path = _backup(DB_PATH)
    print(f"Backed up database to {backup_path}")

    controller = _MinimalController()
    try:
        # Create the religion vocabulary (parents before children).
        by_name = {}
        for name, parent_name, description in RELIGIONS:
            parent_id = by_name[parent_name].religion_id if parent_name else None
            religion = _find_or_create_religion(controller, name, parent_id, description)
            by_name[name] = religion
        print(f"Ensured {len(RELIGIONS)} Religion row(s) exist.")

        updated, missing, already_set = [], [], []
        for artist_name, (religion_name, _source_note) in ASSIGNMENTS.items():
            artist = controller.get.get_entity_object("Artist", artist_name=artist_name)
            if not artist:
                missing.append(artist_name)
                continue
            if artist.religion_id:
                already_set.append(artist_name)
                continue

            religion = by_name[religion_name]
            success = controller.update.update_entity(
                "Artist", artist.artist_id, religion_id=religion.religion_id
            )
            if success:
                updated.append(f"{artist_name} -> {religion_name}")
            else:
                logger.error(f"Failed to update religion for {artist_name}")

        print(f"\nUpdated {len(updated)} artist(s):")
        for line in updated:
            print(f"  {line}")

        if already_set:
            print(f"\nSkipped {len(already_set)} artist(s) that already had a religion set:")
            for name in already_set:
                print(f"  {name}")

        if missing:
            print(f"\nCould not find {len(missing)} artist name(s) in the database:")
            for name in missing:
                print(f"  {name}")

    finally:
        controller.close_session()


if __name__ == "__main__":
    main()
