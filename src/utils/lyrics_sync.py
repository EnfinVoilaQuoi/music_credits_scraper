"""
Alignement paroles structurées (Genius) ↔ paroles synchronisées (YTM/LRC).

But : annoter chaque en-tête de section `[Couplet : artiste]` avec son intervalle
de temps `⏱ 0:12 → 0:45`, en retrouvant le timestamp de la 1ʳᵉ ligne de la section
dans le LRC YTM (matching texte normalisé + similarité). Le LRC complet reste en
base (`track.lyrics_synced`) ; ici on ne fait que de l'affichage.
"""
import re
from difflib import SequenceMatcher
from typing import List, Optional, Tuple

_LRC_RE = re.compile(r'\[(\d+):(\d+)(?:[.:](\d+))?\]\s*(.*)')


def parse_lrc(lrc: str) -> List[Tuple[float, str]]:
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
    return re.sub(r'[^a-z0-9 ]', ' ', (s or '').lower())
    # collapse espaces fait dans l'appelant


def _norm2(s: str) -> str:
    return " ".join(_norm(s).split())


def _fmt(t: float) -> str:
    m, s = int(t) // 60, int(t) % 60
    return f"{m}:{s:02d}"


def _is_header(line: str) -> bool:
    l = line.strip()
    return l.startswith('[') and l.endswith(']')


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

    def find_time(text: str) -> Optional[float]:
        nt = _norm2(text)
        if len(nt) < 3:
            return None
        for ntext, t in norm_lrc:           # match exact
            if ntext == nt:
                return t
        best_t, best_r = None, 0.82          # sinon meilleure similarité
        for ntext, t in norm_lrc:
            r = SequenceMatcher(None, nt, ntext).ratio()
            if r > best_r:
                best_r, best_t = r, t
        return best_t

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

    # Start de chaque section = timestamp de sa 1ʳᵉ ligne alignable (parmi les 3 premières)
    starts: List[Optional[float]] = []
    for sec in sections:
        st = None
        for l in sec["lines"][:3]:
            st = find_time(l)
            if st is not None:
                break
        starts.append(st)

    last_t = lrc_lines[-1][0]
    annotated = list(lines)
    any_annotated = False
    for i, sec in enumerate(sections):
        st = starts[i]
        if st is None:
            continue
        en = next((starts[j] for j in range(i + 1, len(sections)) if starts[j] is not None), last_t)
        h = lines[sec["idx"]].strip()
        if h.endswith(']'):
            annotated[sec["idx"]] = f"{h[:-1].rstrip()}  ⏱ {_fmt(st)} → {_fmt(en)}]"
            any_annotated = True

    return "\n".join(annotated) if any_annotated else structured
