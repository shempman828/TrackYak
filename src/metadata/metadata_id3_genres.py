"""ID3v1 genre table and resolution of ID3v2 TCON numeric genre references."""

import re

from src.foundation.logger_config import logger

# ID3v1 genres 0-79 plus the Winamp extensions 80-191.
ID3V1_GENRES = (
    "Blues", "Classic Rock", "Country", "Dance", "Disco", "Funk", "Grunge", "Hip-Hop",
    "Jazz", "Metal", "New Age", "Oldies", "Other", "Pop", "R&B", "Rap",
    "Reggae", "Rock", "Techno", "Industrial", "Alternative", "Ska", "Death Metal", "Pranks",
    "Soundtrack", "Euro-Techno", "Ambient", "Trip-Hop", "Vocal", "Jazz+Funk", "Fusion", "Trance",
    "Classical", "Instrumental", "Acid", "House", "Game", "Sound Clip", "Gospel", "Noise",
    "Alternative Rock", "Bass", "Soul", "Punk", "Space", "Meditative", "Instrumental Pop", "Instrumental Rock",
    "Ethnic", "Gothic", "Darkwave", "Techno-Industrial", "Electronic", "Pop-Folk", "Eurodance", "Dream",
    "Southern Rock", "Comedy", "Cult", "Gangsta", "Top 40", "Christian Rap", "Pop/Funk", "Jungle",
    "Native American", "Cabaret", "New Wave", "Psychedelic", "Rave", "Showtunes", "Trailer", "Lo-Fi",
    "Tribal", "Acid Punk", "Acid Jazz", "Polka", "Retro", "Musical", "Rock & Roll", "Hard Rock",
    "Folk", "Folk-Rock", "National Folk", "Swing", "Fast Fusion", "Bebop", "Latin", "Revival",
    "Celtic", "Bluegrass", "Avantgarde", "Gothic Rock", "Progressive Rock", "Psychedelic Rock", "Symphonic Rock", "Slow Rock",
    "Big Band", "Chorus", "Easy Listening", "Acoustic", "Humour", "Speech", "Chanson", "Opera",
    "Chamber Music", "Sonata", "Symphony", "Booty Bass", "Primus", "Porn Groove", "Satire", "Slow Jam",
    "Club", "Tango", "Samba", "Folklore", "Ballad", "Power Ballad", "Rhythmic Soul", "Freestyle",
    "Duet", "Punk Rock", "Drum Solo", "A Cappella", "Euro-House", "Dance Hall", "Goa", "Drum & Bass",
    "Club-House", "Hardcore", "Terror", "Indie", "BritPop", "Afro-Punk", "Polsk Punk", "Beat",
    "Christian Gangsta Rap", "Heavy Metal", "Black Metal", "Crossover", "Contemporary Christian", "Christian Rock", "Merengue", "Salsa",
    "Thrash Metal", "Anime", "JPop", "Synthpop", "Abstract", "Art Rock", "Baroque", "Bhangra",
    "Big Beat", "Breakbeat", "Chillout", "Downtempo", "Dub", "EBM", "Eclectic", "Electro",
    "Electroclash", "Emo", "Experimental", "Garage", "Global", "IDM", "Illbient", "Industro-Goth",
    "Jam Band", "Krautrock", "Leftfield", "Lounge", "Math Rock", "New Romantic", "Nu-Breakz", "Post-Punk",
    "Post-Rock", "Psytrance", "Shoegaze", "Space Rock", "Trop Rock", "World Music", "Neoclassical", "Audiobook",
    "Audio Theatre", "Neue Deutsche Welle", "Podcast", "Indie Rock", "G-Funk", "Dubstep", "Garage Rock", "Psybient",
)  # fmt: skip

_SPECIAL_REFS = {"RX": "Remix", "CR": "Cover"}

# A leading "(n)", "(RX)" or "(CR)" reference; "((" escapes a literal "(".
_LEADING_REF = re.compile(r"\((\d+|RX|CR)\)")


def _resolve_ref(token: str) -> str | None:
    """Return the genre name for a numeric or RX/CR token, or None if the number is not in the table."""
    if token in _SPECIAL_REFS:
        return _SPECIAL_REFS[token]
    index = int(token)
    if index < len(ID3V1_GENRES):
        return ID3V1_GENRES[index]
    logger.debug(f"Dropping unknown ID3 genre reference: {token}")
    return None


def resolve_tcon_value(value: str) -> list[str]:
    """Resolve one TCON value such as "(17)", "(17)Rock", "(4)(RX)Eurodisco", "17" or "RX" to genre names."""
    value = value.strip()
    # v2.4 stores bare numbers and RX/CR as their own values.
    if value.isdigit() or value in _SPECIAL_REFS:
        resolved = _resolve_ref(value)
        return [resolved] if resolved else []

    genres = []
    pos = 0
    while match := _LEADING_REF.match(value, pos):
        if resolved := _resolve_ref(match.group(1)):
            genres.append(resolved)
        pos = match.end()

    refinement = value[pos:]
    if refinement.startswith("(("):
        refinement = refinement[1:]
    refinement = refinement.strip()
    if refinement:
        genres.append(refinement)

    # "(17)Rock" names the same genre twice.
    return list(dict.fromkeys(genres))
