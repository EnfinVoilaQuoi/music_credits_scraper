"""Un morceau est-il VALIDÉ ? — les règles, à UN seul endroit.

Refonte du 2026-09-22 (règles réécrites par l'utilisateur). Ce qui change par
rapport au statut historique de `gui/helpers` :

- **crédits** : un PRODUCTEUR et un AUTEUR, quelle que soit la source. Exiger
  la concordance Genius↔Discogs a été mesuré et écarté : Discogs ne touche que
  1 373 morceaux sur 9 766 (les deux : 1 311), il ne référence ni freestyles ni
  lives, et le JOURNAL a déjà établi qu'il est ADDITIF et non confirmatif. Le
  croisement devient un niveau « confirmé » AFFICHÉ, jamais exigé ;
- **paroles** : les timestamps ne sont exigés que sur un album ou un EP de
  l'artiste. Un single, une compilation, un freestyle d'émission (Planète Rap,
  Grünt) ou un morceau sans disque se contentent du texte ; un instrumental
  CONSTATÉ (e27) est validé sans rien ;
- **inédits et désactivés** : rien n'est exigé, et ils ne comptent pas dans les
  morceaux « à valider ».

Module PUR : aucune I/O, aucun import de `src.models` (canard-typage, comme
l'ancien `_streams_complets`) — `src/utils/__init__` tire `DataEnricher`, un
import de modèle ferait remonter tout le pipeline. Ce que le morceau seul ne
peut pas savoir (désactivé ? nature de son disque ?) arrive par un `Contexte`,
que `src/services/validation.py` construit depuis la base.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from src.utils.logger import get_logger
from src.utils.title_matching import cle_album
from src.youtube.track_classifier import is_show_performance

logger = get_logger(__name__)

#: Types de disque qui exigent des paroles SYNCHRONISÉES. Un single ou une
#: compilation n'en font pas partie : le morceau n'y est pas « extrait d'un
#: album », c'est une parution isolée.
TYPES_AVEC_TIMESTAMPS = frozenset({"album", "ep"})


class Verdict(StrEnum):
    VALIDE = "valide"
    INCOMPLET = "incomplet"
    INEDIT = "inedit"
    DESACTIVE = "desactive"


class Manque(StrEnum):
    """Ce qui manque à un morceau. Nom distinct de `services.runtime.Manque`,
    qui désigne autre chose : ce qu'un FLUX doit aller chercher."""

    DATE = "Date"
    DUREE = "Durée"
    CREDITS = "Crédits"
    PAROLES = "Paroles"
    TIMESTAMPS = "Timestamps"
    BPM = "BPM"
    KEY_MODE = "Key/Mode"
    STREAMS = "Streams"


ICONES = MappingProxyType(
    {
        Verdict.VALIDE: "✅",
        Verdict.INCOMPLET: "⚠️",
        Verdict.INEDIT: "🔒",
        Verdict.DESACTIVE: "❌",
    }
)


@dataclass(frozen=True)
class Contexte:
    """Ce qu'un morceau seul ne peut pas dire."""

    #: Identifiants des morceaux désactivés (fichier par artiste, hors base).
    desactives: frozenset = frozenset()
    #: Titre d'album NORMALISÉ → `album`|`ep`|`single`|`compile`|None.
    #: `None` = disque connu mais NON typé (2 374 parutions dans ce cas).
    types_par_album: Mapping[str, str | None] = MappingProxyType({})
    #: `track_id` → type d'une parution `own` confirmée (catalogue e31),
    #: prioritaire : elle sait qu'un enregistrement vit sur plusieurs disques.
    types_par_morceau: Mapping[int, str | None] = MappingProxyType({})


