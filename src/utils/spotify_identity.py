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

import re

from src.utils.logger import get_logger
from src.utils.title_matching import (
    either_contains_as_words,
    names_match_as_words,
    normalize_name,
    normalize_title,
    strip_featuring,
)
from src.utils.track_mapper import _clean_duration
from src.utils.version_descriptors import (
    meme_famille,
    parse_variant,
    titre_generique,
    titre_sans_son_disque,
    titres_equivalents,
)

logger = get_logger(__name__)

#: Écart de durée toléré, en secondes. Deux plateformes ne coupent pas le silence
#: de fin au même endroit, et Genius arrondit : quelques secondes ne prouvent
#: rien. Au-delà, c'est un autre enregistrement.
TOLERANCE_DUREE = 5

#: Même FICHIER chez deux plateformes : 99,4 % à ±2 s (Deezer / Apple, même
#: place d'un même code-barres, mesuré le 2026-10-14). Sert à départager deux
#: prises au même titre (« Version EP »), où 5 s ne suffisent pas.
TOLERANCE_MEME_FICHIER = 2

#: Sources de durée INDÉPENDANTES de l'ID Spotify (`DISCOGRAPHY_PRIORITIES`
#: hors spotify_web et reccobeats, qui lisent la durée PAR l'ID), dans l'ordre
#: où l'on prend le témoin.
DUREES_INDEPENDANTES: tuple[str, ...] = (
    "manual",
    "deezer",
    "apple_music",
    "ytmusic",
    "youtube_officiel",
    "youtube",
    "songbpm",
    "youtube_video",
)

#: Sources qui lisent la durée PAR l'ID Spotify : leur durée suit l'ID, juste
#: ou faux — elle ne peut pas le corroborer.
DUREES_PAR_L_ID = frozenset({"spotify_web", "reccobeats"})


def duree_temoin(track) -> int | None:
    """PUR. La durée de la fiche qui ne doit RIEN à son ID Spotify, ou None.

    Une durée observée par une source indépendante d'abord ; à défaut, la
    colonne — mais seulement si aucune source n'a lu de durée PAR l'ID : sinon
    la colonne a pu suivre l'ID fautif, et un ID faux se corroborerait lui-même
    (Flynt « Version équipe », SCH « Massimo » ← « La mallette »)."""
    observees = getattr(track, "durations_observees", None) or {}
    for source in DUREES_INDEPENDANTES:
        if observees.get(source):
            return _clean_duration(observees[source])
    if any(observees.get(s) for s in DUREES_PAR_L_ID):
        return None
    return _clean_duration(track.duration)


#: Champ d'OBSERVATION d'un ID Spotify PROPOSÉ par une source qui n'a plus le
#: droit d'en poser (② de l'étape Identité, 2026-09-28 : SongBPM lit un ID sur
#: sa page, trouvée par une recherche de titre). Déclaré, jamais écrit en
#: colonne : le panneau « À trancher » le montre (`revue.id_spotify_propose`).
CHAMP_ID_PROPOSE = "spotify_id_propose"

#: Fournisseur des fiches de l'artiste : `artist_id -> [(track_id, titre)]`.
#: Posé par `DataManager` — ce module ne touche jamais la base. `None` (tests
#: purs, scripts sans base) ⇒ la règle « ID d'une autre fiche » est inactive.
fournisseur_titres = None

#: Fiches PROPRES de l'artiste (ni invité, ni rôle secondaire) :
#: `artist_id -> [(track_id, titre)]`. Même contrat que `fournisseur_titres`.
fournisseur_propres = None

#: Autres profils Spotify CONNUS de l'artiste : `artist_id -> set[str]` — ceux
#: que lie sa fiche MusicBrainz (e53) et ceux de ses ALIAS confirmés. Même
#: contrat que `fournisseur_titres` ; l'ID de l'artiste lui-même vient de la
#: fiche (`track.artist.spotify_id`). Les formations n'en sont PAS : Lunatic
#: n'est pas Booba, son URI ne prouve pas qu'un morceau est à Booba.
fournisseur_ids_artiste = None


#: IDs Spotify des AUTRES fiches du disque : `(artist_id, album) -> [id]`.
#: Même contrat ; sert la preuve par la pochette d'un titre générique.
fournisseur_ids_du_disque = None


