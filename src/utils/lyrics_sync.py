"""
Paroles synchronisées : cross-check des sources + alignement d'affichage.

Deux rôles :

1. **Cross-check des sources** (`compare_synced`) : croise le LRC LRCLIB (source 1) et
   le LRC YTM (source 2). Si les deux concordent → `confidence=2` (on garde LRCLIB).
   Sinon on **départage par la durée réelle** du morceau (`sync_error`) et on pose
   `confidence=1`. Une seule source → `confidence=1`. C'est le LRC retenu qui part en
   base (`track.lyrics.synced`) avec sa source/confidence.

2. **Découpage temporel** (`extract_sections`) : retrouve, pour chaque en-tête de
   section `[Couplet : artiste]`, l'intervalle `(start, end)` en secondes, en
   alignant sa 1ʳᵉ ligne sur le LRC. Le matching est **monotone** (recherche en
   avant uniquement) pour éviter qu'une ligne de refrain répétée ne se cale sur une
   occurrence antérieure → intervalles non croissants / incohérents.

3. **Alignement d'affichage** (`annotate_sections`) : habillage texte de (2) — annote
   chaque en-tête avec son intervalle `⏱ 0:12 → 0:45`. Les consommateurs qui veulent
   les temps (dataviz « Structure ») passent par `extract_sections`, pas par le texte.
"""

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

_LRC_RE = re.compile(r"\[(\d+):(\d+)(?:[.:](\d+))?\]\s*(.*)")


def parse_lrc(lrc: str) -> list[tuple[float, str]]:
    """Texte LRC → [(secondes, texte)]."""
    out = []
    for line in (lrc or "").splitlines():
        m = _LRC_RE.match(line.strip())
        if not m:
            continue
        mn, sc, cs, txt = m.groups()
        t = int(mn) * 60 + int(sc) + (int(cs) / 100 if cs else 0)
        out.append((t, txt.strip()))
    return out


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", " ", (s or "").lower())
    # collapse espaces fait dans l'appelant


def _norm2(s: str) -> str:
    return " ".join(_norm(s).split())


def _fmt(t: float) -> str:
    m, s = int(t) // 60, int(t) % 60
    return f"{m}:{s:02d}"


# Longueur normalisée minimale du début commun pour valoir alignement : en
# dessous, « j'te jure » ouvrirait n'importe quelle ligne.
_PREFIX_MIN = 15


def _is_prefix_match(a: str, b: str) -> bool:
    """Les deux lignes normalisées partagent-elles un DÉBUT assez long ?

    Les deux sources ne découpent pas les vers pareil et le match exact comme la
    similarité globale s'y cassent, alors que l'ancrage temporel est certain :
    - Genius agrège plusieurs lignes LRC en une (« …pas différents : moi aussi,
      j'en veux… » = 2 lignes LRC) → l'une est préfixe de l'autre ;
    - Genius double une ligne de hook (« chaque jour… chaque jour… ») là où le
      LRC la joint à la suivante → ni l'une ni l'autre n'est préfixe, mais le
      début commun est franc.
    Comparaison **par mots** (jamais au milieu d'un mot) pour éviter les
    faux positifs.
    """
    wa, wb = a.split(), b.split()
    shared: list[str] = []
    for x, y in zip(wa, wb, strict=False):
        if x != y:
            break
        shared.append(x)
    return len(" ".join(shared)) >= _PREFIX_MIN


