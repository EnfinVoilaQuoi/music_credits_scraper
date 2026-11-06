"""Textes abîmés par un encodage, tels que Genius les sert (PUR).

Des octets de Mac Roman ou de Windows-1252 lus comme du Latin-1 arrivent sous la
forme de caractères de contrôle C1 (U+0080 à U+009F), qu'aucun texte n'emploie
exprès. Mesuré le 2026-11-06 sur la base :

- dans un TITRE, du Mac Roman : « Le myst\\x8fre feminin », « Arr\\x90te-moi » ;
- dans des PAROLES (28 fiches), du Windows-1252 : « l\\x92héro », « s\\x9cur »,
  « C'est\\x85 », « \\x93la gaine\\x94 », souvent à côté du caractère qu'il
  remplaçait (« c\\x9coeur », « L\\x92'aube », « \\x93"Ich bin »).

Un même octet ne se lit donc pas pareil partout : `\\x92` est l'apostrophe dans
« l\\x92héro », `\\x8f` n'existe pas en Windows-1252 et n'a de sens qu'à
l'intérieur d'un mot, en Mac Roman.
"""

import re

_C1 = re.compile("[\x80-\x9f]")

#: Un caractère réparé dans le texte, avant la lecture caractère par caractère.
_SEQUENCES = (
    # Trois octets UTF-8 lus de travers, ou leur reste (« haâ€\x9d », « s'\x80\x99exprime »).
    (re.compile("â€\x9d"), "”"),
    (re.compile("'?\x80\x99"), "’"),
    # À côté de ce qu'il remplaçait : un seul caractère à garder.
    (re.compile("oe\x9c|\x9coe"), "œ"),
    (re.compile("[\x80\x91\x92](?=')|(?<=')[\x91\x92]"), ""),
    (re.compile('[\x93\x94](?=")|(?<=")[\x93\x94]'), ""),
    (re.compile("\x85(?=\\.)|(?<=\\.)\x85"), ""),
)

#: Dans un mot, ces octets gardent leur sens Windows-1252 (apostrophes, œ, Œ) ;
#: les autres sont une lettre accentuée en Mac Roman (« Ž » n'est pas français).
_WINDOWS_DANS_UN_MOT = frozenset("\x91\x92\x8c\x9c")
#: Ces octets valent Š, š, Ž, ž, Ÿ en Windows-1252 : jamais dans ces titres, où
#: qu'ils soient (« caf\x8e » finit le mot : c'est « café »).
_LETTRES_ETRANGERES = frozenset("\x8a\x9a\x8e\x9e\x9f")


def _windows_1252(ch: str) -> str | None:
    try:
        return ch.encode("latin-1").decode("cp1252")
    except UnicodeDecodeError:
        return None


def _lire(m: re.Match) -> str:
    ch, texte = m.group(), m.string
    dans_un_mot = (
        m.start() > 0
        and texte[m.start() - 1].isalpha()
        and m.end() < len(texte)
        and texte[m.end()].isalpha()
    )
    if ch in _LETTRES_ETRANGERES or (dans_un_mot and ch not in _WINDOWS_DANS_UN_MOT):
        return ch.encode("latin-1").decode("mac_roman")
    # Hors d'un mot, un octet que Windows-1252 ne définit pas ne dit rien.
    return _windows_1252(ch) or ""


def reparer_c1(texte: str) -> str:
    """PUR. Le texte sans caractère de contrôle C1, chacun lu pour ce qu'il était
    (voir le module). Un texte qui n'en a pas revient tel quel."""
    if not texte or not _C1.search(texte):
        return texte
    for motif, remplacement in _SEQUENCES:
        texte = motif.sub(remplacement, texte)
    return _C1.sub(_lire, texte)