def ids_du_disque(track, spotify_id: str) -> list[str]:
    """Jusqu'à 3 IDs d'autres pistes du disque de la fiche (références de
    pochette), jamais l'ID jugé."""
    artist_id = getattr(track.artist, "id", None)
    if fournisseur_ids_du_disque is None or not artist_id or not getattr(track, "album", None):
        return []
    try:
        ids = fournisseur_ids_du_disque(artist_id, track.album) or []
    except Exception:  # noqa: BLE001 — garde-fou : un fournisseur en panne ne prouve rien
        logger.exception("Fournisseur des IDs du disque en échec")
        return []
    return [i for i in ids if i and i != spotify_id][:3]


def meme_pochette(identite: dict | None, references) -> bool:
    """PUR. L'ID partage la pochette d'une piste du disque de la fiche : même
    parution (2026-10-14). Sans pochette lisible, rien n'est prouvé."""
    pochette = (identite or {}).get("pochette")
    return bool(pochette) and any((r or {}).get("pochette") == pochette for r in references)


def ids_artiste_attendus(track) -> frozenset[str]:
    """Les URI Spotify (IDs) sous lesquels l'artiste de la fiche peut être
    crédité : le sien, ceux que lie MusicBrainz, ceux de ses alias confirmés.
    Vide ⇒ inconnu."""
    artiste = track.artist
    ids = {getattr(artiste, "spotify_id", None)}
    artist_id = getattr(artiste, "id", None)
    if fournisseur_ids_artiste is not None and artist_id:
        try:
            ids |= set(fournisseur_ids_artiste(artist_id) or ())
        except Exception:  # noqa: BLE001 — garde-fou : un fournisseur en panne ne refuse rien
            logger.exception("Fournisseur d'IDs d'artiste en échec")
    return frozenset(i for i in ids if i)


def _credites(identite: dict | None) -> list[tuple[str, str | None]]:
    """`[(nom, id)]` des artistes crédités ; l'id est None quand l'identité ne
    porte pas d'URI (lecteur ancien, double de test)."""
    noms = (identite or {}).get("artists") or []
    ids = list((identite or {}).get("artist_ids") or [])
    ids += [None] * (len(noms) - len(ids))
    return [(n, i) for n, i in zip(noms, ids, strict=False) if n]


def uri_attendue_creditee(track, identite: dict | None) -> bool:
    """Spotify crédite l'artiste par son URI CONNUE — preuve plus forte que le
    nom, qui peut avoir changé chez Spotify (renommage) ou s'écrire autrement."""
    connus = ids_artiste_attendus(track)
    return bool(connus) and any(i in connus for _, i in _credites(identite))


#: Séparateurs d'un profil COMPOSÉ par le distributeur (« Diam's - Sinik »,
#: « Medine feat. Nneka »).
_SEPARATEURS_DE_PROFIL = re.compile(
    r"\s+-\s+|\s*,\s*|\s*&\s*|\s*/\s*|\s+(?:feat\.?|ft\.?|x|et|and|with)\s+", re.IGNORECASE
)


def nom_compose_de(credite: str, nom: str) -> bool:
    """PUR. `credite` est un nom composé dont l'une des parties est EXACTEMENT
    `nom` (« Admiral T - Diam's » pour Diam's) — pas « Los Josman » pour
    Josman, ni « Diam's » lui-même."""
    cible = normalize_name(nom)
    parties = [p for p in _SEPARATEURS_DE_PROFIL.split(credite or "") if p.strip()]
    return (
        len(parties) >= 2
        and normalize_name(credite) != cible
        and any(normalize_name(p) == cible for p in parties)
    )


