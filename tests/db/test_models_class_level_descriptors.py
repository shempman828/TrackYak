"""Regression: every ORM descriptor must be readable at class level.

The bug: Track.sampled_tracks/sampling_tracks/primary_artists and
Album.total_duration were `@hybrid_property` with no SQL expression. Since
SQLAlchemy 2.1, an ORM bulk UPDATE reads every descriptor in
`mapper.all_orm_descriptors` at class level, which ran these Python-only
getters against InstrumentedAttributes and raised NotImplementedError -- so
every `update_entities_bulk("Track", ...)` call failed.
"""

import pytest
from sqlalchemy import inspect as sa_inspect

from src.db.db_helpers.registry import MODEL_REGISTRY

MAPPED = {name: cls for name, cls in MODEL_REGISTRY.items() if sa_inspect(cls, raiseerr=False) is not None}


@pytest.mark.parametrize("name", sorted(MAPPED))
def test_all_orm_descriptors_resolve_at_class_level(name):
    cls = MAPPED[name]
    for key, _descriptor in sa_inspect(cls).all_orm_descriptors.items():
        if key == "__mapper__":
            continue
        getattr(cls, key)
