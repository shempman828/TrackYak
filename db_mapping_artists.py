from dataclasses import dataclass
from json import tool  # noqa: F401
from typing import Optional, Type


@dataclass
class ArtistField:
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


ARTIST_FIELDS = {
    "artist_id": ArtistField(
        type=int,
        editable=False,
        friendly="Artist ID",
        short="ID",
        tooltip="The database's unique ID for the artist.",
    ),
    "artist_name": ArtistField(
        type=str,
        placeholder="Artist Name",
        friendly="Artist Name",
        short="Name",
        tooltip="The official name of the artist.",
    ),
    "isgroup": ArtistField(
        type=int,
        friendly="Is Group",
        short="Group",
        tooltip="This artist name represents a group of people.",
    ),
    "begin_year": ArtistField(
        type=int,
        placeholder=1900,
        friendly="Start Year",
        short="Start",
        tooltip="The year the person was born or the group was founded.",
    ),
    "end_year": ArtistField(
        type=int,
        placeholder=2030,
        friendly="End Year",
        short="End",
        tooltip="The year the person died or the group disbanded.",
    ),
    "end_month": ArtistField(
        type=int,
        friendly="End Month",
        tooltip="The month the person died or the group disbanded.",
        max=12,
    ),
    "end_day": ArtistField(
        type=int,
        friendly="End Day",
        tooltip="The day the person died or the group disbanded.",
        max=31,
    ),
    "begin_month": ArtistField(
        type=int,
        friendly="Begin Month",
        tooltip="The month the person was born or the group was founded.",
    ),
    "begin_day": ArtistField(
        type=int,
        friendly="Begin Day",
        tooltip="The day the person was born or the group was founded.",
    ),
    "is_fixed": ArtistField(
        type=int,
        friendly="Metadata Complete",
        short="Complete",
        max=1,
        tooltip="Indicates that you consider this artist's profile to be complete.",
    ),
    "biography": ArtistField(
        type=str,
        friendly="Biography",
        short="Bio",
        longtext=True,
        tooltip="A biography about the artist.",
    ),
    "MBID": ArtistField(
        type=str,
        friendly="MusicBrainz ID",
        short="MBID",
        tooltip="The ID page pointing to the MusicBrainz entry for this artist.",
    ),
    "profile_pic_path": ArtistField(
        type=str,
        friendly="Profile Picture Path",
        short="Image Path",
        tooltip="The file path to the profile image of this artist.",
    ),
    "wikipedia_link": ArtistField(
        type=str,
        friendly="Wikipedia Link",
        short="Wiki Link",
        tooltip="The link to the artist's entry on Wikipedia.",
    ),
    "gender": ArtistField(
        type=str,
        friendly="Gender",
    ),
    "website_link": ArtistField(
        type=str,
        friendly="Artist Website Link",
        short="Website",
        tooltip="The link to the artist's official webpage.",
    ),
    "artist_type": ArtistField(
        type=str,
        friendly="Artist Type",
        short="Type",
        placeholder="Person, Band, Orchestra, Choir",
        tooltip="The type of artist",
    ),
    "aliases_list": ArtistField(
        type=list,
        friendly="Aliases",
        tooltip="Other names this artist may go by.",
    ),
}
