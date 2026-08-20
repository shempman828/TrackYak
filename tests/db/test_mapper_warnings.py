"""Regression test: SQLAlchemy's mapper configuration must not emit
SAWarning at startup.

The place/award/mood association tables are polymorphic (one physical
table, discriminated by entity_type, shared by several entity models).
Album.places, Artist.places, Track.places (and the .awards/.moods
equivalents) are pure read accessors -- every real write goes through the
association object directly (PlaceAssociation/AwardAssociation/
MoodTrackAssociation via AddToDB.add_entities), never through these
relationship collections. Without viewonly=True on those accessors,
configure_mappers() sees multiple relationships claiming to write the same
columns and warns on every app startup, even though only one path ever
actually writes.

configure_mappers() only validates once per process (subsequent calls are a
no-op if no new mappers were added), so this has to run in a fresh
subprocess to reliably reproduce/catch the warning regardless of what other
tests already configured the ORM earlier in the same test session.
"""

import subprocess
import sys

_SCRIPT = (
    "import warnings\n"
    "from sqlalchemy.exc import SAWarning\n"
    "from sqlalchemy.orm import configure_mappers\n"
    "import src.db.db_tables  # noqa: F401\n"
    "with warnings.catch_warnings(record=True) as caught:\n"
    "    warnings.simplefilter('always')\n"
    "    configure_mappers()\n"
    "sa_warnings = [str(w.message) for w in caught if issubclass(w.category, SAWarning)]\n"
    "for msg in sa_warnings:\n"
    "    print(msg)\n"
    "import sys\n"
    "sys.exit(1 if sa_warnings else 0)\n"
)


def test_configure_mappers_raises_no_sawarning():
    result = subprocess.run(
        [sys.executable, "-c", _SCRIPT],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout or result.stderr
