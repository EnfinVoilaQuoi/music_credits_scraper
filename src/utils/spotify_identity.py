"""Un identifiant Spotify désigne-t-il BIEN le morceau qui le porte ?

Le garde-fou historique (`DataEnricher.validate_spotify_id_unique`) contrôle
l'**unicité** — « cet ID est-il déjà pris ? » — et jamais la **justesse** —
« est-ce le bon morceau ? ». Les deux questions sont indépendantes, et c'est la
seconde qui manquait : mesuré le 2026-09-08 sur un run réel, **52 des 152 IDs
écrits (34 %) désignaient le morceau de quelqu'un d'autre**, tous parfaitement
uniques. Swing « Mouton noir » portait l'ID de *Dessine-moi un mouton* (Mylène
Farmer), « Nulle Part » celui de *Gravé dans la roche* (SNIPER).

La cause est structurelle : le scraper cherche, ne trouve pas — freestyles,
Booska, lives et inédits ne sont pas sur Spotify — et retient le résultat le plus
proche. D'où 54 % de faux sur les morceaux sans album, contre 27 % avec.

**Refuser est un bon résultat.** « Pas sur Spotify » est un verdict légitime, et
`tracks.spotify_id_checked_at` (e17) le date pour qu'on ne cherche pas en boucle.

Le coût de la vérification est UNE requête `requests` sur la page `/embed/`,
server-rendered : ni Playwright, ni patchright, ni navigateur.
"""

from __future__ import annotations

from src.utils.logger import get_logger
from src.utils.title_matching import (
    either_contains_as_words,
    names_match_as_words,
    normalize_title,
)
from src.utils.track_mapper import _clean_duration
from src.utils.version_descriptors import meme_famille, parse_variant

logger = get_logger(__name__)

#: Écart de durée toléré, en secondes. Deux plateformes ne coupent pas le silence
#: de fin au même endroit, et Genius arrondit : quelques secondes ne prouvent
#: rien. Au-delà, c'est un autre enregistrement.
TOLERANCE_DUREE = 5


def noms_attendus(track) -> list[str]:
    """Les noms d'artiste qui doivent apparaître chez Spotify pour ce morceau.

    Pour un FEATURING, c'est l'artiste PRINCIPAL qu'il faut retrouver : Spotify
    ne crédite pas toujours l'invité, et exiger le nom de la ligne produirait des
    faux positifs (mesuré : 6 sur 86).
    """
    artiste = track.artist
    # Frontière d'objet : `track.artist` est un `Artist` dans l'application, une
    # chaîne dans certains harnais — idiome déjà en place dans le provider.
    noms = [artiste.name if hasattr(artiste, "name") else str(artiste)]
    if track.is_featuring and track.primary_artist_name:
        noms.append(track.primary_artist_name)
    return [n for n in noms if n]