def artiste_homonyme(track, identite: dict | None) -> str | None:
    """PUR (hors fournisseur). Spotify crédite un artiste au NOM de celui de la
    fiche, mais sous une AUTRE URI que la sienne → motif, sinon None.

    Le garde d'identité compare par NOM (`names_match_as_words`) : un homonyme
    passe. Mesuré le 2026-09-26 : *Intro (Black Album)* de Lunatic portait l'ID
    d'un autre « Lunatic » (spotify:artist:2X3LaSoc110Uv1m0PtzLPh) — 1 ID sur
    27 couples Lunatic. L'embed expose l'URI de CHAQUE artiste crédité.

    Ne juge que si l'ID de l'artiste est CONNU (sinon rien ne distingue deux
    homonymes : c'est le panneau qui les montre). Sur une ligne d'INVITÉ, le
    principal crédité par son nom suffit — son ID n'est pas connu, et c'est
    sur lui qu'un featuring se juge (`noms_attendus`)."""
    connus = ids_artiste_attendus(track)
    credites = _credites(identite)
    if not connus or any(i in connus for _, i in credites):
        return None
    artiste = track.artist
    nom = artiste.name if hasattr(artiste, "name") else str(artiste or "")
    # Un nom COMPOSÉ par le distributeur (« Diam's - Sinik », « Admiral T -
    # Diam's », « Medine feat. Nneka » — confirmés le 2026-10-15 : profils
    # communs de l'artiste et de son invité) n'est pas un homonyme. Un autre
    # nom qui CONTIENT le sien en reste un (« Los Josman », « Django Bates » :
    # d'autres groupes), comme le même nom sous une autre URI (« Isha »).
    homonymes = [
        (n, i)
        for n, i in credites
        if i and names_match_as_words(n, nom) and not nom_compose_de(n, nom)
    ]
    if not homonymes:
        return None
    autres = [n for n in noms_attendus(track) if not names_match_as_words(n, nom)]
    if any(names_match_as_words(p, n) for p in autres for n, _ in credites):
        return None
    n, i = homonymes[0]
    return (
        f"homonyme : Spotify crédite « {n} » (spotify:artist:{i}), "
        f"pas l'artiste de la fiche (spotify:artist:{', '.join(sorted(connus))})"
    )


def profils_homonymes(lectures) -> list[dict]:
    """Les artistes SANS profil Spotify connu (`ids_artiste_attendus` vide) que
    Spotify crédite sous PLUSIEURS URI à leur nom — l'homonyme que
    `artiste_homonyme` ne peut pas juger faute de référence, montré au panneau
    « À trancher ».

    `lectures` : `[(track, identite)]`, une par ID lu. Rend
    `[{"artiste", "uris": {uri: [titres]}}]`, l'URI la plus créditée en tête :
    c'est souvent le vrai profil, et l'autre un homonyme (Lunatic, 2026-09-26 :
    26 couples sur un profil, 1 sur l'autre)."""
    par_nom: dict[str, dict[str, list[str]]] = {}
    connus: dict[str, bool] = {}
    for track, identite in lectures:
        artiste = track.artist
        nom = artiste.name if hasattr(artiste, "name") else str(artiste or "")
        if not nom:
            continue
        if nom not in connus:
            connus[nom] = bool(ids_artiste_attendus(track))
        if connus[nom]:
            continue
        uris = par_nom.setdefault(nom, {})
        for n, i in _credites(identite):
            if i and names_match_as_words(nom, n):
                uris.setdefault(i, []).append(track.title)
    return [
        {"artiste": nom, "uris": dict(sorted(uris.items(), key=lambda kv: -len(kv[1])))}
        for nom, uris in par_nom.items()
        if len(uris) >= 2
    ]


def fiche_du_titre_spotify(track, identite: dict | None) -> str | None:
    """Le titre d'une AUTRE fiche de l'artiste que l'ID désigne, ou None.

    Mesuré le 2026-09-24 : 64 lignes Kworb (6,3 Md de streams) partaient par
    l'ID vers une fiche au titre différent alors qu'une fiche au titre EXACT
    existait — « Wolves » → « Wolves (BOOTS Reference) », « Only One » →
    « Only », Jazzy Bazz « Éternité » → « Feu Grégeois ». Toutes les sources
    en posaient (scraper, Kworb, SongBPM, Genius, historique). Le titre seul
    ne refuse pas un ID (« 1 pour la plume » / « Un pour la plume ») ; il le
    refuse quand ce titre est celui d'une fiche SŒUR.

    Jamais pour un titre servi GÉNÉRIQUE (2026-10-19) : chaque projet a son
    « Intro », une autre fiche au même mot ne dit rien de l'ID — Booba
    « Intro (0.9) » refusait l'Intro de *0.9* parce qu'une autre fiche
    s'appelle « Intro ». C'est le disque qui tranche (`motif_titre_generique`).
    """
    nom = (identite or {}).get("name")
    if fournisseur_titres is None or not nom or titres_equivalents(track.title, nom):
        return None
    if titre_generique(nom):
        return None
    artist_id = getattr(track.artist, "id", None)
    if not artist_id:
        return None
    try:
        fiches = fournisseur_titres(artist_id)
    except Exception:  # noqa: BLE001 — garde-fou : un fournisseur en panne ne refuse rien
        logger.exception("Fournisseur de titres en échec")
        return None
    for tid, titre in fiches:
        if tid != track.id and titres_equivalents(titre, nom):
            return titre
    return None


