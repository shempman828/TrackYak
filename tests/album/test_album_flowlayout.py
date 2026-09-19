"""Regression test: negative spacing used to be accepted unvalidated, which
overlaps items instead of spacing them. h_spacing/v_spacing now clamp to 0."""

from src.album.album_flowlayout import FlowLayout


def test_negative_spacing_clamps_to_zero(qapp):
    layout = FlowLayout()

    layout.h_spacing = -10
    assert layout.h_spacing == 0

    layout.v_spacing = -5
    assert layout.v_spacing == 0

    layout.h_spacing = 8
    assert layout.h_spacing == 8
