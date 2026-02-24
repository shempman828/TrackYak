"""mapping of metadata tags to database fields"""

ID3_TRACK_MAPPINGS = {
    "COMM": {"field": "comment", "type": str, "entity": "Track"},
    "PCNT": {"field": "play_count", "type": int, "entity": "Track"},
    "TBPM": {"field": "bpm", "type": float, "entity": "Track"},
    "TCOP": {"field": "track_copyright", "type": str, "entity": "Track"},
    "TIT2": {"field": "track_name", "type": str, "entity": "Track"},
    "TIT3": {"field": "track_description", "type": str, "entity": "Track"},
    "TKEY": {"field": "key", "type": str, "entity": "Track"},
    "TLEN": {"field": "duration", "type": int, "entity": "Track"},
    "TRCK": {"field": "track_number", "type": int, "entity": "Track"},
    "TSRC": {"field": "isrc", "type": str, "entity": "Track"},
    "USLT": {"field": "lyrics", "type": str, "entity": "Track"},
    "TIT1": {"field": "work_name", "type": str, "entity": "Track"},
}
ID3_MOOD_MAPPINGS = {"TMOO": {"field": "mood_name", "type": str, "entity": "Mood"}}
ID3_ALBUM_MAPPINGS = {
    "TALB": {"field": "album_name", "type": str, "entity": "Album"},
    "TLAN": {"field": "album_language", "type": str, "entity": "Album"},
}
ID3_PUBLISHER_MAPPINGS = {
    "TPUB": "publisher_name",
}
ID3_DISC_MAPPINGS = {"TPOS": {"field": "disc_number", "type": int, "entity": "Disc"}}
ID3_ARTIST_MAPPINGS = {
    "TCOM": {
        "field": "artist_name",
        "type": str,
        "entity": "Artist",
        "role_name": "Composer",
    },
    "TPE1": {
        "field": "artist_name",
        "type": str,
        "entity": "Artist",
        "role_name": "Primary Artist",
    },
    "TPE2": {
        "field": "artist_name",
        "type": str,
        "entity": "Artist",
        "role_name": "Album Artist",
    },
    "TEXT": {
        "field": "artist_name",
        "type": str,
        "entity": "Artist",
        "role_name": "Lyricist",
    },
    "TOLY": {
        "field": "artist_name",
        "type": str,
        "entity": "Artist",
        "role_name": "Original Lyricist",
    },
    "TOPE": {
        "field": "artist_name",
        "type": str,
        "entity": "Artist",
        "role_name": "Original Performer",
    },
    "TPE3": {
        "field": "artist_name",
        "type": str,
        "entity": "Artist",
        "role_name": "Conductor",
    },
}
ID3_SPECIAL_MAPPINGS = {
    "TMCL": {
        "role_field": "role_name",
        "artist_field": "artist_name",
        "separator": ",",
        "entity": "Artist",
        "type": "special",
    },
    "TIPL": {
        "role_field": "role_name",
        "artist_field": "artist_name",
        "separator": ",",
        "entity": "Artist",
        "type": "special",
    },
}
ID3_GENRE_MAPPINGS = {
    "TOCN": {"field": "genre_name", "type": str, "entity": "Genre"},
    "TCON": {"field": "genre_name", "type": str, "entity": "Genre"},
}

ID3_DATE_MAPPINGS = {
    "TDRC": {
        "target": "track",
        "fields": ["recorded_year", "recorded_month", "recorded_day"],
        "type": "date",
        "format": "YYYY-MM-DD",
        "entity": "Track",
    },
    "TYER": {
        "target": "track",
        "fields": ["recorded_year"],
        "type": "year",
        "entity": "Track",
    },
    "TDOR": {
        "target": "album",
        "fields": ["release_year", "release_month", "release_day"],
        "type": "date",
        "format": "YYYY-MM-DD",
        "entity": "Album",
    },
}


