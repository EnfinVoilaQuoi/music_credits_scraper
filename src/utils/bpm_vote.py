"""Vote / réconciliation BPM (§8.2 borne unique + §8.3 demi/double).

Logique pure extraite de `DataEnricher`. Un `BpmBallot` collecte des candidats
`(source, valeur)` sanitizés et les réconcilie en `(bpm, bpm_alt, source,
confiance)`. Indépendant de `Track` : seul `finalize()` écrit sur un morceau.
C'est l'embryon du futur concept `Observation` (cf. REFONTE phase E).

Ne PAS régresser la sémantique du départage demi/double : elle est figée par
`tests/test_bpm_vote.py` (§8.3 du JOURNAL).
"""

from src.utils.logger import get_logger

logger = get_logger(__name__)

# Rang de fiabilité des sources — départage à ÉGALITÉ de vote (le vote prime).
#: Fiabilité (départage à égalité de voix / de confiance, BPM ET tonalité).
#: SongBPM passe DEVANT GetSongBPM le 2026-09-28, sur mesure : dans les 64
#: désaccords à trois sources de la base, la source isolée était GetSongBPM 61
#: fois (95 %), SongBPM 3 fois — et il gagnait pourtant les 57 conflits à deux.
BPM_SOURCE_RANK = {"reccobeats": 3, "songbpm": 2, "getsongbpm": 1, "deezer": 0}

# Seuil sous lequel un BPM ISOLÉ (1 seule source) est considéré half-time et
# remonté en double-time (logique prod rap & co.). N'agit PAS quand plusieurs
# sources concordent ou qu'une source confirme déjà le double.
BPM_HALFTIME_THRESHOLD = 90


def sanitize_bpm(value):
    """Cast en int + borne unique 40–220. None si invalide/hors borne."""
    try:
        v = int(round(float(value)))
    except (ValueError, TypeError):
        return None
    return v if 40 <= v <= 220 else None


def bpm_agree(a: int, b: int, tol: int = 3) -> bool:
    """Concordance à la tolérance près, demi/double inclus (71 ≡ 142)."""
    return abs(a - b) <= tol or abs(a - 2 * b) <= tol or abs(2 * a - b) <= tol


#: ReccoBeats et SongBPM donnent la MÊME donnée (99,4 % d'accord) : une seule
#: voix au départage d'octave, jamais deux.
_VOIX_DE_LA_SOURCE = {"reccobeats": "reccobeats+songbpm", "songbpm": "reccobeats+songbpm"}

#: Deezer double le tempo des autres dans ~11 % de ses valeurs comparables
#: (×0,5 : 1 %), contre 0 à 0,2 % pour les autres (mesure du 2026-10-30) :
#: seul, il ne fait jamais monter l'octave.
_SOURCE_QUI_DOUBLE = "deezer"


def _octave_du_consensus(basse: list, haute: list) -> str | None:
    """Octave que suit le consensus INDÉPENDANT d'un cluster à deux octaves
    (`"basse"` / `"haute"`), ou None sans consensus (une voix contre une).

    Une voix = une source indépendante (ReccoBeats et SongBPM n'en font
    qu'une) ; une voix présente aux DEUX octaves (ReccoBeats 75, SongBPM 150)
    ne compte pour aucune. Deezer seul ne fait jamais monter l'octave."""
    voix_b = {_VOIX_DE_LA_SOURCE.get(s, s) for s, _ in basse}
    voix_h = {_VOIX_DE_LA_SOURCE.get(s, s) for s, _ in haute}
    partagees = voix_b & voix_h
    voix_b -= partagees
    voix_h -= partagees
    if voix_h == {_SOURCE_QUI_DOUBLE}:
        return "basse"
    if len(voix_b) > len(voix_h):
        return "basse"
    if len(voix_h) > len(voix_b):
        return "haute"
    return None


def _le_plus_fiable(candidats: list) -> int:
    """Valeur de la source la plus fiable (`BPM_SOURCE_RANK`) d'une liste."""
    return max(candidats, key=lambda sb: BPM_SOURCE_RANK.get(sb[0], 0))[1]


