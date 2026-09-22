"""Mise à jour des streams Spotify (kworb.net) pour les morceaux et albums d'un artiste.

v2 — refonte après session d'exploration du site (JOURNAL 2026-07-02) :
  · VALIDATION D'IDENTITÉ : le <title> de la page Kworb donne le nom de l'artiste ;
    mismatch → re-vote de l'ID (sans les feats !) → re-scrape → abort si toujours faux.
    (Bug historique : l'ID d'Isha pointait vers Limsa d'Aulnay, élu par un vote
    dominé par les pages de ses feats.)
  · MATCHING PAR SPOTIFY ID d'abord (les lignes Kworb contiennent l'URL du track),
    titre normalisé en fallback ; backfill des spotify_id manquants en base.
  · Fraîcheur = date "Last updated" de la page Kworb (pas now()).
  · Totaux artiste (récap Total/As lead/As feature) stockés sur artists.
  · Albums : total calculé sur les MORCEAUX de l'album, jamais en sommant les
    lignes d'édition de Kworb (corrigé le 2026-09-05 — ses compteurs sont ceux de
    Spotify, cumulés PAR ENREGISTREMENT, donc deux éditions d'un même disque
    portent les mêmes titres et les additionner les compte deux fois). Les IDs
    d'édition restent conservés. Filtrés aux albums PROPRES (≥2 morceaux de
    l'artiste en base sur l'album — garde les projets communs type Bitume Caviar,
    écarte les simples apparitions).
"""

import difflib
import logging
import sys
from collections import defaultdict
from dataclasses import dataclass, field

from playwright.sync_api import Error as PlaywrightError
from sqlalchemy.exc import SQLAlchemyError

from src.scrapers.kworb_scraper import KworbScraper
from src.utils.logger import get_logger
from src.utils.spotify_identity import (
    artiste_etranger,
    identite_concorde,
    lire_identite_http,
    valider_identite,
)
from src.utils.version_descriptors import (
    MOTS_DE_MEME_MORCEAU,
    Kind,
    doublons_evidents,
    meme_famille,
    meme_prise,
    parse_variant,
)

logger = get_logger(__name__)


# Normaliseur PARTAGÉ (même matching que update_ytmusic — cf. title_matching.py)
from src.utils.title_matching import base_album_key, contains_as_words, names_match_as_words
from src.utils.title_matching import normalize_title as _normalize_title


def _names_match(page_name: str | None, artist_name: str) -> bool:
    """Le nom affiché par la page Kworb correspond-il à notre artiste ?

    C'est le GARDE-FOU d'identité de tout le passage Kworb : un faux positif ici
    ne se trompe pas d'un morceau, il fait écrire le catalogue de streams d'un
    INCONNU sur notre artiste (plus ses totaux et ses albums).

    L'inclusion ne compte qu'en MOTS ENTIERS (2026-09-04). La comparaison était
    une sous-chaîne nue : sur les 2 515 noms d'artistes réellement croisés en
    base, 18 pages étaient acceptées à tort — « SCH » matchait « Coline
    Schneider », « Sébastien Tedeschi », « ScHoolboy Q » ; « Isha » matchait
    « Misha Van Der Werf ». Après correctif il en reste 4, toutes légitimes :
    « ISHA », « Isha (7) », « Sch (5) » (suffixes de désambiguïsation Genius)
    et « Jazzy Jazz », retenue par le repli difflib et non par l'inclusion.
    """
    if not page_name:
        return False
    a, b = _normalize_title(page_name), _normalize_title(artist_name)
    if not a or not b:
        return False
    if a == b or contains_as_words(a, b) or contains_as_words(b, a):
        return True
    import difflib

    # Repli tolérant aux coquilles (« Nekfeu » / « Nekfeuu »), inchangé.
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.8


def _vote_artist_spotify_id(artist, data_manager, max_pages: int = 5) -> str | None:
    """
    Déduit l'ID Spotify de l'ARTISTE par vote majoritaire sur les crédits de
    plusieurs de ses morceaux (pages embed). Doubles garde-fous anti-Limsa
    (bug historique : projets communs Isha × Limsa) :
      · ne vote que sur les NON-FEATS ;
      · vote NAME-AWARE : seul l'artiste crédité portant le bon nom vote —
        un morceau dont aucun crédité ne matche s'abstient.
    Secours : recherche par nom si pas assez de morceaux propres avec spotify_id.
    """
    from collections import Counter

    try:
        from src.scrapers.spotify_id_scraper_v2 import SpotifyIDScraper
    except ImportError as e:
        logger.error(f"SpotifyIDScraper indisponible: {e}")
        return None

    try:
        tracks = data_manager.get_artist_tracks(artist.id)
    except SQLAlchemyError:
        tracks = []
    track_ids = [
        t.spotify_id
        for t in tracks
        if getattr(t, "spotify_id", None) and not getattr(t, "is_featuring", False)
    ]

    votes = Counter()
    try:
        with SpotifyIDScraper(headless=True) as scraper:
            for sid in track_ids[:max_pages]:
                aid = scraper.get_artist_id_from_track(sid, expected_name=artist.name)
                if aid:
                    votes[aid] += 1
            if votes:
                best, count = votes.most_common(1)[0]
                total = sum(votes.values())
                if (count >= 2 and count > total / 2) or total == 1:
                    logger.info(f"🗳️ ID artiste Spotify: {best} ({count}/{total} voix, non-feats)")
                    return best
                logger.warning(f"🗳️ Vote ID artiste non concluant: {dict(votes)}")
            # Secours : recherche par nom (ambiguïté possible)
            return scraper.get_artist_spotify_id(artist.name)
    except (PlaywrightError, AttributeError, KeyError, TypeError, ValueError) as e:
        logger.error(f"Vote ID artiste Spotify échoué: {e}")
        return None


