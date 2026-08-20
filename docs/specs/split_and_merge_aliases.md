# Split & Merge Aliases

Supersedes the earlier Role-only draft. Scope expanded per follow-up
discussion to: generalize split-aliasing to Genre/Artist/Publisher/Role
(Option C), stop MusicBrainz from combining multi-instrument credits into
one string at the source, add Role merge-aliasing (a gap Genre/Artist/
Publisher already don't have — see Non-goals), and build one menu-bar
dialog that manages every merge-alias and split-alias table plus the
existing skipped-genres list.

## Problem

MusicBrainz reports one person's contribution as a single relation with
multiple attribute values (e.g. a performer relation with
`attribute-list: ["viola", "violin"]`). `_relation_role_name()`
([src/musicbrainz/musicbrainz_release.py:268](../../src/musicbrainz/musicbrainz_release.py#L268))
joins these into one string, `"Viola & Violin"`, and the importer finds-or-
creates one combined `Role` for it. A user can split that `Role` by hand
(`SplitDB.split_role()` already exists, just unreachable from the UI), but
nothing remembers the split — the next import recreates the combined role.

Two more gaps surfaced during design:

- The same "combine into one string, then have to split it back apart by
  hand, forever" pattern is possible for Artist (MB join-phrase artist
  credits like `"Simon & Garfunkel"`) and, less commonly, Genre/Publisher —
  and none of `split_genre`/`split_artist`/`split_publisher` record
  anything either.
- `Role` has no merge-alias table at all (`Genre`/`Artist`/`Publisher` do —
  merging a duplicate into a canonical entity remembers the discarded name
  via `_ALIAS_ON_MERGE_REGISTRY` in
  [src/db/db_helpers/merge.py:41](../../src/db/db_helpers/merge.py#L41); `Role`
  is absent from that registry).

## User-facing behavior

**MusicBrainz parsing.** `_parse_artist_credits()`
([musicbrainz_release.py:300](../../src/musicbrainz/musicbrainz_release.py#L300))
stops joining a performer/instrument/vocal relation's multiple attribute
values into one `" & "`-separated role name. A relation with
`attribute-list: ["viola", "violin"]` now yields two separate
`MBTrackCredit` entries (`"Viola"`, `"Violin"`), each for the same artist —
matching what actually happened (one person played two instruments), so
the review dialog shows two checkable credit rows instead of one combined
one. Production-relation combining (`"Assistant Engineer"` from type
`engineer` + attribute `assistant`) is untouched — that's a real modifier
relationship, not multiple independent values, per the existing code
comment at musicbrainz_release.py:270-276.

**Splitting** (Role view already has Merge…; gets a **Split…** action too,
mirroring Genre/Artist/Publisher's existing `SplitDBDialog` wiring).
Splitting any of Genre/Artist/Publisher/Role into 2+ names now also
records a **split-alias rule**: "this exact combined name means these N
entities, not one." A rename-via-duplicate split (exactly 1 target name)
records nothing — nothing was actually split.

**Merging.** Merging two Roles now records a **merge-alias** the same way
Genre/Artist/Publisher merges already do: the discarded role's name
resolves to the surviving role from then on.

**New "Manage Aliases…" dialog**, reachable from a new top-level **Tools**
menu in the menu bar. Non-modal,
lazily-constructed-and-cached like the Statistics dialog
(`show()`/`raise()`/`activateWindow()`, not `exec_()`), since it's a
reference/editing surface someone may want open alongside other work. Nine
tabs:

1. Skipped Genres — moved from `ConfigDialog`'s Library tab (same
   underlying `excluded_genres` config value, UI relocated, not
   duplicated — per your answer).
2. Genre Aliases (merge-aliases, global — every `GenreAlias` row, not
   scoped to one genre)
3. Genre Split Aliases
4. Artist Aliases (merge-aliases, global)
5. Artist Split Aliases
6. Publisher Aliases (merge-aliases, global)
7. Publisher Split Aliases
8. Role Aliases (merge-aliases, global — new capability, Role had none)
9. Role Split Aliases

Each merge-alias tab: table of (alias name, target entity), Add (type/pick
an alias name + target entity), Edit (repoint or rename), Delete. Each
split-alias tab: table of (combined name, ordered target entities), Add
(combined name + 2+ target entities), Edit (change the target set),
Delete. Two reusable widget classes handle all 4 entity types each ("lay
the groundwork" = parameterize by model name once, not four near-duplicate
tab implementations).

## Data model changes

**Four new split-alias tables** (one per entity type), same shape as the
`RoleSplitAlias` design from the earlier draft — flat, one-to-many,
`alias_name` **not** unique (multiple rows share it, one per target),
ordered by `sort_order`, cascade-deletes with their target:

```python
class GenreSplitAlias(Base):        # src/db/db_tables/genre.py
    __tablename__ = "genre_split_alias"
    split_alias_id = Column(Integer, primary_key=True)
    alias_name = Column(String, nullable=False, index=True)
    genre_id = Column(Integer, ForeignKey("genres.genre_id", ondelete="CASCADE"), nullable=False)
    sort_order = Column(Integer, nullable=False, default=0)
    genre = relationship("Genre")
    __table_args__ = (UniqueConstraint("alias_name", "genre_id", name="uq_genre_split_alias_name_genre"),)
```

...and identically shaped `ArtistSplitAlias` (`artist_split_alias`, in
`src/db/db_tables/artist.py`), `PublisherSplitAlias`
(`publisher_split_alias`, in `src/db/db_tables/publisher.py`),
`RoleSplitAlias` (`role_split_alias`, in `src/db/db_tables/role.py`).

**One new merge-alias table**, mirroring the existing `GenreAlias`/
`ArtistAlias`/`PublisherAlias` shape exactly (1:1, `alias_name` unique):

```python
class RoleAlias(Base):              # src/db/db_tables/role.py
    __tablename__ = "role_alias"
    alias_id = Column(Integer, primary_key=True)
    alias_name = Column(String, unique=True, nullable=False)
    role_id = Column(Integer, ForeignKey("roles.role_id", ondelete="CASCADE"), nullable=False)
    role = relationship("Role", back_populates="aliases")
    role_name = association_proxy("role", "role_name")
```

No changes to `Role`/`Genre`/`Artist`/`Publisher`/`TrackArtistRole`/
`AlbumRoleAssociation`/`AlbumPublisher` schemas themselves.

## Migration plan

- All 5 new ORM classes → `Base.metadata.create_all()` creates them on any
  brand-new database automatically.
- For existing databases: add `"genre_split_alias"`, `"artist_split_alias"`,
  `"publisher_split_alias"`, `"role_split_alias"`, `"role_alias"` to the
  `expected_tables` set in `MusicDatabase._verify_integrity()`
  ([src/db/db_tables/database.py:62-96](../../src/db/db_tables/database.py#L62))
  — the existing missing-table-diff-then-`create_all()` retrofit pattern,
  same one every prior table addition has used (no Alembic in this repo).
- No backfill: every new table starts empty; it populates only as users
  split/merge going forward, or add rules directly via the new dialog.
- Rehearsal (Phase 2, before touching the real DB, per CLAUDE.md): copy the
  real DB to a scratch path, run the app against the scratch copy, confirm
  all 5 new tables appear (`sqlite3 <scratch>.db ".tables"`), exercise one
  split and one merge of a real Role, confirm the alias rows appear and
  survive an app restart against the same scratch file (round-trip).

## Blast radius

**MusicBrainz parsing**
| File | Change |
|---|---|
| [src/musicbrainz/musicbrainz_release.py](../../src/musicbrainz/musicbrainz_release.py) | `_relation_role_name` → `_relation_role_names` (returns `list[str]`, one entry per performer/instrument/vocal attribute value instead of joining them); `_parse_artist_credits` (line 300) loops over the returned names, emitting one `MBTrackCredit` per name |

**New tables + registries**
| File | Change |
|---|---|
| `src/db/db_tables/genre.py`, `artist.py`, `publisher.py`, `role.py` | Add the 4 `*SplitAlias` models + `RoleAlias`, as above |
| [src/db/db_tables/__init__.py](../../src/db/db_tables/__init__.py) | Import + `__all__` entries for all 5 new classes |
| [src/db/db_helpers/registry.py](../../src/db/db_helpers/registry.py) | `MODEL_REGISTRY` entries for all 5 |
| [src/db/db_tables/database.py](../../src/db/db_tables/database.py) | `expected_tables` additions, as above |

**Split path** ([src/db/db_helpers/split.py](../../src/db/db_helpers/split.py))
| Change |
|---|
| New `_SPLIT_ALIAS_REGISTRY` (model name → split-alias model, fk field), mirroring the existing `_ALIAS_REGISTRY` shape |
| New shared helper `_record_split_alias(model_name, original_name, new_entities)`: when `len(new_entities) >= 2`, delete any existing split-alias rows for that `alias_name` (replace semantics — no accumulation across repeated re-splits), then insert one row per entity with its `sort_order`. Called from the end of `split_genre`, `split_artist`, `split_publisher`, `split_role`, before the original entity is deleted |
| `_ALIAS_REGISTRY` (existing, read-direction): add `"Role": (RoleAlias, "role")` so a split target name that matches a role's merge-alias resolves to the existing role, same as Artist/Genre/Publisher already do |

**Merge path** ([src/db/db_helpers/merge.py](../../src/db/db_helpers/merge.py))
| Change |
|---|
| `_ALIAS_ON_MERGE_REGISTRY` (line 41): add `"Role": (RoleAlias, "role_name", "role_id")` |

**MB-adjacent resolution paths** — these are the alias-aware call sites;
see Non-goals for why the exact-match-only paths are deliberately left
alone.
| File | Change |
|---|---|
| [src/album/album_musicbrainz_review_import.py](../../src/album/album_musicbrainz_review_import.py) | `_resolve_artist` (~132) → `_resolve_artists`, returns `list[Artist]` (split-alias match, else today's single-artist logic wrapped in a list). New `_resolve_roles_for_credit` alongside `find_or_create_by_name`, same shape. `_plan_track_credit`/`_plan_album_credit` (174-269) resolve both lists and emit one planned row per (artist, role) pair — nearly always 1×1, occasionally 1×N/N×1. Return type becomes `list[dict]`. Call sites in `_ReviewAcceptWorker`'s accept loop (~497-547) change from "append the one row" to "extend with however many came back" |
| [src/publisher/publisher_musicbrainz_import.py](../../src/publisher/publisher_musicbrainz_import.py) | `resolve_or_create_publisher` (47) → `resolve_or_create_publishers`, returns `list[Publisher]`. `import_album_labels` (229) fans out `AlbumPublisher` rows per label × resolved publisher instead of assuming one publisher per label |
| [src/importing/library_import.py](../../src/importing/library_import.py) | Genre loop (565-586): consult `GenreSplitAlias` before `resolve_entity_or_alias`; on match, attach every target genre instead of creating/reusing one |
| [src/track/track_edit_genres.py](../../src/track/track_edit_genres.py) | `_find_or_create` (33): same split-alias check before delegating to `find_or_create_by_name`, so manually typing a combined genre name also expands |

**Role UI**
| File | Change |
|---|---|
| [src/role/role_view.py](../../src/role/role_view.py) | Wire `"Split…"` into `show_context_menu()` (720-747, currently missing — Merge… exists, Split… doesn't) |

**New unified dialog**
| File | Change |
|---|---|
| `src/common/alias_management_dialog.py` (new) | `AliasManagementDialog(QDialog)` — `QTabWidget` container, 9 tabs, built via `_create_X_tab()` factory methods mirroring `ConfigDialog`'s structure |
| `src/common/global_merge_alias_tab.py` (new) | Generic merge-alias tab widget, parameterized by `model_name` — table of all `<Model>Alias` rows across the whole table (not scoped to one entity, unlike the existing `EntityAliasesTab` in `entity_alias_tab.py`, which stays as-is and untouched for its current per-entity-edit-dialog use) |
| `src/common/global_split_alias_tab.py` (new) | Generic split-alias tab widget, parameterized by `model_name` — table of all `<Model>SplitAlias` rules, grouped by `alias_name`, with add/edit/delete |
| [src/core/menu_bar.py](../../src/core/menu_bar.py) | New top-level `Tools` menu (`menu_bar.addMenu("Tools")`, mirroring `file_menu`/`audio_menu`/`view_menu` at lines 58/102/115) with one action, `"Manage Aliases…"`, handler mirroring `show_statistics_dialog` (338-343): lazily construct + cache, `show()`/`raise()`/`activateWindow()` |
| [src/core/config_dialog.py](../../src/core/config_dialog.py) | Remove the `excluded_genres_list` widget + its Add/Remove wiring (149, 154-182, 497, 643) from the Library tab — moved to the new dialog's Skipped Genres tab, same underlying config field |

**Tests**
| File | Covers |
|---|---|
| `tests/db/test_split_alias.py` (new) | `_record_split_alias` behavior across all 4 entity types: 2+ names records a rule, 1 name doesn't, re-split replaces, cascade-delete of a member |
| `tests/db/test_merge_role_alias.py` (new) | Role merge now records `RoleAlias`, matching existing Genre/Artist/Publisher merge-alias test coverage |
| `tests/musicbrainz/test_relation_role_names.py` (new) | `_relation_role_names` returns N separate names for a multi-value performer relation, still combines production-relation type+modifier as one string |
| `tests/album/test_mb_import_split_alias.py` (new) | Import path: a role-name or artist-name split-alias match expands into multiple association rows; non-matching credits are an unchanged regression check |
| `tests/publisher/test_mb_import_publisher_split.py` (new) | Same, for label/publisher resolution |
| `tests/importing/test_genre_split_alias.py` (new) | File-tag import path: genre split-alias match attaches multiple genres |
| `tests/common/test_alias_management_dialog.py` (new, headless) | Add/edit/delete through each tab type (merge + split), for at least one entity type each — the generic widgets make the other 3 a thin parametrization, not full duplicate suites |

## Non-goals

- **Not bringing the exact-match-only Artist-resolution paths onto alias
  awareness.** `library_import.py::_get_or_create_artist`,
  `library_import_album.py::_process_artist_name`, and
  `track_edit_roles.py::_resolve_artist` don't consult `ArtistAlias`
  *today* (a pre-existing gap, confirmed during research — they're
  exact-match only, no alias fallback at all), so they won't consult
  `ArtistSplitAlias` either. Making them alias-aware is a real
  improvement but a separate, unrelated fix — folding it in here would
  quietly change matching behavior for three codepaths nobody asked to
  touch. Split-alias support here targets the specific call sites that
  are *already* alias-aware: `_resolve_artist` (MB role credits),
  `resolve_or_create_publisher` (MB labels), `library_import.py`'s and
  `track_edit_genres.py`'s genre resolution.
- **Not touching `track_edit_album.py::_resolve_or_create_artist`** (the
  single "create this new Album's artist from an MB match" path) — it's a
  scalar, one-artist-at-a-time flow structurally, not a credits list;
  making it split-aware would mean deciding how a new Album gets *two*
  Album Artists from one split, which is a UI/data-model question of its
  own, not implied by anything requested here.
- **No fuzzy/qualifier-aware matching** — split-alias and merge-alias
  lookups stay exact-string-match, consistent with how
  `resolve_entity_or_alias` already works for every existing alias table.
- **No retroactive re-splitting** of already-imported association rows
  when a rule is created or edited after the fact — a rule only affects
  future resolution, never rewrites existing `TrackArtistRole`/
  `AlbumRoleAssociation`/`AlbumPublisher`/track-genre rows.

## Acceptance criteria

1. `_relation_role_names()` returns two separate names for a performer
   relation with `attribute-list: ["viola", "violin"]`, and still returns
   one combined name for a production relation (`type=engineer`,
   `attribute=assistant` → `"Assistant Engineer"`) — both cases covered by
   one unit test each, no live API call needed (pure function on a dict).
2. Importing an MB release whose recording has a `["viola", "violin"]`
   performer relation creates two separate `TrackArtistRole` rows (`Viola`,
   `Violin`) for that artist, not one `"Viola & Violin"` role.
3. Role view's context menu shows a **Split…** action; using it with 2+
   names splits via the existing `SplitDB.split_role()` backend.
4. Splitting a Role/Genre/Artist/Publisher into 2+ names creates one
   split-alias row per target, sharing the original name as `alias_name`;
   splitting into exactly 1 name creates none. (One test per entity type,
   or one parameterized test across all 4 — implementation's call.)
5. Re-splitting/re-editing a rule for a name that already has one replaces
   the old rows rather than accumulating them.
6. Merging two Roles records a `RoleAlias` for the discarded name, matching
   existing Genre/Artist/Publisher merge behavior (regression-style test
   against the same pattern `test_merge.py` already exercises).
7. Deleting an entity that's a split-alias or merge-alias target cascades
   correctly (FK `ondelete="CASCADE"`) without orphaning rows or erroring,
   leaving a rule's other members (if any) intact.
8. Importing an MB credit whose role name exactly matches a Role
   split-alias rule creates one association row per target role instead of
   one combined-role row; a non-matching credit imports exactly as it does
   today (regression check).
9. Importing an MB credit whose artist name exactly matches an Artist
   split-alias rule creates one association row per (target artist ×
   role); non-matching artist names import exactly as today.
10. Importing an MB label whose name matches a Publisher split-alias rule
    creates one `AlbumPublisher` row per target publisher.
11. File-tag import (`library_import.py`) of a genre name matching a Genre
    split-alias rule attaches every target genre to the track instead of
    one combined genre.
12. Manually adding a genre via the track editor's Genre tab, typing a name
    that matches a split-alias rule, expands the same way.
13. A new "Tools" menu appears in the menu bar with "Manage Aliases…",
    which opens the new
    non-modal, cached dialog (`show()`/`raise()`/`activateWindow()` — a
    second invocation raises the existing instance instead of opening a
    duplicate).
14. Each of the 9 tabs lists its table's current rows correctly on open.
15. Each merge-alias tab (Genre/Artist/Publisher/Role) supports add/edit/
    delete, reflected immediately in its underlying `*Alias` table.
16. Each split-alias tab (Genre/Artist/Publisher/Role) supports add (2+
    target names, no pre-existing combined entity required)/edit/delete,
    reflected immediately in its underlying `*SplitAlias` table.
17. The Skipped Genres tab reads from and writes to the same
    `excluded_genres` config value `ConfigDialog` used to own, and that
    widget/wiring is gone from `ConfigDialog`'s Library tab.
18. A pre-feature database (missing all 5 new tables) upgrades cleanly on
    next launch — tables created automatically, no manual migration step,
    no data loss to any existing table.

Each item is independently testable: 1, 4-7, 17-18 via DB-level unit
tests; 2, 8-12 via a scripted import against fixture/live MB data (per
CLAUDE.md's "verify empirically" rule) or recorded MB response fixtures;
3, 13-16 via headless UI interaction/screenshot per project convention.
