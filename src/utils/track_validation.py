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
from datetime import date
from enum import StrEnum
from types import MappingProxyType

from src.utils.dates import completer
from src.utils.logger import get_logger
from src.utils.paroles_genius import inedit_par_le_texte, texte_utile
from src.utils.title_matching import cle_album, normalize_name
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
    A_VENIR = "a_venir"
    SANS_INFO = "sans_info"
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
        Verdict.A_VENIR: "📅",
        Verdict.SANS_INFO: "🕳️",
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
    # Les crédits PROVISOIRES de l'API Genius (`credits_genius_api.SOURCE_API`,
    # en dur : module sans import de modèle) ne confirment rien — le scrape
    # n'est pas encore passé.
    genius = any(s.startswith("genius") and s != "genius_api" for s in sources)
    return genius and "discogs" in sources


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


def sortie_a_venir(track, aujourd_hui: date | None = None) -> str | None:
    """La date (`AAAA-MM-JJ`) d'une sortie POSTÉRIEURE à aujourd'hui, sinon None.

    Même idée que l'inédit 🔒, mais on SAIT quand ça sort (décision utilisateur
    2026-09-30 : l'album à paraître de Django, celui de BEN plg). La colonne
    est une date complète dont une précision moindre donne la BORNE BASSE
    (« 2026 » → 2026-01-01) : un doute ne met donc jamais un morceau sorti « à
    venir ». Le jour venu, le verdict tombe de lui-même — rien n'est stocké.
    """
    jour = completer(track.release_date) if track.release_date else None
    if not jour:
        return None
    return jour if jour > (aujourd_hui or date.today()).isoformat() else None


#: En dessous, le « texte » n'en est pas un : « (...) », « a », « ,,e, ».
_PAROLES_SIGNIFICATIVES = 20


def page_lue(track) -> bool:
    """Paroles ET crédits de la page Genius ont été lus. Les paroles seules ne
    suffisent pas : *Tu voulais du rap (Interlude)* avait ses paroles lues et
    ses crédits jamais (Népal, Doums, Lomepal) — vide en base, pas sur Genius.
    `last_scraped` date la lecture des crédits depuis le 2026-09-26 ; avant,
    un crédit Genius en base en est la seule preuve."""
    if track.lyrics.scraped_at is None:
        return False
    return track.last_scraped is not None or any(
        (c.source or "") == "genius" for c in track.credits
    )


def sans_info(track) -> bool:
    """Page Genius LUE qui ne dit rien (2026-09-26) : ni présence sur une
    plateforme, ni album, ni vidéo, ni bio, ni paroles utiles, ni crédit d'une
    autre personne que l'artiste. Cas type : *NICE THAT* (texte « (...) »).

    Calculé, jamais stocké, et seulement APRÈS lecture (`page_lue`) : avant,
    les vrais inédits sont vides aussi (*Real shit*, *BLEED IT*). Tout ce qui
    porte une info suffit à sortir de la catégorie (décisions utilisateur) :
    un crédit d'un tiers (« All Night », feat. non sorti avec Roddy Ricch), un
    ALBUM (l'interlude de Népal sur *La folie des glandeurs*), une VIDÉO — au
    risque de garder une poubelle à trier plutôt que perdre un vrai morceau (les
    freestyles de Kid Cudi n'ont que « Kid Cudi: » en texte). Un placeholder
    d'inédit (« Unreleased ») est un constat : 🔒, pas 🕳️. « Lyrics from
    snippet » seul n'est pas un texte (`paroles_genius.texte_utile`)."""
    if not page_lue(track) or track.lyrics.instrumental:
        return False
    if inedit_par_le_texte(track.lyrics.text):
        return False
    if track.spotify_id or track.deezer_id or track.isrc or track.streams.spotify_streams:
        return False
    if track.album or track.youtube_url or (track.anecdotes or "").strip():
        return False
    texte = "".join(ch for ch in texte_utile(track.lyrics.text) if ch.isalnum())
    if len(texte) >= _PAROLES_SIGNIFICATIVES:
        return False
    artiste = normalize_name(track.artist.name) if track.artist else ""
    return all(normalize_name(c.name) == artiste for c in track.credits)


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


def album_affiche(track, ctx: Contexte) -> str:
    """Le nom de disque à AFFICHER dans la table : vide quand l'album repère
    n'est qu'un SINGLE (décision utilisateur 2026-09-30 — un single n'est pas
    un album, le nommer dans la colonne le laisse croire). Seul le TYPE du
    distributeur décide (`albums.record_type`, Deezer) : un disque non typé
    reste affiché, un titre égal au nom du disque ne prouve rien (*Seul(s)*,
    *Rétina*, *Doberman* sont les titres phares de vrais EP). Affichage
    seulement : `track.album` et la fiche ne changent pas."""
    album = (track.album or "").strip()
    if album and ctx.types_par_album.get(cle_album(album)) == "single":
        return ""
    return track.album or ""


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
    """Le verdict d'un morceau. Ordre : désactivé → à venir → sans info →
    inédit → manques.

    Le geste humain prime : un inédit qu'on a désactivé reste ❌.
    """
    ctx = ctx or Contexte()
    try:
        if getattr(track, "id", None) is not None and track.id in ctx.desactives:
            return Constat(verdict=Verdict.DESACTIVE, compte_a_valider=False)
        # AVANT « sans info » : la page d'un morceau annoncé est vide PAR
        # NATURE (paroles non transcrites, aucune plateforme) — ce n'est pas
        # une page à trier.
        sortie = sortie_a_venir(track)
        if sortie:
            a, m, j = sortie.split("-")
            return Constat(
                verdict=Verdict.A_VENIR,
                compte_a_valider=False,
                details=(f"Rien n'est exigé — sortie prévue le {j}/{m}/{a}",),
            )
        if sans_info(track):
            return Constat(
                verdict=Verdict.SANS_INFO,
                compte_a_valider=False,
                details=(
                    "Page Genius sans aucune info (ni plateforme, ni bio, ni paroles, "
                    "ni autre crédit) — à vérifier",
                ),
            )
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
