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
    "TITLE": {"field": "track_name", "type": str, "entity": "Track"},
    "TRACKNUMBER": {"field": "track_number", "type": int, "entity": "Track"},
    "COPYRIGHT": {"field": "track_copyright", "type": str, "entity": "Track"},
    "ISRC": {"field": "isrc", "type": str, "entity": "Track"},
    "REPLAYGAIN_TRACK_GAIN": {"field": "track_gain", "type": float, "entity": "Track"},
    "REPLAYGAIN_TRACK_PEAK": {"field": "track_peak", "type": float, "entity": "Track"},
    "MUSICBRAINZ_TRACKID": {"field": "MBID", "type": str, "entity": "Track"},
    "BPM": {"field": "bpm", "type": float, "entity": "Track"},
    "COMMENT": {"field": "comment", "type": str, "entity": "Track"},
    "KEY": {"field": "key", "type": str, "entity": "Track"},
    "LYRICS": {"field": "lyrics", "type": str, "entity": "Track"},
    "MOVEMENTNAME": {"field": "movement_name", "type": str, "entity": "Track"},
    "MOVEMENTNUMBER": {"field": "movement_number", "type": int, "entity": "Track"},
    "MUSICBRAINZ_RELEASETRACKID": {"field": "MBID", "type": str, "entity": "Track"},
    "WORK": {"field": "work_name", "type": str, "entity": "Track"},
    "PLAYCOUNT": {"field": "play_count", "type": int, "entity": "Track"},
    "EXPLICIT": {"field": "is_explicit", "type": int, "entity": "Track"},
    "INSTRUMENTAL": {"field": "is_instrumental", "type": int, "entity": "Track"},
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
    "MODE": {"field": "mode", "type": str, "entity": "Track"},
    "QUALITY": {"field": "track_quality", "type": str, "entity": "Track"},
    "SIDE": {"field": "side", "type": str, "entity": "Track"},
    "CLASSICAL": {"field": "is_classical", "type": int, "entity": "Track"},
    "BARCODE": {"field": "track_barcode", "type": str, "entity": "Track"},
    "DANCEABILITY": {"field": "danceability", "type": float, "entity": "Track"},
    "VALENCE": {"field": "valence", "type": float, "entity": "Track"},
    "ENERGY": {"field": "energy", "type": float, "entity": "Track"},
    "ACOUSTICNESS": {"field": "acousticness", "type": float, "entity": "Track"},
    "KEYCONFIDENCE": {"field": "key_confidence", "type": float, "entity": "Track"},
    "TEMPOCONFIDENCE": {"field": "tempo_confidence", "type": float, "entity": "Track"},
}
VORBIS_ALBUM_MAPPINGS = {
    "ALBUM": {"field": "album_name", "type": str, "entity": "Album"},
    "REPLAYGAIN_ALBUM_GAIN": {"field": "album_gain", "type": float, "entity": "Album"},
    "REPLAYGAIN_ALBUM_PEAK": {"field": "album_peak", "type": float, "entity": "Album"},
    "CATALOGNUMBER": {"field": "catalog_number", "type": str, "entity": "Album"},
    "LANGUAGE": {"field": "album_language", "type": str, "entity": "Album"},
    "MUSICBRAINZ_ALBUMID": {"field": "MBID", "type": str, "entity": "Album"},
    "RELEASETYPE": {"field": "release_type", "type": str, "entity": "Album"},
    "STATUS": {"field": "status", "type": str, "entity": "Album"},
    "SALES": {"field": "estimated_sales", "type": int, "entity": "Album"},
}


VORBIS_DISC_MAPPINGS = {
    "DISCSUBTITLE": {"field": "disc_name", "type": str, "entity": "Disc"},
    "DISCTITLE": {"field": "disc_name", "type": str, "entity": "Disc"},
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
    "TIME": {
        "target": "album",
        "fields": ["release_year"],
        "type": "year",
        "entity": "Album",
    },
}
VORBIS_SPECIAL_MAPPINGS = {
    "PERFORMER": {
        "artist_field": "artist_name",
        "patterns": [
            # MusicBrainz format: "Artist (Role)"
            r"^(?P<artist>.+?)\s*\((?P<role>.+)\)$",
            # Alternative format: "Role: Artist"
            r"^(?P<role>.+?):\s*(?P<artist>.+)$",
            # Some use " - " as separator: "Artist - Role"
            r"^(?P<artist>.+?)\s*-\s*(?P<role>.+)$",
            # Just the artist name (fallback)
            r"^(?P<artist>.+)$",
        ],
        "default_role": "Performer",  # For fallback case
        "entity": "Artist",
        "type": "special",
        "priority": "high",  # Give this special processing high priority
    }
}