VORBIS_TRACK_MAPPINGS = {
    # Core identification
    "TITLE": {"field": "track_name", "type": str, "entity": "Track"},
    "TRACKNUMBER": {"field": "track_number", "type": int, "entity": "Track"},
    "ISRC": {"field": "isrc", "type": str, "entity": "Track"},
    "MUSICBRAINZ_TRACKID": {"field": "MBID", "type": str, "entity": "Track"},
    "MUSICBRAINZ_RELEASETRACKID": {"field": "MBID", "type": str, "entity": "Track"},
    # Alternate title forms (multilingual / stylized)
    "TITLESORT": {"field": "track_name_transcribed", "type": str, "entity": "Track"},
    "TITLE_ORIGINAL": {"field": "track_name_original", "type": str, "entity": "Track"},
    "TITLE_TRANSLATION": {
        "field": "track_name_translated",
        "type": str,
        "entity": "Track",
    },
    "TITLE_OFFICIAL": {"field": "track_name_official", "type": str, "entity": "Track"},
    "TITLE_STYLIZED": {"field": "track_name_stylized", "type": str, "entity": "Track"},
    # Audio characteristics
    "BPM": {"field": "bpm", "type": float, "entity": "Track"},
    "KEY": {"field": "key", "type": str, "entity": "Track"},
    "MODE": {"field": "mode", "type": str, "entity": "Track"},
    "TIMESIGNATURE": {
        "field": "primary_time_signature",
        "type": str,
        "entity": "Track",
    },
    "REPLAYGAIN_TRACK_GAIN": {"field": "track_gain", "type": float, "entity": "Track"},
    "REPLAYGAIN_TRACK_PEAK": {"field": "track_peak", "type": float, "entity": "Track"},
    # Legal / identification
    "COPYRIGHT": {"field": "track_copyright", "type": str, "entity": "Track"},
    "BARCODE": {"field": "track_barcode", "type": str, "entity": "Track"},
    # User data
    "COMMENT": {"field": "comment", "type": str, "entity": "Track"},
    "LYRICS": {"field": "lyrics", "type": str, "entity": "Track"},
    "DESCRIPTION": {"field": "track_description", "type": str, "entity": "Track"},
    "PLAYCOUNT": {"field": "play_count", "type": int, "entity": "Track"},
    "RATING": {"field": "user_rating", "type": float, "entity": "Track"},
    # Content flags
    "EXPLICIT": {"field": "is_explicit", "type": int, "entity": "Track"},
    "INSTRUMENTAL": {"field": "is_instrumental", "type": int, "entity": "Track"},
    "SIDE": {"field": "side", "type": str, "entity": "Track"},
    "QUALITY": {"field": "track_quality", "type": str, "entity": "Track"},
    # Classical
    "WORK": {"field": "work_name", "type": str, "entity": "Track"},
    "WORKTYPE": {"field": "work_type", "type": str, "entity": "Track"},
    "MOVEMENTNAME": {"field": "movement_name", "type": str, "entity": "Track"},
    "MOVEMENTNUMBER": {"field": "movement_number", "type": int, "entity": "Track"},
    "CLASSICALPREFIX": {
        "field": "classical_catalog_prefix",
        "type": str,
        "entity": "Track",
    },
    "CLASSICALCATALOGNUMBER": {
        "field": "classical_catalog_number",
        "type": int,
        "entity": "Track",
    },
    "CLASSICALTEMPO": {"field": "classical_tempo", "type": str, "entity": "Track"},
    "CLASSICAL": {"field": "is_classical", "type": int, "entity": "Track"},
    # Spectral / advanced analysis (custom tags — readable by any tagger)
    "DANCEABILITY": {"field": "danceability", "type": float, "entity": "Track"},
    "VALENCE": {"field": "valence", "type": float, "entity": "Track"},
    "ENERGY": {"field": "energy", "type": float, "entity": "Track"},
    "ACOUSTICNESS": {"field": "acousticness", "type": float, "entity": "Track"},
    "LIVENESS": {"field": "liveness", "type": float, "entity": "Track"},
    "KEYCONFIDENCE": {"field": "key_confidence", "type": float, "entity": "Track"},
    "TEMPOCONFIDENCE": {"field": "tempo_confidence", "type": float, "entity": "Track"},
    "DYNAMICRANGE": {"field": "dynamic_range", "type": float, "entity": "Track"},
    "STEREOWIDTH": {"field": "stereo_width", "type": float, "entity": "Track"},
    "FIDELITYSCORE": {"field": "fidelity_score", "type": float, "entity": "Track"},
    "SPECTRALCENTROID": {
        "field": "spectral_centroid",
        "type": float,
        "entity": "Track",
    },
}
VORBIS_ALBUM_MAPPINGS = {
    # Core
    "ALBUM": {"field": "album_name", "type": str, "entity": "Album"},
    "ALBUMSORT": {
        "field": "album_name",
        "type": str,
        "entity": "Album",
    },  # read-only alias
    "SUBTITLE": {"field": "album_subtitle", "type": str, "entity": "Album"},
    "MUSICBRAINZ_ALBUMID": {"field": "MBID", "type": str, "entity": "Album"},
    # Release metadata
    "RELEASETYPE": {"field": "release_type", "type": str, "entity": "Album"},
    "STATUS": {"field": "status", "type": str, "entity": "Album"},
    "CATALOGNUMBER": {"field": "catalog_number", "type": str, "entity": "Album"},
    "LANGUAGE": {"field": "album_language", "type": str, "entity": "Album"},
    # Flags
    "COMPILATION": {"field": "is_compilation", "type": int, "entity": "Album"},
    "LIVE": {"field": "is_live", "type": int, "entity": "Album"},
    # Replay gain
    "REPLAYGAIN_ALBUM_GAIN": {"field": "album_gain", "type": float, "entity": "Album"},
    "REPLAYGAIN_ALBUM_PEAK": {"field": "album_peak", "type": float, "entity": "Album"},
    # Stats / misc
    "SALES": {"field": "estimated_sales", "type": int, "entity": "Album"},
}