def sienne_homonyme(track, identite: dict | None) -> str | None:
    """Sur une ligne d'INVITÉ, l'ID désigne-t-il la PROPRE fiche homonyme de
    l'artiste ? → son titre, sinon None.

    Le gate accepte l'artiste de la ligne OU le principal (`noms_attendus`) :
    Dinos « Par Amour » feat. Diam's recevait donc l'ID de « Par amour » de
    Diam's, Hiatus « Soundtrack 2 My Life » celui de Kid Cudi (2026-10-10).
    Exiger le principal partout refusait surtout des ID JUSTES — mesuré sur les
    lignes d'invité : graphies (« CYRIL.mp4 » / « CYRILmp4 », « $tanlee (FRA) »)
    et compilations où Genius met le producteur ou le collectif en principal
    (Kilomaître, Sachons Dire Non). Ce qui sépare les vrais faux : Spotify ne
    crédite pas le principal ET l'artiste a SA fiche au titre servi.

    Un titre servi GÉNÉRIQUE n'en dit rien (2026-10-19, Freeze Corleone
    « Intro » refusé pour sa fiche « INTRO » d'un autre projet) : le disque
    tranche, comme dans `fiche_du_titre_spotify`."""
    if not (track.is_featuring and track.primary_artist_name) or fournisseur_propres is None:
        return None
    credites = [a for a in (identite or {}).get("artists") or [] if a]
    nom = (identite or {}).get("name")
    if not credites or not nom or titre_generique(nom):
        return None
    if any(names_match_as_words(track.primary_artist_name, a) for a in credites):
        return None
    artist_id = getattr(track.artist, "id", None)
    if not artist_id:
        return None
    try:
        fiches = fournisseur_propres(artist_id)
    except Exception:  # noqa: BLE001 — garde-fou : un fournisseur en panne ne refuse rien
        logger.exception("Fournisseur de fiches propres en échec")
        return None
    for tid, titre in fiches:
        if tid != track.id and titres_equivalents(titre, nom):
            return titre
    return None


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
       Quand l'ID Spotify de l'artiste est CONNU, l'URI tranche (2026-10-14) :
       créditée, elle suffit ; un crédité au même NOM sous une autre URI est
       un homonyme, refusé (`artiste_homonyme`).
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
        and not uri_attendue_creditee(track, identite)
        and not any(names_match_as_words(n, a) for n in attendus for a in artistes)
    ):
        return False, (
            f"artiste : Spotify crédite {', '.join(artistes)}, attendu {' ou '.join(attendus)}"
        )
    homonyme = artiste_homonyme(track, identite)
    if homonyme:
        return False, homonyme

    titre_spotify = identite.get("name") or ""
    if not titres_tranches and variante_etrangere(track, identite):
        return False, (
            f"variante : Spotify sert « {titre_spotify} », la base attend « {track.title} »"
        )

    duree_base = _clean_duration(track.duration)
    duree_spotify = identite.get("duration")
    if duree_base and duree_spotify and abs(duree_base - duree_spotify) > tolerance:
        return False, f"durée : {duree_base} s attendus, {duree_spotify} s sur Spotify"
    temoin = duree_temoin(track)
    if temoin and duree_spotify and abs(temoin - duree_spotify) > tolerance:
        return False, f"durée : {temoin} s (source indépendante), {duree_spotify} s sur Spotify"

    # « Intro (0.9) » sur *0.9* : Genius nomme le disque, Spotify sert « Intro »
    # — le disque reste prouvé à part (`motif_titre_generique`).
    titre_fiche = titre_sans_son_disque(track.title, getattr(track, "album", None))
    a, b = normalize_title(titre_fiche), normalize_title(titre_spotify)
    # Deux signaux indépendants qui s'accordent valent mieux qu'un titre — à
    # condition que la durée ne doive rien à l'ID jugé (`duree_temoin`).
    corrobore = bool(artistes and attendus and temoin and duree_spotify)
    if (
        a
        and b
        and a != b
        and not corrobore
        and not titres_tranches
        and not titres_equivalents(titre_fiche, titre_spotify)
    ):
        # Un titre CONTENU dans l'autre ne passe plus seul (2026-10-14) : sur
        # les 14 faux probables de la relecture du 2026-10-11, 9 passaient par
        # là — Kanye « Only » ← « Only One », « Wait » ← « I Can't Wait »,
        # Kid Cudi « Tree » ← « That Tree », Travis « Dream » ← « Dream
        # Killer », Diam's « L'amour Véritable » ← « L'AMOUR ». Le mot en plus
        # fait souvent un AUTRE morceau ; seule une durée indépendante
        # concordante dit que c'est le même.
        #
        # Mesuré le lendemain sur la base (audit, 6 325 ID) : 16 titres
        # contenus, 3 faux seulement. Les 13 justes n'ajoutaient qu'une
        # ANNOTATION hors du titre principal — « (& Lil Pump) », « (Prod by
        # Kanye West) », « - Darina », « (Physical Edition) », « - From
        # SR3MM », « To… » tronqué. Seul un mot en plus DANS le titre principal
        # exige la durée (`memes_titres_principaux`).
        if either_contains_as_words(a, b):
            if memes_titres_principaux(titre_fiche, titre_spotify):
                return True, ""
            return False, (
                f"titre : Spotify sert « {titre_spotify} », non corroboré par une "
                "durée indépendante"
            )
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