def _resolve_homonym(candidates, artist_name: str, credited_norm: set[str]):
    """Départage des HOMONYMES par les artistes crédités du track Spotify.

    Deux morceaux distincts peuvent porter le même titre (« MEILLEUR » de
    Souffrance vs « Meilleur » de Goldee Money — cf. JOURNAL). On compare
    l'artiste principal de chaque candidat aux artistes crédités sur la page
    embed. ABSTENTION dès qu'il n'y a pas exactement UN candidat compatible :
    attribuer les streams au mauvais morceau est pire que ne rien écrire.

    Fonction PURE : les crédits sont fournis en entrée, le scraping reste au
    site d'appel (extraite d'une closure le 2026-09-03 pour être testable).
    """
    if not credited_norm:
        return None
    matches = []
    for cand in candidates:
        primary = (
            getattr(cand, "primary_artist_name", None)
            if getattr(cand, "is_featuring", False)
            else None
        ) or artist_name
        p = _normalize_title(primary)
        # Inclusion en MOTS ENTIERS (2026-09-04) : la comparaison était une
        # sous-chaîne nue, qui faisait passer « IAM » pour « WILLIAMS ». Le
        # relâchement voulu reste entier — « Jul » matche toujours « Jul & SCH ».
        if any(p == c or contains_as_words(p, c) or contains_as_words(c, p) for c in credited_norm):
            matches.append(cand)
    return matches[0] if len(matches) == 1 else None


def _fuzzy_unique(entry_title, tracks, threshold: float = 0.87):
    """Rapproche un titre Kworb d'UN SEUL morceau en base par similarité
    (difflib sur titre normalisé) — attrape les coquilles (« Rhythm » vs
    « Rythm »). GARDE-FOUS anti-fusion : ne matche QUE s'il existe
    exactement UN candidat au-dessus du seuil (abstention si ambigu, ex.
    plusieurs variantes « My Love »). Ne strippe PAS les descripteurs de
    version (Acoustic/Intro/Remix) → studio et acoustique restent distincts.
    """
    nk = _normalize_title(entry_title)
    hits = []
    for t in tracks:
        r = difflib.SequenceMatcher(None, nk, _normalize_title(t.title)).ratio()
        if r >= threshold:
            hits.append((r, t))
    return (hits[0][1], hits[0][0]) if len(hits) == 1 else (None, 0.0)


def _best_candidate(entry_title, tracks):
    """Meilleur candidat unique dans la bande INCERTAINE [0.55, 0.87) — pour
    proposer une confirmation à l'utilisateur (ex. « Matrix » vs
    « Matrix (Intro) ») sans écrire. Unique = pas de 2e candidat proche."""
    nk = _normalize_title(entry_title)
    scored = sorted(
        ((difflib.SequenceMatcher(None, nk, _normalize_title(t.title)).ratio(), t) for t in tracks),
        key=lambda x: x[0],
        reverse=True,
    )
    if not scored:
        return None, 0.0
    best_r, best_t = scored[0]
    if not (0.55 <= best_r < 0.87):
        return None, 0.0
    # écart net avec le 2e (évite de proposer quand plusieurs se valent)
    if len(scored) > 1 and (best_r - scored[1][0]) < 0.08:
        return None, 0.0
    return best_t, best_r


@dataclass
class Index:
    """Ce que la base sait des morceaux de l'artiste, indexé pour le rapprochement.

    Construit UNE fois par run (`construire_index`) — les IDs de TOUS les
    magasins (colonne + table e23), par nature ; les titres normalisés ; les
    socles (titre sans descripteur de version) des morceaux qui n'en portent
    pas, pour rattacher une rendition.
    """

    by_id: dict[int, object] = field(default_factory=dict)
    by_edition_id: dict[str, object] = field(default_factory=dict)
    by_rendition_id: dict[str, object] = field(default_factory=dict)
    #: ID porté par ≥ 2 lignes de l'artiste (défaut C) : rien ne lui sera attribué.
    ids_partages: dict[str, list] = field(default_factory=dict)
    #: ID porté par des DOUBLONS évidents (même titre / même prise) : attribué
    #: à la première fiche (id le plus bas) — écrire sur chacune gonflerait le
    #: total d'album d'un morceau ; les autres restent à fusionner.
    doublons: dict[str, list] = field(default_factory=dict)
    by_title: dict[str, list] = field(default_factory=dict)
    by_socle: dict[str, list] = field(default_factory=dict)
    tracks: list = field(default_factory=list)


def construire_index(tracks) -> Index:
    index = Index(tracks=list(tracks))
    porteurs: dict[str, list] = defaultdict(list)
    for t in tracks:
        index.by_id[t.id] = t
        index.by_title.setdefault(_normalize_title(t.title), []).append(t)
        if parse_variant(t.title).kind == Kind.NONE:
            index.by_socle.setdefault(_normalize_title(t.title), []).append(t)
        editions = set()
        if getattr(t, "spotify_id", None):
            editions.add(t.spotify_id)
        for e in getattr(t, "spotify_id_entries", None) or []:
            if e.est_rendition:
                index.by_rendition_id[e.spotify_id] = t
            else:
                editions.add(e.spotify_id)
        for sid in editions:
            porteurs[sid].append(t)
    for sid, ts in porteurs.items():
        if len(ts) > 1 and doublons_evidents([(t.title, t.album) for t in ts]):
            premiere, *autres = sorted(ts, key=lambda t: t.id)
            index.by_edition_id[sid] = premiere
            index.doublons[sid] = autres
        elif len(ts) > 1:
            index.ids_partages[sid] = ts
        else:
            index.by_edition_id[sid] = ts[0]
    return index


