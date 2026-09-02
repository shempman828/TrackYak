"""
One-off cleanup, from an alias audit run 2026-08-22:

1. Delete self-referential alias rows (alias_name == the owning entity's
   own primary name, case/whitespace-insensitive) from artist_alias and
   publisher_alias -- harmless leftovers (mostly case variants like
   "MICK JAGGER" aliasing "Mick Jagger") that only clutter alias-list UIs.

2. Merge 13 confirmed duplicate-entity pairs where an alias's text exactly
   matches a *different*, separate entity's primary name -- meaning that
   name was unreachable via resolve_entity_or_alias() (primary-name lookup
   always wins), so imports/edits using that name kept landing on the
   duplicate instead of the canonical entity the alias already pointed to.
   Every pair was diffed field-by-field first; none had two different
   non-null values for the same field (which would mean "not actually the
   same entity" and would need a human call, not an auto-merge) -- only
   null-vs-value gaps, which are safe to fill from the discarded row.

Run once, manually, from the repo root:

    python scripts/merge_alias_duplicates.py [db_path]

db_path defaults to music_library.db; pass a scratch copy's path to dry-run
first.
"""

from datetime import datetime
import os
import shutil
import sys

from sqlalchemy import bindparam, text
from sqlalchemy.orm import scoped_session, sessionmaker

from src.db.db_helpers import AddToDB, DeleteDB, GetFromDB, MergeDB, SplitDB, UpdateDB
from src.db.db_tables.database import MusicDatabase
from src.foundation.logger_config import logger

DB_PATH = sys.argv[1] if len(sys.argv) > 1 else "music_library.db"

# (model_name, alias_table, name_field)
ALIAS_TABLES = [
    ("Artist", "artist_alias", "artist_name"),
    ("Genre", "genre_alias", "genre_name"),
    ("Publisher", "publisher_alias", "publisher_name"),
    ("Role", "role_alias", "role_name"),
    ("Album", "album_alias", "album_name"),
]

# (model_name, target_id, source_id, resolved_fields or None)
# target survives, source is merged away. resolved_fields fills a null
# target field from source's value -- built from a prior field-by-field
# diff; every other field already agreed or was blank on both sides.
DUPLICATE_MERGES = [
    ("Artist", 20303, 5023, None),       # 2 Chainz <- Tauheed Epps
    ("Artist", 7235, 23925, None),       # Beloyd Taylor <- B. Taylor
    ("Artist", 3204, 22061, None),       # Sonny Burke <- Joseph Francis Burke
    ("Artist", 957, 24334, None),        # Steve Tyler <- Steven Tyler
    ("Artist", 11645, 23986, None),      # Gregg Rolie <- G. Rolie
    ("Artist", 809, 23912, None),        # Thomas McClary <- T. McClary
    ("Artist", 1530, 24304, None),       # Fats Waller <- Waller
    ("Artist", 4593, 23875, None),       # Michel Legrand <- M. Legrand
    ("Artist", 6866, 24000, None),       # Drake <- Aubrey Graham
    ("Artist", 15048, 24103, None),      # Matthew Thiesson <- Matthew Thiessen
    ("Artist", 10623, 24460, None),      # Phil Spector <- Philip Spector
    ("Artist", 22488, 23728, {"isgroup": 0}),  # [unknown] <- Unknown
    ("Publisher", 1118, 2332, None),     # Curb Records <- Curb
]


class _MinimalController:
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
    os.makedirs("backups", exist_ok=True)
    backup_path = f"backups/{os.path.basename(db_path)}.{timestamp}.bak"
    shutil.copy2(db_path, backup_path)
    return backup_path


def _clear_self_referential_aliases(controller):
    session = controller.get.session
    total = 0
    for model_name, alias_table, name_field in ALIAS_TABLES:
        entity_table = alias_table.rsplit("_alias", 1)[0] + "s"
        fk_field = alias_table.rsplit("_alias", 1)[0] + "_id"
        pk_field = fk_field
        rows = session.execute(
            text(
                f"""
                SELECT aa.alias_id, aa.alias_name
                FROM {alias_table} aa
                JOIN {entity_table} e ON e.{pk_field} = aa.{fk_field}
                WHERE lower(trim(e.{name_field})) = lower(trim(aa.alias_name))
                """
            )
        ).fetchall()
        if not rows:
            continue
        ids = [r[0] for r in rows]
        print(f"  {model_name}: deleting {len(ids)} self-referential alias row(s): "
              f"{sorted({r[1] for r in rows})}")
        session.execute(
            text(f"DELETE FROM {alias_table} WHERE alias_id IN :ids").bindparams(
                bindparam("ids", expanding=True)
            ),
            {"ids": ids},
        )
        total += len(ids)
    session.commit()
    print(f"Cleared {total} self-referential alias row(s) total.\n")


def main():
    backup_path = _backup(DB_PATH)
    print(f"Backed up database to {backup_path}\n")

    controller = _MinimalController()
    try:
        print("=== Step 1: clear self-referential aliases ===")
        _clear_self_referential_aliases(controller)

        print("=== Step 2: merge confirmed duplicate entities ===")
        succeeded, failed = [], []
        for model_name, target_id, source_id, resolved_fields in DUPLICATE_MERGES:
            target = controller.get.get_entity_object(model_name, **{f"{model_name.lower()}_id": target_id})
            source = controller.get.get_entity_object(model_name, **{f"{model_name.lower()}_id": source_id})
            if not target or not source:
                failed.append((model_name, target_id, source_id, "target or source no longer exists"))
                continue
            name_field = "artist_name" if model_name == "Artist" else f"{model_name.lower()}_name"
            t_name = getattr(target, name_field)
            s_name = getattr(source, name_field)

            ok = controller.merge.merge_entities(model_name, source_id, target_id, resolved_fields)
            if ok:
                succeeded.append(f"{model_name}: {source_id} ({s_name!r}) -> {target_id} ({t_name!r})")
            else:
                failed.append((model_name, target_id, source_id, "merge_entities returned False"))

        print(f"\nMerged {len(succeeded)} pair(s):")
        for line in succeeded:
            print(f"  {line}")

        if failed:
            print(f"\nFailed {len(failed)} pair(s):")
            for model_name, target_id, source_id, reason in failed:
                print(f"  {model_name} {source_id} -> {target_id}: {reason}")

    finally:
        controller.close_session()


if __name__ == "__main__":
    main()
