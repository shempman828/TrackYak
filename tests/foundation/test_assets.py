"""Bundled assets: every icon loads in Qt, every referenced asset exists, and no icon is unused."""

from pathlib import Path
import re

import pytest

from src.foundation.asset_paths import ASSETS_DIR, BASE_DIR

SVG_FILES = sorted(ASSETS_DIR.glob("*.svg"))
# Asset names written as string literals in code ("play_button.svg") or QSS (ASSETS_DIR_PLACEHOLDER/checkmark.svg).
_ICON_NAME_RE = re.compile(r"""["'/]([A-Za-z0-9_\-]+\.(?:svg|png))\b""")


def _source_texts() -> list[str]:
    """Text of every Python file under src/ and every theme stylesheet."""
    files = [*(BASE_DIR / "src").rglob("*.py"), *(BASE_DIR / "themes").glob("*.qss")]
    return [f.read_text(encoding="utf-8") for f in files]


@pytest.mark.parametrize("svg", SVG_FILES, ids=lambda p: p.name)
def test_svg_loads_in_qt(qapp, svg: Path):
    from PySide6.QtSvg import QSvgRenderer

    renderer = QSvgRenderer(str(svg))
    assert renderer.isValid()
    size = renderer.viewBox().size()
    assert size.width() == size.height(), "a non-square viewBox renders smaller than its sibling icons"


def test_every_icon_is_referenced():
    text = "\n".join(_source_texts())
    unused = [svg.name for svg in SVG_FILES if svg.name not in text]
    assert unused == []


def test_every_referenced_asset_exists():
    bundled = {p.name for p in ASSETS_DIR.iterdir()}
    names = {m.group(1) for text in _source_texts() for m in _ICON_NAME_RE.finditer(text)}
    assert sorted(names - bundled) == []