def identite_concorde(
    track,
    identite: dict | None,
    *,
    tolerance: int = TOLERANCE_DUREE,
    titres_tranches: bool = False,
):
    """Le morceau servi par Spotify est-il celui-ci ? → `(verdict, motif)`.

    Trois règles, de la plus forte à la plus faible :

    1. **Artiste** — au moins un nom attendu doit apparaître, par MOTS ENTIERS
       (`names_match_as_words`). Jamais par sous-chaîne nue : « IAM » ⊂
       « Williams », « SCH » ⊂ « ScHoolboy Q » (JOURNAL 2026-09-04).
    2. **Durée** — au-delà de la tolérance, c'est un autre enregistrement. C'est
       le signal OBJECTIF, et le seul qui attrape certains cas : Flynt « Rap
       théorie » avait le bon titre, le bon artiste, et 64 secondes d'écart.
    3. **Version** (2026-09-21) — la base attend « Heartless (Remix) » et Spotify
       sert « Heartless » nu, ou l'inverse (« FACTS » ↔ « Facts (Charlie Heat
       Version) »), ou un autre remixeur : un DESCRIPTEUR DE VERSION asymétrique
       est un autre enregistrement, quoi qu'en disent l'artiste et la durée.
       Règle INCONDITIONNELLE, et c'est le point : c'est le cas où la durée
       s'est corroborée elle-même — mesuré, **73 variantes portaient l'ID de
       l'original et 22,7 Md de streams étaient écrits sur la mauvaise ligne**,
       toutes avec le bon artiste et une durée qui avait SUIVI l'ID fautif.
       Le vocabulaire est celui de `version_descriptors` (fermé).
    4. **Titre** — le plus faible, et le seul CONDITIONNEL : il ne refuse que
       si l'artiste ou la durée manque à l'appel. Quand les deux concordent, un
       titre différent est une variante d'écriture (« 1 pour la plume » chez
       Spotify, « Un pour la plume » chez Genius).

    **Un ID non vérifiable n'est pas un ID fautif** : `identite=None` (embed
    illisible, réseau coupé) rend `(True, "")` — on ne conclut pas, même règle
    qu'`absent` en observabilité, où ce qu'on n'a pas pu lire n'accuse personne.

    `titres_tranches=True` désarme les deux règles de TITRE (3 et 4) : réservé
    aux décisions HUMAINES (dialogue Kworb, où l'utilisateur a lu les deux
    titres et dit que la ligne « Dolce Camara - Snight B Remix » EST « DCR
    (Dolce Camara Remix) »). L'artiste et la durée continuent de garder.
    """
    if not identite:
        return True, ""

    artistes = [a for a in identite.get("artists") or [] if a]
    attendus = noms_attendus(track)
    if (
        artistes
        and attendus
        and not any(names_match_as_words(n, a) for n in attendus for a in artistes)
    ):
        return False, (
            f"artiste : Spotify crédite {', '.join(artistes)}, attendu {' ou '.join(attendus)}"
        )

    titre_spotify = identite.get("name") or ""
    if not titres_tranches and variante_etrangere(track, identite):
        return False, (
            f"variante : Spotify sert « {titre_spotify} », la base attend « {track.title} »"
        )

    duree_base = _clean_duration(track.duration)
    duree_spotify = identite.get("duration")
    if duree_base and duree_spotify and abs(duree_base - duree_spotify) > tolerance:
        return False, f"durée : {duree_base} s attendus, {duree_spotify} s sur Spotify"

    a, b = normalize_title(track.title or ""), normalize_title(titre_spotify)
    # Deux signaux indépendants qui s'accordent valent mieux qu'un titre.
    corrobore = bool(artistes and attendus and duree_base and duree_spotify)
    if (
        a
        and b
        and a != b
        and not corrobore
        and not titres_tranches
        and not either_contains_as_words(a, b)
    ):
        # Le titre ne refuse QUE s'il est seul à parler. Quand l'artiste ET la
        # durée concordent, deux signaux indépendants s'accordent, et un titre
        # écrit autrement n'est qu'une variante éditoriale : Spotify sert « 1
        # pour la plume » là où Genius écrit « Un pour la plume » — même morceau,
        # même artiste, mêmes 249 secondes. Refuser là-dessus effacerait un ID
        # JUSTE, et le morceau repartirait en recherche à chaque run.
        #
        # Contrepartie assumée : un ID faux dont la durée a DÉJÀ contaminé la
        # colonne se corrobore lui-même (le cas de la « Version équipe » de
        # Flynt). Ces cas-là ne se règlent pas par un prédicat — ils demandent
        # l'oracle et une décision humaine.
        return False, f"titre : Spotify sert « {titre_spotify} »"

    return True, ""


def artiste_etranger(track, identite: dict | None) -> bool:
    """Aucun des artistes attendus n'apparaît chez Spotify.

    C'est le motif le plus SÛR des trois — bien plus qu'un titre, qui s'écrit de
    dix façons, ou qu'une durée, qui peut coïncider. Il sépare « le même morceau
    écrit autrement » de « le morceau de quelqu'un d'autre », et c'est pourquoi
    la RÉPARATION ne retire que sur ce motif-là : on n'efface que ce qu'on peut
    montrer.
    """
    if not identite:
        return False
    artistes = [a for a in identite.get("artists") or [] if a]
    attendus = noms_attendus(track)
    if not artistes or not attendus:
        return False
    return not any(names_match_as_words(n, a) for n in attendus for a in artistes)


