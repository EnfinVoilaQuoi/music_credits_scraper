"""Panneau « À trancher » — les DÉTECTEURS (étape 1, 2026-09-27).

Chaque défaut trouvé le 2026-09-26 l'a été par hasard, en creusant autre chose
(LRC d'un autre morceau, vidéos d'un autre artiste, mesures SongBPM d'une autre
version…). À l'échelle d'une discographie de 2 000 fiches, vérifier ligne par
ligne est impossible : on ne montre que ce qu'un détecteur trouve SUSPECT, trié
par IMPACT (streams), pour trancher le haut de la liste.

Règles :
- un détecteur est une fonction PURE d'une fiche (et de sa discographie), sans
  réseau ni base : le panneau se calcule à l'ouverture, en lecture seule ;
- il SIGNALE, il ne corrige rien — décision utilisateur (2026-09-27) : une
  donnée n'est retirée que sur preuve, et c'est l'utilisateur qui tranche ;
- un cas se re-calcule à chaque ouverture : une meilleure donnée arrivée par un
  run (une durée YouTube, une page relue) le fait disparaître d'elle-même.

Étapes suivantes (WIP) : actions par type de cas en réutilisant les écrivains
existants, puis une table `revues` qui mémorise les verdicts (un cas tranché
n'est jamais reproposé) et absorbe les fenêtres éparpillées.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field

from src.utils.concordance_paroles import (
    SEUIL_FAUX,
    SEUIL_JUSTE,
    paroles_de_reference,
    recouvrement,
)
from src.utils.duree_youtube import SOURCE_AUDIO
from src.utils.track_validation import sans_info
from src.utils.version_descriptors import titre_generique

#: Écart de durée au-delà duquel deux sources ne décrivent plus le même fichier
#: (2 s = même fichier ; 5 s = tolérance inter-plateformes, comme la revue des
#: liens Deezer).
ECART_DUREE_S = 5

#: Sources de durée lues PAR l'identifiant Spotify de la fiche.
_SOURCES_PAR_ID_SPOTIFY = ("spotify_web", "reccobeats")


@dataclass(frozen=True)
class Cas:
    detecteur: str
    track_id: int | None
    morceau: str
    motif: str
    impact: int = 0
    preuves: dict = field(default_factory=dict, hash=False, compare=False)


@dataclass(frozen=True)
class Detecteur:
    code: str
    libelle: str
    icone: str
    #: `(fiche, discographie) -> (motif, preuves) | None`
    juger: Callable


def impact(track) -> int:
    """Ce qu'une erreur sur la fiche fausserait : ses streams, toutes plateformes."""
    return (track.streams.spotify_streams or 0) + (track.streams.ytm_streams or 0)


# ── Les détecteurs ──────────────────────────────────────────────────────────


def _duree_youtube(track) -> int | None:
    return (track.durations_observees or {}).get(SOURCE_AUDIO)


def duree_songbpm_dementie(track, _disco=None):
    """L'audio Topic dément la durée SongBPM : la page SongBPM était sans doute
    celle d'une autre version, son BPM et sa tonalité sont suspects aussi (cas
    « Blues (Live at AK Studios) » → page de « Blues »)."""
    yt, sb = _duree_youtube(track), (track.durations_observees or {}).get("songbpm")
    if yt and sb and abs(yt - sb) > ECART_DUREE_S:
        return (
            f"durée SongBPM {sb} s ≠ audio YouTube {yt} s — page d'une autre version ? "
            "(BPM et tonalité SongBPM suspects)",
            {"youtube": yt, "songbpm": sb},
        )
    return None


def duree_spotify_dementie(track, _disco=None):
    """L'audio Topic dément la durée de l'identifiant Spotify : l'ID désigne
    peut-être un autre enregistrement (ses streams et ses mesures avec)."""
    yt = _duree_youtube(track)
    if not yt or not track.spotify_id:
        return None
    for src in _SOURCES_PAR_ID_SPOTIFY:
        d = (track.durations_observees or {}).get(src)
        if d and abs(yt - d) > ECART_DUREE_S:
            return (
                f"durée de l'ID Spotify ({src}) {d} s ≠ audio YouTube {yt} s — "
                "autre enregistrement ?",
                {"youtube": yt, src: d, "spotify_id": track.spotify_id},
            )
    return None


