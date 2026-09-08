"""Best-effort parser that pulls classical metadata out of a track title.

Used by the Classical tab of the track edit dialog -- see
docs/specs/classical_metadata_from_title.md. Pure: no Qt, no DB. The caller
decides what to do with the result (fill blank fields, rewrite the title).

The grammar recognised is the common
    <work> [in <key> <mode>][, <catalogue>][ "<nickname>"] : <movement>
shape, e.g. ``Symphony No. 5 in C minor, Op. 67: I. Allegro con brio``.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

# ---------------------------------------------------------------------------
# Keyword tables
# ---------------------------------------------------------------------------

# Longest phrases first so the anchored work-type match is greedy.
_WORK_TYPES: tuple[str, ...] = (
    "piano sonata",
    "violin sonata",
    "cello sonata",
    "flute sonata",
    "piano concerto",
    "violin concerto",
    "cello concerto",
    "flute concerto",
    "horn concerto",
    "oboe concerto",
    "clarinet concerto",
    "double concerto",
    "triple concerto",
    "concerto grosso",
    "string quartet",
    "string quintet",
    "string trio",
    "piano quintet",
    "piano quartet",
    "piano trio",
    "wind quintet",
    "brass quintet",
    "clarinet quintet",
    "cello suite",
    "orchestral suite",
    "keyboard suite",
    "symphonic poem",
    "tone poem",
    "moment musical",
    "te deum",
    "stabat mater",
    "symphony",
    "sinfonia",
    "sonata",
    "sonatina",
    "concerto",
    "quartet",
    "quintet",
    "sextet",
    "septet",
    "octet",
    "nonet",
    "trio",
    "suite",
    "partita",
    "overture",
    "prelude",
    "prélude",
    "fugue",
    "toccata",
    "fantasia",
    "fantasy",
    "nocturne",
    "étude",
    "etude",
    "study",
    "waltz",
    "valse",
    "mazurka",
    "polonaise",
    "ballade",
    "scherzo",
    "impromptu",
    "rhapsody",
    "caprice",
    "capriccio",
    "bagatelle",
    "romance",
    "intermezzo",
    "elegy",
    "berceuse",
    "barcarolle",
    "arabesque",
    "humoresque",
    "serenade",
    "divertimento",
    "cassation",
    "variations",
    "mass",
    "requiem",
    "cantata",
    "oratorio",
    "motet",
    "magnificat",
    "passion",
    "anthem",
    "gymnopédie",
    "gymnopedie",
    "gnossienne",
    "lied",
    "aria",
    "chorale",
    "canzona",
    "ricercar",
    "passacaglia",
    "chaconne",
    "minuet",
    "menuet",
    "gavotte",
    "sarabande",
    "gigue",
    "allemande",
    "courante",
    "bourrée",
    "bourree",
)
_WORK_TYPE_CANON: dict[str, str] = {t: t.title() for t in _WORK_TYPES}
_WORK_TYPE_RE = re.compile(
    r"^\s*(" + "|".join(re.escape(t) for t in _WORK_TYPES) + r")\b", re.IGNORECASE
)

# Catalogue tokens -> canonical stored prefix. Keyed by token lowercased with
# any trailing dot removed.
_CATALOG_CANON: dict[str, str] = {
    "op": "Op.",
    "opus": "Op.",
    "k": "K.",
    "kv": "K.",
    "bwv": "BWV",
    "buxwv": "BuxWV",
    "hob": "Hob.",
    "woo": "WoO",
    "wq": "Wq.",
    "hwv": "HWV",
    "rv": "RV",
    "trv": "TrV",
    "fp": "FP",
    "sz": "Sz.",
    "d": "D.",
    "h": "H.",
    "s": "S.",
    "l": "L.",
    "p": "P.",
    "anh": "Anh.",
    "b": "B.",
}
_CATALOG_TOKEN = (
    r"Op\.?|Opus|KV|K\.?|BWV|BuxWV|Hob\.?|WoO|Wq\.?|HWV|RV|TrV|FP|Sz\.?|"
    r"D\.?|H\.?|S\.?|L\.?|P\.?|Anh\.?|B\.?"
)
_CATALOG_RE = re.compile(
    r"(?<![A-Za-z])(" + _CATALOG_TOKEN + r")\s*"
    r"(?:[IVXLC]+[a-z]?\s*[:/]\s*)?"  # optional Hoboken-style roman group
    r"(\d+)[a-z]?"  # the catalogue number itself
    r"(\s*(?:No\.?|Nr\.?)\s*\d+)?",  # optional work-number-within-opus
    re.IGNORECASE,
)

_KEY_CLAUSE_RE = re.compile(
    r"\bin\s+[A-G](?:[-\s]?(?:sharp|flat|#|♯|♭|is|es|major|minor))*"
    r"\s+(?:major|minor)\b",
    re.IGNORECASE,
)

# Real punctuation characters, kept as normal strings so the \u escapes
# resolve (a raw regex literal would treat "\u2013" as six literal chars).
_EN_DASH = "\u2013"
_EM_DASH = "\u2014"
_CLOSE_QUOTES = "\"'\u201c\u201d\u2018\u2019"
_OPEN_QUOTES = "\"'\u201c\u2018"
_ALL_QUOTES = _CLOSE_QUOTES + "\u201e"

_MOVEMENT_MARKER_RE = re.compile(
    r"^\s*(?:(?:No\.?|Nr\.?|Movement|Mov\.?|Satz)\s*)?"
    r"([IVXLCDM]+|\d{1,2})"
    r"\s*[.):\-" + _EN_DASH + r"]\s+",
    re.IGNORECASE,
)
_ROMAN_RE = re.compile(r"^M{0,3}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})$", re.IGNORECASE)
_ROMAN_VALUES: tuple[tuple[str, int], ...] = (
    ("M", 1000),
    ("CM", 900),
    ("D", 500),
    ("CD", 400),
    ("C", 100),
    ("XC", 90),
    ("L", 50),
    ("XL", 40),
    ("X", 10),
    ("IX", 9),
    ("V", 5),
    ("IV", 4),
    ("I", 1),
)

_TEMPO_WORDS: frozenset[str] = frozenset(
    {
        # base markings
        "grave",
        "largo",
        "larghetto",
        "larghissimo",
        "lento",
        "adagio",
        "adagietto",
        "andante",
        "andantino",
        "moderato",
        "allegretto",
        "allegro",
        "vivace",
        "vivo",
        "vivacissimo",
        "presto",
        "prestissimo",
        "tempo",
        "marcia",
        "marziale",
        # common modifiers
        "assai",
        "molto",
        "con",
        "brio",
        "moto",
        "fuoco",
        "ma",
        "non",
        "troppo",
        "poco",
        "piu",
        "più",
        "meno",
        "mosso",
        "cantabile",
        "espressivo",
        "agitato",
        "maestoso",
        "grazioso",
        "scherzando",
        "tranquillo",
        "energico",
        "appassionato",
        "giocoso",
        "affettuoso",
        "semplice",
        "risoluto",
        "marcato",
        "animato",
        "comodo",
        "spiritoso",
        "dolce",
        "funebre",
        "alla",
        "un",
        "e",
        "quasi",
        "ben",
        "sempre",
        "sostenuto",
        "amoroso",
        "brillante",
        "capriccioso",
        "deciso",
        "delicato",
        "furioso",
        "gioioso",
        "grandioso",
        "lacrimoso",
        "leggiero",
        "lugubre",
        "misterioso",
        "nobilmente",
        "pesante",
        "rubato",
        "scherzoso",
        "sotto",
        "voce",
        "teneramente",
    }
)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

_DB_FIELDS: tuple[str, ...] = (
    "work_name",
    "work_type",
    "classical_catalog_prefix",
    "classical_catalog_number",
    "classical_tempo",
    "movement_name",
    "movement_number",
)


@dataclass
class ClassicalTitleParse:
    """Outcome of :func:`parse_classical_title`.

    ``matched`` is True iff at least one structured field was extracted;
    ``cleaned_title`` always holds the proposed replacement track title
    (equal to the untouched input when nothing matched).
    """

    work_name: str | None = None
    work_type: str | None = None
    classical_catalog_prefix: str | None = None
    classical_catalog_number: int | None = None
    classical_tempo: str | None = None
    movement_name: str | None = None
    movement_number: int | None = None
    cleaned_title: str = ""
    matched: bool = False

    def to_field_dict(self) -> dict[str, object]:
        """Non-None classical column values -- feed straight to
        ``FieldFormTab.set_if_empty``. ``is_classical`` is the caller's
        responsibility (there is no title fragment for it)."""
        return {name: getattr(self, name) for name in _DB_FIELDS if getattr(self, name) is not None}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _roman_to_int(text: str) -> int | None:
    """Convert a Roman numeral to int, or None if it isn't a well-formed one
    (e.g. ``IIII``)."""
    if not _ROMAN_RE.match(text):
        return None
    upper = text.upper()
    total = 0
    for numeral, value in _ROMAN_VALUES:
        while upper.startswith(numeral):
            total += value
            upper = upper[len(numeral) :]
    return total or None


def _tidy(text: str) -> str:
    """Collapse whitespace and strip stray separators left behind after
    clauses are cut out of a title fragment."""
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r",\s*,", ",", text)  # ", ," -> ","
    text = re.sub(r"\s+,", ",", text)  # " ," -> ","
    text = re.sub(r",\s*$", "", text)  # trailing comma
    text = re.sub(r",\s*(?=[" + _CLOSE_QUOTES + r")\]])", " ", text)  # comma before a closer
    text = re.sub(r"(?<=[" + _OPEN_QUOTES + r"(\[]),\s*", "", text)  # comma right after an opener
    text = re.sub(r",(?=\S)", ", ", text)  # one space after a comma
    text = re.sub(r"\(\s*\)", "", text)  # empty parens
    text = re.sub(r"\s+", " ", text)
    return text.strip(" \t,;:.-" + _EN_DASH + _EM_DASH)


_COMPOSER_PREFIX_RE = re.compile(r"^[A-Z][A-Za-z.'-]+:\s+")


def _split_work_movement(title: str) -> tuple[str, str | None]:
    """Split into ``(work_segment, movement_segment_or_None)``.

    A leading ``Composer:`` / ``Performer:`` prefix is dropped first. The
    divider is the first colon that (a) sits outside a quoted nickname and
    (b) has whitespace on at least one side -- so an inline catalogue colon
    (``Hob. XVI:32``) is never mistaken for the work/movement divider.
    """
    prefix = _COMPOSER_PREFIX_RE.match(title)
    if prefix and ":" in title[prefix.end() :]:
        title = title[prefix.end() :]

    quote_chars = _ALL_QUOTES
    open_quote = False
    for i, ch in enumerate(title):
        if ch in quote_chars:
            open_quote = not open_quote
            continue
        if ch == ":" and not open_quote:
            before_ws = i > 0 and title[i - 1].isspace()
            after_ws = i + 1 < len(title) and title[i + 1].isspace()
            if before_ws or after_ws:
                return title[:i].strip(), title[i + 1 :].strip()
    return title.strip(), None


def _parse_work(segment: str) -> tuple[dict[str, object], bool]:
    """Pull work_type / work_name / catalogue out of the work segment."""
    out: dict[str, object] = {}
    text = segment

    key_hit = bool(_KEY_CLAUSE_RE.search(text))
    text = _KEY_CLAUSE_RE.sub(" ", text)

    catalog_hit = False
    matches = list(_CATALOG_RE.finditer(text))
    if matches:
        first = matches[0]
        token = first.group(1).rstrip(".").lower()
        canon = _CATALOG_CANON.get(token)
        if canon:
            catalog_hit = True
            out["classical_catalog_prefix"] = canon
            out["classical_catalog_number"] = int(first.group(2))
        # Cut the primary catalogue two ways: one that also drops the
        # trailing "No. n" sub-number, one that keeps it. The sub-number is
        # only worth keeping when it's all that would be left of the name.
        sub = first.group(3) or ""  # e.g. " No. 2" (may be empty)
        text_no_sub = text[: first.start()] + text[first.end() :]
        text_with_sub = text[: first.start()] + sub + " " + text[first.end() :]
        # Secondary catalogues (", RV 269") -> strip entirely from both.
        for extra in matches[1:]:
            text_no_sub = text_no_sub.replace(extra.group(0), " ", 1)
            text_with_sub = text_with_sub.replace(extra.group(0), " ", 1)
    else:
        text_no_sub = text_with_sub = text

    type_hit = False

    def _extract_type(chunk: str) -> str:
        nonlocal type_hit
        m = _WORK_TYPE_RE.match(chunk)
        if not m:
            return chunk
        type_hit = True
        out["work_type"] = _WORK_TYPE_CANON[m.group(1).lower()]
        return chunk[m.end() :]

    name_no_sub = _tidy(_extract_type(text_no_sub))
    name_with_sub = _tidy(_extract_type(text_with_sub))

    work_name = name_no_sub or name_with_sub
    if work_name:
        out["work_name"] = work_name

    return out, (key_hit or catalog_hit or type_hit)


_TEMPO_CONNECTORS: frozenset[str] = frozenset(
    {
        "con",
        "e",
        "ed",
        "ma",
        "non",
        "un",
        "una",
        "alla",
        "poco",
        "molto",
        "assai",
        "piu",
        "più",
        "meno",
        "sotto",
        "quasi",
        "ben",
        "il",
        "la",
        "di",
        "del",
        "in",
        "sempre",
        "troppo",
        "d",
    }
)

_MAX_MOVEMENT_NUMBER = 30  # real movements don't run higher; a bigger number
#                            is almost always a catalogue digit misread


def _leading_tempo_run(tokens: list[str]) -> int:
    """Number of leading tokens that are all Italian tempo terms."""
    count = 0
    for tok in tokens:
        bare = tok.strip(",.;:()").lower()
        if bare and bare in _TEMPO_WORDS:
            count += 1
        else:
            break
    return count


def _trim_tempo(tempo: str) -> str:
    """Drop trailing connective words ("Allegro con" -> "Allegro") so the
    stored tempo marking doesn't end mid-phrase."""
    parts = tempo.split()
    while parts and parts[-1].strip(",.;:").lower() in _TEMPO_CONNECTORS:
        parts.pop()
    return " ".join(parts)