def _is_header(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("[") and stripped.endswith("]")


@dataclass(frozen=True)
class LyricSection:
    """Une section de paroles datée : `[Couplet 1 : Isha]` → 12.0 s → 45.0 s.

    `label` = en-tête SANS les crochets. `header_index` = index de la ligne
    d'en-tête dans le texte structuré. `start`/`end` sont `None` quand la section
    n'a pas pu être alignée sur le LRC (aucune de ses 3 premières lignes retrouvée).

    `content_lines == 0` distingue le **non-alignable** de l'**échec d'alignement** :
    un en-tête de regroupement (« [Partie 2 : Risotto Gambas] » sur un 2-en-1) ou
    une « [Outro Instrumentale] » n'a aucune parole à ancrer — ce n'est pas un raté.

    `end` = borne d'AFFICHAGE (début de la section suivante) ; `sung_end` = dernière
    ligne réellement chantée. L'écart entre les deux est une **plage instrumentale**
    (solo, interlude non chanté) : c'est lui qui permet de la colorer à part.
    """

    label: str
    header_index: int
    start: float | None
    end: float | None
    content_lines: int = 0  # lignes de paroles sous l'en-tête (0 = rien à aligner)
    sung_end: float | None = None  # dernière ligne CHANTÉE (≠ `end` si plage instru)


def extract_sections(structured: str, lrc: str) -> list[LyricSection]:
    """Sections du texte structuré, datées par alignement sur le LRC.

    Renvoie `[]` si pas de LRC, pas de texte ou aucun en-tête `[...]`. L'`end` de
    la dernière section vaut le DERNIER TIMESTAMP du LRC (pas la durée du morceau :
    une coda instrumentale n'est pas couverte — c'est à l'appelant de l'étendre).

    Le matching est **monotone** : chaque section cherche après la dernière ligne
    alignée, ce qui empêche un refrain répété de se caler sur une occurrence
    antérieure (piège documenté dans CLAUDE.md, verrouillé par les tests).
    """
    lrc_lines = parse_lrc(lrc)
    if not lrc_lines or not structured:
        return []

    norm_lrc = [(_norm2(txt), t) for t, txt in lrc_lines if txt.strip()]
    n_lrc = len(norm_lrc)

    def find_from(text: str, start_idx: int, skip: int = 0):
        """
        Timestamp alignant `text` à une position >= start_idx (recherche EN AVANT).
        Trois passes, de la plus sûre à la plus permissive : match exact, **préfixe**
        (découpage différent des deux sources, cf. `_PREFIX_MIN`), puis similarité
        >= 0.82. Renvoie (time, index) ou None.
        La contrainte « en avant » impose la monotonie.

        `skip` = nombre d'occurrences à ignorer, quand la ligne cherchée figure
        aussi dans la section PRÉCÉDENTE (elle y a donc déjà été consommée).
        Borné au dernier candidat : un `skip` trop grand ne fait jamais échouer.
        """
        nt = _norm2(text)
        if len(nt) < 3:
            return None
        passes = (
            lambda other: other == nt,
            lambda other: _is_prefix_match(nt, other),
            lambda other: SequenceMatcher(None, nt, other).ratio() >= 0.82,
        )
        for matches in passes:
            hits = [i for i in range(start_idx, n_lrc) if matches(norm_lrc[i][0])]
            if hits:
                i = hits[min(skip, len(hits) - 1)]
                return norm_lrc[i][1], i
        return None

    lines = structured.splitlines()

    # Sections = (index en-tête, premières lignes de contenu)
    sections = []
    cur = None
    for idx, ln in enumerate(lines):
        if _is_header(ln):
            cur = {"idx": idx, "lines": []}
            sections.append(cur)
        elif cur is not None and ln.strip():
            cur["lines"].append(ln.strip())

    if not sections:
        return []

    # Start de chaque section = timestamp de sa 1ʳᵉ ligne alignable (parmi les 3 premières).
    starts: list[float | None] = []
    indexes: list[int | None] = []
    cursor = 0
    # Lignes de la section précédente NON encore consommées : si la section
    # courante s'ouvre sur l'une d'elles, la 1ʳᵉ occurrence rencontrée appartient
    # à la précédente, pas à celle-ci. « On peut t'éteindre… » clôt l'[Intro] ET
    # ouvre le [Couplet unique] de « 3ein / Risotto Gambas » — sans ce décompte
    # le couplet démarrait 7 s trop tôt, sur la ligne de l'intro.
    pending: list[str] = []
    for sec in sections:
        found, matched_pos = None, 0
        for pos, line in enumerate(sec["lines"][:3]):
            # Le décompte doit suivre la MÊME relation que le matching : la ligne
            # d'ouverture du couplet est souvent une ligne fusionnée par Genius,
            # elle ne s'égale pas à celle de la section précédente, elle la préfixe.
            nt = _norm2(line)
            skip = sum(1 for p in pending if p == nt or _is_prefix_match(nt, p))
            found = find_from(line, cursor, skip=skip)
            if found is not None:
                matched_pos = pos
                break
        if found is not None:
            starts.append(found[0])
            indexes.append(found[1])
            cursor = found[1] + 1  # la section suivante repart après ce point
            pending = [_norm2(line) for line in sec["lines"][matched_pos + 1 :]]
        else:
            starts.append(None)  # non alignée : on ne recule pas le curseur
            indexes.append(None)

    last_t = lrc_lines[-1][0]
    out: list[LyricSection] = []
    for i, sec in enumerate(sections):
        st = starts[i]
        # Fin = start de la 1ʳᵉ section alignée suivante, sinon dernier timestamp.
        nxt = next((j for j in range(i + 1, len(sections)) if starts[j] is not None), None)
        en = None if st is None else (starts[nxt] if nxt is not None else last_t)
        out.append(
            LyricSection(
                label=lines[sec["idx"]].strip().strip("[]").strip(),
                header_index=sec["idx"],
                start=st,
                end=en,
                content_lines=len(sec["lines"]),
                sung_end=_sung_end(norm_lrc, indexes[i], indexes[nxt] if nxt is not None else None),
            )
        )
    return out


def _sung_end(norm_lrc, idx: int | None, next_idx: int | None) -> float | None:
    """Timestamp de la DERNIÈRE ligne chantée d'une section.

    `end` est la borne d'affichage (début de la section suivante) ; entre les
    deux il peut y avoir une longue plage instrumentale (solo, interlude non
    chanté). `sung_end` donne le vrai bout du chant : la dernière ligne LRC
    située avant le début de la section suivante.
    """
    if idx is None:
        return None
    last = (len(norm_lrc) - 1) if next_idx is None else (next_idx - 1)
    return norm_lrc[max(idx, min(last, len(norm_lrc) - 1))][1]


def annotate_sections(structured: str, lrc: str) -> str:
    """
    Retourne les paroles structurées avec, sur chaque en-tête `[...]`, l'intervalle
    de temps inséré DANS les crochets (pour rester décoré par l'affichage) :
        [Couplet 1 : Isha]  →  [Couplet 1 : Isha ⏱ 0:12 → 0:45]
    Si pas de LRC ou aucun alignement, retourne `structured` inchangé.

    Habillage texte de `extract_sections` (qui porte toute la logique temporelle).
    """
    sections = extract_sections(structured, lrc)
    if not sections:
        return structured

    lines = structured.splitlines()
    annotated = list(lines)
    any_annotated = False
    for sec in sections:
        if sec.start is None:
            continue
        h = lines[sec.header_index].strip()
        if h.endswith("]"):
            annotated[sec.header_index] = (
                f"{h[:-1].rstrip()}  ⏱ {_fmt(sec.start)} → {_fmt(sec.end)}]"
            )
            any_annotated = True

    return "\n".join(annotated) if any_annotated else structured


# ── Cross-check des sources (LRCLIB vs YTM) ─────────────────────────────────────


def lrc_last_timestamp(lrc: str) -> float | None:
    """Timestamp (secondes) de la dernière ligne synchronisée, ou None."""
    lines = parse_lrc(lrc)
    return lines[-1][0] if lines else None


def sync_error(lrc: str, duration: float | None) -> float | None:
    """
    Erreur d'une synchro vis-à-vis de la durée réelle (pour départager deux sources).
    Plus petit = meilleur. Le **dépassement** (dernier timestamp au-delà de la durée)
    est plus pénalisant qu'un **déficit** (outro instrumental → normal). None si on ne
    peut pas juger (pas de durée ou LRC vide).
    """
    last = lrc_last_timestamp(lrc)
    if last is None or not duration:
        return None
    over = max(0.0, last - float(duration))
    under = max(0.0, float(duration) - last)
    return over * 2.0 + under


def _line_offsets(lrc_a: str, lrc_b: str) -> list[float]:
    """Décalages temporels (a - b) des lignes de texte communes aux deux LRC."""
    b_index = {}
    for tm, txt in parse_lrc(lrc_b):
        nt = _norm2(txt)
        if nt and nt not in b_index:
            b_index[nt] = tm
    offs = []
    for tm, txt in parse_lrc(lrc_a):
        nt = _norm2(txt)
        if nt and nt in b_index:
            offs.append(tm - b_index[nt])
    return offs


def compare_synced(
    lrclib_lrc: str | None, ytm_lrc: str | None, duration: float | None = None
) -> dict | None:
    """
    Croise LRCLIB (source 1) et YTM (source 2) et choisit le LRC à conserver.

    Renvoie `{'lrc', 'source', 'confidence', 'note'}` ou None si aucune synchro :
    - **2 sources concordantes** (même timeline) → `confidence=2`, on garde LRCLIB ;
    - **divergence** → départage par la durée réelle (`sync_error`), `confidence=1` ;
    - **une seule source** → `confidence=1`.

    `confidence` suit la sémantique du BPM (nb de sources concordantes) : 2 = croisé/validé,
    1 = source unique ou retenue après divergence (candidate à vérification manuelle).
    """
    has_l = bool(lrclib_lrc and parse_lrc(lrclib_lrc))
    has_y = bool(ytm_lrc and parse_lrc(ytm_lrc))
    if not has_l and not has_y:
        return None
    if has_l and not has_y:
        return {
            "lrc": lrclib_lrc,
            "source": "LRCLIB",
            "confidence": 1,
            "note": "source unique (LRCLIB)",
        }
    if has_y and not has_l:
        return {
            "lrc": ytm_lrc,
            "source": "YouTube Music",
            "confidence": 1,
            "note": "source unique (YTM)",
        }

    # Les deux présentes : concordance = décalage global faible + dispersion faible.
    offs = _line_offsets(lrclib_lrc, ytm_lrc)
    if len(offs) >= 3:
        offs_sorted = sorted(offs)
        median = offs_sorted[len(offs_sorted) // 2]
        spread = sum(abs(o - median) for o in offs) / len(offs)
        if abs(median) <= 2.0 and spread <= 1.5:
            return {
                "lrc": lrclib_lrc,
                "source": "LRCLIB",
                "confidence": 2,
                "note": f"concordant avec YTM ({len(offs)} lignes communes)",
            }

    # Divergence → départage par la durée réelle.
    el = sync_error(lrclib_lrc, duration)
    ey = sync_error(ytm_lrc, duration)
    if el is not None and ey is not None and el != ey:
        if ey < el:
            return {
                "lrc": ytm_lrc,
                "source": "YouTube Music",
                "confidence": 1,
                "note": "divergent → durée favorise YTM",
            }
        return {
            "lrc": lrclib_lrc,
            "source": "LRCLIB",
            "confidence": 1,
            "note": "divergent → durée favorise LRCLIB",
        }

    # Durée indisponible ou ex-æquo → priorité à la source 1 (LRCLIB).
    return {
        "lrc": lrclib_lrc,
        "source": "LRCLIB",
        "confidence": 1,
        "note": "divergent, durée indispo → LRCLIB (source 1)",
    }
