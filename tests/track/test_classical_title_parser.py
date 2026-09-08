"""Unit tests for the classical-title parser
(docs/specs/classical_metadata_from_title.md). Each test maps 1:1 to a
numbered acceptance criterion in that spec (ACs 1-9).
"""

from src.track.classical_title_parser import parse_classical_title


# AC1 ---------------------------------------------------------------------------
def test_symphony_with_catalogue_and_roman_movement():
    r = parse_classical_title("Symphony No. 5 in C minor, Op. 67: I. Allegro con brio")
    assert r.matched is True
    assert r.work_type == "Symphony"
    assert r.work_name == "No. 5"
    assert r.classical_catalog_prefix == "Op."
    assert r.classical_catalog_number == 67
    assert r.movement_number == 1
    assert r.classical_tempo == "Allegro con brio"
    assert r.movement_name == "Allegro con brio"
    assert r.cleaned_title == "Allegro con brio"


# AC2 ---------------------------------------------------------------------------
def test_moonlight_sonata_trailing_opus_number_is_not_a_movement():
    r = parse_classical_title(
        'Piano Sonata No. 14 in C-sharp minor, Op. 27 No. 2 "Moonlight": I. Adagio sostenuto'
    )
    assert r.work_type == "Piano Sonata"
    assert r.work_name == 'No. 14 "Moonlight"'
    assert r.classical_catalog_prefix == "Op."
    assert r.classical_catalog_number == 27
    assert r.movement_number == 1
    assert r.classical_tempo == "Adagio sostenuto"
    assert r.movement_name == "Adagio sostenuto"
    assert r.cleaned_title == "Adagio sostenuto"


# AC3 ---------------------------------------------------------------------------
def test_bach_cello_suite_bwv_and_non_tempo_movement_name():
    r = parse_classical_title("Cello Suite No. 1 in G major, BWV 1007: I. Prélude")
    assert r.classical_catalog_prefix == "BWV"
    assert r.classical_catalog_number == 1007
    assert r.work_type == "Cello Suite"
    assert r.work_name == "No. 1"
    assert r.movement_number == 1
    assert r.movement_name == "Prélude"
    assert r.classical_tempo is None
    assert r.cleaned_title == "Prélude"


# AC4 ---------------------------------------------------------------------------
def test_arabic_movement_number():
    r = parse_classical_title("Violin Concerto in D major, Op. 77: 2. Adagio")
    assert r.movement_number == 2
    assert r.classical_tempo == "Adagio"
    assert r.movement_name == "Adagio"
    assert r.cleaned_title == "Adagio"


# AC5 ---------------------------------------------------------------------------
def test_standalone_piece_no_movement_segment():
    r = parse_classical_title("Nocturne in E-flat major, Op. 9 No. 2")
    assert r.matched is True
    assert r.work_type == "Nocturne"
    assert r.classical_catalog_prefix == "Op."
    assert r.classical_catalog_number == 9
    assert r.work_name == "No. 2"
    assert r.movement_number is None
    assert r.movement_name is None
    assert r.cleaned_title == "Nocturne No. 2"


# AC6 ---------------------------------------------------------------------------
def test_secondary_catalogue_is_stripped_but_not_stored():
    r = parse_classical_title(
        'The Four Seasons, Violin Concerto in E major, Op. 8 No. 1, RV 269 "Spring": I. Allegro'
    )
    assert r.classical_catalog_prefix == "Op."
    assert r.classical_catalog_number == 8
    assert r.movement_number == 1
    assert r.classical_tempo == "Allegro"
    assert r.cleaned_title == "Allegro"
    assert "RV 269" not in (r.work_name or "")
    assert "RV" not in r.cleaned_title


# AC7 ---------------------------------------------------------------------------
def test_non_classical_titles_are_left_alone():
    for title in ("Bohemian Rhapsody", "Track 07"):
        r = parse_classical_title(title)
        assert r.matched is False
        assert r.cleaned_title == title
        assert r.to_field_dict() == {}


# AC8 ---------------------------------------------------------------------------
def test_roman_numeral_conversion_and_malformed_marker():
    cases = {"III. Adagio": 3, "IV. Presto": 4, "IX. Finale": 9, "XII. Coda": 12, "V. Rondo": 5}
    for movement, expected in cases.items():
        r = parse_classical_title(f"Symphony No. 1, Op. 1: {movement}")
        assert r.movement_number == expected, movement

    bad = parse_classical_title("Symphony No. 1, Op. 1: IIII. Something")
    assert bad.movement_number is None
    assert "IIII" in (bad.movement_name or "")


# Hardening regressions (found against real library data) -------------------- -
def test_inline_catalogue_colon_is_not_the_movement_divider():
    # "Hob. XVI:32" -- the colon inside the catalogue must not split the title,
    # and "32" must never be read as a movement number.
    r = parse_classical_title("Piano Sonata in B Minor, Hob. XVI:32: II. Menuet")
    assert r.movement_number == 2
    assert r.classical_catalog_prefix == "Hob."
    assert r.classical_catalog_number == 32
    assert r.movement_name == "Menuet"
    assert r.cleaned_title == "Menuet"


def test_leading_composer_prefix_is_dropped():
    r = parse_classical_title("Corelli: Violin Sonata No. 3 in C Major, Op. 5: IV. Allegro")
    assert r.work_type == "Violin Sonata"
    assert r.classical_catalog_prefix == "Op."
    assert r.classical_catalog_number == 5
    assert r.movement_number == 4
    assert r.cleaned_title == "Allegro"


def test_tempo_is_not_stored_ending_on_a_connective_word():
    r = parse_classical_title("String Quintet No. 6 in E Major, G. 282: II. Allegro con spirito")
    assert r.classical_tempo == "Allegro"  # not "Allegro con"
    assert r.movement_name == "Allegro con spirito"  # full name kept
    assert r.movement_number == 2


def test_implausible_movement_number_is_rejected():
    r = parse_classical_title("Sonata, Op. 1: 41. Not A Real Movement")
    assert r.movement_number is None


# AC9 ---------------------------------------------------------------------------
def test_whitespace_and_trailing_punctuation_are_normalised():
    messy = parse_classical_title("  Symphony No. 5 in C minor, Op. 67:  I. Allegro con brio. ")
    clean = parse_classical_title("Symphony No. 5 in C minor, Op. 67: I. Allegro con brio")
    for field in (
        "work_name",
        "work_type",
        "classical_catalog_prefix",
        "classical_catalog_number",
        "classical_tempo",
        "movement_name",
        "movement_number",
        "cleaned_title",
    ):
        assert getattr(messy, field) == getattr(clean, field), field