@dataclass(frozen=True)
class Constat:
    verdict: Verdict
    manques: tuple[Manque, ...] = ()
    #: Phrases lisibles, dont les NON-exigences motivées — un trou qu'on ne
    #: dit pas est un trou qu'on ne répare jamais.
    details: tuple[str, ...] = ()
    #: False pour un inédit ou un désactivé : ils sortent du compte.
    compte_a_valider: bool = True
    #: Crédits vus par Genius ET Discogs — affiché, jamais exigé.
    credits_confirmes: bool = False

    @property
    def icone(self) -> str:
        return ICONES[self.verdict]

    @property
    def complet(self) -> bool:
        return self.verdict is Verdict.VALIDE


# ── sous-prédicats, testés un à un ──────────────────────────────────────────


def date_valide(track) -> bool:
    return bool(track.release_date)


def duree_valide(track) -> bool:
    return bool(track.duration)


def credits_valides(track) -> bool:
    """Un producteur ET un auteur, toute source confondue.

    Le vocabulaire des rôles vit dans le modèle (`get_producers`/`get_writers`,
    qui lisent `_PRODUCER_ROLES`/`_WRITER_ROLES`) : le recopier ici ferait une
    seconde table à tenir en phase.
    """
    return bool(track.get_producers()) and bool(track.get_writers())


def credits_confirmes(track) -> bool:
    """Genius ET Discogs ont tous deux crédité ce morceau.

    Un NIVEAU, pas une exigence : Discogs est additif (JOURNAL 2026-09-03) et
    ne couvre que 14 % du corpus.
    """
    sources = {(c.source or "").lower() for c in track.credits}
    return any(s.startswith("genius") for s in sources) and "discogs" in sources


def audio_valide(track) -> tuple[bool, bool]:
    """(bpm, key/mode) — exigés partout."""
    bpm = bool(track.audio.bpm)
    tonalite = bool(track.audio.musical_key or (track.audio.key and track.audio.mode))
    return bpm, tonalite


def streams_valides(track) -> bool:
    """Les streams qu'on peut légitimement réclamer (règle inchangée).

    ASYMÉTRIQUE, parce que les deux absences ne se valent pas.

    **YouTube : toujours exigé.** Un lien manquant ne prouve rien — le
    catalogue de YouTube est plus large que celui des plateformes de streaming
    (rips de titres supprimés, versions physiques, inédits, lyrics vidéos). Son
    absence n'est pas observable, elle ne peut donc jamais servir d'excuse.

    **Spotify : exigé seulement si l'absence est CONSTATÉE.** Un `spotify_id`
    vide dit aussi bien « pas sur Spotify » que « jamais cherché » ;
    `spotify_id_checked_at` (e17) date une résolution menée à terme. Un ISRC
    sans identifiant trahit une résolution RATÉE, pas une absence (l'inverse ne
    vaut pas : 292 morceaux ont un ID sans ISRC, le nôtre vient de Deezer).
    """
    if not track.streams.ytm_streams:
        return False
    if track.spotify_id:
        return bool(track.streams.spotify_streams)
    if track.isrc:
        return False
    return bool(track.spotify_id_checked_at)


def est_inedit(track) -> bool:
    """Constat « non sorti » (e34). Tri-état : seul `True` compte."""
    return getattr(track, "unreleased", None) is True


def sur_un_album_de_lartiste(track, ctx: Contexte) -> bool | None:
    """True / False / None (= nature du disque inconnue).

    Cascade, du plus sûr au moins sûr : le catalogue des parutions (qui sait
    qu'un enregistrement vit sur plusieurs disques), puis le type de l'album
    repère. Deux verdicts FRANCS court-circuitent :

      · pas de disque du tout ⇒ False (2 330 morceaux) — on n'est pas extrait
        d'un disque qu'on n'a pas ;
      · émission / freestyle (`is_show_performance`) ⇒ False quel que soit le
        type trouvé : un Planète Rap regroupé dans une compilation reste un
        freestyle, et c'est la règle énoncée par l'utilisateur.
    """
    if is_show_performance(track.title, track.album):
        return False
    if not (track.album or "").strip():
        return False

    par_morceau = ctx.types_par_morceau.get(getattr(track, "id", None), "__absent__")
    if par_morceau not in ("__absent__", None):
        return par_morceau in TYPES_AVEC_TIMESTAMPS
    # `cle_album` (title_matching) et non le normaliseur du GUI : c'est la clé
    # du catalogue des parutions, et un module de `src/utils` n'importe pas
    # `src/gui` (qui tirerait tout le pipeline).
    type_album = ctx.types_par_album.get(cle_album(track.album), "__absent__")
    if type_album in ("__absent__", None):
        return None
    return type_album in TYPES_AVEC_TIMESTAMPS


