from src.db.field_spec import FieldSpec

ALBUM_FIELDS = {
    "album_id": FieldSpec(
        type=int,
        editable=False,
        friendly="Album ID",
        short="ID",
        tooltip="The database's unique ID for the album.",
    ),
    "album_name": FieldSpec(
        friendly="Album Title",
        short="Title",
        tooltip="The preferred display name for the album.",
    ),
    "album_language": FieldSpec(
        friendly="Album Primary Language",
        short="Language",
        tooltip="The primary language used in the album",
    ),
    "album_subtitle": FieldSpec(
        friendly="Album Subtitle",
        short="Subtitle",
        tooltip="The album's subtitle, if any.",
    ),
    "MBID": FieldSpec(
        friendly="MusicBrainz ID",
        short="MBID",
        tooltip="The MusicBrainz ID number for the release",
    ),
    "release_type": FieldSpec(
        friendly="Release Type",
        short="Type",
        tooltip="Indicates the general category of the release, describing its format and how it was originally published.",
        placeholder="Album, Single, Compilation, Soundtrack",
    ),
    "album_description": FieldSpec(
        friendly="Album Description",
        short="Description",
        tooltip="A detailed overview of the album, typically summarizing its background, themes, and notable information.",
        longtext=True,
    ),
    "release_year": FieldSpec(
        type=int,
        friendly="Release Year",
        short="Year",
        tooltip="The year the album was released",
    ),
    "release_month": FieldSpec(
        type=int,
        friendly="Release Month",
        short="Month",
        tooltip="The month the album was released",
    ),
    "release_day": FieldSpec(
        type=int,
        friendly="Release Day",
        short="Day",
        tooltip="The day the album was released",
    ),
    "catalog_number": FieldSpec(
        friendly="Catalog Number",
        short="Catalog #",
        tooltip="The official identifier assigned to this release by the label or distributor, used for inventory, manufacturing, and archival tracking.",
    ),
    "is_fixed": FieldSpec(
        type=bool,
        friendly="Metadata Complete",
        short="Complete",
        tooltip="Marks this album as having fully verified and finalized metadata, indicating no further edits are expected.",
    ),
    "album_gain": FieldSpec(
        type=float,
        friendly="Album Gain",
        short="Gain",
        tooltip="Relative volume of the album to reference.",
    ),
    "album_peak": FieldSpec(
        type=float,
        friendly="Track Peak",
        short="Peak",
        tooltip="The largest amplitude in the album",
    ),
    "album_wikipedia_link": FieldSpec(
        friendly="Album Wikipedia Link",
        short="Wikipedia Link",
        tooltip="The link to the album's Wikipedia Page",
    ),
    "is_live": FieldSpec(
        type=bool,
        friendly="Live",
        short="Live",
        tooltip="The album is dedicated to live recordings",
    ),
    "is_compilation": FieldSpec(
        type=bool,
        friendly="Compilation",
        tooltip="The album compiles music by different artists.",
    ),
    "art_is_explicit": FieldSpec(
        type=bool,
        friendly="Explicit Art",
        short="Explicit Art",
        tooltip="Indicates the cover/liner art contains explicit imagery. "
        "When the 'Blur explicit album art' display option is enabled, "
        "this album's art is shown blurred until revealed.",
    ),
    "estimated_sales": FieldSpec(
        type=int,
        friendly="Estimated Sales",
        short="Sales",
        tooltip="The estimated number of copies this release has sold.",
    ),
    "status": FieldSpec(
        friendly="Album Status",
        short="Status",
        tooltip="Indicates the authenticity or publication status of the release as recognized by labels, distributors, or collectors.",
        placeholder="Official, Promotional, Bootleg, Withdrawn, Expunged, Cancelled",
    ),
    "total_duration": FieldSpec(
        type=int,
        friendly="Album Duration",
        short="Duration",
        tooltip="The total length of time for the album",
        editable=False,
    ),
    "album_artist_names": FieldSpec(
        type=list,
        friendly="Album Artist Names",
        short="Album Artists",
        tooltip="The names of the album artists",
        editable=False,
    ),
    "total_plays": FieldSpec(
        type=int,
        friendly="Album Play Count",
        short="Play Count",
        tooltip="The number of plays in all tracks from this album.",
        editable=False,
    ),
    "average_rating": FieldSpec(
        type=float,
        friendly="Average Album Rating",
        short="Album Rating",
        tooltip="The average rating of all tracks with a rating in this album.",
        editable=False,
    ),
    "track_count": FieldSpec(
        type=int,
        friendly="Track Count",
        short="Tracks",
        tooltip="The number of tracks associated with this album.",
        editable=False,
    ),
    "RIAA_certification": FieldSpec(
        friendly="RIAA Certification",
        tooltip="The estimated RIAA status based on estimated sales figures.",
        editable=False,
        short="RIAA",
    ),
}
