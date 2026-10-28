"""Helpers for putting free-text (DB-derived names) into Qt widgets."""


def esc_amp(text) -> str:
    """Double ``&`` so Qt doesn't treat it as a mnemonic prefix; None becomes ""."""
    # Only for mnemonic-bearing text (buttons, checkboxes, menus, tabs, group
    # boxes). QLabel/QMessageBox text has no mnemonics; rich text needs html.escape.
    if text is None:
        return ""
    return str(text).replace("&", "&&")