@dataclass
class Rapprochement:
    """Verdict de `rapprocher` pour UNE ligne Kworb."""

    track: object = None
    via: str | None = None
    score: float = 0.0
    #: Le morceau SOUCHE quand la ligne est une rendition à rattacher (auto ou
    #: mémorisée) ; `track` reste None.
    rendition_de: object = None
    #: Proposition pour le dialogue (remix, ou suggestion floue) — rien n'est écrit.
    suggestion: dict | None = None
    #: Pourquoi rien : id_partage | ambigu | rejete | ignore | aucun.
    motif: str | None = None


def sommer_editions(lignes: list[dict], tolerance: float = 0.02) -> dict:
    """Total d'un morceau qui a PLUSIEURS lignes Kworb — sommées, sauf doublons purs.

    Décision utilisateur (2026-09-21) après mesure : sur 67 paires de lignes au
    même titre, 64 ont des compteurs DISTINCTS (uploads séparés — Runaway
    1,26 Md + 35 M ; tout le catalogue MC Solaar re-sorti en 2021) : toutes les
    écoutes de l'enregistrement, on ADDITIONNE. 3 sont des doublons purs
    (« La zone » 19 422 364 / 19 411 834 — le même compteur relevé deux jours
    différents), et les additionner double le morceau : une ligne dont le
    compteur est à moins de `tolerance` d'une ligne déjà retenue compte UNE fois.
    """
    tri = sorted(lignes, key=lambda x: -(x.get("streams") or 0))
    retenues: list[dict] = []
    ecartees: list[dict] = []
    for ligne in tri:
        v = ligne.get("streams") or 0
        if any(
            r["streams"] and abs(r["streams"] - v) <= tolerance * max(r["streams"], 1)
            for r in retenues
        ):
            ecartees.append(ligne)
        else:
            retenues.append(ligne)
    return {
        "streams": sum(r["streams"] or 0 for r in retenues),
        "daily": sum(r.get("daily") or 0 for r in retenues),
        "retenues": retenues,
        "ecartees": ecartees,
    }


def _departager_homonymes(lignes: list[dict], track, lire_identite) -> tuple[list, list]:
    """Plusieurs lignes Kworb sur un morceau : sont-elles bien SON enregistrement ?

    Deux uploads d'un même morceau (à sommer — décision utilisateur, 2026-09-21)
    et deux morceaux DIFFÉRENTS au même titre nu (« Forever » de Drake, 809 M,
    et « FOREVER » de Vultures 2) se présentent pareil : deux IDs, un titre.
    Ce qui les sépare est l'ARTISTE crédité par l'embed : une ligne dont aucun
    artiste attendu n'est crédité est un autre morceau, écartée (rendue, pour
    le rapport).

    La DURÉE ne départage PAS, et c'est mesuré : un catalogue re-sorti (MC
    Solaar 2021, Vultures 1 réédité) donne deux uploads du même morceau à
    durées différentes — les écarter perdait 21 morceaux chez Solaar et ~150 M
    de streams chez Kanye. Elle ne tranche que pour les titres GÉNÉRIQUES
    (« Intro », « Interlude », « Outro », « Skit »), où deux morceaux distincts
    du même artiste portent couramment le même mot : là, seules les lignes dont
    la durée concorde avec la ligne de base restent.

    Ne coûte une lecture que lorsqu'il y a litige : ≥ 2 lignes à IDs distincts
    et compteurs distincts (les compteurs ≈ identiques sont des doublons purs,
    `sommer_editions` s'en charge). Sans ID, on ne peut rien lire : on garde.
    """
    avec_id = [x for x in lignes if x.get("spotify_id")]
    if len({x["spotify_id"] for x in avec_id}) < 2:
        return lignes, []
    valeurs = sorted(x["streams"] or 0 for x in avec_id)
    if valeurs[-1] and (valeurs[-1] - valeurs[0]) <= 0.02 * valeurs[-1]:
        return lignes, []
    identites = {x["spotify_id"]: lire_identite(x["spotify_id"]) for x in avec_id}
    generique = bool(set(_normalize_title(track.title).split()) & MOTS_DE_MEME_MORCEAU)
    retenues, ecartees = [], []
    for x in lignes:
        identite = identites.get(x.get("spotify_id"))
        etranger = identite is not None and (
            artiste_etranger(track, identite)
            or (generique and not identite_concorde(track, identite)[0])
        )
        (ecartees if etranger else retenues).append(x)
    if not retenues:
        # Aucune ne concorde : on ne sait pas laquelle est la bonne, on garde
        # tout plutôt que d'écrire zéro (l'abstention vaut pour l'ÉCART, pas
        # pour le morceau entier).
        return lignes, []
    return retenues, ecartees


def _fiche_de_la_version(v, index: Index):
    """Une FICHE de cette version existe-t-elle déjà (« Nudes (Live at AK
    Studios) » chez Genius pour « Nudes - Acoustic » chez Spotify) ? Alors la
    ligne est À ELLE — un morceau, pas une variante du souche. Unique, sinon rien."""
    if v.kind != Kind.RENDITION:
        return None
    socle = _normalize_title(v.socle)
    fiches = [
        t
        for t in index.tracks
        if (tv := parse_variant(t.title)).kind == Kind.RENDITION
        and _normalize_title(tv.socle) == socle
        and meme_prise(tv, v)
    ]
    return fiches[0] if len(fiches) == 1 else None


def _candidats_remix_existants(v, index: Index) -> list:
    """Lignes de base qui SONT déjà ce remix : par relation `remix_of` vers le
    socle (Genius : « DCR (Dolce Camara Remix) » → Dolce Camara), ou par leur
    propre titre (« X (Remix) »)."""
    socle = _normalize_title(v.socle)
    out = []
    for t in index.tracks:
        tv = parse_variant(t.title)
        if tv.est_remix and _normalize_title(tv.socle) == socle:
            out.append(t)
            continue
        for rel in getattr(t, "relationships", None) or []:
            if rel.get("type") == "remix_of" and _normalize_title(rel.get("title") or "") == socle:
                out.append(t)
                break
    return out


