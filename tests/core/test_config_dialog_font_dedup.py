"""Regression test for the font dropdown duplicate-alias bug: fontconfig
registers each named-instance weight/width of a variable font as its own
Qt font "family" (e.g. "Noto Sans Thin" for the same file as "Noto Sans"),
which used to be dumped straight into the Appearance settings font combo
with no de-duplication.
"""

import subprocess

from PySide6.QtGui import QFontDatabase

from src.core.config_dialog import ConfigDialog


def test_compute_canonical_font_families_collapses_named_instance_aliases(
    qapp, monkeypatch
):
    fake_families = [
        "Test Sans",
        "Test Sans Thin",
        "Test Sans Condensed SemiBold",
        "Test Sans Arabic",
        "Test Sans Arabic Cond Blk",
        "Test Serif",
        "Test Serif Black",
    ]
    fake_styles = {
        "Test Sans": ["Regular", "Bold", "Thin", "Condensed SemiBold"],
        "Test Sans Thin": ["Regular", "Italic"],
        "Test Sans Condensed SemiBold": ["Regular", "Italic"],
        "Test Sans Arabic": ["Regular", "Bold", "Condensed Black"],
        "Test Sans Arabic Cond Blk": ["Regular"],
        "Test Serif": ["Regular", "Bold", "Italic"],
        "Test Serif Black": ["Regular"],
    }
    fake_fc_list = "\n".join(
        [
            "Test Sans",
            "Test Sans,Test Sans Thin",
            "Test Sans,Test Sans Condensed SemiBold",
            "Test Sans Arabic",
            "Test Sans Arabic,Test Sans Arabic Cond Blk",
            "Test Serif",
            # Test Serif Black is its own dedicated file, never co-registered
            # with "Test Serif" — a genuinely distinct typeface, not a
            # named-instance alias, and must survive de-duplication.
            "Test Serif Black",
        ]
    )

    monkeypatch.setattr(
        QFontDatabase, "families", staticmethod(lambda *a, **k: list(fake_families))
    )
    monkeypatch.setattr(
        QFontDatabase,
        "styles",
        staticmethod(lambda family: list(fake_styles[family])),
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(
            args=a, returncode=0, stdout=fake_fc_list, stderr=""
        ),
    )

    canonical = ConfigDialog._compute_canonical_font_families()

    assert canonical == {
        "Test Sans",
        "Test Sans Arabic",
        "Test Serif",
        "Test Serif Black",
    }


def test_compute_canonical_font_families_falls_back_when_fc_list_missing(
    qapp, monkeypatch
):
    fake_families = ["Test Sans", "Test Sans Thin"]
    monkeypatch.setattr(
        QFontDatabase, "families", staticmethod(lambda *a, **k: list(fake_families))
    )

    def raise_missing(*a, **k):
        raise FileNotFoundError("fc-list not found")

    monkeypatch.setattr(subprocess, "run", raise_missing)

    canonical = ConfigDialog._compute_canonical_font_families()

    assert canonical == set(fake_families)
