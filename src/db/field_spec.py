from dataclasses import dataclass


@dataclass
class FieldSpec:
    friendly: str | None = None  # Human-readable name
    short: str | None = None  # shorter human readable name
    type: type = str  # Python type with default value
    editable: bool = True  # Can the user edit this field?
    placeholder: str | None = None  # Placeholder text for UI
    longtext: bool = False  # useTextEdit if true, QlineEdit if false
    min: float | None = None  # Minimum value (for numbers)
    max: float | None = None  # Maximum value (for numbers)
    length: int | None = None  # Maximum length (for strings)
    step: float | None = None  # Spin box increment (for numbers); default 1.0
    decimals: int | None = None  # Decimal places shown (for floats); default 4
    category: str | None = (
        None  # Grouping or section name (Basic, Properties, Alias (different track name types), Date, Classical, Identification, User, Lyrics, Description, Advanced)
    )
    tooltip: str | None = None  # UI popup hint
    multiple: bool = False  # can be edited on multiple tracks at once
    section: str | None = (
        None  # Optional sub-heading within a tab (e.g. splits Properties into "File Info" / "Musical Properties")
    )