_GROUPES = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]")
_SEGMENT = re.compile(r"\s+[-–—]\s+.*$")
_POINTS_DE_SUSPENSION = re.compile(r"\s*(?:…|\.\.\.)\s*$")


def _titre_principal(titre: str) -> tuple[str, bool]:
    """`(titre principal normalisé, coupé ?)` : sans featuring, sans groupes
    entre parenthèses ou crochets, sans segment « - … », sans « … » final
    (coupé = la plateforme a tronqué le titre)."""
    t = strip_featuring(titre or "")
    t = _GROUPES.sub(" ", t)
    t = _SEGMENT.sub("", t)
    coupe = bool(_POINTS_DE_SUSPENSION.search(t))
    return normalize_title(_POINTS_DE_SUSPENSION.sub("", t)), coupe


def memes_titres_principaux(a: str | None, b: str | None) -> bool:
    """PUR. Les deux titres ont le même titre PRINCIPAL — ce qui les sépare
    n'est qu'une annotation (crédit, sous-titre, provenance, support) — ou
    l'un est le début tronqué (« … ») de l'autre."""
    (pa, ca), (pb, cb) = _titre_principal(a or ""), _titre_principal(b or "")
    if not pa or not pb:
        return False
    if pa == pb:
        return True
    return (ca and pb.startswith(pa + " ")) or (cb and pa.startswith(pb + " "))


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
    if not artistes or not attendus or uri_attendue_creditee(track, identite):
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
    va, vb = parse_variant(track.title), parse_variant(titre_spotify)
    if meme_famille(va, vb):
        return False
    # Un qualificatif DE PLUS d'un seul côté, même famille de prise (Diam's
    # « Dans ma bulle (Live 2006; Version EP) » / « Dans Ma Bulle - Live
    # 2006 ») : le titre ne départage pas — l'EP live de 2006 et l'album live
    # de 2007 portent le même titre chez Spotify et Deezer, avec des ISRC et
    # des durées différents (312 / 278 s ; 4 s seulement pour « La
    # Gamberge »). Seule une durée INDÉPENDANTE à ±2 s (même fichier : 99,4 %)
    # dit que c'est la bonne prise — mesuré le 2026-10-15 : les 12 ID
    # « Version EP » de la base avaient la durée de l'EP.
    if meme_famille(va, vb, compter_qualificatifs=False):
        temoin, duree = duree_temoin(track), identite.get("duration")
        if temoin and duree and abs(temoin - duree) <= TOLERANCE_MEME_FICHIER:
            return False
    return True


