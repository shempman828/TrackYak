"""
artist_alias_dialog.py

Alias utilities shared across the app:
  - SUGGESTED_ALIAS_TYPES — autocomplete suggestions for the alias "type"
                             field (see artist_edit_alias.AliasesTab).
"""

# ── Well-known alias types offered as autocomplete suggestions ─────────────
SUGGESTED_ALIAS_TYPES = [
    "Legal Name",
    "Stylized Name",
    "Project Name",
    "Persona",
    "Birth Name",
    "Former Name",
    "Localized Name",
    "Romanized Name",
    "Phonetic Name",
    "Nickname",
    "Other",
]