def lrc_douteux(track, _disco=None):
    """LRC ni démenti (< 0,4, écarté d'office) ni confirmé (≥ 0,6) par les
    paroles Genius : la tranche que l'oracle ne sait pas trancher seul."""
    if not track.lyrics.synced:
        return None
    ref = paroles_de_reference(track.lyrics.text, track.lyrics.source)
    score = recouvrement(ref, track.lyrics.synced)
    if score is not None and SEUIL_FAUX <= score < SEUIL_JUSTE:
        return (
            f"LRC ({track.lyrics.synced_source or '?'}) : {score:.0%} de mots communs "
            "avec les paroles Genius",
            {"recouvrement": score, "source": track.lyrics.synced_source},
        )
    return None


def page_sans_info(track, _disco=None):
    """Page Genius lue qui ne dit rien (🕳️) : morceau poubelle ou vrai inédit."""
    if sans_info(track):
        return ("page Genius lue sans aucune information (🕳️)", {})
    return None


def cle_doublon(titre: str | None) -> str:
    """Le titre à la casse, aux accents et à la ponctuation près — mais AVEC ses
    descripteurs : « Heartless » et « Heartless (Live) » sont deux fiches
    légitimes (`normalize_title` les confondait : 465 faux doublons chez Kanye)."""
    t = unicodedata.normalize("NFKD", titre or "").encode("ascii", "ignore").decode()
    return re.sub(r"[\W_]+", "", t.casefold())


def _interprete(track) -> str:
    """L'interprète de la fiche : depuis e36 le titre n'est plus une identité,
    deux « Heartless » (l'original, une reprise d'un tiers) sont légitimes."""
    return cle_doublon(track.primary_artist_name) if track.is_featuring else ""


def doublon_de_titre(track, disco):
    """Deux fiches de l'artiste au même titre, à la casse et à la ponctuation
    près (Josman « BOSS » / « Boss ») : doublon à fusionner, ou deux morceaux
    distincts à renommer."""
    if track.secondary_role:
        return None
    cle = (cle_doublon(track.title), _interprete(track))
    autres = [
        t
        for t in disco
        if t is not track
        and not t.secondary_role
        and (cle_doublon(t.title), _interprete(t)) == cle
        and not (t.genius_id and track.genius_id and t.genius_id == track.genius_id)
    ]
    if cle[0] and autres:
        return (
            f"même titre que « {autres[0].title} »"
            + (f" ({autres[0].album})" if autres[0].album else " (sans album)")
            + (f" (+{len(autres) - 1})" if len(autres) > 1 else "")
            + (f" — celle-ci : {track.album}" if track.album else ""),
            {"autres": [t.id for t in autres]},
        )
    return None


def generique_meme_duree(track, disco):
    """Un titre GÉNÉRIQUE (intro, outro, interlude…) à la même durée qu'un autre
    titre générique de l'artiste : une donnée venue d'un rapprochement par titre
    (les 3 intros d'*Autopsie* à 94 s, toutes celles de SongBPM)."""
    if not titre_generique(track.title) or not track.duration:
        return None
    autres = [
        t
        for t in disco
        if t is not track and titre_generique(t.title) and t.duration == track.duration
    ]
    if autres:
        return (
            f"même durée ({track.duration} s) que « {autres[0].title} »"
            + (f" (+{len(autres) - 1})" if len(autres) > 1 else ""),
            {"duree": track.duration, "autres": [t.id for t in autres]},
        )
    return None


DETECTEURS: tuple[Detecteur, ...] = (
    Detecteur("duree_songbpm", "Durée SongBPM démentie par YouTube", "⏱️", duree_songbpm_dementie),
    Detecteur("duree_spotify", "Durée de l'ID Spotify démentie", "🎧", duree_spotify_dementie),
    Detecteur("lrc_douteux", "LRC douteux (40-60 %)", "📝", lrc_douteux),
    Detecteur("generique_duree", "Titre générique, durée partagée", "🔁", generique_meme_duree),
    Detecteur("doublon", "Doublon de titre", "👯", doublon_de_titre),
    Detecteur("sans_info", "Page Genius sans info", "🕳️", page_sans_info),
)


def detecter(tracks, detecteurs=DETECTEURS) -> list[Cas]:
    """Tous les cas de la discographie, triés par impact décroissant."""
    tracks = list(tracks)
    cas = []
    for d in detecteurs:
        for t in tracks:
            verdict = d.juger(t, tracks)
            if verdict:
                motif, preuves = verdict
                cas.append(Cas(d.code, t.id, t.title, motif, impact(t), preuves))
    return sorted(cas, key=lambda c: (-c.impact, c.detecteur, c.morceau))


def par_detecteur(cas: list[Cas]) -> dict[str, int]:
    compte: dict[str, int] = {}
    for c in cas:
        compte[c.detecteur] = compte.get(c.detecteur, 0) + 1
    return compte