def _parse_movement(segment: str) -> tuple[dict[str, object], bool]:
    """Pull movement_number / classical_tempo / movement_name out of the
    movement segment (the part after the work/movement colon)."""
    out: dict[str, object] = {}
    text = segment.strip()

    marker = _MOVEMENT_MARKER_RE.match(text)
    number_hit = False
    if marker:
        token = marker.group(1)
        value = int(token) if token.isdigit() else _roman_to_int(token)
        if value and value <= _MAX_MOVEMENT_NUMBER:
            out["movement_number"] = value
            number_hit = True
            text = text[marker.end() :]

    text = _tidy(text)

    # Trailing ": <tempo>" (e.g. "Menuetto: Allegretto") -> tempo, and the
    # part before the colon is the real movement name.
    trailing_tempo = None
    if ":" in text:
        head, _, tail = text.rpartition(":")
        tail_tokens = tail.split()
        if tail_tokens and _leading_tempo_run(tail_tokens) == len(tail_tokens):
            trailing_tempo = _tidy(tail)
            text = _tidy(head)

    tokens = text.split()
    lead = _leading_tempo_run(tokens)
    tempo = _tidy(" ".join(tokens[:lead])) if lead else None
    tempo = trailing_tempo or tempo
    if tempo:
        tempo = _trim_tempo(tempo)
    if tempo:
        out["classical_tempo"] = tempo

    movement_name = _tidy(text)
    if movement_name:
        out["movement_name"] = movement_name

    return out, (number_hit or bool(tempo))


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def parse_classical_title(title: str) -> ClassicalTitleParse:
    """Best-effort parse of ``title`` into classical metadata plus a
    stripped-down replacement title (:attr:`ClassicalTitleParse.cleaned_title`).

    Nothing is extracted unless a real structural signal is present -- a
    catalogue token, a work-type keyword at the start, an ``in <key> <mode>``
    clause, or a movement marker/tempo after a colon. Titles with none of
    these come back with ``matched=False`` and the input unchanged.
    """
    result = ClassicalTitleParse(cleaned_title=title)
    if not title or not title.strip():
        return result

    norm = re.sub(r"\s+", " ", title.strip())
    work_seg, move_seg = _split_work_movement(norm)

    work_fields, work_signal = _parse_work(work_seg)
    move_fields: dict[str, object] = {}
    move_signal = False
    if move_seg is not None:
        move_fields, move_signal = _parse_movement(move_seg)

    # An odd leading prefix ("<transcription> : Hungarian Dance ...") can push
    # the work-like content into the movement half. If neither half parsed,
    # try reading the movement half as a work before giving up.
    if not work_signal and not move_signal and move_seg is not None:
        alt_fields, alt_signal = _parse_work(move_seg)
        if alt_signal:
            work_fields, work_signal = alt_fields, alt_signal
            move_fields, move_seg = {}, None

    if not (work_signal or move_signal):
        return result  # nothing classical about this title

    for name, value in {**work_fields, **move_fields}.items():
        setattr(result, name, value)
    result.matched = any(getattr(result, n) is not None for n in _DB_FIELDS)
    if not result.matched:
        return result

    # Build the stripped title: the bare movement name when there is a
    # movement, otherwise the de-cluttered work name.
    work_title = _tidy(" ".join(p for p in (result.work_type, result.work_name) if p))
    if move_seg is not None:
        cleaned = result.movement_name or result.classical_tempo or work_title or norm
    else:
        cleaned = work_title or norm
    result.cleaned_title = cleaned
    return result
