from dataclasses import dataclass
from typing import Optional, Type


@dataclass
class FieldSpec:
    friendly: Optional[str] = None  # Human-readable name
    short: Optional[str] = None  # shorter human readable name
    type: Type = str  # Python type with default value
    editable: bool = True  # Can the user edit this field?
    placeholder: Optional[str] = None  # Placeholder text for UI
    longtext: bool = False  # useTextEdit if true, QlineEdit if false
    min: Optional[float] = None  # Minimum value (for numbers)
    max: Optional[float] = None  # Maximum value (for numbers)
    length: Optional[int] = None  # Maximum length (for strings)
    step: Optional[float] = None  # Spin box increment (for numbers); default 1.0
    decimals: Optional[int] = None  # Decimal places shown (for floats); default 4
    category: Optional[str] = (
        None  # Grouping or section name (Basic, Properties, Alias (different track name types), Date, Classical, Identification, User, Lyrics, Description, Advanced)
    )
    tooltip: Optional[str] = None  # UI popup hint
    multiple: bool = False  # can be edited on multiple tracks at once
    section: Optional[str] = None  # Optional sub-heading within a tab (e.g. splits Properties into "File Info" / "Musical Properties")