VORBIS_DISC_MAPPINGS = {
    "DISCNUMBER": {"field": "disc_number", "type": int, "entity": "Disc"},
    "DISCTITLE": {"field": "disc_title", "type": str, "entity": "Disc"},
    "DISCSUBTITLE": {"field": "disc_title", "type": str, "entity": "Disc"},
    "MEDIA": {"field": "media_type", "type": str, "entity": "Disc"},
}
VORBIS_MOOD_MAPPINGS = {
    "MOOD": {"field": "mood_name", "type": str, "entity": "Mood"},
    "MOODDESC": {"field": "mood_description", "type": str, "entity": "Mood"},
}


VORBIS_ARTIST_MAPPINGS = {
    "ARTIST": {
        "field": "artist_name",
        "type": str,
        "entity": "Artist",
        "role": "Primary Artist",
    },
    "PERFORMER": {
        "field": "artist_name",
        "type": str,
        "entity": "Artist",
        "role": "Performer",
        "priority": "low",
    },
    "ALBUMARTIST": {
        "field": "artist_name",
        "type": str,
        "entity": "Artist",
        "role": "Album Artist",
    },
    "COMPOSER": {
        "field": "artist_name",
        "type": str,
        "entity": "Artist",
        "role": "Composer",
    },
    "LYRICIST": {
        "field": "artist_name",
        "type": str,
        "entity": "Artist",
        "role": "Lyricist",
    },
    "ARRANGER": {
        "field": "artist_name",
        "type": str,
        "entity": "Artist",
        "role": "Arranger",
    },
    "ORIGINALPERFORMER": {
        "field": "artist_name",
        "type": str,
        "entity": "Artist",
        "role": "Original Performer",
    },
    "CONDUCTOR": {
        "field": "artist_name",
        "type": str,
        "entity": "Artist",
        "role": "Conductor",
    },
    "ENGINEER": {
        "field": "artist_name",
        "type": str,
        "entity": "Artist",
        "role": "Engineer",
    },
    "MIXER": {"field": "artist_name", "type": str, "entity": "Artist", "role": "Mixer"},
    "PRODUCER": {
        "field": "artist_name",
        "type": str,
        "entity": "Artist",
        "role": "Producer",
    },
    "REMIXER": {
        "field": "artist_name",
        "type": str,
        "entity": "Artist",
        "role": "Remixer",
    },
    "WRITER": {
        "field": "artist_name",
        "type": str,
        "entity": "Artist",
        "role": "Writer",
    },
    "VOCALS": {
        "field": "artist_name",
        "type": str,
        "entity": "Artist",
        "role": "Vocalist",
    },
    "VOCALIST": {
        "field": "artist_name",
        "type": str,
        "entity": "Artist",
        "role": "Vocalist",
    },
    "SPOKEN": {
        "field": "artist_name",
        "type": str,
        "entity": "Artist",
        "role": "Narrator",
    },
    "NARRATOR": {
        "field": "artist_name",
        "type": str,
        "entity": "Artist",
        "role": "Narrator",
    },
    "ORCHESTRA": {
        "field": "artist_name",
        "type": str,
        "entity": "Artist",
        "role": "Orchestra",
    },
    "CHOIR": {"field": "artist_name", "type": str, "entity": "Artist", "role": "Choir"},
    "ARRANGEMENT": {
        "field": "artist_name",
        "type": str,
        "entity": "Artist",
        "role": "Arranger",
    },
    "DJ": {"field": "artist_name", "type": str, "entity": "Artist", "role": "DJ"},
    "MASTERING": {
        "field": "artist_name",
        "type": str,
        "entity": "Artist",
        "role": "Mastering Engineer",
    },
}