def _proposition_remix(entry, v, index: Index, artist, lire_identite, motifs) -> dict:
    """Le dict que le dialogue affiche pour un remix — jamais écrit d'office."""
    socle = _normalize_title(v.socle)
    parents = index.by_socle.get(socle) or index.by_title.get(socle) or []
    parent = parents[0] if len(parents) == 1 else None
    identite = lire_identite(entry["spotify_id"]) if entry.get("spotify_id") else None
    credited = [a for a in (identite or {}).get("artists") or [] if a]
    proposition = "tiers" if v.kind == Kind.REMIX_NAMED else "collab"
    if v.remixer and credited:
        remixeur_credite = any(names_match_as_words(v.remixer, a) for a in credited)
        deja_collaborateur = parent is not None and any(
            names_match_as_words(v.remixer, c.name) for c in parent.get_music_credits()
        )
        if remixeur_credite and not deja_collaborateur:
            motifs.append(f"« {v.remixer} » est crédité par Spotify et absent des crédits du socle")
    if entry.get("is_feature"):
        motifs.append("Kworb : l'artiste n'est pas premier crédité (*)")
        proposition = "tiers"
    existants = _candidats_remix_existants(v, index)
    if existants:
        motifs.append(
            "un morceau en base est déjà ce remix : " + " | ".join(t.title for t in existants)
        )
    return {
        "kworb_title": entry["title"],
        "streams": entry["streams"],
        "daily": entry["daily_streams"],
        "spotify_id": entry.get("spotify_id"),
        "is_feature": bool(entry.get("is_feature")),
        "kind": v.kind.value,
        "socle": v.socle,
        "remixer": v.remixer,
        "parent_track_id": parent.id if parent else None,
        "parent_title": parent.title if parent else None,
        "credited": credited,
        "identite": identite,
        "existants": [(t.id, t.title) for t in existants],
        "proposition": "existant" if len(existants) == 1 else proposition,
        "motifs": motifs,
        # Champs de la suggestion historique, pour un dialogue qui ne saurait
        # pas encore les nouveaux.
        "track_id": existants[0].id if len(existants) == 1 else (parent.id if parent else None),
        "db_title": (
            existants[0].title if len(existants) == 1 else (parent.title if parent else None)
        ),
        "score": 0.0,
    }


def rapprocher(entry, index: Index, artist, decisions: dict, lire_identite) -> Rapprochement:
    """À quel morceau de la base appartient cette ligne Kworb ?

    Niveaux, du plus sûr au moins sûr :
      ① ID partagé par deux lignes de l'artiste ⇒ rien (défaut C) ;
      ② ID connu comme ÉDITION ⇒ le morceau ; comme RENDITION ⇒ la variante
         (sauf si le titre Kworb désigne désormais une ligne de base à part
         entière : la variante est devenue morceau, l'ID est à elle) ;
      ③ titre exact unique / homonymes départagés par les artistes crédités /
         flou à candidat unique / confirmation mémorisée — inchangé ;
      ④ sinon le DESCRIPTEUR de version tranche : rien ⇒ suggestion floue ;
         RENDITION dont une FICHE de même famille existe ⇒ ce morceau ; sinon à
         socle unique ⇒ rattachement automatique au souche ; REMIX ⇒ jamais
         automatique, proposition pour le dialogue (décision mémorisée ensuite).
    """
    sid = entry.get("spotify_id")
    norm = _normalize_title(entry["title"])
    if sid and sid in index.ids_partages:
        return Rapprochement(motif="id_partage")
    if sid and sid in index.by_edition_id:
        return Rapprochement(track=index.by_edition_id[sid], via="id")
    if sid and sid in index.by_rendition_id:
        devenue = index.by_title.get(norm) or []
        if len(devenue) == 1:
            return Rapprochement(track=devenue[0], via="id")
        fiche = _fiche_de_la_version(parse_variant(entry["title"]), index)
        if fiche is not None:
            return Rapprochement(track=fiche, via="fiche")
        return Rapprochement(track=index.by_rendition_id[sid], via="rendition_id")

    candidates = index.by_title.get(norm, [])
    if len(candidates) == 1:
        return Rapprochement(track=candidates[0], via="title")
    if len(candidates) > 1:
        # Homonymes (« MEILLEUR » Souffrance / « Meilleur » Goldee Money) :
        # les artistes crédités du track Spotify départagent.
        identite = lire_identite(sid) if sid else None
        credited_norm = {_normalize_title(a) for a in (identite or {}).get("artists") or [] if a}
        track = _resolve_homonym(candidates, artist.name, credited_norm)
        if track:
            return Rapprochement(track=track, via="title+artistes")
        return Rapprochement(motif="ambigu")

    ftrack, fscore = _fuzzy_unique(entry["title"], index.tracks)
    if ftrack:
        return Rapprochement(track=ftrack, via="fuzzy", score=fscore)

    confirmed = decisions.get("confirmed", {})
    if norm in confirmed:
        track = index.by_id.get(confirmed[norm])
        if track:
            return Rapprochement(track=track, via="confirmed")

    decision = (decisions.get("decisions") or {}).get(norm)
    if decision:
        kind, tid = decision.get("kind"), decision.get("track_id")
        cible = index.by_id.get(tid) if tid else None
        if kind == "ignore":
            return Rapprochement(motif="ignore")
        if kind == "rendition" and cible is not None:
            return Rapprochement(rendition_de=cible, via="decision")
        if cible is not None:
            return Rapprochement(track=cible, via="decision")
        # Décision orpheline (morceau supprimé) : on retombe sur la proposition.

    v = parse_variant(entry["title"])
    rejete = norm in decisions.get("rejected", [])
    if v.kind == Kind.NONE:
        if rejete:
            return Rapprochement(motif="rejete")
        cand, score = _best_candidate(entry["title"], index.tracks)
        if cand:
            return Rapprochement(
                suggestion={
                    "kworb_title": entry["title"],
                    "streams": entry["streams"],
                    "daily": entry["daily_streams"],
                    "track_id": cand.id,
                    "db_title": cand.title,
                    "score": score,
                }
            )
        return Rapprochement(motif="aucun")

    motifs = []
    if rejete:
        # L'ancien dialogue posait une AUTRE question (« même morceau ? ») :
        # un rejet n'a pas répondu à celle-ci. Reproposé UNE fois — la décision
        # prise ferme la porte.
        motifs.append("précédemment rejeté (ancienne question « même morceau ? »)")
    if v.kind == Kind.RENDITION:
        socle = _normalize_title(v.socle)
        fiche = _fiche_de_la_version(v, index)
        if fiche is not None:
            return Rapprochement(track=fiche, via="fiche")
        parents = index.by_socle.get(socle, [])
        if len(parents) == 1 and sid and not rejete:
            return Rapprochement(rendition_de=parents[0], via="rendition_auto")
        parent = parents[0] if len(parents) == 1 else None
        if parent is None and not parents:
            return Rapprochement(motif="aucun")
        return Rapprochement(
            suggestion={
                "kworb_title": entry["title"],
                "streams": entry["streams"],
                "daily": entry["daily_streams"],
                "spotify_id": sid,
                "is_feature": bool(entry.get("is_feature")),
                "kind": v.kind.value,
                "socle": v.socle,
                "remixer": None,
                "parent_track_id": parent.id if parent else None,
                "parent_title": parent.title if parent else None,
                "credited": [],
                "identite": None,
                "existants": [],
                "proposition": "rendition" if parent else "ignore",
                "motifs": motifs
                + ([] if parent else [f"{len(parents)} morceaux portent ce socle"]),
                "track_id": parent.id if parent else None,
                "db_title": parent.title if parent else None,
                "score": 0.0,
            }
        )
    return Rapprochement(
        suggestion=_proposition_remix(entry, v, index, artist, lire_identite, motifs)
    )


