"""
Paroles synchronisées : cross-check des sources + alignement d'affichage.

Deux rôles :

1. **Cross-check des sources** (`compare_synced`) : croise le LRC LRCLIB (source 1) et
   le LRC YTM (source 2). Si les deux concordent → `confidence=2` (on garde LRCLIB).
   Sinon on **départage par la durée réelle** du morceau (`sync_error`) et on pose
   `confidence=1`. Une seule source → `confidence=1`. C'est le LRC retenu qui part en
   base (`track.lyrics_synced`) avec sa source/confidence.

2. **Alignement d'affichage** (`annotate_sections`) : annote chaque en-tête de section
   `[Couplet : artiste]` avec son intervalle `⏱ 0:12 → 0:45`, en retrouvant le timestamp
   de la 1ʳᵉ ligne de section dans le LRC. Le matching est désormais **monotone**
   (recherche en avant uniquement) pour éviter qu'une ligne de refrain répétée ne se
   cale sur une occurrence antérieure → intervalles non croissants / incohérents.
"""

import re
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


def _is_header(line: str) -> bool:
    l = line.strip()
    return l.startswith("[") and l.endswith("]")


def annotate_sections(structured: str, lrc: str) -> str:
    """
    Retourne les paroles structurées avec, sur chaque en-tête `[...]`, l'intervalle
    de temps inséré DANS les crochets (pour rester décoré par l'affichage) :
        [Couplet 1 : Isha]  →  [Couplet 1 : Isha ⏱ 0:12 → 0:45]
    Si pas de LRC ou aucun alignement, retourne `structured` inchangé.
    """
    lrc_lines = parse_lrc(lrc)
    if not lrc_lines or not structured:
        return structured

    norm_lrc = [(_norm2(txt), t) for t, txt in lrc_lines if txt.strip()]
    n_lrc = len(norm_lrc)

    def find_from(text: str, start_idx: int):
        """
        1er timestamp alignant `text` à une position >= start_idx (recherche EN AVANT).
        Match exact prioritaire, sinon 1ʳᵉ ligne de similarité >= 0.82.
        Renvoie (time, index) ou None. La contrainte « en avant » impose la monotonie.
        """
        nt = _norm2(text)
        if len(nt) < 3:
            return None
        for i in range(start_idx, n_lrc):  # match exact, le plus proche en avant
            if norm_lrc[i][0] == nt:
                return norm_lrc[i][1], i
        for i in range(start_idx, n_lrc):  # sinon 1ʳᵉ similarité suffisante en avant
            if SequenceMatcher(None, nt, norm_lrc[i][0]).ratio() >= 0.82:
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
        return structured

    # Start de chaque section = timestamp de sa 1ʳᵉ ligne alignable (parmi les 3 premières).
    # Recherche MONOTONE : chaque section cherche APRÈS la dernière ligne alignée, ce qui
    # empêche un refrain répété de se caler sur une occurrence antérieure.
    starts: list[float | None] = []
    cursor = 0
    for sec in sections:
        found = None
        for l in sec["lines"][:3]:
            found = find_from(l, cursor)
            if found is not None:
                break
        if found is not None:
            starts.append(found[0])
            cursor = found[1] + 1  # la section suivante repart après ce point
        else:
            starts.append(None)  # non alignée : on ne recule pas le curseur

    last_t = lrc_lines[-1][0]
    annotated = list(lines)
    any_annotated = False
    for i, sec in enumerate(sections):
        st = starts[i]
        if st is None:
            continue
        en = next((starts[j] for j in range(i + 1, len(sections)) if starts[j] is not None), last_t)
        h = lines[sec["idx"]].strip()
        if h.endswith("]"):
            annotated[sec["idx"]] = f"{h[:-1].rstrip()}  ⏱ {_fmt(st)} → {_fmt(en)}]"
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