VORBIS_PUBLISHER_MAPPINGS = {
    "ORGANIZATION": {"field": "publisher_name", "type": str, "entity": "Publisher"},
    "LABEL": {"field": "publisher_name", "type": str, "entity": "Publisher"},
    "PUBLISHER": {"field": "publisher_name", "type": str, "entity": "Publisher"},
    "COMPANY": {"field": "publisher_name", "type": str, "entity": "Publisher"},
}

VORBIS_GENRE_MAPPINGS = {
    "GENRE": {"field": "genre_name", "type": str, "entity": "Genre"},
    "STYLE": {"field": "genre_name", "type": str, "entity": "Genre"},
    "GENREDESC": {"field": "genre_description", "type": str, "entity": "Genre"},
}
VORBIS_PLACE_MAPPINGS = {
    "LOCATION": {
        "field": "place_name",
        "type": str,
        "entity": "Place",
        "association_type": "Recording Location",
        "entity_type": "Track",
    },
    "RELEASECOUNTRY": {
        "field": "place_name",
        "type": str,
        "entity": "Place",
        "association_type": "Release Country",
        "entity_type": "Album",
    },
}

VORBIS_DATE_MAPPINGS = {
    "DATE": {
        "target": "album",
        "fields": ["release_year", "release_month", "release_day"],
        "type": "date",
        "format": "auto",
        "entity": "Album",
    },
    "RELEASE_DATE": {
        "target": "album",
        "fields": ["release_year", "release_month", "release_day"],
        "type": "date",
        "format": "auto",
        "entity": "Album",
    },
    "ORIGINALDATE": {
        "target": "album",
        "fields": ["release_year", "release_month", "release_day"],
        "type": "date",
        "format": "auto",
        "entity": "Album",
    },
    "YEAR": {
        "target": "album",
        "fields": ["release_year"],
        "type": "year",
        "entity": "Album",
    },
    "ORIGINALYEAR": {
        "target": "album",
        "fields": ["release_year"],
        "type": "year",
        "entity": "Album",
    },
    "RECORDINGDATE": {
        "target": "track",
        "fields": ["recorded_year", "recorded_month", "recorded_day"],
        "type": "date",
        "format": "auto",
        "entity": "Track",
    },
    "COMPOSEDDATE": {
        "target": "track",
        "fields": ["composed_year", "composed_month", "composed_day"],
        "type": "date",
        "format": "auto",
        "entity": "Track",
    },
}

VORBIS_SPECIAL_MAPPINGS = {
    # PERFORMER = "Artist Name (Role)" — MusicBrainz Picard convention
    "PERFORMER": {
        "artist_field": "artist_name",
        "patterns": [
            r"^(?P<artist>.+?)\s*\((?P<role>.+)\)$",  # "Artist (Role)"
            r"^(?P<role>.+?):\s*(?P<artist>.+)$",  # "Role: Artist"
            r"^(?P<artist>.+?)\s*-\s*(?P<role>.+)$",  # "Artist - Role"
            r"^(?P<artist>.+)$",  # fallback: just artist
        ],
        "default_role": "Performer",
        "entity": "Artist",
        "type": "special",
        "priority": "high",
    },
}