def _ecrire_rendition(data_manager, parent, entry, kworb_date, result, via) -> None:
    ok = data_manager.record_variant_streams(
        parent.id,
        entry.get("spotify_id"),
        entry["streams"],
        entry["daily_streams"],
        kworb_date,
        label=entry["title"],
    )
    if ok:
        result["renditions_rattachees"].append((entry["title"], parent.title, entry["streams"]))
        logger.info(
            f"🎚️ Rendition ({via}) : Kworb '{entry['title']}' → « {parent.title} » "
            f"({entry['streams']:,} streams, hors total)"
        )
    else:
        result["unmatched"] += 1
        result["unmatched_titles"].append(entry["title"])
        result["unmatched_details"].append((entry["title"], entry["streams"]))


def _scrape_validated(scraper, artist, data_manager, spotify_artist_id):
    """Scrape la page songs et valide l'identité. Re-vote une fois si mismatch.

    Returns:
        (page_songs, spotify_artist_id_corrigé) — page_songs=None si échec/identité fausse.
    """
    page = scraper.scrape_songs(spotify_artist_id)
    page_name = page.get("artist_name") if page else None

    if page and page["entries"] and _names_match(page_name, artist.name):
        return page, spotify_artist_id

    if page and page_name and not _names_match(page_name, artist.name):
        logger.warning(
            f"🚨 Page Kworb de {spotify_artist_id} = '{page_name}' ≠ '{artist.name}' "
            f"— ID artiste erroné (homonyme/collab), re-vote sans feats"
        )
    else:
        logger.warning(f"⚠️ Page Kworb vide/absente pour {spotify_artist_id} — re-vote")

    revoted = _vote_artist_spotify_id(artist, data_manager)
    if not revoted or revoted == spotify_artist_id:
        return None, spotify_artist_id

    page = scraper.scrape_songs(revoted)
    page_name = page.get("artist_name") if page else None
    if page and page["entries"] and _names_match(page_name, artist.name):
        logger.info(f"🔁 ID artiste corrigé: {spotify_artist_id} → {revoted}")
        data_manager.update_artist_spotify_id(artist.id, revoted)
        artist.spotify_id = revoted
        return page, revoted

    if page_name:
        logger.error(f"🚨 Après re-vote, page = '{page_name}' ≠ '{artist.name}' — abandon")
    return None, spotify_artist_id


