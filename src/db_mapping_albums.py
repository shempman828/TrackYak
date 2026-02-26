from dataclasses import dataclass
from typing import Optional, Type


@dataclass
class AlbumField:
    friendly: Optional[str] = None  # Human-readable name
    short: Optional[str] = None  # shorter human readable name
    type: Type = str  # Python type with default value
    editable: bool = True  # Can the user edit this field?
    placeholder: Optional[str] = None  # Placeholder text for UI
    longtext: bool = False  # useTextEdit if true, QlineEdit if false
    min: Optional[float] = None  # Minimum value (for numbers)
    max: Optional[float] = None  # Maximum value (for numbers)
    length: Optional[int] = None  # Maximum length (for strings)
    tooltip: Optional[str] = None  # UI popup hint


ALBUM_FIELDS = {
    "album_id": AlbumField(
        type=int,
        editable=False,
        friendly="Album ID",
        short="ID",
        tooltip="The database's unique ID for the album.",
    ),
    "album_name": AlbumField(
        friendly="Album Title",
        short="Title",
        tooltip="The preferred display name for the album.",
    ),
    "album_language": AlbumField(
        friendly="Album Primary Language",
        short="Language",
        tooltip="The primary language used in the album",
    ),
    "album_subtitle": AlbumField(
        friendly="Album Subtitle",
        short="Subtitle",
        tooltip="The album's subtitle, if any.",
    ),
    "MBID": AlbumField(
        friendly="MusicBrainz ID",
        short="MBID",
        tooltip="The MusicBrainz ID number for the release",
    ),
    "release_type": AlbumField(
        friendly="Release Type",
        short="Type",
        tooltip="Indicates the general category of the release, describing its format and how it was originally published.",
        placeholder="Album, Single, Compilation, Soundtrack",
    ),
    "album_description": AlbumField(
        friendly="Album Description",
        short="Description",
        tooltip="A detailed overview of the album, typically summarizing its background, themes, and notable information.",
    ),
    "release_year": AlbumField(
        type=int,
        friendly="Release Year",
        short="Year",
        tooltip="The year the album was released",
    ),
    "release_month": AlbumField(
        type=int,
        friendly="Release Month",
        short="Month",
        tooltip="The month the album was released",
    ),
    "release_day": AlbumField(
        type=int,
        friendly="Release Day",
        short="Day",
        tooltip="The day the album was released",
    ),
    "catalog_number": AlbumField(
        friendly="Catalog Number",
        short="Catalog #",
        tooltip="The official identifier assigned to this release by the label or distributor, used for inventory, manufacturing, and archival tracking.",
    ),
    "is_fixed": AlbumField(
        type=bool,
        friendly="Metadata Complete",
        short="Complete",
        tooltip="Marks this album as having fully verified and finalized metadata, indicating no further edits are expected.",
    ),
    "album_gain": AlbumField(
        type=float,
        friendly="Album Gain",
        short="Gain",
        tooltip="Relative volume of the album to reference.",
    ),
    "album_peak": AlbumField(
        type=float,
        friendly="Track Peak",
        short="Peak",
        tooltip="The largest amplitude in the album",
    ),
    "front_cover_path": AlbumField(
        friendly="Front Cover Path",
        short="Front Cover",
        tooltip="The file path to the album's front cover.",
    ),
    "rear_cover_path": AlbumField(
        friendly="Rear Cover Path",
        short="Rear Cover",
        tooltip="The path to the album's rear cover.",
    ),
    "album_liner_path": AlbumField(
        friendly="Album Liner Path",
        short="Liner Path",
        tooltip="The path to the album's liner art",
    ),
    "album_wikipedia_link": AlbumField(
        friendly="Album Wikipedia Link",
        short="Wikipedia Link",
        tooltip="The link to the album's Wikipedia Page",
    ),
    "is_live": AlbumField(
        type=bool,
        friendly="Live",
        short="Live",
        tooltip="The album is dedicated to live recordings",
    ),
    "is_compilation": AlbumField(
        type=bool,
        friendly="Compilation",
        tooltip="The album compiles music by different artists.",
    ),
    "estimated_sales": AlbumField(
        type=int,
        friendly="Estimated Sales",
        short="Sales",
        tooltip="The estimated number of copies this release has sold.",
    ),
    "status": AlbumField(
        friendly="Album Status",
        short="Status",
        tooltip="Indicates the authenticity or publication status of the release as recognized by labels, distributors, or collectors.",
        placeholder="Official, Promotional, Bootleg, Withdrawn, Expunged, Cancelled",
    ),
    "total_duration": AlbumField(
        type=int,
        friendly="Album Duration",
        short="Duration",
        tooltip="The total length of time for the album",
        editable=False,
    ),
    "album_artist_names": AlbumField(
        type=list,
        friendly="Album Artist Names",
        short="Album Artists",
        tooltip="The names of the album artists",
        editable=False,
    ),
    "total_plays": AlbumField(
        type=int,
        friendly="Album Play Count",
        short="Play Count",
        tooltip="The number of plays in all tracks from this album.",
        editable=False,
    ),
    "average_rating": AlbumField(
        type=float,
        friendly="Average Album Rating",
        short="Album Rating",
        tooltip="The average rating of all tracks with a rating in this album.",
        editable=False,
    ),
    "track_count": AlbumField(
        type=int,
        friendly="Track Count",
        short="Tracks",
        tooltip="The number of tracks associated with this album.",
        editable=False,
    ),
    "RIAA_certification": AlbumField(
        friendly="RIAA Certification",
        tooltip="The estimated RIAA status based on estimated sales figures.",
        editable=False,
        short="RIAA",
    ),
}
