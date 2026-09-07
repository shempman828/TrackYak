"""Helpers for putting free-text (DB-derived names) into Qt widgets.

Qt treats a single ``&`` in the *plain* text of buttons, checkboxes, radio
buttons, group-box titles, menu items / QActions and tab labels as a
mnemonic prefix: it is hidden and the next character is underlined. Any
name that can contain an ampersand -- "Simon & Garfunkel", "R&B",
"Trinidad & Tobago", "Sam & Dave's" -- therefore renders with the ``&``
silently eaten unless it is doubled first. Route such text through
``esc_amp`` before handing it to those widgets.

Rich-text labels (``<b>...</b>`` etc.) are a different case: escape those
with ``html.escape`` so ``&`` becomes ``&amp;``.
"""


def esc_amp(text) -> str:
    """Double ``&`` so Qt doesn't treat it as a mnemonic prefix.

    Accepts ``None`` (and other non-strings) and coerces to ``str`` /
    ``""`` so callers can pass a possibly-missing name directly.
    """
    if text is None:
        return ""
    return str(text).replace("&", "&&")