def update_kworb_streams(artist, data_manager, scraper=None, lire_identite=None) -> dict:
    """Scrape kworb.net et met à jour les streams des morceaux et albums de l'artiste.

    Args:
        artist: objet Artist avec au moins `id`, `name`, `spotify_id`
        data_manager: instance de DataManager
        scraper: KworbScraper injecté (StreamsProvider) ; créé en interne si None
        lire_identite: lecteur d'identité Spotify (embed) injectable ; par défaut
            `lire_identite_http` — sert aux homonymes, aux remix, au backfill.

    Returns:
        dict résumé {matched, unmatched, albums_updated, unmatched_titles,
                     matched_by_id, matched_by_title, spotify_ids_backfilled,
                     albums_excluded, artist_name, kworb_updated}
    """
    result = {
        "matched": 0,
        "unmatched": 0,
        "albums_updated": 0,
        "unmatched_titles": [],
        "unmatched_details": [],  # [(titre kworb, streams)] triés desc — pour la GUI
        "matched_by_id": 0,
        "matched_by_title": 0,
        "matched_by_fuzzy": 0,
        "fuzzy_matched": [],  # [(titre kworb, titre base, score)] — à vérifier côté GUI
        # Rapprochements INCERTAINS (bande sous le seuil auto) : NON écrits,
        # à confirmer/rejeter par l'utilisateur (mémorisé ensuite).
        "suggestions": [],  # [{kworb_title, streams, daily, track_id, db_title, score}]
        "spotify_ids_backfilled": 0,
        "albums_excluded": [],
        "artist_name": None,
        "kworb_updated": None,
        # 2026-09-21 — ce que le rapprochement SIGNALE (cf. `rapprocher`) :
        "ids_partages": [],  # [(spotify_id, [titres])] : un ID sur 2 lignes, rien écrit
        "doublons_evidents": [],  # [(spotify_id, [titres])] : écrit sur la 1ʳᵉ fiche, à fusionner
        "renditions_rattachees": [],  # [(titre kworb, titre parent, streams)]
        "variantes_suspectes": [],  # [(titre base, titre spotify, id)] : ID mal attribué ?
        "multi_lignes": [],  # [(titre, n lignes, n comptées, total)]
        "lignes_ecartees": [],  # [(titre kworb, streams, titre base)] : autre enregistrement
    }

    # ── 1. S'assurer que l'ID Spotify artiste est disponible ──────────────────
    spotify_artist_id: str | None = getattr(artist, "spotify_id", None)

    if not spotify_artist_id:
        logger.info(f"spotify_id manquant pour '{artist.name}' — vote sur les pages tracks")
        spotify_artist_id = _vote_artist_spotify_id(artist, data_manager)
        if spotify_artist_id:
            data_manager.update_artist_spotify_id(artist.id, spotify_artist_id)
            artist.spotify_id = spotify_artist_id
            logger.info(f"✅ spotify_id artiste récupéré et stocké: {spotify_artist_id}")
        else:
            logger.error(f"❌ Impossible de récupérer l'ID Spotify de '{artist.name}'. Abandon.")
            return result

    logger.info(f"🎵 Mise à jour Kworb pour '{artist.name}' (spotify_id={spotify_artist_id})")

    if scraper is None:
        scraper = KworbScraper()

    # ── 2. Page songs + VALIDATION D'IDENTITÉ ─────────────────────────────────
    page_songs, spotify_artist_id = _scrape_validated(
        scraper, artist, data_manager, spotify_artist_id
    )

    if not page_songs:
        logger.error("❌ Impossible d'obtenir une page Kworb validée. Aucune écriture.")
        return result

    result["artist_name"] = page_songs["artist_name"]
    kworb_date = page_songs["last_updated"]
    result["kworb_updated"] = kworb_date.strftime("%Y-%m-%d") if kworb_date else None

    # ── 3. Totaux artiste (tableau récap) ─────────────────────────────────────
    summary = page_songs.get("summary") or {}
    streams_sum = summary.get("streams") or {}
    daily_sum = summary.get("daily") or {}
    if streams_sum.get("total"):
        data_manager.update_artist_kworb_totals(
            artist.id,
            total=streams_sum.get("total"),
            daily=daily_sum.get("total"),
            lead=streams_sum.get("as_lead"),
            feat=streams_sum.get("as_feature"),
            kworb_date=kworb_date,
        )
        logger.info(
            f"📊 Totaux Kworb: {streams_sum.get('total'):,} streams "
            f"({daily_sum.get('total') or 0:,}/jour)"
        )

    # ── 4. Streams des morceaux ───────────────────────────────────────────────
    tracks = data_manager.get_artist_tracks(artist.id)
    index = construire_index(tracks)
    lire_identite = lire_identite or lire_identite_http
    result["ids_partages"] = [
        (sid, sorted(t.title for t in ts)) for sid, ts in sorted(index.ids_partages.items())
    ]
    for sid, titres in result["ids_partages"]:
        logger.warning(
            f"⚠️ ID Spotify {sid} porté par {len(titres)} morceaux de l'artiste "
            f"({' | '.join(titres)}) — aucune ligne Kworb ne lui sera attribuée"
        )
    result["doublons_evidents"] = [
        (sid, sorted([index.by_edition_id[sid].title] + [t.title for t in ts]))
        for sid, ts in sorted(index.doublons.items())
    ]

    # Décisions mémorisées (confirmé/rejeté/décidé) pour ne pas redemander
    try:
        from src.utils.kworb_links_manager import KworbLinksManager

        _links = KworbLinksManager()
        _decisions = _links.load(artist.name)
    except (OSError, ValueError):
        _decisions = {"confirmed": {}, "rejected": [], "decisions": {}}

    # Accumulation par track : un morceau peut avoir PLUSIEURS lignes Kworb
    # (éditions, uploads séparés) — sommées SAUF doublons purs, cf. `sommer_editions`.
    agg: dict[int, list[dict]] = {}

    for entry in page_songs["entries"]:
        r = rapprocher(entry, index, artist, _decisions, lire_identite)

        if r.track is not None and r.via == "rendition_id":
            # L'ID est connu comme RENDITION du morceau : son compteur va sur la
            # ligne de la variante, jamais sur le parent.
            _ecrire_rendition(data_manager, r.track, entry, kworb_date, result, r.via)
            continue
        if r.rendition_de is not None:
            _ecrire_rendition(data_manager, r.rendition_de, entry, kworb_date, result, r.via)
            continue
        if r.suggestion is not None:
            result["suggestions"].append(r.suggestion)
            logger.info(
                f"❓ Proposition ({r.suggestion.get('proposition') or 'même morceau ?'}) : "
                f"Kworb '{entry['title']}' — à confirmer"
            )
            continue
        if r.track is None:
            if r.motif == "id_partage":
                pass  # signalé une fois pour toutes ci-dessus
            elif r.motif == "ambigu":
                logger.warning(f"⚠️ Titre ambigu NON résolu, passé: '{entry['title']}'")
            elif r.motif != "rejete":
                logger.debug(f"⚠️ Pas de match en DB: '{entry['title']}'")
            if r.motif != "id_partage":
                result["unmatched"] += 1
                result["unmatched_titles"].append(entry["title"])
                result["unmatched_details"].append((entry["title"], entry["streams"]))
            continue

        track, via = r.track, r.via
        if via == "fiche" and entry.get("spotify_id"):
            # La version a sa propre fiche : ses streams sont à elle. Le SOUCHE
            # garde quand même l'indication (décision utilisateur), avec le
            # pointeur vers la fiche — affichée sans compter.
            socle = _normalize_title(parse_variant(entry["title"]).socle)
            parents = index.by_socle.get(socle) or []
            parent = index.by_rendition_id.get(entry["spotify_id"]) or (
                parents[0] if len(parents) == 1 else None
            )
            if parent is not None and parent.id != track.id:
                data_manager.record_variant_streams(
                    parent.id,
                    entry["spotify_id"],
                    entry["streams"],
                    entry["daily_streams"],
                    kworb_date,
                    label=entry["title"],
                    variant_track_id=track.id,
                )
        if via == "fuzzy":
            result["fuzzy_matched"].append((entry["title"], track.title, r.score))
            logger.info(
                f"≈ Match flou ({r.score:.0%}): Kworb '{entry['title']}' → base '{track.title}'"
            )
        elif via == "title+artistes":
            logger.info(
                f"🔎 Homonyme résolu par artistes crédités: '{entry['title']}' "
                f"→ track #{track.id} ({getattr(track, 'primary_artist_name', None) or artist.name})"
            )
        elif via in ("confirmed", "decision"):
            logger.info(f"🔗 Kworb (décision mémorisée): '{entry['title']}' → '{track.title}'")
        if via == "id" and not meme_famille(
            parse_variant(entry["title"]), parse_variant(track.title)
        ):
            # L'ID en base dit « ce morceau », son titre Spotify dit une AUTRE
            # version : c'est la signature d'un ID mal attribué (« Heartless
            # (Remix) » qui porte l'ID de « Heartless »). On écrit ce que l'ID
            # affirme — c'est la clé — mais on le DIT, et la vérification des
            # identifiants Spotify (motif « variante ») le répare.
            result["variantes_suspectes"].append((track.title, entry["title"], entry["spotify_id"]))
            logger.warning(
                f"🔀 ID {entry['spotify_id']} : la base attend « {track.title} », Spotify "
                f"sert « {entry['title']} » — à vérifier (identifiants Spotify)"
            )

        # Backfill du Spotify ID depuis le lien Kworb (jamais d'écrasement).
        # L'if interne est volontairement séparé : c'est une écriture DB dont
        # le résultat conditionne la suite, pas une simple condition.
        if (  # noqa: SIM102
            via != "id"
            and entry.get("spotify_id")
            and not getattr(track, "spotify_id", None)
            # Kworb rapproche par titre avant de livrer son lien : l'ID qu'il
            # propose peut désigner un autre morceau (1 cas sur les 130 qu'il
            # a posés). Une requête n'est dépensée que lorsqu'un ID est sur le
            # point d'être écrit.
            and valider_identite(track, entry["spotify_id"], lire_identite=lire_identite)
        ):
            if data_manager.update_track_spotify_id(track.id, entry["spotify_id"]):
                track.spotify_id = entry["spotify_id"]
                result["spotify_ids_backfilled"] += 1

        agg.setdefault(track.id, []).append(
            {
                "title": entry["title"],
                "spotify_id": entry.get("spotify_id"),
                "streams": entry["streams"],
                "daily": entry["daily_streams"] or 0,
                "via": via,
            }
        )
        result["matched"] += 1
        _key = {"id": "matched_by_id", "fuzzy": "matched_by_fuzzy"}.get(via, "matched_by_title")
        result[_key] += 1
        logger.debug(f"✅ Match ({via}): '{entry['title']}' → {entry['streams']:,} streams")

    agg_by_track: dict[int, dict] = {}
    for track_id, lignes in agg.items():
        track = index.by_id[track_id]
        retenues, ecartees = _departager_homonymes(lignes, track, lire_identite)
        for ligne in ecartees:
            result["lignes_ecartees"].append((ligne["title"], ligne["streams"], track.title))
            result["unmatched"] += 1
            result["unmatched_titles"].append(ligne["title"])
            result["unmatched_details"].append((ligne["title"], ligne["streams"]))
            result["matched"] -= 1
            logger.warning(
                f"⚠️ Ligne Kworb '{ligne['title']}' ({ligne['streams']:,}) écartée : autre "
                f"enregistrement que « {track.title} » (durée)"
            )
        somme = sommer_editions(retenues)
        agg_by_track[track_id] = {"streams": somme["streams"], "daily": somme["daily"]}
        # Kworb déclare ce qu'il a vu ; c'est le repository qui ARBITRE la valeur
        # de la colonne à partir de toutes les observations du morceau. Cet
        # updater n'a donc pas à savoir s'il est maître — et l'ordre des sources
        # n'a aucun effet sur le résultat.
        data_manager.record_spotify_streams(
            track_id,
            somme["streams"],
            "kworb",
            updated_at=kworb_date,
            daily_streams=somme["daily"],
        )
        if len(retenues) > 1 or somme["ecartees"]:
            result["multi_lignes"].append(
                (track.title, len(retenues), len(somme["retenues"]), somme["streams"])
            )
            logger.info(
                f"🎛️ '{track.title}': {len(retenues)} lignes Kworb, "
                f"{len(somme['retenues'])} comptées → {somme['streams']:,}"
            )

    result["unmatched_details"].sort(key=lambda x: x[1], reverse=True)
    logger.info(
        f"Songs Kworb: {result['matched']} matchés "
        f"({result['matched_by_id']} par ID, {result['matched_by_title']} par titre, "
        f"{result['spotify_ids_backfilled']} ID backfillés), "
        f"{result['unmatched']} non matchés"
    )
    if result["unmatched_titles"]:
        logger.warning(f"Titres non matchés: {result['unmatched_titles']}")

    # ── 5. Albums : agrégation des éditions + filtre albums propres ───────────
    page_albums = scraper.scrape_albums(spotify_artist_id)
    if page_albums and page_albums["entries"]:
        # Albums connus en base : titre normalisé → nb de morceaux dessus
        album_track_counts = defaultdict(int)
        for t in tracks:
            if getattr(t, "album", None):
                album_track_counts[_normalize_title(t.album)] += 1

        # Streams des MORCEAUX de chaque album, tels qu'on vient de les écrire.
        # C'est la seule somme juste (cf. plus bas) : chaque enregistrement y
        # compte UNE fois, quel que soit le nombre d'éditions qui le portent.
        par_album = defaultdict(lambda: {"streams": 0, "daily": 0})
        for track in tracks:
            piste = agg_by_track.get(track.id)
            if not piste or not getattr(track, "album", None):
                continue
            cumul = par_album[_normalize_title(track.album)]
            cumul["streams"] += piste["streams"]
            cumul["daily"] += piste["daily"] or 0

        # Les lignes d'album de Kworb ne servent plus qu'à nommer le disque et à
        # collecter les IDs de ses éditions.
        #
        # ⚠️ Leurs STREAMS ne sont volontairement PAS sommés (corrigé le
        # 2026-09-05). Kworb liste une ligne par édition, mais ses compteurs sont
        # ceux de Spotify, qui sont CUMULÉS PAR ENREGISTREMENT : une réédition ne
        # repart pas de zéro. Les deux lignes d'un album réédité comptent donc les
        # mêmes titres, et les additionner les compte deux fois. Mesuré sur
        # « Bitume Caviar (vol.1) » : la ligne de l'édition originale (46 379 590)
        # est INTÉGRALEMENT constituée des 11 titres que la réédition reprend, et
        # la somme des deux lignes donnait 99 206 484 pour un total réel de
        # 50 342 979.
        editions = defaultdict(lambda: {"title": None, "streams": 0, "daily": 0, "ids": []})
        for entry in page_albums["entries"]:
            brut = _normalize_title(entry["title"])
            # Une ligne « … (Bonus) » est une ÉDITION d'un album connu, pas un
            # autre disque : sans ce rattachement elle partait dans son propre
            # groupe, était écartée faute de morceaux en base, et son IDENTIFIANT
            # d'édition se perdait — le scrape Spotify ne pouvait alors plus la
            # totaliser (constaté sur « DOM PERIGNON CRYING (Bonus) », dont
            # l'album ressortait ~10 % sous son vrai total).
            key = base_album_key(brut, album_track_counts) or brut
            agg = editions[key]
            if agg["title"] is None or brut == key:
                agg["title"] = entry["title"]
            if entry.get("spotify_id"):
                agg["ids"].append(entry["spotify_id"])
        for key, agg in editions.items():
            agg["streams"] = par_album.get(key, {}).get("streams", 0)
            agg["daily"] = par_album.get(key, {}).get("daily", 0)

        for key, agg in editions.items():
            n_tracks = album_track_counts.get(key, 0)
            if n_tracks < 2:
                # Simple apparition (ex. XX5 de Dinos avec 1 feat) — les streams
                # du morceau sont déjà comptés au niveau track. Les projets
                # communs (Bitume Caviar…) ont ≥2 morceaux en base → gardés.
                result["albums_excluded"].append(agg["title"])
                logger.info(
                    f"⏭️ Album écarté (apparition, {n_tracks} morceau en base): "
                    f"'{agg['title']}'"
                )
                continue
            if not agg["streams"]:
                # Aucun morceau de cet album n'a de compteur ce run : écrire 0
                # effacerait un total valide par une valeur qui n'en est pas une.
                logger.info(f"⏭️ Album sans morceau chiffré, total inchangé : '{agg['title']}'")
                continue
            ok = data_manager.upsert_album(
                artist.id,
                agg["title"],
                agg["streams"],
                agg["daily"],
                spotify_album_ids=",".join(agg["ids"]) or None,
                updated_at=page_albums["last_updated"] or kworb_date,
            )
            if ok:
                result["albums_updated"] += 1
                if len(agg["ids"]) > 1:
                    logger.info(
                        f"💿 '{agg['title']}': {len(agg['ids'])} éditions, comptées une "
                        f"seule fois → {agg['streams']:,} streams"
                    )

    logger.info(
        f"Albums Kworb: {result['albums_updated']} mis à jour, "
        f"{len(result['albums_excluded'])} écartés (apparitions)"
    )

    return result