def variante_indecidable(track, identite: dict | None) -> bool:
    """PUR. La variante ne tient qu'à un qualificatif de plus, de même famille
    de prise, et AUCUNE durée indépendante ne départage (« Version EP » sans
    témoin) : le gate refuse de POSER l'ID, mais la réparation ne le RETIRE
    pas — on n'efface que ce qu'on peut montrer."""
    if not identite or not track.title or not identite.get("name"):
        return False
    va, vb = parse_variant(track.title), parse_variant(identite["name"])
    return (
        not meme_famille(va, vb)
        and meme_famille(va, vb, compter_qualificatifs=False)
        and not duree_temoin(track)
    )


def lire_identite_http(spotify_id: str) -> dict | None:
    """Lecteur par DÉFAUT : la page `/embed/` en `requests` nu, sans navigateur.

    Sert les producteurs qui ne possèdent pas de scraper Spotify (les liens media
    de Genius, le backfill Kworb). Le parseur n'est pas recopié : c'est
    `SpotifyIDScraper._identite_depuis_embed`, point de lecture unique du
    `__NEXT_DATA__`. Import LOCAL — le module de scraping tire Playwright, dont
    ces appelants n'ont aucun besoin au chargement.
    """
    import requests

    from src.observability import source_usage
    from src.scrapers.spotify_id_scraper_v2 import _UA, SpotifyIDScraper

    try:
        # Par le point de passage : gardé brut, rejouable (chantier E).
        resp = source_usage.requests_get(
            "spotify_embed",
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


def lire_album_page(spotify_id: str) -> str | None:
    """Lecteur par DÉFAUT du disque d'un ID : la page titre complète (l'embed ne
    le donne pas). Une page de navigateur par appel — réservé aux titres
    génériques. Neutralisé en test (conftest)."""
    from src.scrapers.spotify_web_scraper import lire_identite_page

    try:
        return (lire_identite_page(spotify_id) or {}).get("album")
    except Exception as e:  # noqa: BLE001 — une page illisible ne prouve rien
        logger.debug(f"Disque Spotify illisible ({spotify_id}) : {e}")
        return None


_GROUPE_DE_DISQUE = re.compile(r"\s*(?:[\(\[][^\)\]]*[\)\]]|\s[-–]\s.*$)")


def memes_disques(a: str | None, b: str | None) -> bool:
    """PUR. Deux titres de disque désignent-ils le même album, éditions
    comprises (« Temps mort » / « Temps mort (Réédition) », « Adamant-ium »
    2016 / 2020) ?"""
    if not a or not b:
        return False
    nus = [normalize_title(_GROUPE_DE_DISQUE.sub("", x)) for x in (a, b)]
    return bool(nus[0]) and (nus[0] == nus[1] or either_contains_as_words(nus[0], nus[1]))


def motif_titre_generique(track, identite: dict | None, album: str | None) -> str | None:
    """PUR. Un titre GÉNÉRIQUE (« Intro », « Interlude », « Outro ») ne désigne
    une piste que dans SON disque (2026-10-13) : Diam's « Intro » (*Premier
    mandat*) portait l'Intro de *Brut de femme*, Josman « Outro » (*Reste
    Calme*) « Outro Cypher » de 2024. Même règle que le gate Deezer. Sans disque
    lisible, rien ne prouve que c'est la bonne : refus."""
    if not titre_generique(track.title or "") or not getattr(track, "album", None):
        return None
    album = (identite or {}).get("album") or album
    if not album:
        return f"titre générique : le disque Spotify est illisible, attendu « {track.album} »"
    if not memes_disques(album, track.album):
        return f"titre générique : piste du disque « {album} », attendu « {track.album} »"
    return None


def _juger(
    track,
    spotify_id: str,
    identite: dict | None,
    tolerance: int,
    titres_tranches: bool = False,
    album: str | None = None,
) -> bool:
    """Décision COMMUNE aux voies sync et async : seul le transport diffère.

    Factorisée dès l'écriture, et pas après : c'est le défaut du jumeau
    Musixmatch (2026-09-05), où la logique pure était bien partagée mais où le
    GARDE-FOU, lui, n'avait atterri que sur une voie — celle qu'aucun appelant
    n'empruntait.
    """
    # Un ID retiré À LA MAIN (data/corrections/fiches.json) ne revient pas,
    # quelle que soit la source qui le repropose.
    from src.utils.corrections_fiches import ids_refuses

    if spotify_id in ids_refuses(track):
        # Décision déjà prise par un humain : rien à regarder (INFO, 2026-10-19 —
        # en WARNING, chaque run la remontait comme un problème).
        logger.info(f"❌ Spotify ID {spotify_id} REFUSÉ pour « {track.title} » — retiré à la main")
        return False
    autre = None if titres_tranches else fiche_du_titre_spotify(track, identite)
    if autre is not None:
        logger.warning(
            f"❌ Spotify ID {spotify_id} REFUSÉ pour « {track.title} » — Spotify sert "
            f"« {(identite or {}).get('name')} », titre d'une autre fiche : « {autre} »"
        )
        return False
    autre = sienne_homonyme(track, identite)
    if autre is not None:
        # Les deux fiches portent souvent le MÊME titre : sans le principal, le
        # message « c'est celui de la fiche X » se lisait comme un non-sens.
        logger.warning(
            f"❌ Spotify ID {spotify_id} REFUSÉ pour « {track.title} » (invité chez "
            f"{track.primary_artist_name}) — Spotify ne crédite pas "
            f"{track.primary_artist_name} : c'est la fiche propre « {autre} »"
        )
        return False
    accepte, motif = identite_concorde(
        track, identite, tolerance=tolerance, titres_tranches=titres_tranches
    )
    if accepte:
        motif = motif_titre_generique(track, identite, album)
        accepte = motif is None
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
    lire_album=None,
):
    """Vérifie qu'un ID désigne bien ce morceau avant de l'accepter (voie sync).

    `lire_identite` est INJECTÉ (le scraper qui vient de trouver l'ID, ou un
    double en test) : ce module ne dépend d'aucun réseau. Sans lui, le lecteur
    HTTP par défaut fait l'affaire. `titres_tranches` : cf. `identite_concorde`.
    """
    if not spotify_id:
        return False
    lecteur = lire_identite or lire_identite_http
    identite = lecteur(spotify_id)
    album = None
    if _disque_a_lire(track, identite):
        # La pochette d'abord : une requête embed par référence, là où la page
        # titre coûte un navigateur.
        references = [lecteur(i) for i in ids_du_disque(track, spotify_id)]
        if meme_pochette(identite, references):
            album = track.album
        else:
            album = (lire_album or lire_album_page)(spotify_id)
    return _juger(track, spotify_id, identite, tolerance, titres_tranches, album)


def _disque_a_lire(track, identite) -> bool:
    """Le disque ne se prouve (pochette d'une piste sœur, sinon page titre) que
    pour un titre générique d'une fiche à disque connu, quand l'identité ne
    porte pas déjà le disque."""
    return bool(
        titre_generique(track.title or "")
        and getattr(track, "album", None)
        and not (identite or {}).get("album")
    )


async def valider_identite_async(
    track, spotify_id: str, lire_identite, *, tolerance=TOLERANCE_DUREE, lire_album=None
):
    """Miroir async : même décision, transport différent. La page titre (titre
    générique) se lit hors de la boucle : son pont sync l'exige."""
    if not spotify_id:
        return False
    import asyncio

    identite = await lire_identite(spotify_id)
    album = None
    if _disque_a_lire(track, identite):
        references = [await lire_identite(i) for i in ids_du_disque(track, spotify_id)]
        if meme_pochette(identite, references):
            album = track.album
        else:
            album = await asyncio.to_thread(lire_album or lire_album_page, spotify_id)
    return _juger(track, spotify_id, identite, tolerance, album=album)
