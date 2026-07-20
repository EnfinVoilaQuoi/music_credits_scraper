"""Utilitaires de théorie musicale

Format CANONIQUE de `musical_key` (affichage « Tonalité ») : français, avec
composite enharmonique pour les touches noires — ex. "Do majeur", "Do#/Réb mineur".
Toute écriture de `musical_key` doit passer par `key_mode_to_french` (ints)
ou `key_mode_to_french_from_string` (strings), qui convergent vers ce format.
`normalize_musical_key` re-normalise une chaîne existante (US/FR/Unicode/mixte).
"""

# ── Parsing robuste note → pitch class (US, FR, Unicode ♯/♭, composites X/Y) ──

_NOTE_TO_PC = {
    # Notation anglaise
    "C": 0,
    "C#": 1,
    "DB": 1,
    "D": 2,
    "D#": 3,
    "EB": 3,
    "E": 4,
    "F": 5,
    "F#": 6,
    "GB": 6,
    "G": 7,
    "G#": 8,
    "AB": 8,
    "A": 9,
    "A#": 10,
    "BB": 10,
    "B": 11,
    # Notation française (accents retirés au préalable : Ré→RE)
    "DO": 0,
    "DO#": 1,
    "REB": 1,
    "RE": 2,
    "RE#": 3,
    "MIB": 3,
    "MI": 4,
    "FA": 5,
    "FA#": 6,
    "SOLB": 6,
    "SOL": 7,
    "SOL#": 8,
    "LAB": 8,
    "LA": 9,
    "LA#": 10,
    "SIB": 10,
    "SI": 11,
}

_MODE_WORDS = {
    "major": 1,
    "majeur": 1,
    "maj": 1,
    "1": 1,
    "minor": 0,
    "mineur": 0,
    "min": 0,
    "0": 0,
}


def _clean_note_token(s: str) -> str:
    """Normalise un token de note : Unicode ♯/♭, accents français, casse."""
    s = str(s).strip().replace("♯", "#").replace("♭", "b")
    for accented, plain in (("é", "e"), ("è", "e"), ("ê", "e"), ("É", "E"), ("È", "E")):
        s = s.replace(accented, plain)
    return s.upper()


def note_to_pitch_class(note) -> int | None:
    """
    Note (str US/FR, composite "C#/Db"/"Do#/Réb", Unicode "G♯/A♭", int 0-11
    ou chaîne "7") → pitch class (0=C/Do … 11=B/Si), ou None si non reconnue.
    """
    if note is None:
        return None
    if isinstance(note, (int, float)):
        pc = int(note)
        return pc if 0 <= pc <= 11 else None
    token = _clean_note_token(note)
    if not token:
        return None
    if token.isdigit():  # entier sous forme de chaîne ("7")
        pc = int(token)
        return pc if 0 <= pc <= 11 else None
    if "/" in token:  # composite enharmonique "X/Y" : la 1re partie suffit
        token = token.split("/")[0].strip()
    # Suffixe mineur collé ("EM" = E minor) : retiré seulement si le reste est valide
    if token not in _NOTE_TO_PC and token.endswith("M") and token[:-1] in _NOTE_TO_PC:
        token = token[:-1]
    return _NOTE_TO_PC.get(token)


def parse_mode(mode) -> int | None:
    """Mode ('major'/'minor'/'majeur'/'mineur'/'0'/'1', int) → 1=majeur, 0=mineur, None sinon."""
    if mode is None:
        return None
    if isinstance(mode, (int, float)):
        m = int(mode)
        return m if m in (0, 1) else None
    return _MODE_WORDS.get(str(mode).strip().lower())