def variante_etrangere(track, identite: dict | None) -> bool:
    """Le titre Spotify et le titre en base ne désignent pas la même VERSION.

    « Heartless (Remix) » en base contre « Heartless » chez Spotify, « FACTS »
    contre « Facts (Charlie Heat Version) », « Dolce Camara - Snight B Remix »
    contre « … - Dee Mad x Akalex Remix ». Second motif de RÉPARATION, à côté
    d'`artiste_etranger` : lui aussi se montre (le titre servi est dans le
    rapport), et il ne dépend pas d'une durée qui a pu suivre l'ID fautif.

    Prédicat pur, sur le SEUL descripteur : deux titres qui diffèrent par autre
    chose (« 1 pour la plume » / « Un pour la plume ») ne sont pas son affaire.
    """
    if not identite:
        return False
    titre_spotify = identite.get("name") or ""
    if not titre_spotify or not track.title:
        return False
    return not meme_famille(parse_variant(track.title), parse_variant(titre_spotify))


def lire_identite_http(spotify_id: str) -> dict | None:
    """Lecteur par DÉFAUT : la page `/embed/` en `requests` nu, sans navigateur.

    Sert les producteurs qui ne possèdent pas de scraper Spotify (les liens media
    de Genius, le backfill Kworb). Le parseur n'est pas recopié : c'est
    `SpotifyIDScraper._identite_depuis_embed`, point de lecture unique du
    `__NEXT_DATA__`. Import LOCAL — le module de scraping tire Playwright, dont
    ces appelants n'ont aucun besoin au chargement.
    """
    import requests

    from src.scrapers.spotify_id_scraper_v2 import _UA, SpotifyIDScraper

    try:
        resp = requests.get(
            f"https://open.spotify.com/embed/track/{spotify_id}",
            timeout=15,
            headers={"User-Agent": _UA},
        )
    except requests.RequestException as e:
        logger.debug(f"Identité Spotify illisible ({spotify_id}) : {e}")
        return None
    if not resp.ok:
        return None
    resp.encoding = "utf-8"  # titres et noms accentués (anti-mojibake)
    return SpotifyIDScraper._identite_depuis_embed(resp.text)


def _juger(
    track, spotify_id: str, identite: dict | None, tolerance: int, titres_tranches: bool = False
) -> bool:
    """Décision COMMUNE aux voies sync et async : seul le transport diffère.

    Factorisée dès l'écriture, et pas après : c'est le défaut du jumeau
    Musixmatch (2026-09-05), où la logique pure était bien partagée mais où le
    GARDE-FOU, lui, n'avait atterri que sur une voie — celle qu'aucun appelant
    n'empruntait.
    """
    accepte, motif = identite_concorde(
        track, identite, tolerance=tolerance, titres_tranches=titres_tranches
    )
    if not accepte:
        logger.warning(f"❌ Spotify ID {spotify_id} REFUSÉ pour « {track.title} » — {motif}")
    return accepte


def valider_identite(
    track,
    spotify_id: str,
    lire_identite=None,
    *,
    tolerance=TOLERANCE_DUREE,
    titres_tranches: bool = False,
):
    """Vérifie qu'un ID désigne bien ce morceau avant de l'accepter (voie sync).

    `lire_identite` est INJECTÉ (le scraper qui vient de trouver l'ID, ou un
    double en test) : ce module ne dépend d'aucun réseau. Sans lui, le lecteur
    HTTP par défaut fait l'affaire. `titres_tranches` : cf. `identite_concorde`.
    """
    if not spotify_id:
        return False
    lecteur = lire_identite or lire_identite_http
    return _juger(track, spotify_id, lecteur(spotify_id), tolerance, titres_tranches)


async def valider_identite_async(
    track, spotify_id: str, lire_identite, *, tolerance=TOLERANCE_DUREE
):
    """Miroir async : même décision, transport différent."""
    if not spotify_id:
        return False
    return _juger(track, spotify_id, await lire_identite(spotify_id), tolerance)