# ── CLI standalone ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    import argparse

    parser = argparse.ArgumentParser(description="Met à jour les streams Kworb pour un artiste")
    parser.add_argument("artist_name", help="Nom exact de l'artiste dans la DB")
    parser.add_argument(
        "--spotify-id",
        help="ID Spotify artiste (optionnel, sinon récupéré automatiquement)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )

    from src.utils.data_manager import DataManager

    dm = DataManager()
    artist = dm.get_artist_by_name(args.artist_name)

    if not artist:
        print(f"❌ Artiste '{args.artist_name}' non trouvé en base de données.")
        sys.exit(1)

    if args.spotify_id:
        artist.spotify_id = args.spotify_id

    summary = update_kworb_streams(artist, dm)
    print("\n── Résumé ──────────────────────────────────")
    print(f"Artiste (page Kworb) : {summary['artist_name']}  (maj {summary['kworb_updated']})")
    print(
        f"Morceaux matchés    : {summary['matched']} "
        f"({summary['matched_by_id']} par ID, {summary['matched_by_title']} par titre)"
    )
    print(f"Spotify IDs backfillés : {summary['spotify_ids_backfilled']}")
    print(f"Morceaux non matchés: {summary['unmatched']}")
    print(
        f"Albums mis à jour   : {summary['albums_updated']} "
        f"(+{len(summary['albums_excluded'])} apparitions écartées)"
    )
    if summary["unmatched_titles"]:
        print(f"Titres non matchés  : {summary['unmatched_titles']}")