def reconcile_bpm(candidates):
    """
    (bpm_real, bpm_alt, 'src1+src2', confidence) à partir des candidats.
    bpm_real = octave retenue ; bpm_alt = autre octave.

    Résolution demi/double par ÉVIDENCE (pas de seuil aveugle) :
      1. Deux octaves dans le cluster (74 + 145 + 72) → le verdict suit
         l'octave du CONSENSUS INDÉPENDANT (décision utilisateur 2026-10-30 :
         « la même convention partout ») — ReccoBeats et SongBPM ne font
         qu'une voix, Deezer seul ne fait jamais monter l'octave (il double
         ~11 % de ses valeurs). Sans consensus (une voix contre une autre,
         hors Deezer seul en haut) → la valeur HAUTE (comportement d'avant).
      2. Une seule octave, ≥2 sources d'accord (88 + 88) → consensus = vrai
         tempo → on NE double PAS.
      3. Une seule octave, 1 source sous le seuil (71 seul) → aucune preuve
         → convention rap : on double (71 → 142).
    """
    if not candidates:
        return (None, None, None, 0)
    clusters = []
    for cand in candidates:
        for cl in clusters:
            if any(bpm_agree(cand[1], m[1]) for m in cl):
                cl.append(cand)
                break
        else:
            clusters.append([cand])

    # Meilleur cluster : d'abord la taille (vote), puis la source la plus fiable
    def rank(cl):
        return (len(cl), max(BPM_SOURCE_RANK.get(s, 0) for s, _ in cl))

    best = max(clusters, key=rank)
    conf = len(best)
    srcs = sorted({s for s, _ in best}, key=lambda s: -BPM_SOURCE_RANK.get(s, 0))
    th = BPM_HALFTIME_THRESHOLD

    vals = [v for _, v in best]
    lo, hi = min(vals), max(vals)

    if hi >= lo * 1.5:
        # (1) Deux octaves : l'octave du consensus indépendant, sinon la haute
        high = [(s, v) for s, v in best if v >= lo * 1.5]
        low = [(s, v) for s, v in best if v < lo * 1.5]
        if _octave_du_consensus(low, high) == "basse":
            bpm_real, bpm_alt = _le_plus_fiable(low), _le_plus_fiable(high)
        else:
            bpm_real, bpm_alt = _le_plus_fiable(high), lo
    else:
        # Une seule octave : valeur de la source la plus fiable
        V = max(best, key=lambda sb: BPM_SOURCE_RANK.get(sb[0], 0))[1]
        if conf < 2 and th > V and V * 2 <= 220:
            # (3) half-time isolé, aucune confirmation → on double
            bpm_real, bpm_alt = V * 2, V
        else:
            # (2) consensus, ou déjà bande haute → on garde V
            bpm_real = V
            if th > V and V * 2 <= 220:
                bpm_alt = V * 2  # ex. 88 (consensus) → alt 176
            else:
                half = V // 2
                bpm_alt = half if half >= 55 else None
    return (bpm_real, bpm_alt, "+".join(srcs), conf)


class BpmBallot:
    """Collecte des candidats BPM sur un run d'enrichissement et les réconcilie."""

    def __init__(self):
        self._candidates: list[tuple[str, int]] = []

    def add(self, source: str, raw) -> None:
        """Enregistre un candidat BPM (sanitizé). Ignoré si invalide/hors borne."""
        v = sanitize_bpm(raw)
        if v is None:
            return
        self._candidates.append((source, v))
        logger.debug(f"🎚️ Candidat BPM: {source}={v}")

    @property
    def candidates(self) -> list[tuple[str, int]]:
        return list(self._candidates)

    def reconcile(self):
        """(bpm_real, bpm_alt, 'src1+src2', confidence) — sans effet de bord."""
        return reconcile_bpm(self._candidates)

    def consensus_reached(self) -> bool:
        """True si ≥2 candidats concordent déjà (→ pas besoin du scrape SongBPM)."""
        if len(self._candidates) < 2:
            return False
        return reconcile_bpm(self._candidates)[3] >= 2