def paroles_valides(track, ctx: Contexte) -> tuple[tuple[Manque, ...], tuple[str, ...]]:
    """Manques de paroles, et ce qu'on a décidé de ne PAS exiger.

    Un instrumental CONSTATÉ sur Genius (e27) n'a rien à fournir. Sinon le
    texte est exigé partout ; les timestamps seulement sur un album/EP de
    l'artiste — et quand la nature du disque est INCONNUE, on ne les exige pas
    et on le DIT : un ⚠️ qu'on ne sait pas justifier est pire qu'un ✅ optimiste,
    et la phrase rend le trou réparable (`set_album_record_type`).
    """
    if track.lyrics.instrumental:
        return (), ("Paroles non exigées — instrumental constaté sur Genius",)

    manques: list[Manque] = []
    details: list[str] = []
    if track.lyrics.a_chercher():
        manques.append(Manque.PAROLES)

    sur_album = sur_un_album_de_lartiste(track, ctx)
    if sur_album is None:
        details.append(f"Timestamps non exigés — nature du disque « {track.album} » inconnue")
    elif not sur_album:
        details.append("Timestamps non exigés — le morceau n'est pas extrait d'un album")
    elif not track.lyrics.synced:
        manques.append(Manque.TIMESTAMPS)
    return tuple(manques), tuple(details)


def evaluer(track, ctx: Contexte | None = None) -> Constat:
    """Le verdict d'un morceau. Ordre : désactivé → inédit → manques.

    Le geste humain prime : un inédit qu'on a désactivé reste ❌.
    """
    ctx = ctx or Contexte()
    try:
        if getattr(track, "id", None) is not None and track.id in ctx.desactives:
            return Constat(verdict=Verdict.DESACTIVE, compte_a_valider=False)
        if est_inedit(track):
            return Constat(
                verdict=Verdict.INEDIT,
                compte_a_valider=False,
                details=("Rien n'est exigé — morceau inédit (Genius)",),
            )

        manques: list[Manque] = []
        details: list[str] = []
        if not date_valide(track):
            manques.append(Manque.DATE)
        if not duree_valide(track):
            manques.append(Manque.DUREE)
        if not credits_valides(track):
            manques.append(Manque.CREDITS)

        manques_paroles, details_paroles = paroles_valides(track, ctx)
        manques.extend(manques_paroles)
        details.extend(details_paroles)

        bpm, tonalite = audio_valide(track)
        if not bpm:
            manques.append(Manque.BPM)
        if not tonalite:
            manques.append(Manque.KEY_MODE)
        if not streams_valides(track):
            manques.append(Manque.STREAMS)

        return Constat(
            verdict=Verdict.VALIDE if not manques else Verdict.INCOMPLET,
            manques=tuple(manques),
            details=tuple(details),
            credits_confirmes=credits_confirmes(track),
        )
    except (AttributeError, TypeError, KeyError, ValueError) as e:
        # Frontière d'objet : une fiche mal formée ne doit pas casser la table.
        logger.error(f"Validation impossible pour {getattr(track, 'title', '?')}: {e}")
        return Constat(verdict=Verdict.INCOMPLET, details=(f"Validation impossible : {e}",))


def icone(constat: Constat) -> str:
    return constat.icone
