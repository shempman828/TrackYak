"""
Date Formatter Module for Artist ORM Objects

Formats date fields into visually pleasing strings with the following rules:
- Shows only available data (partial dates are formatted with available parts)
- Uses em dash "—" as separator for date ranges
- Returns "Current" when end_year is null (but begin date exists)
- Returns False when all date fields are empty
- Uses full month names for months 1-12
- Ignores incomplete combinations (day without month, month without year)
"""


class DateFormatter:
    """Formats date information from Artist ORM objects"""

    # Month number to name mapping
    MONTH_NAMES = {
        1: "January",
        2: "February",
        3: "March",
        4: "April",
        5: "May",
        6: "June",
        7: "July",
        8: "August",
        9: "September",
        10: "October",
        11: "November",
        12: "December",
    }

    @classmethod
    def _format_single_date(cls, day, month, year):
        """
        Format a single date from its components.

        Args:
            day (int or None): Day of month
            month (int or None): Month number (1-12)
            year (int or None): Year

        Returns:
            str: Formatted date string or empty string if no valid components
        """
        if year is None:
            # Need at least a year to format anything
            return ""

        parts = []

        # Add month if available and valid
        if month is not None and 1 <= month <= 12:
            parts.append(cls.MONTH_NAMES[month])

            # Add day only if month exists (ignore day without month)
            if day is not None:
                parts.append(str(day))

        # Always add year if available
        parts.append(str(year))

        return " ".join(parts) if parts else ""

    @classmethod
    def _has_any_date(cls, artist):
        """Check if any date field has data"""
        date_fields = [
            "begin_year",
            "begin_month",
            "begin_day",
            "end_year",
            "end_month",
            "end_day",
        ]

        for field in date_fields:
            if getattr(artist, field, None) is not None:
                return True
        return False

    @classmethod
    def format_artist_dates(cls, artist):
        """
        Format an artist's date range into a string.

        Args:
            artist: ORM object with date fields:
                begin_year, begin_month, begin_day
                end_year, end_month, end_day

        Returns:
            str or False: Formatted date string, or False if no date data
        """
        # Check if we have any date data at all
        if not cls._has_any_date(artist):
            return False

        # Format begin date
        begin_str = cls._format_single_date(
            artist.begin_day, artist.begin_month, artist.begin_year
        )

        # Format end date
        if artist.end_year is not None:
            end_str = cls._format_single_date(
                artist.end_day, artist.end_month, artist.end_year
            )
        elif begin_str:
            # Only show "Current" if we have a begin date
            end_str = "Current"
        else:
            end_str = ""

        # Combine parts
        if begin_str and end_str:
            return f"{begin_str} — {end_str}"
        elif begin_str:
            return begin_str
        elif end_str:
            return end_str
        else:
            # Should not reach here due to _has_any_date check, but just in case
            return False

    @classmethod
    def format_dates_from_fields(cls, **kwargs):
        """
        Alternative method to format dates from field values directly.

        Args:
            begin_day, begin_month, begin_year,
            end_day, end_month, end_year

        Returns:
            str or False: Formatted date string, or False if no date data
        """

        # Create a simple object to mimic ORM behavior
        class SimpleObj:
            pass

        obj = SimpleObj()

        # Set attributes from kwargs
        fields = [
            "begin_day",
            "begin_month",
            "begin_year",
            "end_day",
            "end_month",
            "end_year",
        ]

        for field in fields:
            setattr(obj, field, kwargs.get(field))

        return cls.format_artist_dates(obj)


# Convenience function for direct use
def format_artist_dates(artist):
    """
    Convenience function to format dates from an artist ORM object.

    Args:
        artist: ORM object with date fields

    Returns:
        str or False: Formatted date string, or False if no date data
    """
    return DateFormatter.format_artist_dates(artist)
