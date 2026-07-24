"""Plain-text track display formatting for classical and standard tracks.

Extracted from PlayerUI (player_dock.py) so the formatting logic can be
tested and read independently of the Qt widget it feeds.
"""

from src.core.logger_config import logger

_MONTH_NAMES = [
    "",
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]


def format_track_display(track) -> str:
    """Format track display text based on classical/non-classical classification."""
    try:
        if getattr(track, "is_classical", False):
            return _format_classical_track(track)
        else:
            return _format_standard_track(track)
    except Exception as e:
        logger.error(f"Error formatting track display: {e}")
        return "Unknown Track"


def _format_standard_track(track) -> str:
    """Format standard (non-classical) track display."""
    parts = []

    track_name = getattr(track, "track_name", "Unknown Title")
    parts.append(track_name)

    artist_name = (
        track.primary_artist_names if hasattr(track, "primary_artist_names") else None
    )
    if artist_name:
        parts.append(f"by {artist_name}")

    album_name = getattr(track, "album_name", None)
    release_year = getattr(track, "release_year", None)

    if album_name and release_year:
        parts.append(f"from {album_name} ({release_year})")
    elif album_name:
        parts.append(f"from {album_name}")
    elif release_year:
        parts.append(f"({release_year})")

    return " ".join(parts)


def _format_classical_track(track) -> str:
    """Format classical track display with structured information."""
    lines = []

    composer_names = _get_composer_names(track)
    if composer_names:
        lines.append(f"{composer_names}:")

    work_parts = []

    work_type = getattr(track, "work_type", None)
    if work_type:
        work_parts.append(work_type)

    work_name = getattr(track, "work_name", None)
    if work_name:
        work_parts.append(work_name)

    catalog_prefix = getattr(track, "classical_catalog_prefix", None)
    catalog_number = getattr(track, "classical_catalog_number", None)
    if catalog_prefix and catalog_number:
        work_parts.append(f"{catalog_prefix} {catalog_number}")
    elif catalog_number:
        work_parts.append(catalog_number)

    comp_date = _format_date(
        getattr(track, "composed_year", None),
        getattr(track, "composed_month", None),
        getattr(track, "composed_day", None),
        "composed",
    )
    if comp_date:
        work_parts.append(comp_date)

    if work_parts:
        lines.append(" ".join(work_parts))

    movement_parts = []

    movement_number_roman = getattr(track, "movement_number_roman", None)
    if movement_number_roman:
        movement_parts.append(f"{movement_number_roman}.")

    movement_name = getattr(track, "movement_name", None)
    if movement_name:
        movement_parts.append(movement_name)

    if movement_parts:
        lines.append(" ".join(movement_parts))

    perf_parts = []

    rec_date = _format_date(
        getattr(track, "recorded_year", None),
        getattr(track, "recorded_month", None),
        getattr(track, "recorded_day", None),
        "recorded",
    )
    if rec_date:
        perf_parts.append(rec_date)

    first_date = _format_date(
        getattr(track, "first_performed_year", None),
        getattr(track, "first_performed_month", None),
        getattr(track, "first_performed_day", None),
        "first performed",
    )
    if first_date:
        if perf_parts:
            perf_parts.append(f", {first_date}")
        else:
            perf_parts.append(first_date)

    if perf_parts:
        lines.append(f"({''.join(perf_parts)})")

    performer_name = (
        track.primary_artist_names if hasattr(track, "primary_artist_names") else None
    )
    if performer_name:
        lines.append(f"Performed by {performer_name}")

    return "\n".join(lines)


def _get_composer_names(track) -> str:
    """Extract and format composer names for classical tracks."""
    try:
        composers = []

        track_composers = getattr(track, "composers", [])
        for composer in track_composers:
            if hasattr(composer, "artist_name"):
                composers.append(composer.artist_name)

        if not composers:
            composer_name = getattr(track, "composer_name", None)
            if composer_name:
                composers.extend([name.strip() for name in composer_name.split(",")])

        if composers:
            return ", ".join(composers)
        else:
            return "Unknown Composer"
    except Exception as e:
        logger.error(f"Error getting composer names: {e}")
        return "Unknown Composer"


def _format_date(year, month, day, prefix: str) -> str:
    """Format a date with year, month, day components."""
    if not year:
        return ""

    date_parts = [prefix, year]

    if month:
        date_parts.append(_get_month_name(month))

    if day:
        date_parts.append(str(day))

    return ": " + " ".join(date_parts)


def _get_month_name(month) -> str:
    """Convert month number to month name."""
    try:
        month_int = int(month)
        if 1 <= month_int <= 12:
            return _MONTH_NAMES[month_int]
    except (ValueError, TypeError):
        pass
    return str(month)