def musical_key_to_pitch_mode(musical_key: str) -> tuple[int, int] | None:
    """
    Décompose une tonalité FR ("Si mineur", "Do#/Réb majeur", "G♯/A♭ majeur",
    "A minor") en couple (pitch class 0-11, mode 0/1) — INVERSE de
    `key_mode_to_french`. None si la chaîne n'est pas interprétable.

    Note enharmonique composite : la 1re partie suffit ("Do#/Réb" → 1), le
    round-trip via `key_mode_to_french` réécrit la forme canonique complète.
    """
    if not musical_key or not isinstance(musical_key, str):
        return None
    tokens = musical_key.strip().split()
    if len(tokens) < 2:
        return None
    mode = parse_mode(tokens[-1])
    pc = note_to_pitch_class(" ".join(tokens[:-1]))
    if pc is None or mode is None:
        return None
    return pc, mode


def normalize_musical_key(musical_key: str) -> str | None:
    """
    Re-normalise une chaîne `musical_key` existante vers le format canonique
    français, quelle que soit sa notation d'origine :
      "G♯/A♭ majeur" → "Sol#/Lab majeur" ; "A minor" → "La mineur" ;
      "Do# majeur" → "Do#/Réb majeur" ; "Do majeur" → "Do majeur" (inchangé).
    Retourne None si la chaîne n'est pas interprétable (à laisser telle quelle).
    """
    parsed = musical_key_to_pitch_mode(musical_key)
    if parsed is None:
        return None
    return key_mode_to_french(*parsed)


def convert_key_to_numeric(key_str: str) -> int:
    """
    Convertit une notation de tonalité en pitch class numérique (0-11).
    Rétrocompatible : retourne 0 (Do) si non reconnue — préférer
    `note_to_pitch_class` qui retourne None dans ce cas.

    Args:
        key_str: Note (ex: "F#", "Bb", "C", "F#m", "G♯/A♭", "Sol#")

    Returns:
        Pitch class (0-11 où 0=C, 1=C#, 2=D, etc.)
    """
    pc = note_to_pitch_class(key_str)
    return pc if pc is not None else 0


def key_mode_to_french(key: int, mode: int) -> str:
    """
    Convertit key (0-11) + mode (0-1) en notation française CANONIQUE.

    Args:
        key: Pitch class (0=C, 1=C#, 2=D, etc.)
        mode: 0=mineur, 1=majeur

    Returns:
        Tonalité en français (ex: "Ré mineur", "Do#/Réb majeur")
    """
    notes_fr = {
        0: "Do",
        1: "Do#/Réb",
        2: "Ré",
        3: "Ré#/Mib",
        4: "Mi",
        5: "Fa",
        6: "Fa#/Solb",
        7: "Sol",
        8: "Sol#/Lab",
        9: "La",
        10: "La#/Sib",
        11: "Si",
    }

    mode_fr = "majeur" if mode == 1 else "mineur"
    note = notes_fr.get(key, "Inconnu")
    return f"{note} {mode_fr}"


def key_mode_to_french_from_string(key_str, mode_str) -> str | None:
    """
    Convertit key (string "F", "C#", "G♯/A♭", "Sol#", "7", ou int) + mode
    (string "major"/"minor"/"majeur"/"mineur"/"0"/"1", ou int) en notation
    française CANONIQUE, via le pitch class (converge avec key_mode_to_french :
    "C#" et "Db" donnent tous deux "Do#/Réb").

    Returns:
        Tonalité française canonique (ex: "Fa majeur", "Do#/Réb mineur"),
        ou None si key/mode non interprétables (ne pas polluer musical_key).
    """
    pc = note_to_pitch_class(key_str)
    mode = parse_mode(mode_str)
    if pc is None or mode is None:
        return None
    return key_mode_to_french(pc, mode)


def key_mode_to_english(key: int, mode: int) -> str:
    """Version anglaise pour référence"""
    notes_en = {
        0: "C",
        1: "C#/Db",
        2: "D",
        3: "D#/Eb",
        4: "E",
        5: "F",
        6: "F#/Gb",
        7: "G",
        8: "G#/Ab",
        9: "A",
        10: "A#/Bb",
        11: "B",
    }

    mode_en = "major" if mode == 1 else "minor"
    note = notes_en.get(key, "Unknown")
    return f"{note} {mode_en}"
