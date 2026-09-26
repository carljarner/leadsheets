"""Chord name parsing, transposition, and slug helpers -- shared by the
lead sheet builder (leadsheets.py) and, in principle, anything else that
needs to understand or shift a chord symbol.
"""

import re

ROOTS = ["A", "A#", "B", "C", "C#", "D", "D#", "E", "F", "F#", "G", "G#"]
# Black keys get both spellings (charts are handwritten in either); natural
# keys don't need a flat/sharp alias (no B#, no Fb).
FLAT_OF_SHARP = {"A#": "Bb", "C#": "Db", "D#": "Eb", "F#": "Gb", "G#": "Ab"}
SHARP_OF_FLAT = {flat: sharp for sharp, flat in FLAT_OF_SHARP.items()}

ROOT_SPELLINGS = []
for _note in ROOTS:
    ROOT_SPELLINGS.append(_note)
    if _note in FLAT_OF_SHARP:
        ROOT_SPELLINGS.append(FLAT_OF_SHARP[_note])

QUALITIES = ["", "m", "7", "m7", "m7b5", "maj7", "dim", "sus2", "sus4", "6"]

CHORD_LABELS = [f"{root}{quality}" for quality in QUALITIES for root in ROOT_SPELLINGS]

_QUALITY_PATTERN = "|".join(sorted((q for q in QUALITIES if q), key=len, reverse=True))
_ROOT_PATTERN = "|".join(sorted(ROOT_SPELLINGS, key=len, reverse=True))
CHORD_NAME_RE = re.compile(
    rf"^(?P<root>{_ROOT_PATTERN})(?P<quality>{_QUALITY_PATTERN})?(?:/(?P<bass>{_ROOT_PATTERN}))?$"
)


class ChordError(Exception):
    """Raised for chord parsing or transposition problems."""


def parse_chord(name: str) -> tuple[str, str, str | None]:
    match = CHORD_NAME_RE.match(name.strip())
    if not match:
        raise ChordError(f"Can't parse chord name '{name}'.")
    return match.group("root"), match.group("quality") or "", match.group("bass")


def _pitch_class(note: str) -> int:
    return ROOTS.index(SHARP_OF_FLAT.get(note, note))


def transpose_note(note: str, semitones: int) -> str:
    new_sharp = ROOTS[(_pitch_class(note) + semitones) % 12]
    was_flat = note in SHARP_OF_FLAT
    if was_flat and new_sharp in FLAT_OF_SHARP:
        return FLAT_OF_SHARP[new_sharp]
    return new_sharp


def transpose_chord_name(name: str, semitones: int) -> str:
    root, quality, bass = parse_chord(name)
    new_name = f"{transpose_note(root, semitones)}{quality}"
    if bass:
        new_name += f"/{transpose_note(bass, semitones)}"
    return new_name


def semitones_for_target_key(from_key: str, to_key: str) -> int:
    from_root, _, _ = parse_chord(from_key)
    to_root, _, _ = parse_chord(to_key)
    return (_pitch_class(to_root) - _pitch_class(from_root)) % 12


def slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.strip().lower()).strip("-")
    if not slug:
        raise ChordError("Title can't be empty.")
    return slug


# ── Song keys and singer transpositions ─────────────────────────────────
# A song's key is one of 24 fixed spellings, and each singer's transposition
# is a whole number of half-steps from it (-6..+6, 0 = original key). The
# same lists and spelling rules live in static/keys.js for the pages.

MAJOR_KEYS = ["C", "Db", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
MINOR_KEYS = ["Cm", "C#m", "Dm", "Ebm", "Em", "Fm", "F#m", "Gm", "G#m", "Am", "Bbm", "Bm"]
KEYS = MAJOR_KEYS + MINOR_KEYS
OFFSET_RANGE = range(-6, 7)

_SHARP_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
_FLAT_NAMES = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]
_FLAT_MAJOR_ROOTS = {1, 3, 5, 8, 10}  # Db Eb F Ab Bb


def prefers_flats(pc: int, minor: bool) -> bool:
    """Whether a key on pitch class `pc` (C = 0) is spelled with flats -- by
    its relative major, like the lead sheet editor's semitonePrefersFlats."""
    major = (pc + 3) % 12 if minor else pc
    return major in _FLAT_MAJOR_ROOTS or (minor and major == 6)


def parse_key(key: str) -> tuple[int, bool]:
    """(pitch class with C = 0, is_minor) of a key like "F#m" or "Bb"."""
    minor = key.endswith("m")
    root = key[:-1] if minor else key
    return (_pitch_class(root) - 3) % 12, minor  # ROOTS counts from A


def key_name(pc: int, minor: bool) -> str:
    """The fixed spelling of the key on pitch class `pc` (C = 0)."""
    return (MINOR_KEYS if minor else MAJOR_KEYS)[pc % 12]


_LOOSE_KEY_RE = re.compile(r"^\s*([A-Ha-h])([#b♯♭]?)\s*(m(?!aj)|min|-)?", re.IGNORECASE)


def read_key(text: str) -> tuple[int, bool] | None:
    """(pitch class with C = 0, is_minor) of a free-text key -- a lead
    sheet's "F# minor", or an old repertoire "Eb/Cm" (the first key counts)
    or "Hm" (German H = B). None when there's no key to read."""
    match = _LOOSE_KEY_RE.match(str(text or "").split("/")[0])
    if not match:
        return None
    letter = match.group(1).upper().replace("H", "B")
    acc = {"♯": "#", "♭": "b"}.get(match.group(2), match.group(2))
    try:
        pc = (_pitch_class(letter + acc) - 3) % 12  # ROOTS counts from A
    except ValueError:  # no such note, e.g. Cb
        return None
    return pc, bool(match.group(3))


def major_pc(pc: int, minor: bool) -> int:
    """The pitch class of a key's relative major (Am -> C), so keys that
    share a signature compare equal."""
    return (pc + 3) % 12 if minor else pc


def clean_key(value) -> str:
    """"" or one of KEYS; raises ValueError for anything else."""
    key = str(value or "").strip()
    if key and key not in KEYS:
        raise ValueError(f"Unknown key '{key}'.")
    return key


def clean_offset(value) -> int | None:
    """None (not set) or a whole number of half-steps in OFFSET_RANGE."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        offset = int(str(value).strip())
    except ValueError:
        raise ValueError(f"Transposition must be a whole number of half-steps, not '{value}'.")
    if offset not in OFFSET_RANGE:
        raise ValueError(f"Transposition must be between -6 and +6 half-steps, not {offset}.")
    return offset


def transpose_key(key: str, semitones: int) -> str:
    """The key `semitones` half-steps from `key` ("" stays "")."""
    if not key:
        return ""
    pc, minor = parse_key(key)
    return key_name(pc + semitones, minor)


def offset_label(offset, key: str = "") -> str:
    """How a transposition reads in a table. Stored in half-steps but shown
    in whole tones, the way the singers say it: +1 reads "+0.5 (C#m)", -2
    "-1 (Eb)", 0 "Original (F)"; no parenthesis when the key isn't known."""
    if offset is None or offset == "":
        return ""
    offset = int(offset)
    tones = offset / 2
    text = "Original" if offset == 0 else f"{tones:+g}"
    return f"{text} ({transpose_key(key, offset)})" if key else text
