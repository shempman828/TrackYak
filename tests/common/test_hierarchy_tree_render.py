"""Tests for docs/specs/genre_hierarchy_export.md's
render_hierarchy_as_text (src/common/hierarchy_tree_style.py) -- the
generic box-drawing renderer behind the genre tree's "Export Hierarchy..."
context-menu action. Each test maps to one numbered acceptance criterion
in that spec.
"""

from dataclasses import dataclass

from src.common.hierarchy_tree_style import render_hierarchy_as_text


@dataclass
class _Node:
    id: int
    name: str
    parent_id: int | None = None


def _render(nodes, sort_key=None):
    return render_hierarchy_as_text(
        nodes, id_attr="id", name_attr="name", parent_attr="parent_id", sort_key=sort_key
    )


# ---------------------------------------------------------------------------
# AC1 -- a single root with no children renders as just its name
# ---------------------------------------------------------------------------


def test_single_root_no_children():
    nodes = [_Node(1, "Other")]
    assert _render(nodes) == "Other"


# ---------------------------------------------------------------------------
# AC2 -- feature-request sample data renders with correct connectors and
# indentation at every depth
# ---------------------------------------------------------------------------


def test_sample_data_matches_expected_box_drawing_format():
    nodes = [
        _Node(1, "Other"),
        _Node(2, "Singer & Songwriter", parent_id=1),
        _Node(3, "Spoken Word", parent_id=1),
        _Node(4, "Comedy", parent_id=1),
        _Node(5, "Children's", parent_id=1),
        _Node(6, "Soundtrack", parent_id=1),
        _Node(7, "Film Score", parent_id=6),
        _Node(8, "Musical", parent_id=6),
        _Node(9, "Avant-Garde", parent_id=1),
        _Node(10, "Experimental", parent_id=9),
        _Node(11, "Noise", parent_id=9),
        _Node(12, "Leftfield", parent_id=9),
    ]

    expected = "\n".join(
        [
            "Other",
            "├── Singer & Songwriter",
            "├── Spoken Word",
            "├── Comedy",
            "├── Children's",
            "├── Soundtrack",
            "│   ├── Film Score",
            "│   └── Musical",
            "└── Avant-Garde",
            "    ├── Experimental",
            "    ├── Noise",
            "    └── Leftfield",
        ]
    )

    assert _render(nodes) == expected


# ---------------------------------------------------------------------------
# AC3 -- multiple top-level roots each start their own unprefixed block, in
# sort_key order (or insertion order when sort_key is None)
# ---------------------------------------------------------------------------


def test_multiple_roots_each_start_unprefixed_block_in_given_order():
    nodes = [
        _Node(1, "Other"),
        _Node(2, "Rock"),
        _Node(3, "Child of Rock", parent_id=2),
    ]

    result = _render(nodes)
    assert result == "Other\nRock\n└── Child of Rock"


def test_multiple_roots_ordered_by_sort_key():
    nodes = [
        _Node(1, "Zydeco"),
        _Node(2, "Alpha"),
    ]

    result = _render(nodes, sort_key=lambda n: n.name.lower())
    assert result == "Alpha\nZydeco"


def test_empty_input_returns_empty_string():
    assert _render([]) == ""
