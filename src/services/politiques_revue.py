"""Politiques d'arbitrage AUTOMATIQUE des décisions « À trancher » — PROPOSÉES.

Demande utilisateur (2026-10-19) : « 200 points ou plus n'est ni traitable à
la main ni viable » — arbitrer seul le plus possible, ne garder à la main que
l'essentiel. Une politique par famille de décision : elle dit, pour un cas,
dans quel SENS elle le trancherait, et par quelle action (celle du panneau,
`revue_actions`, quand elle existe).

RIEN ici n'est branché : règle projet « simuler avant d'activer ». Le passage
à blanc (`scripts/simuler_politiques_revue.py`) liste, sur toute la base copiée,
ce que chaque politique trancherait ; l'utilisateur valide famille par
famille ; une politique validée devient un détecteur FORMEL (`Detecteur.formel`
+ un correcteur de `revue_auto`, journal `revue_corrections` réversible).

Chaque politique est PURE : `(cas, fiche, contexte) -> Arbitrage | None`
(None = elle ne tranche pas, le cas reste à l'humain).
"""

from __future__ import annotations

import functools
import re
from collections.abc import Callable
from dataclasses import dataclass

from src.services import revue

# `titre_d_original` vit chez le juge des titres depuis le 2026-10-31 : le
# verdict de durée (`reconcile`) le consulte aussi.
from src.utils.version_descriptors import (
    titre_d_original,
    titre_d_original_a_la_mention_pres,
    titre_generique,
)
from src.utils.vote_durees import FRAGILES, ISOLEE, SOUTENUE, TOLERANCE_S, famille, voter

#: Écart au-delà duquel deux durées ne sont plus une édition du même fichier :
#: la même tolérance que deux catalogues (`vote_durees.TOLERANCE_S`).
ECART_FICHIER_S = 2

#: Écart minimal pour une action DESTRUCTIVE (retirer un ID, délier, retirer
#: SongBPM) : au-dessous, deux éditions du même enregistrement diffèrent encore
#: (6 s mesurés le 2026-09-29) — le vote dit « autre fichier », pas « autre
#: enregistrement ».
ECART_VERSION_S = 10

#: Un ID Spotify démenti à `ECART_VERSION_S` au plus est une édition : ✓ Normal
#: s'il ne tient pas la colonne (2026-10-27). Interrupteur simulé par
#: `scripts/simuler_durees_panneau.py`.
EDITION_SPOTIFY_NORMALE = True


@dataclass(frozen=True)
class Arbitrage:
    #: Ce qui est tranché, en clair (« la durée Deezer est fausse : délier »).
    sens: str
    #: Le code d'action qui l'appliquerait (`revue_actions`), « normal » pour
    #: un « ✓ Normal » (aucune donnée ne change), "" si aucune n'existe encore.
    action: str
    raison: str


@dataclass(frozen=True)
class Politique:
    code: str
    detecteurs: tuple[str, ...]
    libelle: str
    juger: Callable


# ── Durées ──────────────────────────────────────────────────────────────────


#: Source lue par une RECHERCHE de paroles (titre + artiste), sans identifiant ;
#: `_HIT` la renomme hors de la famille `youtube` du vote quand elle est jugée.
_HIT_DE_RECHERCHE = "ytmusic"
_HIT = "ytmusic_recherche"


def _dementie(durees: dict, source: str, editions, minimum: int):
    """La valeur de `source` est démentie FORMELLEMENT : au moins `minimum`
    catalogues SOLIDES d'autres familles la contredisent, ils s'accordent entre
    eux (à la tolérance du vote), aucun autre catalogue ne la soutient. Rend la
    durée commune des contradicteurs, sinon None."""
    audio = durees.get(revue.SOURCE_AUDIO)
    if (
        source == _HIT_DE_RECHERCHE
        and durees.get(source)
        and audio
        and abs(int(audio) - int(durees[source])) > TOLERANCE_S
    ):
        # La piste YTM est le HIT d'une recherche de paroles par titre, l'audio
        # Topic une vidéo lue par son ID : en désaccord, ce sont deux fichiers,
        # et l'audio dément le hit malgré leur famille commune (Kanye
        # « Monster » #11401, 2026-10-29 : YTM 150 s, Topic et Apple 379 s —
        # sans lui, Apple restait seul et le cas allait à l'humain).
        durees = {**{s: d for s, d in durees.items() if s != source}, _HIT: durees[source]}
        source = _HIT
    valeur = durees.get(source)
    vote = voter(durees, valeur, editions=editions) if valeur else None
    if vote is None or vote.nature != ISOLEE or "manual" in durees:
        return None
    # Un catalogue en désaccord avec LUI-MÊME (page Spotify 241 s, ReccoBeats
    # 255 s) ne prouve rien, ni pour ni contre (« Guess Who's Back »).
    douteuses = {
        famille(s)
        for s, d in durees.items()
        if d and s not in FRAGILES and abs(int(d) - int(valeur)) <= TOLERANCE_S
    }
    contre = {f: d for f, d in vote.contre.items() if f != famille(source) and f not in douteuses}
    if len(contre) < minimum or vote.pour - {famille(source)}:
        return None
    if max(contre.values()) - min(contre.values()) > TOLERANCE_S:
        return None
    return sorted(contre.values())[len(contre) // 2]


def _durees(track) -> dict:
    return revue.durees_du_vote(track)


def _colonne_impactee(track, source: str) -> bool:
    return track.duration_source == source


def politique_durees(minimum: int) -> Callable:
    """Le vote des catalogues désigne la valeur fausse ; il est FORMEL quand
    `minimum` catalogues solides, d'accord entre eux, la démentent."""

    def juger(cas, track, _ctx):
        if track is None:
            return None
        durees, editions = _durees(track), tuple(track.durees_editions or ())
        d = cas.detecteur
        if d == "duree_spotify":
            presents = [s for s in ("spotify_web", "reccobeats") if durees.get(s)]
            dementis = {s: _dementie(durees, s, editions, minimum) for s in presents}
            faux = [s for s, v in dementis.items() if v is not None]
            if not faux:
                return None
            if len(faux) == len(presents):
                ref = dementis[faux[0]]
                if abs(durees[faux[0]] - ref) <= ECART_VERSION_S:
                    # Une ÉDITION du même enregistrement (décision utilisateur
                    # 2026-10-19 : radio edit, fondu retouché = une fiche) : ses
                    # streams comptent, et hors colonne rien de faux n'est
                    # affiché (2026-10-27, « Viens danser avec les Lunatic »
                    # 178 s contre 170 s partout).
                    if not EDITION_SPOTIFY_NORMALE or any(
                        _colonne_impactee(track, s) for s in faux
                    ):
                        return None
                    return Arbitrage(
                        "l'ID Spotify est une édition du même enregistrement : ✓ Normal",
                        "normal",
                        f"{', '.join(f'{s} {durees[s]} s' for s in faux)} contre {ref} s, "
                        f"à {ECART_VERSION_S} s au plus",
                    )
                return Arbitrage(
                    "l'ID Spotify désigne un autre enregistrement : retirer l'ID",
                    "retirer_spotify",
                    f"{', '.join(f'{s} {durees[s]} s' for s in faux)} contre {ref} s partout",
                )
            # ReccoBeats lit d'abord par l'ISRC : son accord avec les
            # catalogues ne prouve pas l'ID, seulement que la page de l'ID dit
            # autre chose. Rien n'est retiré. Il n'est d'ailleurs pas compté
            # au démenti (même famille `spotify` que la page, `_dementie`) :
            # ce sont `minimum` catalogues INDÉPENDANTS qui démentent la page.
            # Décision utilisateur 2026-11-23 : la colonne durée suit alors
            # ces catalogues, pas la page démentie. C'est déjà l'ordre de
            # `DISCOGRAPHY_PRIORITIES` : tout catalogue indépendant qui peut
            # démentir passe devant `spotify_web`, sauf une famille au plus
            # (garde : `test_la_page_spotify_dementie_ne_tient_jamais_la_colonne`).
            # Une colonne qui tiendrait encore la page ne peut être qu'un
            # verdict périmé (rematérialiser) : l'humain le voit.
            juste = next(s for s in presents if s not in faux)
            return Arbitrage(
                f"{faux[0]} {durees[faux[0]]} s démentie ; {juste} {durees[juste]} s = les "
                "catalogues : ID d'une autre édition, ou lecture fausse"
                + (
                    " — elle tient la colonne : verdict périmé, à rematérialiser"
                    if _colonne_impactee(track, faux[0])
                    else ""
                ),
                # Hors colonne, rien de faux n'est affiché ni compté : ✓ Normal.
                "" if _colonne_impactee(track, faux[0]) else "normal",
                f"démentie par {minimum}+ catalogues d'accord entre eux",
            )
        if d == "duree_deezer":
            ref = _dementie(durees, "deezer", editions, minimum)
            if ref is None or abs(durees["deezer"] - ref) <= ECART_VERSION_S:
                return None
            return Arbitrage(
                "le lien Deezer est une autre version : délier",
                "delier_deezer",
                f"Deezer {durees['deezer']} s contre {ref} s",
            )
        if d == "duree_isolee":
            # Décision utilisateur 2026-10-20 : pour un ORIGINAL, une divergence de
            # durée ne dit pas grand-chose ; une version (remix, live…) ou un
            # titre générique (« Intro », « Outro ») se vérifie à la main.
            from src.utils.version_descriptors import porte_une_version, titre_generique

            if porte_une_version(track.title) or titre_generique(track.title):
                return None
            for source in revue._TEMOINS_ISOLABLES:
                if not durees.get(source):
                    continue
                ref = _dementie(durees, source, editions, minimum)
                if ref is None:
                    continue
                if _colonne_impactee(track, source):
                    return Arbitrage(
                        f"témoin {source} d'une autre version, et il tient la colonne",
                        "",
                        f"{source} {durees[source]} s contre {ref} s",
                    )
                return Arbitrage(
                    f"témoin {source} d'une autre version ; la colonne garde {track.duration} s",
                    "normal",
                    f"{source} {durees[source]} s contre {ref} s",
                )
            return None
        if d == "duree_songbpm":
            ref = _dementie(durees, "songbpm", editions, minimum)
            if ref is None or abs(durees["songbpm"] - ref) <= ECART_VERSION_S:
                return None
            return Arbitrage(
                "la page SongBPM est une autre version : retirer SongBPM",
                "retirer_songbpm",
                f"SongBPM {durees['songbpm']} s contre {ref} s",
            )
        if d == "generique_duree":
            vote = voter(durees, track.duration, editions=editions)
            solides = (vote.pour if vote else set()) - {famille(s) for s in FRAGILES}
            if vote and vote.nature == SOUTENUE and len(solides) >= minimum:
                return Arbitrage(
                    "durée réelle, partagée par coïncidence : ✓ Normal",
                    "normal",
                    f"{track.duration} s confirmée par {', '.join(sorted(solides))}",
                )
            if set(durees) <= FRAGILES | {"legacy"} and durees.get("songbpm"):
                return Arbitrage(
                    "durée venue du seul SongBPM (titre générique) : retirer SongBPM",
                    "retirer_songbpm",
                    "aucun catalogue lu par un identifiant",
                )
        return None

    return juger


# ── ID Spotify proposé par SongBPM ──────────────────────────────────────────


#: Mots qui ne font pas un autre titre (articles, liaison, crédit).
_MOTS_VIDES = frozenset(
    {"the", "a", "an", "le", "la", "les", "de", "du", "des", "of", "and", "et", "feat", "ft"}
)


def _mots_de(texte: str | None) -> set[str]:
    import unicodedata

    t = unicodedata.normalize("NFKD", texte or "").encode("ascii", "ignore").decode().casefold()
    return set(re.findall(r"[a-z0-9]+", t))


def refus_id_propose(track, identite: dict) -> Arbitrage | None:
    """Refus À LA POSE d'un ID proposé (2026-10-31), quand les preuves
    MONTRENT qu'il n'est pas celui de la fiche :
      · IP2 — un catalogue lu par son identifiant (Apple, Deezer) s'écarte de
        la durée de l'ID de plus de `ECART_VERSION_S`, et aucune durée
        indépendante ne la confirme (« Pursuit of Happiness [Steve Aoki
        Remix] [Mixed] » : Apple 165 s, l'ID sert l'« Extended » de 374 s) ;
      · IP1 — Spotify sert un AUTRE titre (des mots en plus, « Je n'oublie
        pas » pour « Oublie », « Jacques Chirac 2.0 »), aucune durée
        indépendante (clip compris) ne confirme celle de l'ID, et les mots en
        plus ne sont pas dans l'album de la fiche (« Heartless - The Voice
        Performance » d'une fiche de *The Voice: Season 1* : sans doute juste).
    None : rien de montré, l'humain."""
    from src.services.revue import _titre_sans_feat
    from src.utils.version_descriptors import titres_equivalents

    nom, servie = identite.get("name") or "", identite.get("duration")
    durees = _durees(track)
    confirment = [
        s
        for s, d in durees.items()
        if s not in FRAGILES and s not in ("heritage", "reccobeats") and d and servie
        if abs(int(d) - int(servie)) <= ECART_FICHIER_S
    ]
    if confirment:
        return None
    contre = [
        f"{s} {durees[s]} s"
        for s in ("apple_music", "deezer")
        if durees.get(s) and servie and abs(int(durees[s]) - int(servie)) > ECART_VERSION_S
    ]
    servi = f"Spotify sert « {nom} » ({servie} s)"
    if contre:
        return Arbitrage(
            "un catalogue dément la durée de l'ID : refuser",
            "refuser_id",
            f"{servi} ; {', '.join(contre)}",
        )
    if not nom or titres_equivalents(_titre_sans_feat(nom), _titre_sans_feat(track.title)):
        return None
    en_plus = _mots_de(nom) - _mots_de(track.title) - _MOTS_VIDES
    if not en_plus or en_plus & _mots_de(track.album):
        return None
    return Arbitrage(
        "Spotify sert un autre titre : refuser l'ID",
        "refuser_id",
        f"{servi} ; mots en plus : {', '.join(sorted(en_plus))} ; aucune durée indépendante",
    )


def _familles_qui_confirment(durees: dict, servie) -> set:
    """Familles de catalogues INDÉPENDANTS de l'ID (ni fragiles, ni vidéo
    ordinaire, ni la colonne Deezer, souvent une autre édition) qui
    confirment à `ECART_FICHIER_S` la durée que Spotify sert."""
    return {
        famille(s)
        for s, d in durees.items()
        if s not in FRAGILES
        and s not in ("legacy", "heritage", "manual", "youtube_video", "deezer")
        and d
        and servie
        and abs(d - servie) <= ECART_FICHIER_S
    }


def politique_id_propose(lire: Callable, gate: Callable) -> Callable:
    """Une fiche SANS ID (une fiche à un AUTRE ID n'est jamais tranchée : autre
    édition ou ID faux, l'humain). `lire(sid)` rend l'identité de la page embed
    (None : jamais lue, non jugé) ; `gate(track, sid)` est le gate d'identité
    STRICT (artiste, durée, version, titre). Refusé par le gate : refuser
    l'ID, sauf si deux catalogues confirment la durée de l'ID et que le refus
    n'est pas celui d'un homonyme prouvé par l'URI. Accepté ET une durée
    INDÉPENDANTE (un catalogue lu par un autre identifiant) confirme à 2 s la durée de l'ID : poser l'ID. Accepté sans
    durée indépendante : l'humain."""

    def juger(cas, track, _ctx):
        from src.utils.spotify_identity import artiste_homonyme

        if track is None or track.spotify_id:
            return None
        sid = cas.preuves.get("spotify_id_propose")
        identite = lire(sid)
        if identite is None:
            return None
        durees = _durees(track)
        temoins = ", ".join(f"{s} {d} s" for s, d in sorted(durees.items()) if d) or "aucune"
        servi = f"Spotify sert « {identite.get('name')} » ({identite.get('duration')} s)"
        if not gate(track, sid):
            # L'URI passe avant les durées : elles concordent parce que tout le
            # monde lit le morceau de l'homonyme (Sniper « Hustle Di Money » :
            # Spotify crédite un autre « Sniper », Deezer, Apple et YTM aussi).
            homonyme = artiste_homonyme(track, identite)
            if homonyme:
                return Arbitrage(
                    "l'URI de l'artiste désigne un homonyme : refuser", "refuser_id", homonyme
                )
            # Le gate compare la durée de Spotify à la COLONNE de la fiche : si
            # deux catalogues indépendants confirment celle de Spotify, c'est la
            # colonne (souvent une autre édition Deezer) qui dément, pas l'ID —
            # Kanye « Comfy » : Spotify 212 s, YTM 212, ReccoBeats 211, Deezer
            # 206 en colonne (2026-10-21). L'humain tranche.
            if len(_familles_qui_confirment(durees, identite.get("duration"))) >= 2:
                return None
            return Arbitrage(
                "le gate d'identité refuse l'ID : refuser",
                "refuser_id",
                f"{servi} ; durées de la fiche : {temoins}",
            )
        duree_id = identite.get("duration") or durees.get("songbpm")
        accord = sorted(
            s
            for s, d in durees.items()
            if s not in FRAGILES
            # Un CLIP ne donne jamais la durée du morceau (règle du projet).
            and s not in ("legacy", "heritage", "manual", "youtube_video")
            and d
            and duree_id
            and abs(d - duree_id) <= ECART_FICHIER_S
        )
        if not accord:
            return refus_id_propose(track, identite)
        return Arbitrage(
            "poser l'ID",
            "poser_id",
            f"{servi} ; gate strict passé ; {duree_id} s = {', '.join(accord)} "
            f"(durées de la fiche : {temoins})",
        )

    return juger


# ── Liens Deezer ────────────────────────────────────────────────────────────


def _autre_que_la_page_deezer_de_la_fiche(track, piste) -> str | None:
    """La fiche a déjà SA page Deezer, dont la durée est confirmée par un
    autre catalogue lu par son ID (Spotify, Apple) ; la piste candidate, une
    AUTRE page au même titre, a un autre ISRC et s'en écarte de plus de
    `ECART_VERSION_S` : c'est une autre version (2026-10-31, DL1). Le vote
    général ne le voyait pas : il remplace la durée Deezer de la fiche par
    celle de la candidate, et il ne reste que Spotify, une voix. La raison,
    sinon None."""
    duree, isrc = piste.get("duration"), (piste.get("isrc") or "").upper()
    if not (duree and isrc and track.isrc and track.deezer_id):
        return None
    if str(track.deezer_id) == str(piste.get("id")) or isrc == str(track.isrc).upper():
        return None
    if not titre_d_original(track.title):
        return None
    durees = _durees(track)
    sienne = durees.get("deezer")
    temoins = [
        s
        for s in ("spotify_web", "apple_music")
        if sienne and durees.get(s) and abs(int(durees[s]) - int(sienne)) <= ECART_FICHIER_S
    ]
    if not temoins or abs(int(duree) - int(sienne)) <= ECART_VERSION_S:
        return None
    return (
        f"page de la fiche {track.deezer_id} : {sienne} s = {', '.join(temoins)} ; "
        f"candidate {piste.get('id')} : {duree} s, ISRC {isrc} ≠ {track.isrc}"
    )


def _seconde_page_du_meme_enregistrement(track, piste, vote, meme_titre) -> str | None:
    """La fiche a déjà SA page Deezer ; la candidate est une AUTRE page au même
    titre (deux originaux), dont le vote des catalogues SOUTIENT la durée par
    deux voix indépendantes de Deezer (MC Solaar « Clic clic » : single 2011
    à 175 s, Apple et Spotify à 172 s ; 2026-10-31). Une seconde page du même
    enregistrement est une ÉDITION, avec sa durée (décision utilisateur
    2026-10-19) — exactement ce que le motif du cas annonçait (« rattachement
    sûr », `revue._vote_de_parution`). La raison, sinon None."""
    if not (meme_titre and vote and vote.nature == SOUTENUE and track.deezer_id):
        return None
    if str(track.deezer_id) == str(piste.get("id")):
        return None
    if not (titre_d_original(track.title) and titre_d_original(piste.get("title"))):
        return None
    voix = sorted(s for s in vote.pour if famille(s) != famille("deezer"))
    if len(voix) < 2:
        return None
    return (
        f"page {piste.get('id')} ({piste.get('duration')} s) soutenue par "
        f"{', '.join(voix)} ; la fiche garde sa page {track.deezer_id}"
    )


def _hors_du_disque_de_la_fiche(track, ecart: dict, ctx):
    """La parution proposée est-elle sur un AUTRE disque que celui de la fiche
    (2026-11-02, skits récurrents d'Eminem : « Steve Berman (Skit) » de *The
    Eminem Show* rattaché en édition à celui de *Relapse*, « Paul (Skit) »
    d'*Encore* à celui du *Slim Shady LP*) ? Rend False si rien ne l'écarte,
    sinon l'arbitrage (None = l'humain) :

    - une AUTRE fiche de l'artiste, du disque de la piste, au même titre de
      base et à sa durée (± 2 s), unique : la piste est la sienne → ✓ Normal ;
    - un titre générique hors du disque de la fiche ne se rattache jamais
      (règle des titres génériques) → ✓ Normal ;
    - un titre générique dont la fiche n'a pas d'album : l'humain.

    Les deux fiches doivent être jugées sur le disque : la règle « même ISRC »
    ou une durée lue par l'ID déjà posé ne prouvent rien si cet ID est celui
    de l'autre disque."""
    from src.utils.version_descriptors import meme_disque, meme_titre_normalise, titre_de_base

    piste, album = ecart.get("piste") or {}, ecart.get("album") or {}
    disque, titre = album.get("title"), piste.get("title") or ""
    if not disque or meme_disque(track.album, disque):
        return False
    base, duree = titre_de_base(titre), piste.get("duration")
    if base and duree and ctx is not None:
        siennes = [
            t
            for t in ctx.disco
            if t.id != track.id
            and meme_disque(t.album, disque)
            and meme_titre_normalise(titre_de_base(t.title), base)
            and t.duration
            and abs(int(t.duration) - int(duree)) <= ECART_FICHIER_S
        ]
        if len(siennes) == 1:
            return Arbitrage(
                "piste d'une autre fiche (même disque) : ne pas rattacher",
                "normal",
                f"« {titre} » de « {disque} » ({duree} s) = « {siennes[0].title} » "
                f"#{siennes[0].id} ({siennes[0].duration} s)",
            )
    generique = titre_generique(titre) or ecart.get("matched_by") == "title_generique"
    if not generique:
        return False
    if not track.album:
        return None
    return Arbitrage(
        "titre générique d'un autre disque : ne pas rattacher",
        "normal",
        f"« {titre} » de « {disque} », fiche sur « {track.album} »",
    )


def politique_liens_deezer(cas, track, _ctx):
    """Parution à confirmer : rattacher si l'ISRC est celui de la fiche, ou si
    une durée indépendante la confirme à 2 s ET que les titres désignent le
    même enregistrement ; refuser si le vote la dément (autre version). Lien à
    revoir : délier si le vote dément la durée Deezer."""
    from src.utils.version_descriptors import (
        edition_de_diffusion,
        socle_du_titre,
        titres_equivalents,
    )

    if track is None:
        return None
    ecart = cas.preuves.get("ecart") or {}
    piste = ecart.get("piste") or {}
    if cas.detecteur == "deezer_link_candidate":
        hors_disque = _hors_du_disque_de_la_fiche(track, ecart, _ctx)
        if hors_disque is not False:
            return hors_disque
    duree, isrc = piste.get("duration"), (piste.get("isrc") or "").upper()
    durees = {s: d for s, d in _durees(track).items() if s != "deezer"}
    editions = tuple(track.durees_editions or ())
    vote = voter({**durees, "deezer": duree}, duree, editions=editions) if duree else None
    if cas.detecteur == "deezer_link_review":
        ref = _dementie({**durees, "deezer": duree}, "deezer", editions, 2) if duree else None
        if ref is not None:
            return Arbitrage(
                "délier", "deezer_appliquer", f"Deezer {duree} s contre {ref} s (2+ catalogues)"
            )
        if vote and vote.nature == SOUTENUE:
            return Arbitrage("garder le lien", "normal", f"{duree} s confirmée")
        return None
    meme_titre = titres_equivalents(piste.get("title"), track.title)
    if isrc and track.isrc and isrc == str(track.isrc).upper() and meme_titre:
        return Arbitrage("rattacher", "deezer_appliquer", "même ISRC, même titre")
    titre_piste = piste.get("title") or ""
    edition = edition_de_diffusion(titre_piste)
    if edition and titres_equivalents(socle_du_titre(titre_piste), track.title):
        # Décision utilisateur 2026-10-19 : radio edit, version radio = UNE
        # fiche, la parution en ÉDITION avec SA durée — l'écart ne dément rien.
        return Arbitrage(
            "rattacher en ÉDITION (sa durée propre)",
            "deezer_appliquer",
            f"« {edition} » de « {track.title} »",
        )
    accord = sorted(
        s
        for s, d in durees.items()
        if s not in FRAGILES and d and duree and abs(d - duree) <= ECART_FICHIER_S
    )
    # L'accord d'un seul témoin ne suffit pas s'il est lui-même démenti (Flynt
    # « J'éclaire ma ville » : Deezer 159 s = la vidéo isolée, fiche à 265 s).
    if accord and meme_titre and vote and vote.nature == SOUTENUE:
        return Arbitrage(
            "rattacher", "deezer_appliquer", f"{duree} s = {', '.join(accord)}, même titre"
        )
    seconde = _seconde_page_du_meme_enregistrement(track, piste, vote, meme_titre)
    if seconde:
        return Arbitrage("rattacher en ÉDITION (sa durée propre)", "deezer_appliquer", seconde)
    autre_version = _autre_que_la_page_deezer_de_la_fiche(track, piste)
    if autre_version:
        return Arbitrage("autre version : ne pas rattacher", "normal", autre_version)
    if duree and _dementie({**durees, "deezer": duree}, "deezer", editions, 2) is not None:
        return Arbitrage(
            "autre version : ne pas rattacher",
            "normal",
            f"Deezer {duree} s démentie par {vote.dementie_par()}",
        )
    return None


# ── BPM et tonalité ─────────────────────────────────────────────────────────

#: ReccoBeats et SongBPM donnent la MÊME donnée (99,4 % d'accord, mesuré) :
#: une seule voix, celle de l'ID Spotify. ReccoBeats est lu PAR l'ID (vérifié
#: par le gate), SongBPM par une recherche de titre.
_VOIX_SPOTIFY = ("reccobeats", "songbpm")


def politique_bpm(cas, track, ctx):
    """Une voix par source INDÉPENDANTE. La valeur retenue est celle de
    ReccoBeats (lue par l'ID) : gardée si UNE seule voix indépendante la
    contredit (Deezer seul, GetSongBPM seul, ou SongBPM seul — même donnée,
    son désaccord dit que sa page est une autre). Deux voix indépendantes
    d'accord contre elle : l'humain."""
    if track is None or track.audio.bpm is None:
        return None
    bpms = {s: float(v) for s, v in revue._mesures(ctx, track, "bpm").items()}
    retenu = float(track.audio.bpm)
    rb = bpms.get("reccobeats")
    if rb is None or not revue._meme_tempo(rb, retenu):
        return None
    contre = {s: v for s, v in bpms.items() if not revue._meme_tempo(v, retenu)}
    if not contre:
        return None
    if len(contre) >= 2:
        valeurs = list(contre.values())
        if all(revue._meme_tempo(valeurs[0], v) for v in valeurs[1:]):
            return None  # deux voix indépendantes d'accord contre la retenue
    detail = ", ".join(f"{s} {v:g}" for s, v in sorted(contre.items()))
    if set(contre) == {"songbpm"}:
        # Le TÉMOIN d'abord : une page SongBPM à la durée des catalogues est le
        # même enregistrement, seul son tempo diffère — deux estimations
        # (75 / 100 chez Flynt « Seul(s) », 3:4). La retirer effacerait une
        # durée et une tonalité justes : 6 des 7 retraits simulés le
        # 2026-10-26 l'auraient fait.
        durees = _durees(track)
        page = durees.get("songbpm")
        temoins = sorted(
            s
            for s, d in durees.items()
            if s not in FRAGILES
            and s not in ("heritage", "manual", "youtube_video")
            and d
            and page
            and abs(d - page) <= ECART_FICHIER_S
        )
        if temoins:
            return Arbitrage(
                "même enregistrement, deux mesures du tempo : ✓ Normal",
                "normal",
                f"ReccoBeats {rb:g} (par l'ID) contre {detail} ; durée SongBPM {page} s "
                f"= {', '.join(temoins)}",
            )
        return Arbitrage(
            "garder ReccoBeats ; la page SongBPM est un autre morceau : retirer SongBPM",
            "retirer_songbpm",
            f"ReccoBeats {rb:g} (par l'ID) contre {detail}",
        )
    return Arbitrage(
        f"garder {retenu:g} (ReccoBeats, par l'ID) : ✓ Normal",
        "normal",
        f"seuls contre : {detail}",
    )


def politique_tonalite(cas, track, ctx):
    """Même doctrine que le BPM : la tonalité retenue est celle de ReccoBeats ;
    SongBPM, même donnée, ne fait pas une seconde voix. Gardée si les voix
    contre ne sont qu'UNE source indépendante ; GetSongBPM ET SongBPM d'accord
    contre elle : l'humain."""
    if track is None or track.audio.key is None or track.audio.mode is None:
        return None
    paires = (cas.preuves or {}).get("tonalites") or {}
    paires = {s: tuple(v) for s, v in paires.items()}
    retenue = (int(track.audio.key), int(track.audio.mode))
    if paires.get("reccobeats") is None or not revue._meme_tonalite(paires["reccobeats"], retenue):
        return None
    contre = {s: v for s, v in paires.items() if not revue._meme_tonalite(v, retenue)}
    if not contre or len(contre) >= 2:
        return None
    (source,) = contre
    return Arbitrage(
        "garder la tonalité de ReccoBeats (par l'ID) : ✓ Normal",
        "normal",
        f"{source} seul contre",
    )


# ── LRC ─────────────────────────────────────────────────────────────────────

#: Un LRC qui colle à ≥ 90 % des paires de mots d'une AUTRE fiche est le sien.
LRC_AUTRE_MIN = 0.9
#: Un LRC qui s'arrête au moins tant de secondes avant la fin de la fiche est
#: celui d'une prise plus courte (témoin exigé pour le retirer, 2026-10-26).
LRC_COURT_S = 30
#: Part des paires de mots d'un LRC retrouvées dans les paroles Genius sous
#: laquelle il n'est pas un EXTRAIT de la page (2026-10-29) : un couplet
#: synchronisé d'un freestyle collectif y serait presque entier (≥ 0,6, le
#: seuil des LRC justes) ; les LRC YouTube Music des Grünt en retrouvent 0,11
#: à 0,17 — des mots-outils (« c'est », « j'ai »), pas des phrases.
LRC_ETRANGER_MAX = 0.3


def _part_du_lrc_dans_la_page(track) -> float | None:
    """Part des paires de mots du LRC présentes dans les paroles Genius de la
    fiche — dans CE sens seulement : une page qui transcrit une émission
    entière contient tout couplet qui en est extrait. None : non jugeable."""
    from src.utils.concordance_paroles import MOTS_MIN, bigrammes, mots, paroles_de_reference

    ref = paroles_de_reference(track.lyrics.text, track.lyrics.source)
    lrc = track.lyrics.synced
    if len(mots(ref)) < MOTS_MIN or len(mots(lrc)) < MOTS_MIN:
        return None
    paires = bigrammes(lrc)
    return len(paires & bigrammes(ref)) / len(paires) if paires else None


#: Catalogues lus par un IDENTIFIANT (ou le lien) : témoins de l'audio d'un LRC.
#: Ni YTM (le hit d'une recherche de paroles), ni les sources lues au titre.
_CATALOGUES_PAR_ID = ("deezer", "spotify_web", "apple_music", "youtube")


def _lrc_cale_sur_les_catalogues(track, ctx) -> str | None:
    """Le LRC LRCLIB de la colonne est calé sur un audio que deux catalogues
    lus par ID donnent à `ECART_FICHIER_S` près : c'est le LRC de CET
    enregistrement, et l'écart de mots avec la page tient à la transcription
    (2026-10-31, mesuré : « Haaland », « Alors on danse », « If I Ain't Got
    You (Kanye West Remix) »). La raison, sinon None."""
    from src.utils.lrc_garde import audios_des_lrc

    if ctx is None or (track.lyrics.synced_source or "").casefold() != "lrclib":
        return None
    audio = audios_des_lrc(ctx.obs.get(track.id, [])).get("lrclib")
    if not audio:
        return None
    accord = {
        s: d
        for s, d in _durees(track).items()
        if s in _CATALOGUES_PAR_ID and d and abs(int(d) - audio) <= ECART_FICHIER_S
    }
    if len({famille(s) for s in accord}) < 2:
        return None
    return f"audio LRCLIB {audio} s = " + ", ".join(f"{s} {d} s" for s, d in sorted(accord.items()))


def politique_lrc(cas, track, ctx):
    """LRC douteux : celui d'une AUTRE fiche (≥ 90 % de ses paires de mots,
    quelle que soit la part des siennes) se retire ; un LRC minuté au-delà de
    la durée de la fiche (+ 5 s) aussi. 👯 LRC d'une version sœur : deux PRISES
    différentes selon la taxonomie (« R.O.C. Remix » / « Jeezy Remix ») ne
    sont pas le même morceau — le LRC est celui de l'autre, il se retire ; même
    prise : une fusion, l'humain.

    TÉMOIN exigé (2026-10-26) : des paroles proches ne prouvent pas que le LRC
    est celui de l'autre (« Love Galore (Version 1) » : 96 % des mots de
    l'original, mais son LRC finit à 250 s, celui de l'original à 271 s). Le
    LRC se retire s'il s'arrête au moins `LRC_COURT_S` avant la fin de la
    fiche (celui d'une prise plus courte : « Can't Tell Me Nothing (Remix) »,
    316 s, LRC jusqu'à 247 s, la durée du « Jeezy Remix ») ; sans durée,
    rien ne dit lequel des deux LRC est à sa place."""
    from src.utils.lrc_garde import ECART_DUREE_S, duree_confirmee, fin_du_lrc
    from src.utils.version_descriptors import meme_prise_de_version

    if track is None or not track.lyrics.synced:
        return None
    preuves = cas.preuves or {}
    autre = preuves.get("bigrammes_autre")
    fin = fin_du_lrc(track.lyrics.synced)
    trop_court = bool(fin and track.duration and int(track.duration) - fin >= LRC_COURT_S)
    temoin = f"LRC jusqu'à {fin} s, fiche {track.duration} s"
    if cas.detecteur == "doublon_lrc":
        titre_autre = next((t.title for t in ctx.disco if t.id == preuves.get("autre")), None)
        if titre_autre and not meme_prise_de_version(track.title, titre_autre) and trop_court:
            return Arbitrage(
                "deux prises différentes : le LRC est celui de l'autre, le retirer",
                "retirer_lrc",
                f"« {track.title} » ≠ « {titre_autre} » (taxonomie), LRC à "
                f"{(autre or 0):.0%} de l'autre ; {temoin}",
            )
        return None
    part = _part_du_lrc_dans_la_page(track) if cas.detecteur == "lrc_live" else None
    if trop_court and part is not None and part < LRC_ETRANGER_MAX:
        # Une session : ni l'autre fiche ni la durée ne servent de témoin, le
        # texte de la page, si (2026-10-29, Grünt et VH1 Storytellers).
        return Arbitrage(
            "LRC d'un autre enregistrement que la session : le retirer",
            "retirer_lrc",
            f"{part:.0%} de ses paires de mots dans les paroles de la page ; {temoin}",
        )
    if autre is not None and autre >= LRC_AUTRE_MIN and trop_court:
        return Arbitrage(
            "LRC d'une autre fiche : le retirer",
            "retirer_lrc",
            f"{autre:.0%} des paires de mots de l'autre fiche ; {temoin}",
        )
    # Au-delà de la durée : seulement une durée CONFIRMÉE par deux catalogues
    # (la garde LRC du projet) — une durée isolée peut être la fausse.
    confirmee = duree_confirmee(track)
    if fin and confirmee and fin > confirmee + ECART_DUREE_S:
        return Arbitrage(
            "LRC minuté au-delà de la durée : le retirer",
            "retirer_lrc",
            f"LRC jusqu'à {fin} s, fiche {track.duration} s",
        )
    if cas.detecteur == "lrc_douteux":
        cale = _lrc_cale_sur_les_catalogues(track, ctx)
        if cale:
            return Arbitrage("LRC calé sur l'audio de la fiche : ✓ Normal", "normal", cale)
    return None


#: Écart toléré entre la fin du LRC d'une fiche que rien ne publie et celle du
#: LRC de l'autre fiche dont il porte les paroles (2026-10-31 : 2 à 16 s sur
#: les 5 cas du panneau — Gyrophare / Garcimore, Ripaille / Mémoire…).
LRC_MEME_FIN_S = 20


def politique_paroles_autre_fiche(cas, track, ctx):
    """LRC de repli d'une fiche que RIEN ne publie (ni durée, ni ID Spotify ou
    Deezer : un inédit, un extrait, une page « à retranscrire ») dont les
    paroles sont celles d'une autre fiche : YouTube Music ne sert de LRC que
    pour un enregistrement publié, c'est celui de l'autre (Booba « Gyrophare* »,
    extrait sans paroles, portait le LRC de « Garcimore », 2026-10-31).
    TÉMOIN : le LRC de l'autre fiche, lu à part, finit au même moment
    (± `LRC_MEME_FIN_S`) — le LRC couvre tout l'autre morceau, pas un couplet
    cité. Des paroles de repli (hors LRC) restent à l'humain."""
    from src.utils.lrc_garde import fin_du_lrc

    if track is None or not track.lyrics.synced:
        return None
    if track.duration or track.spotify_id or track.deezer_id:
        return None
    preuves = cas.preuves or {}
    textes = preuves.get("textes") or []
    if not textes or not all(t.startswith("LRC") for t in textes):
        return None
    autre = next((t for t in ctx.disco if t.id == preuves.get("autre")), None)
    if autre is None or not autre.lyrics.synced or autre.lyrics.synced == track.lyrics.synced:
        return None
    fin, fin_autre = fin_du_lrc(track.lyrics.synced), fin_du_lrc(autre.lyrics.synced)
    if not fin or not fin_autre or abs(fin - fin_autre) > LRC_MEME_FIN_S:
        return None
    return Arbitrage(
        "LRC de l'autre fiche sur une fiche que rien ne publie : le retirer",
        "retirer_lrc",
        f"paroles de « {autre.title} » ({preuves.get('part', 0):.0%}) ; LRC jusqu'à "
        f"{fin} s, celui de « {autre.title} » jusqu'à {fin_autre} s",
    )


# ── Écarts Deezer et propositions Kworb : par CAS TYPIQUE ───────────────────


def politique_ecarts_deezer(
    isrcs_connus: dict, ids_artiste: set, rejoues: set | None = None
) -> Callable:
    """Pas de règle unique : chaque écart est rangé dans un CAS TYPIQUE, avec le
    sens qu'une politique lui donnerait — à valider cas typique par cas typique.
    `isrcs_connus` : {ISRC: titre} de toute la base ; `ids_artiste` : ID Deezer
    de l'artiste (et de ses formations) ; `rejoues` : les `(nature, piste)` que
    le classement D'AUJOURD'HUI produit encore, rejoué hors ligne depuis le brut
    (None : pas de rejeu) — un signalement qu'il ne produit plus est périmé."""
    from src.utils.version_descriptors import nature_de_l_edition, titre_generique
    from src.utils.version_heritage import famille_de

    def juger(cas, _track, _ctx):
        ecart = cas.preuves.get("ecart") or {}
        piste, album = ecart.get("piste") or {}, ecart.get("album") or {}
        titre = piste.get("title") or cas.morceau
        isrc = (piste.get("isrc") or "").upper()
        propre = album.get("artist_id") in ids_artiste
        d = cas.detecteur
        if rejoues is not None and (d.removeprefix("deezer_"), piste.get("id")) not in rejoues:
            return Arbitrage(
                "le classement d'aujourd'hui ne le produit plus : un run Deezer le retire",
                "run",
                "rejeu hors ligne depuis le brut",
            )
        if d in ("deezer_absent", "deezer_album_absent") and not propre:
            return None  # disque d'un autre (collaboratif, label) : l'humain
        if isrc and isrc in isrcs_connus:
            return Arbitrage(
                f"ISRC déjà porté par « {isrcs_connus[isrc]} » : rattacher, ne pas créer",
                "deezer_rattacher_isrc",
                isrc,
            )
        if d in ("deezer_absent", "deezer_album_absent"):
            if titre_generique(titre):
                return None  # « Intro » d'un disque : l'humain
            return Arbitrage(
                "créer la fiche (disque propre, ISRC inconnu en base)",
                "deezer_appliquer",
                f"disque « {album.get('title')} »"
                + (" — page Genius trouvée" if ecart.get("genius") else ""),
            )
        if d == "deezer_version":
            fam = famille_de(titre)
            if fam in (None, "edition"):
                # « diffusion » : édition de DIFFUSION (« Radio Edit », « Explicit »).
                nature = nature_de_l_edition(titre)
                if nature in ("diffusion", "edition", "montage"):
                    return Arbitrage(
                        "ÉDITION de la fiche du socle (décision du 2026-10-19 : une "
                        "fiche, la version en édition avec SA durée), pas de fiche",
                        "deezer_edition",
                        f"{nature} : « {titre} »",
                    )
            # DV1 (« instrumental / a cappella : pas de fiche », 2026-10-31) levée
            # par la règle C (décision utilisateur 2026-11-19) : une version qui
            # peut avoir des streams est une fiche secondaire
            # (`_version_d_un_disque_de_versions`).
            if ecart.get("genius"):
                return Arbitrage(
                    "créer la fiche (version à page Genius)",
                    "deezer_appliquer",
                    f"famille {fam}, page Genius trouvée",
                )
            return None
        if d == "deezer_apparition":
            if ecart.get("genius"):
                return Arbitrage(
                    "créer la fiche (apparition à page Genius)",
                    "deezer_appliquer",
                    f"disque de {album.get('artist_name')}",
                )
            # DA2 (2026-10-31) : sans page Genius, Deezer crédite l'artiste
            # par SON identifiant (vérifié) parmi les contributeurs : un
            # featuring réel (« 600K » de Heuss L'enfoiré avec PLK).
            credite = [nom for cid, nom in piste.get("contributors") or [] if cid in ids_artiste]
            noms = [nom for _, nom in piste.get("contributors") or []]
            if credite and not credits_de_complaisance(
                titre, noms, piste.get("artist_name") or album.get("artist_name")
            ):
                return Arbitrage(
                    "créer la fiche (Deezer crédite l'artiste par son ID)",
                    "deezer_appliquer",
                    f"disque de {album.get('artist_name')} ; contributeur {credite[0]}",
                )
            return None
        return None

    return juger


#: Les signalements Kworb qui désignent une LIGNE par son titre.
_TITRE_KWORB = ("kworb_title", "titre_kworb")


def politique_kworb(cas, _track, _ctx, rejoues: dict | None = None):
    """Un signalement Kworb PÉRIMÉ : `rejoues` = `{titre Kworb: verdict}` du
    rapprochement d'AUJOURD'HUI, rejoué hors ligne depuis le brut
    (`update_kworb.rapprochements`) — une ligne désormais rattachée sans
    suggestion rend le signalement périmé. Les propositions ★ elles-mêmes se
    jugent par `politique_kworb_proposition` (tiers, variante d'une fiche)."""
    preuves = cas.preuves or {}
    titre = next((preuves[k] for k in _TITRE_KWORB if preuves.get(k)), None)
    verdict = (rejoues or {}).get(titre) if titre else None
    if verdict and verdict.get("fiche") and not verdict.get("suggestion"):
        return Arbitrage(
            "le rapprochement d'aujourd'hui rattache la ligne : un run Kworb le retire",
            "run",
            f"« {titre} » → « {verdict['fiche']['titre']} » ({verdict.get('via')})",
        )
    return None


def _lire_embed_muet(_sid):
    """Aucun réseau : `update_kworb._identite_embed` lit le BRUT d'abord."""
    return None


def juger_variante_kworb(preuves: dict, fiches: dict, lire_identite=_lire_embed_muet):
    """Une ligne Kworb qui ressemble à une VERSION d'une fiche (proposition ★
    « rendition » au socle, ou « existant » au seul morceau qui serait déjà ce
    remix), décision utilisateur du 2026-11-23 :

      · SOMMÉE à la fiche (`kworb_sommer`) quand la durée de l'ID de la ligne,
        lue à son embed (brut), est à ± `ECART_FICHIER_S` de la durée
        INDÉPENDANTE de la fiche (`duree_temoin`, jamais une durée lue par ce
        même ID), dans la même version — le juge des « même morceau ? »
        Kworb, `update_kworb.duree_de_la_ligne` == « meme » (ID refusé exclu) ;
      · sinon une FICHE À PART (`kworb_version`, version secondaire, comme la
        règle C des écarts Deezer), quand rien ne la dit déjà en base : aucune
        fiche au titre de la ligne ni à son ID, et chaque fiche de la même
        version (la cible, le socle) à plus de `ECART_VERSION_S` de sa durée.

    Restent à l'humain : une ligne de featuring (`tiers` a sa règle), sauf
    l'instrumental d'un original en base (fiche à part, 2026-11-23), une
    édition de diffusion ou de support (même enregistrement sous un autre
    libellé), une même version entre 2 et 10 s ou sans durée lisible (une
    autre édition du même remix ?), un remix ou un edit à la durée du socle
    (règle C) et un socle qui est lui-même une version (« La Fin De Leur
    Monde - Live » proposée au socle « (Instrumentale) », inventaire du
    2026-11-22). `fiches` : `{id: Track}` de la discographie réunie.

    Garde-fou ajouté à la règle de l'utilisateur, simulé sur les 17 cas : une
    autre VERSION (« Seine Saint-Denis Style - IV My People Remix
    (Instrumental) », 200 s comme le remix vocal) n'est jamais sommée, même à
    la même durée — une variante ne se somme pas à son original (Kworb)."""
    from src.utils.spotify_identity import duree_temoin
    from src.utils.title_matching import names_match_as_words
    from src.utils.update_kworb import (
        _SUFFIXE_UPLOAD,
        _identite_embed,
        duree_de_la_ligne,
        socle_du_titre,
    )
    from src.utils.version_descriptors import (
        edition_de_diffusion,
        meme_version_stricte,
        porte_une_version,
        titres_equivalents,
    )
    from src.utils.version_heritage import famille_de

    titre, sid = preuves.get("kworb_title"), preuves.get("spotify_id")
    if not titre or not sid:
        return None
    propose = preuves.get("proposition")
    existants = preuves.get("existants") or []
    parent = fiches.get(preuves.get("parent_track_id"))
    if propose == "existant" and len(existants) == 1:
        cible, voie = fiches.get(existants[0][0]), "existant"
    elif propose == "rendition" and parent is not None:
        cible, voie = parent, "edition"
    else:
        return None
    if cible is None:
        return None
    entree = {"title": titre, "spotify_id": sid}
    identite = _identite_embed(sid, lire_identite) or {}
    duree = identite.get("duration")
    if duree_de_la_ligne(entree, cible, lire_identite) == "meme":
        return Arbitrage(
            f"sommer la ligne à « {cible.title} » (même enregistrement)",
            "kworb_sommer",
            f"ID {duree} s, fiche {duree_temoin(cible)} s ({voie})",
        )
    nu = _SUFFIXE_UPLOAD.sub("", titre)
    fam = famille_de(nu)
    if fam in (None, "edition") or edition_de_diffusion(nu):
        return None
    if preuves.get("is_feature") and not (fam == "instrumental" and parent is not None):
        # Une ligne de featuring reste à l'humain, sauf l'INSTRUMENTAL d'un
        # original en base : fiche à part, au même rôle que l'original (Eminem
        # « Forgot About Dre - Instrumental », décision utilisateur 2026-11-23).
        return None
    if parent is not None and (
        porte_une_version(parent.title) or not titres_equivalents(socle_du_titre(nu), parent.title)
    ):
        # Le socle proposé n'est pas l'original de la ligne : la fiche créée
        # en hériterait (« La Fin De Leur Monde - Live » proposée au socle
        # « La fin de leur monde (Instrumentale) »).
        return None
    jugees = {id(f): f for f in (cible, parent) if f is not None}
    for t in fiches.values():
        if sid == t.spotify_id or sid in (t.spotify_ids or ()):
            # La ligne est déjà une fiche : un run Kworb la rattache par son ID.
            return None
        if id(t) not in jugees and titres_equivalents(nu, t.title):
            return None
    remixeur = preuves.get("remixer")
    for t in jugees.values():
        if meme_version_stricte(nu, t.title):
            temoin = duree_temoin(t)
            proche = not temoin or abs(int(duree or 0) - int(temoin)) <= ECART_VERSION_S
            if duree and proche and _meme_remix_nomme(nu, t, remixeur, identite):
                return Arbitrage(
                    f"sommer la ligne à « {t.title} » (le même remix, nommé)",
                    "kworb_sommer",
                    f"remix de {remixeur} ; ID {duree} s, fiche {temoin or '?'} s ({voie})",
                )
            if not duree or not temoin or abs(int(duree) - int(temoin)) <= ECART_VERSION_S:
                return None
            if remixeur and names_match_as_words(remixeur, t.title):
                # Le même remixeur : une autre durée y est une autre ÉDITION
                # du même remix (« Ma Benz … B.O.S.S. 2000 Remix », 220 s,
                # contre « Ma benz (Remix de B.O.S.S) » du *Clash* de 2000,
                # 232 s chez Deezer).
                return None
    socle = parent if parent is not None else None
    if socle is not None and fam not in FAMILLES_JAMAIS_MENTION_DE_SUPPORT:
        temoin = duree_temoin(socle)
        if duree and temoin and abs(int(duree) - int(temoin)) <= ECART_FICHIER_S:
            return None
    return Arbitrage(
        "fiche à part (version secondaire)",
        "kworb_version",
        f"{fam} ; ID {duree or '?'} s, « {cible.title} » {duree_temoin(cible)} s",
    )


def _meme_remix_nomme(ligne: str, fiche, remixeur, identite: dict) -> bool:
    """La ligne Kworb « Titre - X Remix » (invités compris) et la fiche
    « Titre (Remix de X) » sont le même remix NOMMÉ : même remixeur, rien de
    plus dans la ligne (« B.O.S.S. 2000 Remix » ajoute « 2000 » : une autre
    édition, à l'humain), et chaque invité que Spotify crédite sur la ligne est
    crédité sur la fiche ou nommé dans son titre (décision utilisateur
    2026-11-23 : « J'appuie sur la gâchette - B.O.S.S. Remix », « la réponse est
    dans la question »). Les 3 à 6 s d'écart entre l'ID et la fiche n'y font
    rien : la durée de la fiche est souvent celle d'une recherche YTM par titre."""
    from src.utils.title_matching import names_match_as_words
    from src.utils.update_kworb import socle_du_titre
    from src.utils.version_descriptors import meme_titre_normalise

    if not remixeur or not names_match_as_words(remixeur, fiche.title):
        return False
    if not meme_titre_normalise(ligne, f"{socle_du_titre(ligne)} - {remixeur} Remix"):
        return False
    credites = [c.name for c in fiche.credits or []]
    # Le premier artiste est l'interprète principal de la ligne ; le remixeur
    # est nommé par la ligne même.
    for nom in (identite.get("artists") or [])[1:]:
        if names_match_as_words(nom, remixeur):
            continue
        if not (
            names_match_as_words(nom, fiche.title)
            or any(names_match_as_words(nom, c) for c in credites)
        ):
            return False
    return True


def politique_kworb_variante(cas, _track, ctx, lire_identite=_lire_embed_muet):
    """`juger_variante_kworb` sur un cas `kworb_proposition` du panneau."""
    if cas.detecteur != "kworb_proposition" or ctx is None:
        return None
    from src.services.revue import cibles_des_rattachements

    fiches = {t.id: t for t in cibles_des_rattachements(ctx)}
    return juger_variante_kworb(cas.preuves or {}, fiches, lire_identite=lire_identite)


def politique_kworb_proposition(cas, track, ctx):
    """Les propositions ★ Kworb : remix par un tiers, ou variante d'une fiche."""
    return politique_kworb_tiers(cas, track, ctx) or politique_kworb_variante(cas, track, ctx)


# ── Alias proposés ──────────────────────────────────────────────────────────


def politique_alias(cas, _track, ctx):
    """Un ALIAS proposé est confirmé quand MusicBrainz ET Discogs le donnent,
    quand MusicBrainz le type « nom d'artiste », ou quand Genius le crédite
    comme auteur sur `FICHES_MIN_LIEN` fiches de l'artiste (L1, 2026-10-31).
    Une page d'alias Discogs sans autre trace reste à l'humain : nom civil mal
    graphié (« Eli Yaffa » ; Genius écrit « Elie Yaffa ») ou nom douteux
    (« Global » chez Médine). Une formation n'est confirmée que si sa nature
    est connue (L2), ou si MusicBrainz ET Discogs la donnent
    (`_formation_mb_et_discogs`) ; sinon elle se tranche dans la fenêtre Groupes.
    Prudence : un alias confirmé devient un nom de RECHERCHE (certifications,
    collectifs) et un profil Spotify connu."""
    preuves = cas.preuves or {}
    kind, nom = preuves.get("kind"), preuves.get("nom")
    rel = next(
        (r for r in ctx.relations_proposees if r.kind == kind and r.related_name == nom),
        None,
    )
    if rel is None:
        return None
    tranche = _lien_par_les_noms_surs(rel, ctx)
    if tranche is not None:
        return tranche
    sources = set((rel.source or "").split("+"))
    if kind == "member_of":
        return (
            _formation_par_ses_morceaux(rel, ctx)
            or _formation_prouvee_par_genius(rel, ctx)
            or _formation_mb_et_discogs(rel, sources, ctx)
        )
    if kind == "has_member":
        return _formation_mb_et_discogs(rel, sources, ctx)
    if kind != "alias":
        return None
    if {"discogs", "musicbrainz"} <= sources:
        return Arbitrage("confirmer l'alias", "confirmer_lien", "MusicBrainz ET Discogs")
    if "musicbrainz" in sources and rel.detail == "Artist name":
        return Arbitrage(
            "confirmer l'alias", "confirmer_lien", "MusicBrainz : nom d'artiste (Artist name)"
        )
    creditees = _fiches_ou_il_ecrit(nom, ctx)
    if len(creditees) >= FICHES_MIN_LIEN:
        return Arbitrage(
            "confirmer l'alias",
            "confirmer_lien",
            f"Genius le crédite (auteur, compositeur, producteur) sur {len(creditees)} "
            f"fiches de l'artiste : {', '.join(creditees[:3])}…",
        )
    return None


#: Libellé de chaque verdict de `utils.liens_proposes.juger`.
_SENS_DES_LIENS = {
    "taire_lien": "garder pour info, jamais proposé",
    "refuser_lien": "refuser le lien",
    "confirmer_formation": "confirmer le lien de membre",
}


def _lien_par_les_noms_surs(rel, ctx) -> Arbitrage | None:
    """Ce que les noms SÛRS de l'artiste et de ses membres confirmés tranchent
    (`utils.liens_proposes.juger`, 2026-11-22) : nom civil de l'artiste, autre
    nom d'un membre connu, nom d'un membre proposé comme alias d'une formation,
    membre proposé à une personne."""
    from src.utils.liens_proposes import juger

    verdict = juger(rel.kind, rel.related_name, ctx.temoins_liens)
    if verdict is None:
        return None
    return Arbitrage(_SENS_DES_LIENS[verdict.action], verdict.action, verdict.motif)


#: Fiches de l'artiste où Genius nomme un alias ou une formation proposés,
#: au-dessous desquelles le lien reste à l'humain (L1 / L2, 2026-10-31).
FICHES_MIN_LIEN = 3

_ROLES_D_AUTEUR = frozenset({"Writer", "Composer", "Lyricist", "Producer"})


def _fiches_ou_il_ecrit(nom: str, ctx) -> list[str]:
    """Titres des fiches dont les crédits Genius nomment `nom` (nom normalisé
    égal, jamais une sous-chaîne) dans un rôle d'auteur : le témoin d'un nom
    civil ou d'un nom de producteur (« Claude M'Barali », 22 fiches de MC
    Solaar)."""
    from src.utils.title_matching import normalize_name

    cle = normalize_name(nom)
    return [
        t.title
        for t in ctx.disco
        if any(
            c.role.value in _ROLES_D_AUTEUR and normalize_name(c.name) == cle
            for c in t.credits or []
        )
    ]


#: Interrupteur de `_formation_mb_et_discogs` : levé le 2026-11-06, validé par
#: l'utilisateur sur la simulation (12 liens / 13, N.H. tranché à part).
FORMATIONS_MB_ET_DISCOGS = True


def _formation_mb_et_discogs(rel, sources: set, ctx=None) -> Arbitrage | None:
    """Une formation que MusicBrainz ET Discogs donnent se confirme. Sa nature
    suit la règle validée le 2026-11-12 (`formations_genius.nature_par_les_parts`,
    mesurée hors ligne dans le brut, `ctx.natures_formations`) : la part de nos
    membres dans les morceaux Genius de la formation ; mesure indécise ou part
    nulle (homonyme, faux lien) ⇒ l'humain. Sans mesure (pas d'ID Genius, liste
    jamais lue) : « collectif » par défaut — prendre un groupe pour un collectif
    ne coûte presque rien, l'inverse apporte des morceaux en trop (décision
    utilisateur du 2026-11-06). Le lien de membre n'a pas de nature
    (`has_member`, navigation seulement). Une nature déjà proposée autre que
    « collectif » reste à l'humain (N.H. : groupe)."""
    if not FORMATIONS_MB_ET_DISCOGS or not {"discogs", "musicbrainz"} <= sources:
        return None
    if rel.formation and rel.formation != "collectif":
        return None
    if rel.kind != "member_of":
        return Arbitrage(
            "confirmer la formation (lien de membre)",
            "confirmer_formation",
            "MusicBrainz ET Discogs",
        )
    natures = ctx.natures_formations if ctx is not None else {}
    verdict = None if rel.formation else natures.get(rel.related_name)
    if verdict is None:
        return Arbitrage(
            "confirmer la formation (collectif)", "confirmer_formation", "MusicBrainz ET Discogs"
        )
    if verdict.nature is None:
        return None
    return Arbitrage(
        f"confirmer la formation ({verdict.nature})",
        "confirmer_formation",
        f"MusicBrainz ET Discogs ; Genius : {verdict.motif}",
    )


def _formation_prouvee_par_genius(rel, ctx) -> Arbitrage | None:
    """R4 (décision utilisateur 2026-11-13) : une formation proposée est
    confirmée quand la liste Genius du membre la donne pour artiste principal
    d'au moins `FICHES_MIN_LIEN` morceaux, sous un seul ID, ET que la nature
    mesurée avec cet ID est tranchée (groupe / collectif). Nature indécise,
    part nulle : l'humain. Simulé sur la copie du 13/11 : 21 liens prouvés sur
    40, aucune erreur vue (Bad Meets Evil, D12, Ideal J, Mafia K'1 Fry…)."""
    # `formations_prouvees` ne retient que les liens sans nature, à l'ID concordant.
    if rel.formation or ctx is None:
        return None
    entree = ctx.formations_prouvees.get(rel.related_name)
    if entree is None:
        return None
    genius_id, pages, verdict = entree
    if pages < FICHES_MIN_LIEN or verdict is None or verdict.nature is None:
        return None
    return Arbitrage(
        f"confirmer la formation ({verdict.nature}) — prouvée par Genius",
        "confirmer_formation_genius",
        f"Genius {genius_id} principal de {pages} morceaux du membre ; {verdict.motif}",
    )


def _formation_par_ses_morceaux(rel, ctx) -> Arbitrage | None:
    """Une formation proposée dont la NATURE (groupe / collectif) est déjà
    connue, et que Genius donne pour interprète d'au moins `FICHES_MIN_LIEN`
    fiches de l'artiste (L2, 2026-10-31 : « Hustle Gang », collectif, 4
    fiches de Travis Scott). Sans nature, la fenêtre « Groupes » décide."""
    from src.utils.title_matching import normalize_name

    if not rel.formation:
        return None
    cle = normalize_name(rel.related_name)
    fiches = [t.title for t in ctx.disco if cle in revue.interpretes(t)]
    if len(fiches) < FICHES_MIN_LIEN:
        return None
    return Arbitrage(
        f"confirmer la formation ({rel.formation})",
        "confirmer_lien",
        f"interprète de {len(fiches)} fiches de l'artiste : {', '.join(fiches[:3])}…",
    )


# ── Règles VALIDÉES (décisions utilisateur 2026-10-20) ─────────────────────

#: (détecteur, action) que l'utilisateur a validés sur le passage à blanc du
#: 2026-10-20 — gelé par `tests/test_politiques_revue.py`. Une règle ne s'ajoute
#: ici qu'après SA validation, famille par famille.
REGLES_VALIDEES = frozenset(
    {
        # « dès qu'on est sûr que c'est pas la bonne version, on retire »
        ("duree_songbpm", "retirer_songbpm"),
        # « les données viennent d'un autre morceau ça dégage »
        ("duree_spotify", "retirer_spotify"),
        ("duree_spotify", "normal"),
        # un ORIGINAL seulement (la politique écarte versions et génériques)
        ("duree_isolee", "normal"),
        # PAS `generique_duree` : « Intro », « Outro »… se vérifient à la main (2026-10-20).
    }
)

#: Seuil de la politique des durées par nature d'action : un RETRAIT exige 3
#: catalogues solides d'accord contre la valeur (la variante validée), un
#: « ✓ Normal », qui ne change aucune donnée, 2.
_DUREES_POUR_RETIRER = politique_durees(3)
_DUREES_POUR_NORMAL = politique_durees(2)


def arbitrage_valide(cas, track, ctx) -> Arbitrage | None:
    """L'arbitrage qu'une règle VALIDÉE donne à ce cas, sinon None.

    Décision utilisateur 2026-10-20 : « on garde les cas ambigus ou source
    d'erreur facile pour l'instant à la main, intro outro interlude remix
    live » — toute règle ne vaut que pour un ORIGINAL (titre sans descripteur
    de version) au titre non générique."""
    titre = track.title if track is not None else cas.morceau
    if not titre_d_original(titre):
        return None
    for juger in (_DUREES_POUR_NORMAL, _DUREES_POUR_RETIRER):
        arbitrage = juger(cas, track, ctx)
        if arbitrage is None:
            continue
        if juger is _DUREES_POUR_NORMAL and arbitrage.action != "normal":
            continue  # un retrait ne se décide qu'au seuil de 3
        if (cas.detecteur, arbitrage.action) in REGLES_VALIDEES:
            return arbitrage
    return _retrait_au_seuil_de_deux(cas, track, ctx)


#: Retraits décidés au seuil de DEUX catalogues solides d'accord (décision
#: utilisateur 2026-11-13, après simulation sur la copie du 13/11 : 21 retraits,
#: 18 confirmés par un témoin indépendant ; la garde ci-dessous écarte « In The
#: City » et « She Wanna Have My Baby » ; « Cœur de bombe » de Diam's, douteux,
#: est montré à l'utilisateur).
REGLES_VOTE_DEUX = frozenset(
    {("duree_spotify", "retirer_spotify"), ("duree_deezer", "delier_deezer")}
)

#: La valeur démentie, par détecteur.
_SOURCE_DEMENTIE = {"duree_spotify": "spotify_web", "duree_deezer": "deezer"}


def _retrait_au_seuil_de_deux(cas, track, ctx) -> Arbitrage | None:
    """Un retrait (ID Spotify, lien Deezer) que DEUX catalogues solides
    d'accord démentent, quand rien ne soutient la valeur démentie : ni la
    durée de l'audio d'un LRC (`lrc_duree_audio`, lu avec le LRC), ni Discogs
    (une édition physique peut avoir cette durée), et que la fiche n'a pas
    d'ÉDITION d'une autre durée que sa page Deezer (`durees_editions` : la
    valeur démentie peut être celle de l'autre édition)."""
    if track is None or (cas.detecteur not in _SOURCE_DEMENTIE):
        return None
    arbitrage = _DUREES_POUR_NORMAL(cas, track, ctx)
    if arbitrage is None or (cas.detecteur, arbitrage.action) not in REGLES_VOTE_DEUX:
        return None
    if tuple(track.durees_editions or ()):
        return None
    durees = _durees(track)
    valeur = durees.get(_SOURCE_DEMENTIE[cas.detecteur])
    if not valeur:
        return None
    temoins = [durees.get("discogs")]
    if ctx is not None:
        temoins += [o.value for o in (ctx.obs.get(track.id) or ()) if o.field == "lrc_duree_audio"]
    for t in temoins:
        try:
            if t and abs(int(float(t)) - int(valeur)) <= TOLERANCE_S:
                return None
        except (TypeError, ValueError):
            continue
    return Arbitrage(
        arbitrage.sens,
        arbitrage.action,
        f"{arbitrage.raison} — deux catalogues d'accord, rien ne soutient {valeur} s",
    )


# ── 💿 Hors du disque : une édition physique la contient (R3, 2026-11-13) ──

#: Interrupteur : `revue_auto.REGLES_HORS_DISQUE_BRANCHEES`.
REGLES_HORS_DISQUE = frozenset({("hors_du_disque", "normal")})


def parution_discogs_du_brut(release_id) -> dict | None:
    """La parution Discogs `/releases/{id}` gardée au brut (lecture seule), sa
    liste de pistes comprise ; None si jamais lue."""
    import json

    from src.persistence import brut

    for corps in brut.corps_gardes(
        "discogs", f"https://api.discogs.com/releases/{int(release_id)}"
    ):
        try:
            d = json.loads(corps)
        except ValueError:
            continue
        if isinstance(d, dict) and d.get("tracklist"):
            return d
    return None


def arbitrage_hors_disque(cas, track, _ctx, lire_discogs=None) -> Arbitrage | None:
    """R3 (décision utilisateur 2026-11-13) : la fiche que Deezer (et Apple)
    sortent du disque où Genius la range, quand l'ÉDITION PHYSIQUE Discogs de
    ce disque — la parution Discogs de la fiche, au même nom de disque — la
    liste : elle y est, ✓ Normal (Dosseh « Tout est neuf » sur *VIDALO$$A*,
    Vald « PTSD » sur *PANDEMONIUM*). Simulé sur la copie du 13/11 : la règle
    « Deezer + Apple l'excluent » aurait eu tort 6 fois sur 19 ; 8 cas tranchés
    ainsi. Sans parution Discogs lue, rien n'est prouvé : l'humain."""
    from src.utils.version_descriptors import meme_disque, meme_titre_de_base, titres_equivalents

    if cas.detecteur != "hors_du_disque" or track is None or not track.discogs_id:
        return None
    parution = (cas.preuves or {}).get("parution") or ""
    lire = lire_discogs or parution_discogs_du_brut
    d = lire(track.discogs_id)
    if not d or not meme_disque(d.get("title"), parution):
        return None
    titres = [p.get("title") or "" for p in d.get("tracklist") or [] if isinstance(p, dict)]
    if not any(
        titres_equivalents(t, track.title) or meme_titre_de_base(t, track.title) for t in titres
    ):
        return None
    return Arbitrage(
        "une édition physique du disque la contient : ✓ Normal",
        "normal",
        f"Discogs {track.discogs_id} « {d.get('title')} » liste « {track.title} »",
    )


# ── Crédits 🎫 que la page ne confirme pas (2026-11-22) ────────────────────

#: Décision utilisateur du 2026-11-22 : « on valide tout ce qu'on peut, mais
#: le reste va en indécidable, car sans autre info je ne peux pas trancher,
#: rien n'indique qu'ils sont faux ». Interrupteur :
#: `revue_auto.REGLES_CREDITS_API_BRANCHEES`.
REGLES_CREDITS_API = frozenset({("credits_api", "normal"), ("credits_api", "indecidable")})

#: Sources de crédits INDÉPENDANTES de Genius qui confirment un crédit 🎫 :
#: Discogs, les contributeurs Deezer, les descriptions YouTube (Topic, clip).
#: Ni l'héritage, ni un crédit déduit d'un titre, ni Kworb.
SOURCES_TEMOINS_CREDITS = frozenset({"discogs", "deezer", "youtube_topic", "youtube_clip"})
#: Pour un rôle VOISIN seulement : la page Genius (même personne, autre rôle de
#: la même famille). Au même rôle, la page et l'API partageraient la clé.
SOURCES_TEMOINS_VOISINS = frozenset({"genius"})


def _familles_de_roles():
    from src.models.track import CreditRole as R

    familles = {
        "production": (R.PRODUCER, R.CO_PRODUCER, R.ADDITIONAL_PRODUCTION),
        "ecriture": (R.WRITER, R.COMPOSER, R.LYRICIST),
    }
    return {role: nom for nom, roles in familles.items() for role in roles}


#: Rôle → famille de rôles voisins (décision utilisateur 2026-11-23).
_FAMILLE_DE_ROLE = _familles_de_roles()


def arbitrage_credits_api(cas, track, _ctx) -> Arbitrage | None:
    """Chaque crédit 🎫 confirmé AU MÊME RÔLE (même personne au nom normalisé)
    par une source indépendante : ✓ Normal ; sinon « indécidable » — hors du
    panneau et du critère, le cas revient si ses crédits changent (la clé du
    cas porte la liste des crédits). Un cas « à faire par un run » (page pas
    relue depuis le détail API) n'est pas tranché : le run peut le régler.

    Mesuré le 2026-11-22 sur une copie : 189 crédits 🎫 sur 96 cas, 1 seul
    confirmé au même rôle, aucune fiche couverte entière — tout va en
    indécidable : la table ne gardait qu'une source par crédit. Depuis e70
    (2026-11-23), un crédit 🎫 que Discogs, Deezer ou YouTube redonne À LA MÊME
    CLÉ porte cette source en confirmation, et elle compte ici.

    Rôles VOISINS (décision utilisateur 2026-11-23) : la même personne dans la
    même FAMILLE de rôles confirme aussi, et la page Genius y est un témoin —
    production (Producer, Co-Producer, Additional Production) et écriture
    (Writer, Composer, Lyricist). Une confirmation seulement, jamais une fusion
    de rôles ; chanter n'est pas écrire, un ingénieur n'est pas un producteur
    (Vocals, Engineer : hors familles)."""
    from src.utils.title_matching import normalize_name

    if cas.detecteur != "credits_api" or track is None or cas.nature == revue.RUN:
        return None
    api = [c for c in track.credits if c.source == revue.SOURCE_API]
    if not api:
        return None
    # Toutes les sources d'un crédit comptent, pas seulement celle de sa ligne
    # (`Credit.confirmations`, 2026-11-23) : la table n'a qu'une ligne par clé,
    # la seconde source qui donnait le même crédit était perdue.
    temoins = {
        (normalize_name(c.name), c.role)
        for c in track.credits
        if c.sources & SOURCES_TEMOINS_CREDITS and c.name
    }
    voisins = {
        (normalize_name(c.name), _FAMILLE_DE_ROLE[c.role])
        for c in track.credits
        if c.role in _FAMILLE_DE_ROLE
        and c.sources & (SOURCES_TEMOINS_CREDITS | SOURCES_TEMOINS_VOISINS)
        and c.name
    }

    def confirme(c) -> bool:
        cle = normalize_name(c.name)
        if (cle, c.role) in temoins:
            return True
        famille = _FAMILLE_DE_ROLE.get(c.role)
        return famille is not None and (cle, famille) in voisins

    confirmes = [c for c in api if confirme(c)]
    if len(confirmes) == len(api):
        return Arbitrage(
            "chaque crédit 🎫 est confirmé (même rôle ou rôle voisin) par une autre source : ✓ Normal",
            "normal",
            ", ".join(f"{c.name} ({c.role.value})" for c in confirmes),
        )
    return Arbitrage(
        "crédits 🎫 qu'aucune autre source ne confirme : indécidable",
        "indecidable",
        f"{len(confirmes)} / {len(api)} confirmé(s), même rôle ou rôle voisin",
    )


# ── Vidéo ordinaire d'un autre montage (2026-10-27) ────────────────────────

#: Interrupteur : `revue_auto.REGLES_VIDEO_BRANCHEES`.
REGLES_VIDEO = frozenset({("duree_isolee", "normal")})


def arbitrage_video_hors_colonne(cas, track, _ctx) -> Arbitrage | None:
    """Le témoin isolé est une VIDÉO ORDINAIRE (`youtube_video`,
    `youtube_officiel`) que deux catalogues d'accord démentent, et la colonne
    a leur durée : ✓ Normal. Aucune donnée ne change — une vidéo ordinaire ne
    fait jamais la durée d'une fiche qui a une plateforme
    (`DISCOGRAPHY_PRIORITIES`), un clip a ses intros et ses fins.

    Deux écarts à `arbitrage_valide`, mesurés sur les 9 cas du 2026-10-27 :
    · la piste YTM et l'audio Topic sont un AUTRE fichier que la vidéo : ils
      la démentent, même si le vote les range dans la même famille (Kanye
      « Only One » : vidéo 245 s, Apple et piste YTM 224 s) ;
    · versions et freestyles ne sont pas rendus à l'humain : rien n'étant
      modifié, l'ambiguïté du titre (décision du 2026-10-20) ne coûte rien
      (Booba « Kayna (Remix) », clip 171 s, catalogues 178-179 s)."""
    from src.enrichment.reconcile import _VIDEOS_ORDINAIRES

    if (cas.detecteur, "normal") not in REGLES_VIDEO or track is None or not track.duration:
        return None
    durees, editions = _durees(track), tuple(track.durees_editions or ())
    autres = {s: d for s, d in durees.items() if s not in _VIDEOS_ORDINAIRES}
    for source in sorted(_VIDEOS_ORDINAIRES):
        d = durees.get(source)
        if not d or _colonne_impactee(track, source):
            continue
        ref = _dementie({**autres, "video": d}, "video", editions, 2)
        if ref is None or abs(int(track.duration) - ref) > TOLERANCE_S:
            continue
        return Arbitrage(
            f"vidéo ordinaire ({source}) d'un autre montage ; la colonne garde "
            f"{track.duration} s",
            "normal",
            f"{source} {d} s contre {ref} s",
        )
    return None


# ── Piste YTM ou audio de la page d'un autre montage (2026-11-06) ──────────

#: Interrupteur : `revue_auto.REGLES_TEMOIN_BRANCHEES`.
REGLES_TEMOIN = frozenset({("duree_isolee", "normal")})


def _durees_lues_par_id(track, durees: dict, temoin: str) -> dict:
    """`{source: secondes}` des catalogues lus par un IDENTIFIANT, hors `temoin` :
    Deezer, Apple, la page Spotify, l'audio de la vidéo du lien, chacun si la
    fiche porte son identifiant. ReccoBeats est la voix de Spotify (sans page
    Spotify lue : `durees_du_vote`) ; lu par l'ISRC, il ne lit pas l'ID de la
    fiche, et ne compte que contre un simple hit de recherche."""
    lues = {}
    if track.deezer_id and durees.get("deezer"):
        lues["deezer"] = int(durees["deezer"])
    if track.apple_music_id and durees.get("apple_music"):
        lues["apple_music"] = int(durees["apple_music"])
    if temoin != revue.SOURCE_AUDIO and durees.get(revue.SOURCE_AUDIO):
        lues[revue.SOURCE_AUDIO] = int(durees[revue.SOURCE_AUDIO])
    if track.spotify_id and durees.get("spotify_web"):
        lues["spotify_web"] = int(durees["spotify_web"])
    if (
        track.spotify_id
        and durees.get("reccobeats")
        and (track.audio.reccobeats_resolution != "isrc" or temoin == _HIT_DE_RECHERCHE)
    ):
        lues["reccobeats"] = int(durees["reccobeats"])
    return lues


def arbitrage_temoin_hors_colonne(cas, track, _ctx) -> Arbitrage | None:
    """Le témoin isolé est la piste YTM ou l'audio de la vidéo du lien, DEUX
    catalogues (familles) lus par un identifiant le démentent en s'accordant à
    ± 2 s, et la colonne a leur valeur : ✓ Normal. Aucune donnée ne change, et le
    témoin ne tient pas la colonne (`_colonne_impactee`).

    Pendant de `arbitrage_video_hors_colonne`, qui ne couvre que les vidéos
    ordinaires (6 cas du 2026-11-06 : « (Rap) Superstar » : piste YTM 294 s,
    Deezer et Spotify 255 s, audio 256 s). Versions et génériques ne sont pas
    rendus à l'humain pour la même raison : rien n'est modifié.

    La vidéo que la PAGE désigne vaut plus qu'un hit : à plus de
    `ECART_VERSION_S`, sans piste YTM qui rejoigne les catalogues, ce sont
    peut-être eux qui lisent un autre morceau (Yelawolf « Radio », page
    *Radioactive* : audio et piste YTM 333 s ; Deezer et Spotify 267 s, sur
    *Sometimes Y*). L'humain."""
    if (cas.detecteur, "normal") not in REGLES_TEMOIN or track is None or not track.duration:
        return None
    durees = _durees(track)
    if "manual" in durees:
        return None
    piste = durees.get(_HIT_DE_RECHERCHE)
    for source in (revue.SOURCE_AUDIO, _HIT_DE_RECHERCHE):
        d = durees.get(source)
        lues = _durees_lues_par_id(track, durees, source)
        if not d or not lues or len({famille(s) for s in lues}) < 2:
            continue
        if max(lues.values()) - min(lues.values()) > ECART_FICHIER_S:
            continue
        ref = sorted(lues.values())[len(lues) // 2]
        if abs(int(d) - ref) <= TOLERANCE_S:
            continue
        if abs(int(track.duration) - ref) > ECART_FICHIER_S or _colonne_impactee(track, source):
            return None
        if (
            source == revue.SOURCE_AUDIO
            and abs(int(d) - ref) > ECART_VERSION_S
            and not (piste and abs(int(piste) - ref) <= ECART_FICHIER_S)
        ):
            return None
        return Arbitrage(
            f"témoin {source} d'un autre montage ; la colonne garde {track.duration} s",
            "normal",
            f"{source} {d} s contre {ref} s ({', '.join(sorted(lues))})",
        )
    return None


# ── Famille « ID Spotify proposé » — VALIDÉE le 2026-10-20 ─────────────────

#: Validée par l'utilisateur sur le passage à blanc restreint du 2026-10-20
#: (46 corrections : 40 poses, 6 refus) — `revue_auto.REGLES_ID_PROPOSE_BRANCHEES`.
REGLES_ID_PROPOSE = frozenset(
    {("id_spotify_propose", "poser_id"), ("id_spotify_propose", "refuser_id")}
)


def arbitrage_id_propose(cas, track, ctx, lire=None, gate=None) -> Arbitrage | None:
    """L'arbitrage de la famille « ID Spotify proposé ». `lire` (page embed)
    et `gate` (gate d'identité STRICT) sont injectables ; par défaut, la page
    est lue (réseau, gardée au brut) et le gate est celui des runs.

    Toute fiche sans ID passe par le gate, comme à l'étape Identité ; ce qu'il
    refuse est refusé par règle (rétablissable) — un titre générique dont le
    disque n'est pas montré le même, un freestyle auquel Spotify sert le
    titre d'une autre fiche (2026-10-31). Une POSE exige en plus une durée
    indépendante à ± 2 s, et le titre servi : un original sans ambiguïté pour
    un original ; pour une VERSION, le MÊME titre, descripteur compris
    (« Testify - Instrumental » = « Testify (Instrumental) » ; « Hustlin' -
    Remix » n'est pas « Hustlin' (French Remix) »). Décision utilisateur du
    2026-10-31, qui lève pour ce seul cas celle du 2026-10-20 (« une version
    ne reçoit jamais d'ID par règle ») : ce qui trompait le gate sur une
    version, c'est un autre nom de version, que le même titre exclut."""
    from src.utils.spotify_identity import artiste_homonyme
    from src.utils.version_descriptors import meme_titre_normalise

    if cas.detecteur != "id_spotify_propose" or track is None:
        return None
    if lire is None:
        from src.utils.spotify_identity import lire_identite_http as lire
    if gate is None:
        from src.utils.spotify_identity import valider_identite

        def gate(t, sid):
            return bool(valider_identite(t, sid, lire, lire_album=lambda _sid: None))

    arbitrage = politique_id_propose(lire, gate)(cas, track, ctx)
    if arbitrage is not None and arbitrage.action == "poser_id":
        # Le titre SERVI doit lui aussi être un original sans ambiguïté :
        # « Heartless - The Voice Performance » passait le gate (2026-10-20).
        servi = (lire(cas.preuves.get("spotify_id_propose")) or {}).get("name")
        if titre_d_original(track.title):
            if not titre_d_original(servi):
                return None
        elif not meme_titre_normalise(servi, track.title):
            return None
    if (
        arbitrage is not None
        and arbitrage.action == "refuser_id"
        and not titre_d_original(track.title)
        and not titre_generique(track.title)
    ):
        # Une version que le gate refuse sur son seul NOM (« Liberation Time -
        # Hip Hop RMX » pour « (Remix) ») quand un catalogue lu à part a la
        # durée servie : rien n'est montré, l'humain. Un titre générique dont
        # le disque n'est pas montré le même se refuse (gate, 2026-10-13).
        identite = lire(cas.preuves.get("spotify_id_propose")) or {}
        if _familles_qui_confirment(
            _durees(track), identite.get("duration")
        ) and not artiste_homonyme(track, identite):
            return None
    if arbitrage is not None and (cas.detecteur, arbitrage.action) in REGLES_ID_PROPOSE:
        return arbitrage
    return None


# ── Famille « écarts Deezer » — VALIDÉE le 2026-10-21 ──────────────────────

#: Restreinte aux originaux sans ambiguïté (piste ET fiche) : toute version
#: Deezer (live, remix, radio edit…) reste à l'humain, donc aussi les éditions
#: d'un socle ; un lien à revoir (délier) aussi. Interrupteur :
#: `revue_auto.REGLES_DEEZER_BRANCHEES` (validée : 45 corrections au passage à blanc).
REGLES_DEEZER = frozenset(
    {
        ("deezer_absent", "deezer_appliquer"),
        ("deezer_album_absent", "deezer_appliquer"),
        ("deezer_apparition", "deezer_appliquer"),
        ("deezer_absent", "deezer_rattacher_isrc"),
        ("deezer_album_absent", "deezer_rattacher_isrc"),
        ("deezer_apparition", "deezer_rattacher_isrc"),
        ("deezer_link_candidate", "deezer_appliquer"),
        ("deezer_link_candidate", "normal"),
        # 2026-11-06 : l'autre page Deezer a la durée de l'ID Spotify de la fiche
        # (Kery James, *92.2012*) ; validée le 2026-11-06 sur la simulation.
        ("deezer_link_candidate", "deezer_rattacher_edition"),
        # 2026-10-31 : édition de diffusion d'une fiche au sous-titre omis par
        # Deezer (Kid Cudi) ; simulée sur la copie, 3 / 4 `deezer_absent`.
        ("deezer_absent", "deezer_edition"),
        ("deezer_album_absent", "deezer_edition"),
        # 2026-11-02 : la piste d'une fiche de son disque, au titre de base et
        # à la durée de la fiche (Eminem, 9 / 16 `deezer_absent`).
        ("deezer_absent", "deezer_rattacher_fiche"),
        ("deezer_album_absent", "deezer_rattacher_fiche"),
        # A1 (décision utilisateur 2026-10-31) : l'édition de SUPPORT d'une
        # fiche (même ISRC, « Clean », BO) lui est rattachée ; la version
        # d'un autre enregistrement (live, remix, démo) devient une fiche
        # secondaire — le geste de DA1.
        ("deezer_version", "deezer_rattacher_edition"),
        ("deezer_album_absent", "deezer_rattacher_edition"),
        ("deezer_version", "deezer_appliquer"),
        # 2026-11-03 : la piste d'un autre que Deezer ne crédite pas à l'artiste,
        # sur un disque crédité à l'artiste (The Game, *G-Code*, BO de *Bosco*).
        ("deezer_absent", "normal"),
        ("deezer_album_absent", "normal"),
        # Règle C (2), décision utilisateur 2026-11-19 : le générique d'une
        # compilation se rattache à la fiche du disque le plus ancien.
        ("deezer_absent", "deezer_rattacher_generique"),
        ("deezer_album_absent", "deezer_rattacher_generique"),
        # Règle C : la version portée par une fiche sous un autre libellé.
        ("deezer_version", "deezer_rattacher_version"),
    }
)


def _rattacher_par_isrc(cas, ctx, isrcs_connus: dict, ids_artiste: set) -> Arbitrage | None:
    """La piste Deezer « absente » dont l'ISRC est celui d'UNE fiche de
    l'artiste, sa durée SOUTENUE par les autres catalogues de cette fiche
    (vote sans Deezer, dont l'observation peut venir de la piste même) : c'est
    elle, quel que soit le descripteur du titre (« Love64 (Interlude) » /
    « Love64 », « Joe Le Taxi (Inedit) »). Deux témoins indépendants, le code
    de l'enregistrement et la durée — la restriction aux originaux protège les
    rapprochements par TITRE, pas celui-ci (2026-10-27)."""
    if cas.detecteur == "deezer_link_candidate":
        return None
    arbitrage = politique_ecarts_deezer(isrcs_connus, ids_artiste)(cas, None, ctx)
    if arbitrage is None or arbitrage.action != "deezer_rattacher_isrc":
        return None
    piste = cas.preuves["ecart"]["piste"]
    isrc = str(piste.get("isrc")).upper()
    fiches = [t for t in ctx.disco if t.isrc and str(t.isrc).upper() == isrc]
    if len(fiches) != 1 or not piste.get("duration"):
        return None
    durees = {s: d for s, d in _durees(fiches[0]).items() if famille(s) != famille("deezer")}
    vote = voter(durees, int(piste["duration"]), editions=tuple(fiches[0].durees_editions or ()))
    if vote is None or vote.nature != SOUTENUE:
        return None
    return Arbitrage(
        arbitrage.sens,
        arbitrage.action,
        f"{isrc}, durée {piste['duration']} s soutenue par {', '.join(sorted(vote.pour))}",
    )


def _disque_tout_entier_de_la_famille(album_id, fam: str, lire_album: Callable | None) -> bool:
    """R2 (décision utilisateur 2026-11-13) : TOUTES les pistes du disque Deezer
    sont de la famille `fam` (« Projet Purrp Beam » d'Ocho : 11 pistes « …
    (Chopped & $crewed) », le titre du disque ne le dit pas). Illisible : non."""
    from src.utils.version_heritage import famille_de

    if lire_album is None or not album_id:
        return False
    pistes = lire_album(int(album_id)) or []
    return bool(pistes) and all(famille_de(p.get("title")) == fam for p in pistes)


#: Familles de version dont le libellé n'est jamais une mention de support :
#: à la durée du socle, c'est quand même une autre prise (règle C, décision
#: utilisateur 2026-11-19 — un live, une session « A COLORS SHOW » ou
#: « Deezer Sessions », un acoustique, un instrumental, un a cappella). Un
#: « (Remix) », un « (Edit Version) », une « Version Longue » à la durée du
#: socle restent à l'humain : souvent l'original mal étiqueté.
FAMILLES_JAMAIS_MENTION_DE_SUPPORT = frozenset({"instrumental", "a_cappella", "performance"})


def _famille_de_remix(fam: str | None) -> str | None:
    """« (Remix de B.O.S.S) » est un remix NU pour `famille_de`, « (B.O.S.S.
    Remix) » un remix NOMMÉ : une même famille pour `fiche_de_la_version`, la
    durée départage."""
    return "remix" if fam in ("remix_bare", "remix_named") else fam


def fiche_de_la_version(piste: dict, fiches) -> object | None:
    """PUR. Règle C, décision utilisateur 2026-11-19 : la fiche UNIQUE qui porte
    déjà la version Deezer sous un autre libellé (« J'appuie sur la gâchette
    (B.O.S.S. Remix) » 182 s → « (Remix de B.O.S.S) » 183 s ; « My Medicine
    (Demo) » → « (Demo Version) ») : même titre au descripteur près
    (`titres_equivalents`), même famille, sans page Deezer, sa durée à ± 2 s.
    Une performance exige le même titre normalisé : « Où va la vie? (Live) »
    du *Live au Dôme de Marseille* n'est pas « (Live en Egypt) », à 1 s près."""
    from src.utils.version_descriptors import meme_titre_normalise, titres_equivalents
    from src.utils.version_heritage import famille_de

    titre, duree = piste.get("title"), piste.get("duration")
    fam = famille_de(titre)
    if not duree or fam in (None, "edition"):
        return None
    candidates = [
        t
        for t in fiches
        if titres_equivalents(t.title, titre)
        and _famille_de_remix(famille_de(t.title)) == _famille_de_remix(fam)
        and (fam != "performance" or meme_titre_normalise(t.title, titre))
    ]
    if len(candidates) != 1:
        return None
    t = candidates[0]
    # Le même remixeur NOMMÉ des deux côtés (« (B.O.S.S. Remix) » / « (Remix de
    # B.O.S.S) ») : le même remix, à ± `ECART_VERSION_S` — la seule durée de ces
    # fiches Genius est souvent la recherche YTM par titre, à 3-6 s (5 doublons
    # créés chez NTM le 2026-11-24).
    ecart_max = ECART_VERSION_S if _meme_remixeur_nomme(titre, t.title) else ECART_FICHIER_S
    if t.deezer_id or not t.duration or abs(int(t.duration) - int(duree)) > ecart_max:
        return None
    return t


def _meme_remixeur_nomme(titre_deezer: str, titre_fiche: str) -> bool:
    """PUR. Le remixeur que nomme le titre Deezer est nommé, en mots entiers,
    dans le titre de la fiche."""
    from src.utils.title_matching import names_match_as_words
    from src.utils.version_descriptors import remixeur_du_titre

    remixeur = remixeur_du_titre(titre_deezer)
    return bool(remixeur) and names_match_as_words(remixeur, titre_fiche)


def _version_portee_par_une_fiche(cas, ctx) -> Arbitrage | None:
    """`fiche_de_la_version` pour une version Deezer : rattacher, ne pas créer."""
    if cas.detecteur != "deezer_version" or ctx is None:
        return None
    ecart = (cas.preuves or {}).get("ecart") or {}
    if ecart.get("genius"):
        return None
    piste = ecart.get("piste") or {}
    from src.services.revue import cibles_des_rattachements

    fiche = fiche_de_la_version(piste, cibles_des_rattachements(ctx))
    if fiche is None:
        return None
    return Arbitrage(
        f"version déjà portée par « {fiche.title} » : rattacher, ne pas créer",
        "deezer_rattacher_version",
        f"« {piste.get('title')} » {piste.get('duration')} s ; #{fiche.id} {fiche.duration} s",
    )


def _version_d_un_disque_de_versions(
    cas, ctx, lire_album: Callable | None = None
) -> Arbitrage | None:
    """DA1 (décision utilisateur 2026-10-31) : la piste d'un disque d'un TIERS
    qui est tout entier une version (« LM$ (Chopped & $crewed) » d'Ocho, C&S de
    l'album de Freeze Corleone), sans page Genius, de la même famille que son
    disque et version d'une fiche connue de l'artiste (`socle_id`), devient une
    fiche SECONDAIRE : version de l'original (relation `version_of`, posée par
    `creer_lignes`), ses streams jamais sommés à ceux de l'original — comme les
    15 fiches C&S de Freeze déjà créées. Instrumental et a cappella aussi
    (règle C, décision utilisateur 2026-11-19, qui lève DV1 : une version qui
    peut avoir des streams est une fiche ; l'étape identité lui pose son ID
    Spotify) — sans le garde de la durée du socle : leur libellé n'est jamais
    une mention de support, et leur durée est d'ordinaire celle du socle.

    A1 (décision utilisateur 2026-10-31) : le même geste pour la version
    Deezer (`deezer_version` : live, remix, démo, mix) d'une fiche sur un
    disque de l'artiste, quand aucune fiche ne la porte déjà — ni par son
    ISRC, ni par son titre (même version), ni comme édition d'une fiche
    (`titre_sans_edition`, le « (Remix) (Album Version Explicit) » d'une
    fiche de remix) — et que sa durée n'est pas celle du socle (± 2 s : une
    autre prise a sa durée ; à la même, un libellé de version peut être une
    mention de support)."""
    from src.utils.version_descriptors import titre_sans_edition, titres_equivalents
    from src.utils.version_heritage import famille_de

    if cas.detecteur not in ("deezer_apparition", "deezer_version"):
        return None
    ecart = (cas.preuves or {}).get("ecart") or {}
    if ecart.get("genius") or not ecart.get("socle_id"):
        return None
    socle = next((t for t in ctx.disco if t.id == ecart["socle_id"]), None) if ctx else None
    if socle is None:
        return None
    piste = ecart.get("piste") or {}
    titre = piste.get("title") or cas.morceau
    album = (ecart.get("album") or {}).get("title") or ""
    fam = famille_de(titre)
    if fam in (None, "edition"):
        return None
    if cas.detecteur == "deezer_apparition":
        album_id = (ecart.get("album") or {}).get("id")
        if famille_de(album) != fam and not _disque_tout_entier_de_la_famille(
            album_id, fam, lire_album
        ):
            return None
        sens = "disque de versions d'un tiers"
    else:
        isrc, duree = str(piste.get("isrc") or "").upper(), piste.get("duration")
        if not duree:
            return None
        # Une mention de support (« (Live) [Bonus Track] ») sur une prise qui
        # n'en est jamais une : la version se juge aussi sous le titre sans
        # elle (règle C, 2026-11-19). Sur un remix (« Shake That (Remix) (Album
        # Version Explicit) »), c'est l'édition d'une autre fiche : l'humain.
        sans_mention = titre_sans_edition(titre)
        if sans_mention is not None and fam not in FAMILLES_JAMAIS_MENTION_DE_SUPPORT:
            return None
        titres = {titre, sans_mention or titre}
        if any(
            # Le même MORCEAU dans la même version : `meme_version` ne jugeait que
            # le descripteur — « Crack Music (Live at Abbey Road Studios) »
            # écartait « Dreams (Live from Dublin Version) » (The Game, 2026-11-03).
            (isrc and t.isrc and str(t.isrc).upper() == isrc)
            or any(titres_equivalents(t.title, x) for x in titres)
            for t in ctx.disco
        ):
            return None
        meme_duree = socle.duration and abs(int(socle.duration) - int(duree)) <= ECART_FICHIER_S
        if meme_duree and fam not in FAMILLES_JAMAIS_MENTION_DE_SUPPORT:
            return None
        sens = "aucune fiche ne la porte"
    return Arbitrage(
        f"créer la fiche secondaire (version d'une fiche, {sens})",
        "deezer_appliquer",
        f"« {album} » ({fam}) ; version de « {socle.title} »",
    )


def _duree_lue_par_id(fiche, duree, deezer_id) -> str | None:
    """Le catalogue de la fiche, lu par SON identifiant, qui a `duree` à
    `ECART_FICHIER_S` près — la durée Deezer de la fiche ne compte que si elle
    a une AUTRE page Deezer que la piste jugée (sinon, une durée trouvée au
    titre)."""
    durees = _durees(fiche)
    for s in _CATALOGUES_LUS_PAR_ID:
        if s == "deezer" and (not fiche.deezer_id or int(fiche.deezer_id) == int(deezer_id or 0)):
            continue
        if durees.get(s) and abs(int(durees[s]) - int(duree)) <= ECART_FICHIER_S:
            return s
    return None


def fiche_de_l_edition_de_support(piste: dict, fiches) -> tuple[object, str] | None:
    """PUR. A1 (décision utilisateur 2026-10-31) : la fiche UNIQUE dont la
    piste Deezer est une édition de SUPPORT — même enregistrement sur un autre
    disque (best-of, bande originale, single « Clean »). Deux témoins :
      · l'identité : le même ISRC, sinon le même titre de base
        (`titre_de_base` : ni sous-titre, ni édition de diffusion, ni mention
        de bande originale ; une version n'en a pas) ;
      · la durée : un catalogue de la fiche lu par son identifiant
        (`_duree_lue_par_id`) l'a à ± 2 s.
    Rend `(fiche, preuve)`, ou None (aucune, ou plusieurs : l'humain)."""
    from src.utils.version_descriptors import meme_titre_normalise, titre_de_base

    duree, isrc = piste.get("duration"), str(piste.get("isrc") or "").upper()
    if not duree:
        return None
    par_isrc = [t for t in fiches if isrc and t.isrc and str(t.isrc).upper() == isrc]
    if len(par_isrc) > 1:
        return None
    if par_isrc:
        candidates, preuve = par_isrc, f"ISRC {isrc}"
    else:
        base = titre_de_base(piste.get("title"))
        if not base:
            return None
        # Homonymes au titre de base : la durée lue par ID les départage.
        candidates = [
            t
            for t in fiches
            if not t.secondary_role and meme_titre_normalise(titre_de_base(t.title), base)
        ]
        preuve = f"titre de base « {base} »"
    temoins = [(t, s) for t in candidates if (s := _duree_lue_par_id(t, duree, piste.get("id")))]
    if len(temoins) != 1:
        return None
    fiche, source = temoins[0]
    return fiche, f"{preuve} ; {source} {_durees(fiche)[source]} s pour {duree} s"


def _edition_de_support(cas, ctx) -> Arbitrage | None:
    """A1 : la piste « version » ou d'un disque absent est l'édition de
    support d'une fiche (`fiche_de_l_edition_de_support`) : rattacher — une
    seconde page Deezer, une édition de diffusion, notées en ÉDITION de la
    fiche (`ecarts_deezer._renseigner_fiche`), jamais une fiche."""
    if (
        cas.detecteur
        not in ("deezer_version", "deezer_album_absent", "deezer_absent", "deezer_apparition")
        or ctx is None
    ):
        return None
    piste = ((cas.preuves or {}).get("ecart") or {}).get("piste") or {}
    trouvee = fiche_de_l_edition_de_support(piste, ctx.disco)
    if trouvee is None:
        return None
    fiche, preuve = trouvee
    return Arbitrage(
        f"édition de support de « {fiche.title} » : rattacher, ne pas créer",
        "deezer_rattacher_edition",
        f"« {piste.get('title')} » ; #{fiche.id} ; {preuve}",
    )


def _edition_a_la_duree_de_l_id_spotify(cas, track) -> Arbitrage | None:
    """Une AUTRE page Deezer, au même titre, a la durée de l'ID Spotify de la
    fiche à ± 2 s : les deux catalogues ont la même édition du disque, dont la
    durée diffère de la page principale de la fiche. Elle se note en édition, et
    `duree_spotify` tombe seul : le vote lit les durées d'édition. Un
    rapprochement ambigu ou un titre générique reste à l'humain, une page
    refusée n'est jamais rattachée, ni un AUTRE enregistrement
    (`ecarts_deezer.autre_enregistrement`, règle 1 du 2026-11-22) : le cas qui
    l'avait fait écrire, « 28 Décembre 1977 » de *92.2012* (537 s, la fiche à
    581 s), était un réenregistrement à l'ISRC neuf, et l'ID Spotify de la
    fiche celui de *92.2012* (Kery James, 2026-11-14)."""
    from src.utils.corrections_fiches import deezer_refuses
    from src.utils.version_descriptors import meme_titre_normalise

    if cas.detecteur != "deezer_link_candidate" or track is None:
        return None
    ecart = (cas.preuves or {}).get("ecart") or {}
    piste = ecart.get("piste") or {}
    duree, spotify = piste.get("duration"), _durees(track).get("spotify_web")
    if ecart.get("matched_by") != "title" or not (duree and spotify and track.spotify_id):
        return None
    if not track.deezer_id or str(track.deezer_id) == str(piste.get("id")):
        return None
    if abs(int(duree) - int(spotify)) > ECART_FICHIER_S:
        return None
    if not meme_titre_normalise(piste.get("title"), track.title) or titre_generique(track.title):
        return None
    if int(piste.get("id") or 0) in deezer_refuses(track):
        return None
    # Règle 1 (2026-11-22) : l'ID Spotify peut être celui du réenregistrement
    # (le cas même de *92.2012*) ; la page Deezer et Apple de la fiche, eux,
    # disent si la piste est un autre enregistrement.
    from src.services.ecarts_deezer import autre_enregistrement

    if autre_enregistrement(piste.get("isrc"), duree, piste.get("id"), track):
        return None
    disque = (ecart.get("album") or {}).get("title")
    return Arbitrage(
        f"édition du disque « {disque} » : rattacher à « {track.title} »",
        "deezer_rattacher_edition",
        f"page {piste.get('id')} {duree} s = ID Spotify {spotify} s ; "
        f"page de la fiche {track.deezer_id}",
    )


def _edition_d_une_fiche(cas, ctx, ids_artiste: set) -> Arbitrage | None:
    """La piste Deezer « absente » d'un disque PROPRE est une édition de
    diffusion (« Album Version Edited », « Explicit ») d'une fiche dont Deezer
    omet le sous-titre Genius, à la durée de la fiche ou d'une de ses
    éditions (`editions.socle_au_sous_titre_pres`) : le même enregistrement,
    noté en ÉDITION avec sa durée (décision utilisateur 2026-10-19), pas une
    fiche à créer (Kid Cudi, 2026-10-31)."""
    from src.services.editions import socle_au_sous_titre_pres

    if cas.detecteur not in ("deezer_absent", "deezer_album_absent") or ctx is None:
        return None
    ecart = (cas.preuves or {}).get("ecart") or {}
    piste, album = ecart.get("piste") or {}, ecart.get("album") or {}
    if album.get("artist_id") not in ids_artiste:
        return None
    titre = piste.get("title") or cas.morceau
    socle = socle_au_sous_titre_pres(titre, piste.get("duration"), ctx.disco)
    if socle is None:
        return None
    return Arbitrage(
        f"ÉDITION de « {socle.title} » (sa durée propre), pas de fiche",
        "deezer_edition",
        f"« {titre} » {piste.get('duration')} s ; « {socle.title} » {socle.duration} s",
    )


def _generique_au_nom_du_disque(titre: str | None, disque: str | None, fiche) -> bool:
    """PUR. « No Pressure Intro » sur *No Pressure* est la fiche « No Pressure »
    de ce disque quand elle n'a pas de page Deezer : Genius nomme cette plage du
    nom du disque (Logic #19125, 175 s des deux côtés, paroles « [Intro: Thalia] »,
    2026-11-06). La durée reste exigée par l'appelant."""
    from src.utils.version_descriptors import meme_titre_normalise

    if fiche.deezer_id or not disque or not meme_titre_normalise(fiche.title, disque):
        return False
    return any(meme_titre_normalise(titre, f"{disque} {mot}") for mot in ("Intro", "Outro"))


def fiche_du_meme_disque(piste: dict, disque: str | None, fiches) -> object | None:
    """PUR. La fiche UNIQUE de l'artiste qu'est la piste Deezer « absente » :
    du même disque (`meme_disque`), au même titre de base (`titre_de_base` :
    ni sous-titre, ni édition de diffusion — « (From "8 Mile" Soundtrack) »,
    « (Album Version Explicit) » / « (Bonus Track) », l'année que Genius ajoute
    à un skit récurrent, « Em Calls Paul (Skit) [2004] ») et à sa durée
    (± 2 s, la colonne de la fiche). Une version (live, remix…) n'a pas de
    titre de base : jamais (2026-11-02, Eminem : 9 / 16 `deezer_absent`).
    Le titre de base se lit aux lettres masquées près (`meme_titre_de_base` :
    « Da S*** (Album Version) » = « Da Shit ») et sans le nom du disque que
    Genius ajoute à un générique (« Intro (LAX) », `titre_sans_son_disque`) —
    The Game, 2026-11-03. Le générique au nom du disque est la plage-titre sans
    page Deezer (`_generique_au_nom_du_disque`, 2026-11-06)."""
    from src.utils.version_descriptors import (
        meme_disque,
        meme_titre_de_base,
        titre_de_base,
        titre_sans_son_disque,
    )

    base, duree = titre_de_base(piste.get("title")), piste.get("duration")
    if not base or not duree or not disque:
        return None
    candidates = [
        t
        for t in fiches
        if meme_disque(t.album, disque)
        and (
            meme_titre_de_base(titre_sans_son_disque(t.title, t.album), piste.get("title"))
            or _generique_au_nom_du_disque(piste.get("title"), disque, t)
        )
        and t.duration
        and abs(int(t.duration) - int(duree)) <= ECART_FICHIER_S
    ]
    return candidates[0] if len(candidates) == 1 else None


def _fiches_au_titre_du_disque(piste: dict, disque: str | None, fiches) -> list:
    """PUR. Les fiches de `disque` au titre de base de la piste."""
    from src.utils.version_descriptors import (
        meme_disque,
        meme_titre_de_base,
        titre_de_base,
        titre_sans_son_disque,
    )

    if not disque or not titre_de_base(piste.get("title")):
        return []
    return [
        t
        for t in fiches
        if meme_disque(t.album, disque)
        and meme_titre_de_base(titre_sans_son_disque(t.title, t.album), piste.get("title"))
    ]


def fiche_sans_duree_du_disque(piste: dict, disque: str | None, fiches) -> object | None:
    """PUR. La fiche UNIQUE de son disque à ce titre de base, qui n'a ni durée
    ni ID Deezer : rien ne s'oppose à la piste, qui les lui apporte (Souffrance,
    *Noctambus*, 2026-11-04 : 11 / 21 `deezer_absent`). Sauf si les paroles
    Genius ne tiennent pas dans la piste : c'en est une coupe
    (`edition_du_meme_disque`)."""
    from src.utils.duree_youtube import trop_courte_pour_les_paroles

    memes = _fiches_au_titre_du_disque(piste, disque, fiches)
    duree = piste.get("duration")
    if len(memes) != 1 or not duree:
        return None
    t = memes[0]
    if t.duration or t.deezer_id or trop_courte_pour_les_paroles(duree, t.lyrics.text):
        return None
    return t


def edition_du_meme_disque(piste: dict, disque: str | None, fiches) -> object | None:
    """PUR. La fiche UNIQUE de son disque à ce titre de base, que la piste ne
    peut pas renseigner : une AUTRE durée (Souffrance, *Noctambus* : « Pangolin
    ( 2020 ) » 122 s, fiche 149 s, le single), ou trop courte pour ses paroles.
    Une ÉDITION, avec SA durée (décision 2026-10-19), pas une fiche. Jamais un
    titre générique."""
    from src.utils.duree_youtube import trop_courte_pour_les_paroles
    from src.utils.version_descriptors import titre_generique

    memes = _fiches_au_titre_du_disque(piste, disque, fiches)
    duree = piste.get("duration")
    if len(memes) != 1 or not duree or titre_generique(piste.get("title")):
        return None
    t = memes[0]
    if t.duration:
        return t if abs(int(t.duration) - int(duree)) > ECART_FICHIER_S else None
    return t if t.deezer_id or trop_courte_pour_les_paroles(duree, t.lyrics.text) else None


def _generique_de_son_disque(cas, track, ctx) -> Arbitrage | None:
    """Parution à confirmer d'un titre GÉNÉRIQUE (« Intro », « Outro ») sur le
    disque même de la fiche : pas d'ambiguïté quand la fiche est la SEULE de
    ce disque à ce titre de base et à sa durée (`fiche_du_meme_disque`), et
    qu'un catalogue lu par un autre identifiant que Deezer la confirme à ± 2 s
    (The Game, *LAX* : « Intro (LAX) » 80 s et « Outro (LAX) » 88 s, Spotify
    de la fiche = Deezer ; 2026-11-03). Un générique d'un AUTRE disque est
    jugé par `_hors_du_disque_de_la_fiche`."""
    if cas.detecteur != "deezer_link_candidate" or track is None or ctx is None:
        return None
    ecart = (cas.preuves or {}).get("ecart") or {}
    if ecart.get("matched_by") != "title_generique":
        return None
    piste, album = ecart.get("piste") or {}, ecart.get("album") or {}
    if fiche_du_meme_disque(piste, album.get("title"), ctx.disco) is not track:
        return None
    duree = piste.get("duration")
    accord = sorted(
        s
        for s, d in _durees(track).items()
        if s not in FRAGILES
        and s not in ("deezer", "heritage", "manual", "youtube_video")
        and d
        and abs(int(d) - int(duree)) <= ECART_FICHIER_S
    )
    if not accord:
        return None
    return Arbitrage(
        "rattacher (titre générique de son disque)",
        "deezer_appliquer",
        f"« {piste.get('title')} » de « {album.get('title')} » {duree} s = {', '.join(accord)}",
    )


def _rattacher_a_la_fiche_du_disque(cas, ctx) -> Arbitrage | None:
    """La piste « absente » est celle d'une fiche existante de son disque
    (`fiche_du_meme_disque`) : rattacher, pas créer."""
    if cas.detecteur not in ("deezer_absent", "deezer_album_absent") or ctx is None:
        return None
    ecart = (cas.preuves or {}).get("ecart") or {}
    piste, album = ecart.get("piste") or {}, ecart.get("album") or {}
    fiche = fiche_du_meme_disque(piste, album.get("title"), ctx.disco)
    preuve = "même disque, même durée"
    if fiche is None:
        fiche = fiche_sans_duree_du_disque(piste, album.get("title"), ctx.disco)
        preuve = "même disque, fiche sans durée"
    if fiche is None:
        return None
    return Arbitrage(
        f"piste de « {fiche.title} » ({preuve}) : rattacher, ne pas créer",
        "deezer_rattacher_fiche",
        f"« {piste.get('title')} » {piste.get('duration')} s de « {album.get('title')} » ; "
        f"#{fiche.id} {fiche.duration} s sur « {fiche.album} »",
    )


def _edition_du_disque(cas, ctx) -> Arbitrage | None:
    """La piste « absente » est une édition de la fiche de son disque
    (`edition_du_meme_disque`) : notée sur elle, pas de fiche."""
    if cas.detecteur not in ("deezer_absent", "deezer_album_absent") or ctx is None:
        return None
    ecart = (cas.preuves or {}).get("ecart") or {}
    piste, album = ecart.get("piste") or {}, ecart.get("album") or {}
    fiche = edition_du_meme_disque(piste, album.get("title"), ctx.disco)
    if fiche is None:
        return None
    return Arbitrage(
        f"ÉDITION de « {fiche.title} » (même disque, sa durée propre), pas de fiche",
        "deezer_edition",
        f"« {piste.get('title')} » {piste.get('duration')} s ; #{fiche.id} {fiche.duration} s",
    )


def _piste_nue_d_un_disque_live(cas) -> bool:
    """La piste d'un disque LIVE (MusicBrainz, e64, ou le titre du disque)
    au titre sans version : Deezer omet « (Live) » (*Le tour de la question*
    de MC Solaar, 2026-11-02 : 7 pistes `deezer_absent` qu'une règle aurait
    créées ou rattachées au titre des fiches studio). Ni fiche, ni lien par
    règle : l'humain (`studio_sur_disque_live`)."""
    from src.utils.deezer_identity import album_live
    from src.utils.version_descriptors import porte_une_version, prise_live

    ecart = (cas.preuves or {}).get("ecart") or {}
    album = ecart.get("album") or {}
    if not (album_live({"album": album}) or prise_live(album.get("title"))):
        return False
    return not porte_une_version((ecart.get("piste") or {}).get("title") or cas.morceau)


def _credits_de_la_piste(cas, lire_deezer: Callable) -> list[dict]:
    """Les contributeurs Deezer de la piste du cas (`GET /track/{id}`) ; vide
    si illisibles."""
    piste = ((cas.preuves or {}).get("ecart") or {}).get("piste") or {}
    if not piste.get("id"):
        return []
    lue = lire_deezer(int(piste["id"])) or {}
    return [c for c in lue.get("contributors") or [] if isinstance(c, dict)]


#: Contributeurs (hors principal) à partir desquels une liste de crédits
#: muette dans le titre est suspecte : `credits_de_complaisance`.
CONTRIBUTEURS_DE_COMPLAISANCE = 5


def credits_de_complaisance(titre: str, contributeurs: list[str], principal: str | None) -> bool:
    """PUR. Une piste qui crédite au moins `CONTRIBUTEURS_DE_COMPLAISANCE`
    contributeurs en plus de son principal, dont moins de la moitié sont nommés
    dans son titre : des crédits de complaisance (Felisberto Araujo, *TESTE DE
    FÉ* : KIDS SEE GHOSTS, Juice WRLD, Pop Smoke, Only The Family… sur un rap
    brésilien ; 16 fiches créées chez KSG et Kanye le 2026-11-13). Un vrai
    featuring à crédit de piste reste en deçà (simulé le 2026-11-13 sur les 229
    fiches sans page ni ID Spotify : aucun ; « J'entre dans le rap », 13
    contributeurs, est sur le disque de Swift Guad lui-même). La création de
    fiche va alors à l'humain."""
    from src.utils.title_matching import contains_as_words, names_match_as_words

    autres = [
        n for n in contributeurs if n and not (principal and names_match_as_words(principal, n))
    ]
    if len(autres) < CONTRIBUTEURS_DE_COMPLAISANCE:
        return False
    nommes = sum(1 for n in autres if contains_as_words(n, titre or ""))
    return nommes * 2 < len(autres)


def _nomme_dans_un_credit(credits: list[dict], nom: str) -> bool:
    """Un contributeur dont le nom CONTIENT celui de l'artiste (mots entiers) :
    un duo, une formation (« Caballero & JeanJass »)."""
    from src.utils.title_matching import contains_as_words

    return bool(nom) and any(contains_as_words(nom, c.get("name") or "") for c in credits)


def _nom_de_l_artiste(ctx) -> str:
    """Le nom de l'artiste de la discographie du contexte (le plus fréquent)."""
    from collections import Counter

    noms = Counter(t.artist.name for t in (ctx.disco if ctx is not None else ()) if t.artist)
    return noms.most_common(1)[0][0] if noms else ""


def _credite_l_artiste(credits: list[dict], ids_artiste: set, nom: str) -> bool:
    from src.utils.title_matching import names_match_as_words

    return any(
        c.get("id") in ids_artiste or (nom and names_match_as_words(nom, c.get("name") or ""))
        for c in credits
    )


def _piste_d_un_autre_artiste(
    cas, ids_artiste: set, lire_deezer: Callable, nom_artiste: str = ""
) -> Arbitrage | None:
    """La piste « absente » que Deezer ne crédite pas à l'artiste — ni par
    l'ID de l'artiste ou de ses formations, ni par son nom sous un autre profil :
    la piste d'un autre sur une compilation, un EP partagé, une bande originale
    (The Game, 2026-11-03 : *G-Code*, les quatre pistes de Garry ; « Shoot
    First » de Big Kuza sur la BO de *Bosco*). Pas de fiche. Vaut aussi sur le
    disque d'un TIERS où l'artiste n'a qu'une piste (R1, décision utilisateur
    2026-11-13 : Kaaris sur *Classico* de Galactik beat, JID sur la BO de
    *Madden NFL 22* — 27 pistes d'autres artistes). Les crédits se lisent à la
    piste (`GET /track/{id}`, `contributors`) ; illisibles ou vides, rien n'est
    prouvé : l'humain."""
    if cas.detecteur not in ("deezer_absent", "deezer_album_absent"):
        return None
    ecart = (cas.preuves or {}).get("ecart") or {}
    piste, album = ecart.get("piste") or {}, ecart.get("album") or {}
    credits = _credits_de_la_piste(cas, lire_deezer)
    if not credits:
        return None
    propre = album.get("artist_id") in ids_artiste
    # Sur le disque d'un tiers, l'ID seul prouve l'artiste (homonymes).
    nom = (album.get("artist_name") if propre else "") or ""
    if _credite_l_artiste(credits, ids_artiste, nom):
        return None
    if not propre and _nomme_dans_un_credit(credits, nom_artiste):
        # « Caballero & JeanJass » sur *Double Hélice 2* : sa formation, peut-être
        # pas encore liée — ni « pas de fiche » ni création : l'humain.
        return None
    return Arbitrage(
        "piste d'un autre artiste (crédits Deezer) : pas de fiche",
        "normal",
        f"« {piste.get('title')} » de « {album.get('title')} » créditée à "
        + ", ".join(c.get("name") or "?" for c in credits),
    )


def _piste_creditee_sur_le_disque_d_un_autre(
    cas, ids_artiste: set, lire_deezer: Callable, nom_artiste: str
) -> Arbitrage | None:
    """R1, variante « créer » (décision utilisateur 2026-11-13) : la piste
    « absente » du disque d'un TIERS que Deezer crédite à l'artiste (invité :
    Furax sur *Jour De Deuil* de Reda La pire des races, Aketo chez L'Homme de
    L'Ombre) est une participation : la fiche se crée, comme une apparition.
    Titre d'original aux mentions près ; un titre générique reste à l'humain."""
    if cas.detecteur not in ("deezer_absent", "deezer_album_absent"):
        return None
    ecart = (cas.preuves or {}).get("ecart") or {}
    piste, album = ecart.get("piste") or {}, ecart.get("album") or {}
    if album.get("artist_id") in ids_artiste:
        return None
    titre = piste.get("title") or cas.morceau
    if titre_generique(titre) or not titre_d_original_a_la_mention_pres(titre):
        return None
    credits = _credits_de_la_piste(cas, lire_deezer)
    # L'ID seul (homonymes) ; un nom composé qui le contient (sa formation) : l'humain.
    if not credits or not _credite_l_artiste(credits, ids_artiste, ""):
        return None
    if credits_de_complaisance(
        titre, [c.get("name") or "" for c in credits], album.get("artist_name")
    ):
        return None
    if any(c.get("id") not in ids_artiste for c in credits) and _nomme_dans_un_credit(
        [c for c in credits if c.get("id") not in ids_artiste], nom_artiste
    ):
        return None
    return Arbitrage(
        f"piste créditée à l'artiste sur le disque de {album.get('artist_name') or 'un autre'} : "
        "créer la fiche",
        "deezer_appliquer",
        f"« {titre} » de « {album.get('title')} » — "
        + ", ".join(c.get("name") or "?" for c in credits),
    )


def arbitrage_deezer(
    cas,
    track,
    ctx,
    isrcs_connus: dict,
    ids_artiste: set,
    lire_deezer=None,
    lire_album=None,
    nom_artiste: str = "",
):
    """L'arbitrage de la famille « écarts Deezer », restreint aux originaux
    sans ambiguïté — le titre de la PISTE Deezer et celui de la fiche.
    `lire_deezer` (`id -> GET /track`) : par défaut, Deezer."""
    if not any(cas.detecteur == d for d, _ in REGLES_DEEZER):
        return None
    if _piste_nue_d_un_disque_live(cas):
        return None
    if ((cas.preuves or {}).get("ecart") or {}).get("disque_etranger"):
        return None  # un homonyme sur le même profil Deezer ? l'humain
    if lire_deezer is None:
        from src.utils.deezer_identity import lire_piste_http as lire_deezer
    lire_deezer = functools.lru_cache(maxsize=None)(lire_deezer)
    nom_artiste = nom_artiste or _nom_de_l_artiste(ctx)
    if lire_album is None:
        from src.utils.deezer_identity import lire_pistes_album_http as lire_album
    d_un_autre = _piste_d_un_autre_artiste(cas, ids_artiste, lire_deezer, nom_artiste)
    if d_un_autre is not None:
        return d_un_autre
    par_isrc = _rattacher_par_isrc(cas, ctx, isrcs_connus, ids_artiste)
    if par_isrc is not None:
        return par_isrc
    edition = _edition_d_une_fiche(cas, ctx, ids_artiste)
    if edition is not None:
        return edition
    du_disque = _rattacher_a_la_fiche_du_disque(cas, ctx)
    if du_disque is not None:
        return du_disque
    du_meme_disque = _edition_du_disque(cas, ctx)
    if du_meme_disque is not None:
        return du_meme_disque
    de_support = _edition_de_support(cas, ctx)
    if de_support is not None:
        return de_support
    a_la_duree_spotify = _edition_a_la_duree_de_l_id_spotify(cas, track)
    if a_la_duree_spotify is not None:
        return a_la_duree_spotify
    creditee = _piste_creditee_sur_le_disque_d_un_autre(cas, ids_artiste, lire_deezer, nom_artiste)
    if creditee is not None:
        return creditee
    medley = _medley_aux_parties_connues(cas, ctx, ids_artiste)
    if medley is not None:
        return medley if medley.action else None
    generique = _generique_d_un_disque_propre(cas, ctx, ids_artiste)
    if generique is not None:
        return generique
    if cas.detecteur == "deezer_version":
        # Une version n'est jamais un original : la fiche secondaire d'une
        # version qu'aucune fiche ne porte (A1 ; instrumental et a cappella
        # compris depuis la règle C, 2026-11-19).
        arbitrage = politique_ecarts_deezer(isrcs_connus, ids_artiste)(cas, track, ctx)
        if arbitrage is not None and arbitrage.action == "deezer_rattacher_isrc":
            return None  # l'ISRC d'une fiche (peut-être d'un autre artiste) : l'humain
        portee = _version_portee_par_une_fiche(cas, ctx)
        if portee is not None:
            return portee
        return _version_d_un_disque_de_versions(cas, ctx, lire_album)
    piste = ((cas.preuves or {}).get("ecart") or {}).get("piste") or {}
    disque_de_versions = _version_d_un_disque_de_versions(cas, ctx, lire_album)
    if disque_de_versions is not None:
        return disque_de_versions
    generique = _generique_de_son_disque(cas, track, ctx)
    if generique is not None:
        return generique
    titre_piste = piste.get("title") or cas.morceau
    aux_mentions_pres = False
    if not titre_d_original(titre_piste):
        if cas.detecteur == "deezer_link_candidate":
            return None
        if not titre_d_original_a_la_mention_pres(titre_piste):
            return None
        # R5 (décision utilisateur 2026-11-13) : une mention de support (« Bonus
        # Track », bande originale, feat.) ne rend pas le titre ambigu — pour
        # CRÉER seulement, et si aucune fiche n'a ce titre de base ni cet ISRC
        # (sinon c'est elle, ou une édition à prouver par la durée : l'humain).
        if _une_fiche_porte_deja(piste, titre_piste, ctx):
            return None
        aux_mentions_pres = True
    if track is not None and not titre_d_original(track.title):
        return None
    if cas.detecteur == "deezer_link_candidate":
        # Un rapprochement par titre AMBIGU (ligne d'invité d'un autre
        # principal, titre générique, plusieurs fiches au même titre) reste à
        # l'humain : c'est l'ambiguïté même que le classement signale.
        preuve = ((cas.preuves or {}).get("ecart") or {}).get("matched_by")
        if preuve in ("title_autre_principal", "title_generique", "title_ambigu"):
            return None
        arbitrage = politique_liens_deezer(cas, track, ctx)
    else:
        arbitrage = politique_ecarts_deezer(isrcs_connus, ids_artiste)(cas, track, ctx)
    if aux_mentions_pres and (arbitrage is None or arbitrage.action != "deezer_appliquer"):
        return None
    if arbitrage is not None and (cas.detecteur, arbitrage.action) in REGLES_DEEZER:
        return arbitrage
    return None


def _medley_aux_parties_connues(cas, ctx, ids_artiste: set) -> Arbitrage | None:
    """Règle C (3), décision utilisateur 2026-11-19 : la piste « absente » d'un
    disque PROPRE dont le titre joint plusieurs titres (« A + B », « A / B »)
    qui sont TOUS des fiches de l'artiste (`contenants.proposer_contenus` :
    une fiche par partie, un générique sur le même disque) est un medley : la
    fiche se crée, la règle des titres joints (fin d'étape discographie) la
    relie à ses parties par `contient`. Une partie inconnue : l'humain —
    rendu comme un arbitrage SANS action, que l'appelant change en None (sinon
    le titre joint passait pour un original et la fiche se créait)."""
    from types import SimpleNamespace

    from src.utils.contenants import parties_du_titre, proposer_contenus

    if cas.detecteur not in ("deezer_absent", "deezer_album_absent") or ctx is None:
        return None
    ecart = (cas.preuves or {}).get("ecart") or {}
    piste, album = ecart.get("piste") or {}, ecart.get("album") or {}
    if album.get("artist_id") not in ids_artiste:
        return None
    titre = piste.get("title") or cas.morceau
    if not parties_du_titre(titre):
        return None
    medley = SimpleNamespace(id=None, title=titre, album=album.get("title"))
    contenus = proposer_contenus(medley, ctx.disco)
    if not contenus:
        return Arbitrage("medley dont une partie n'est pas une fiche : l'humain", "", titre)
    return Arbitrage(
        "medley dont toutes les parties sont des fiches : créer la fiche",
        "deezer_appliquer",
        f"« {titre} » de « {album.get('title')} » contient "
        + ", ".join(f"#{f.id} « {f.title} »" for f in contenus),
    )


#: Disques Deezer où un générique propre se CRÉE (règle C (2)) : un album ou
#: un EP ; un single ou une compilation ne fait que s'y rattacher.
TYPES_DU_DISQUE_D_ORIGINE = ("album", "ep")


def _meme_plage(titre: str, duree, autre_titre: str | None, autre_duree, disque=None) -> bool:
    """PUR. Le même titre générique (au nom du disque près, `titre_sans_son_disque`)
    à ± 2 s."""
    from src.utils.version_descriptors import meme_titre_de_base, titre_sans_son_disque

    return bool(
        duree
        and autre_duree
        and abs(int(duree) - int(autre_duree)) <= ECART_FICHIER_S
        and meme_titre_de_base(titre_sans_son_disque(autre_titre, disque), titre)
    )


def _plage_titre_sans_page_deezer(fiche, disque: str | None) -> bool:
    """PUR. La fiche du disque qui porte son nom, sans page Deezer : Genius
    nomme parfois ainsi l'intro du disque (`_generique_au_nom_du_disque`)."""
    from src.utils.version_descriptors import meme_disque, meme_titre_normalise

    return bool(
        disque
        and not fiche.deezer_id
        and meme_disque(fiche.album, disque)
        and meme_titre_normalise(fiche.title, disque)
    )


def _date_du_disque(album: dict) -> str:
    return str(album.get("release_date") or "")


def generique_du_disque_le_plus_ancien(piste: dict, album: dict, fiches, absentes) -> tuple:
    """PUR. Règle C (2), décision utilisateur 2026-11-19 : le générique
    (« Intro », « Outro », « Interlude ») « absent » d'un disque propre.
      · une fiche UNIQUE de l'artiste a cette plage (même titre, ± 2 s) et une
        date de sortie qui n'est pas après celle du disque : la compilation
        (ou la réédition) s'y rattache → `("rattacher", fiche)` ;
      · aucune fiche, et le disque est l'album ou l'EP le plus ANCIEN parmi
        ceux qui portent la plage « absente » (`absentes` : les écarts de
        l'artiste) : la fiche se crée sur lui → `("creer", None)` ;
      · sinon (plusieurs fiches, dates égales ou inconnues, disque plus
        récent en attente de la création) → `(None, None)`.
    La base ne marque pas les compilations : l'ordre des dates en tient lieu."""
    titre, duree, date = piste.get("title"), piste.get("duration"), _date_du_disque(album)
    if not duree or not date:
        return None, None
    memes = [t for t in fiches if _meme_plage(titre, duree, t.title, t.duration, t.album)]
    if memes:
        if len(memes) != 1 or memes[0].release_date is None:
            return None, None
        fiche = memes[0]
        # Chaîne ou datetime selon le chemin de chargement : l'ISO se compare.
        if str(fiche.release_date)[:10] > date[:10]:
            return None, None
        return "rattacher", fiche
    if album.get("record_type") not in TYPES_DU_DISQUE_D_ORIGINE:
        return None, None
    if any(_plage_titre_sans_page_deezer(t, album.get("title")) for t in fiches):
        # Keny Arkana, *État d'urgence* : la fiche « État d'urgence » (clip de
        # 60 s) est peut-être cette « Intro » de 88 s — l'humain.
        return None, None
    soeurs = [
        e
        for e in absentes
        if (e.get("album") or {}).get("id") != album.get("id")
        and _meme_plage(
            titre,
            duree,
            (e.get("piste") or {}).get("title"),
            (e.get("piste") or {}).get("duration"),
        )
    ]
    autres_dates = [_date_du_disque(e.get("album") or {}) for e in soeurs]
    if any(not d or d <= date for d in autres_dates):
        return None, None
    return "creer", None


def _generique_d_un_disque_propre(cas, ctx, ids_artiste: set) -> Arbitrage | None:
    """Règle C (2) : `generique_du_disque_le_plus_ancien`, pour une piste
    « absente » d'un disque propre au titre générique."""
    if cas.detecteur not in ("deezer_absent", "deezer_album_absent") or ctx is None:
        return None
    ecart = (cas.preuves or {}).get("ecart") or {}
    piste, album = ecart.get("piste") or {}, ecart.get("album") or {}
    if album.get("artist_id") not in ids_artiste or not titre_generique(piste.get("title")):
        return None
    geste, fiche = generique_du_disque_le_plus_ancien(
        piste, album, ctx.disco, ctx.pistes_deezer_absentes
    )
    if geste == "creer":
        return Arbitrage(
            "générique du disque le plus ancien qui le porte : créer la fiche",
            "deezer_appliquer",
            f"« {piste.get('title')} » {piste.get('duration')} s de « {album.get('title')} » "
            f"({album.get('record_type')}, {album.get('release_date')})",
        )
    if geste == "rattacher":
        return Arbitrage(
            f"générique de « {fiche.album} » repris sur un disque plus récent : rattacher",
            "deezer_rattacher_generique",
            f"« {piste.get('title')} » {piste.get('duration')} s de « {album.get('title')} » "
            f"({album.get('release_date')}) ; #{fiche.id} {fiche.duration} s",
        )
    return None


def _une_fiche_porte_deja(piste: dict, titre: str, ctx) -> bool:
    """Une fiche de l'artiste a le même titre de base que la piste (« The King
    and I » pour « The King and I (From … Soundtrack ELVIS) ») ou son ISRC."""
    from src.utils.version_descriptors import meme_titre_de_base

    isrc = str(piste.get("isrc") or "").upper()
    return any(
        meme_titre_de_base(t.title, titre) or (isrc and str(t.isrc or "").upper() == isrc)
        for t in (ctx.disco if ctx is not None else ())
    )


# ── Famille « liens Deezer » (délier) — branchée le 2026-10-29 ─────────────

#: Délier un lien Deezer que la durée dément, au seuil des RETRAITS validés
#: (3 catalogues indépendants d'accord, plus de 10 s) ; garder (✓ Normal) un
#: lien à revoir que plusieurs catalogues confirment. Originaux sans
#: ambiguïté seulement. Interrupteur : `revue_auto.REGLES_LIENS_DEEZER_BRANCHEES`.
REGLES_LIENS_DEEZER = frozenset(
    {
        ("duree_deezer", "delier_deezer"),
        ("deezer_link_review", "delier_deezer"),
        ("deezer_link_review", "normal"),
    }
)


def arbitrage_liens_deezer(cas, track, ctx) -> Arbitrage | None:
    """Le démenti ne vient que des OBSERVATIONS d'autres catalogues que Deezer,
    jamais de la colonne de la fiche (souvent la durée Deezer elle-même : le
    piège de « Comfy », où le gate comparait à la colonne)."""
    if cas.detecteur not in ("duree_deezer", "deezer_link_review") or track is None:
        return None
    if not titre_d_original(track.title):
        return None
    if cas.detecteur == "duree_deezer":
        arbitrage = politique_durees(3)(cas, track, ctx)
        if arbitrage is None or arbitrage.action != "delier_deezer":
            return None
        return Arbitrage("délier Deezer (autre version)", "delier_deezer", arbitrage.raison)
    piste = ((cas.preuves or {}).get("ecart") or {}).get("piste") or {}
    if not titre_d_original(piste.get("title")):
        return None
    duree = piste.get("duration")
    if not duree:
        return None
    durees = {s: d for s, d in _durees(track).items() if s != "deezer"}
    editions = tuple(track.durees_editions or ())
    ref = _dementie({**durees, "deezer": duree}, "deezer", editions, 3)
    if ref is not None and abs(int(duree) - ref) > ECART_VERSION_S:
        vote = voter({**durees, "deezer": duree}, duree, editions=editions)
        return Arbitrage(
            "délier (autre version)",
            "delier_deezer",
            f"Deezer {duree} s contre {ref} s ({vote.dementie_par()})",
        )
    arbitrage = politique_liens_deezer(cas, track, ctx)
    if arbitrage is not None and arbitrage.action == "normal":
        return arbitrage
    # Démenti par des sources FRAGILES seules (Discogs, SongBPM — souvent la
    # colonne : le piège de « Comfy ») et par aucun catalogue solide : le lien
    # tient (Kanye « I CAN'T WAIT » : Deezer 137 s, colonne SongBPM 127 s).
    vote = voter({**durees, "deezer": duree}, duree, editions=editions)
    if vote is not None and not vote.contre and vote.fragiles_contre:
        fragiles = ", ".join(f"{s} {d} s" for s, d in sorted(vote.fragiles_contre.items()))
        return Arbitrage(
            "garder le lien : seules des sources fragiles le contredisent",
            "normal",
            f"Deezer {duree} s ; contre : {fragiles} ; aucun catalogue solide contre",
        )
    return None


# ── Famille « ID Spotify porté par deux fiches » — VALIDÉE le 2026-10-26 ────

#: Simulée sur toute la base le 2026-10-26 : 2 ID partagés en tout (Kanye),
#: l'un réédition (`reeditions_evidentes`, plus signalé), l'autre tranché par
#: le gate — « I CAN'T WAIT » / « Wait », morceaux différents (décision
#: utilisateur : « on a déjà les infos pour le savoir »). Interrupteur :
#: `revue_auto.REGLES_ID_PARTAGE_BRANCHEES`.
REGLES_ID_PARTAGE = frozenset(
    {("kworb_id_partage", "retirer_spotify"), ("kworb_id_partage", "normal")}
)


def _ids_d_edition(track) -> set[str]:
    ids = {track.spotify_id} if track.spotify_id else set()
    return ids | {e.spotify_id for e in (track.spotify_id_entries or []) if not e.est_rendition}


def politique_id_partage(gate: Callable) -> Callable:
    """Un ID Spotify porté par plusieurs fiches de l'artiste : le gate
    d'identité STRICT est rejoué sur chacune. Une SEULE le passe : l'ID est le
    sien (« ✓ Normal » pour elle) et se retire des autres — le gate qui le
    refuse est la preuve montrable. Aucune, ou plusieurs : l'humain. Originaux
    sans ambiguïté seulement (`titre_d_original`), sur TOUTES les porteuses."""

    def juger(cas, track, ctx):
        if cas.detecteur != "kworb_id_partage" or track is None:
            return None
        sid = (cas.preuves or {}).get("spotify_id")
        porteuses = [t for t in ctx.disco if sid and sid in _ids_d_edition(t)]
        if len(porteuses) < 2 or track.id not in {t.id for t in porteuses}:
            return None
        if not all(titre_d_original(t.title) for t in porteuses):
            return None
        passent = [t for t in porteuses if gate(t, sid)]
        if len(passent) != 1:
            return None
        gardee = passent[0]
        if gardee.id == track.id:
            autres = ", ".join(f"« {t.title} »" for t in porteuses if t.id != track.id)
            return Arbitrage(
                "l'ID est celui de cette fiche",
                "normal",
                f"seule à passer le contrôle d'identité ; retiré de {autres}",
            )
        return Arbitrage(
            "l'ID est celui d'une autre fiche : retirer",
            "retirer_spotify",
            f"« {gardee.title} » seule passe le contrôle d'identité de {sid}",
        )

    return juger


def arbitrage_id_partage(cas, track, ctx, gate=None) -> Arbitrage | None:
    """L'arbitrage de la famille « ID partagé ». `gate` injectable ; par
    défaut, le gate des runs sur la page embed (réseau, gardée au brut)."""
    if cas.detecteur != "kworb_id_partage":
        return None
    if gate is None:
        from src.utils.spotify_identity import lire_identite_http, valider_identite

        def gate(t, sid):
            return bool(valider_identite(t, sid, lire_identite_http, lire_album=lambda _s: None))

    arbitrage = politique_id_partage(gate)(cas, track, ctx)
    if arbitrage is not None and (cas.detecteur, arbitrage.action) in REGLES_ID_PARTAGE:
        return arbitrage
    return None


# ── Famille « même compteur Spotify, deux fiches » (règle 2a) — 2026-10-27 ──

#: Simulée sur toute la base le 2026-10-27 (lecture seule) : 15 paires sur une
#: courbe ; 8 versions proches, la page gardée = l'ID que Kworb affiche (6 / 6
#: quand il en affiche un) ; 4 ID d'un autre morceau, que Spotify nomme (retirés
#: à la main le même jour) ; « Back Like That » / son remix, deux ID justes.
#: Interrupteur : `revue_auto.REGLES_COMPTEUR_BRANCHEES`.
REGLES_COMPTEUR = frozenset(
    {
        ("compteur_partage", "normal"),
        ("compteur_partage", "partager_compteur"),
        ("compteur_partage", "retirer_spotify"),
    }
)


def _garde_du_compteur(track, autre, ctx, preuve: str) -> Arbitrage | None:
    from src.utils.compteurs_partages import page_du_compteur

    kworb = {
        t.id
        for t in (track, autre)
        if any(o.field == "spotify_streams" and o.source == "kworb" for o in ctx.obs.get(t.id, ()))
    }
    choix = page_du_compteur([track, autre], kworb)
    if choix is None:
        return None
    critere, gardee = choix
    if gardee is track:
        return Arbitrage("le compteur est celui de cette fiche", "normal", f"{preuve} ; {critere}")
    return Arbitrage(
        "le compteur compte sur l'autre fiche",
        "partager_compteur",
        f"{preuve} ; {critere} : « {gardee.title} » (#{gardee.id}) le porte",
    )


def politique_compteur_partage(lire_spotify: Callable) -> Callable:
    """Deux fiches au même compteur Spotify. Version PROCHE montrée (ISRC,
    instrumental, paroles) : le compteur ne compte qu'une fois, sur la fiche
    que Kworb a rattachée, sinon l'original (`page_du_compteur`). Sinon, ce que
    Spotify sert pour chaque ID (`catalogue_designe`) : le titre de l'AUTRE
    fiche, aux paroles différentes, désigne un ID faux (retiré) ; chacun son
    titre, ce sont deux ID justes au compteur partagé. Le reste : l'humain."""
    from src.utils.catalogue_designe import page_designee, servi_spotify
    from src.utils.compteurs_partages import PAROLES_DIFFERENTES

    def juger(cas, track, ctx):
        if cas.detecteur != "compteur_partage" or track is None:
            return None
        preuves = cas.preuves or {}
        autre = next((t for t in ctx.disco if t.id == preuves.get("autre")), None)
        if (
            autre is None
            or track.spotify_id != preuves.get("spotify_id")
            or autre.spotify_id != preuves.get("autre_spotify_id")
        ):
            return None
        if preuves.get("version_proche"):
            return _garde_du_compteur(track, autre, ctx, preuves["version_proche"])

        def designee(fiche):
            servi = ctx.memo(
                f"catalogue_servi|spotify_id|{fiche.spotify_id}",
                lambda: servi_spotify(lire_spotify(fiche.spotify_id)),
            )
            verdict = page_designee(servi, [track, autre])
            return (servi, *verdict) if verdict else (servi, None, None)

        servi_t, critere_t, page_t = designee(track)
        servi_a, _critere_a, page_a = designee(autre)
        paroles = preuves.get("paroles")
        differentes = paroles is not None and paroles < PAROLES_DIFFERENTES
        if page_t is autre and differentes:
            return Arbitrage(
                "l'ID est celui de l'autre morceau : retirer",
                "retirer_spotify",
                f"Spotify sert « {servi_t.titre} » : {critere_t} désigne « {autre.title} » "
                f"(#{autre.id}) ; paroles communes {paroles:.0%}",
            )
        if page_a is track and differentes:
            return Arbitrage(
                "l'ID est celui de cette fiche",
                "normal",
                f"l'ID de « {autre.title} » sert « {servi_a.titre} », ce morceau",
            )
        # Les DEUX ID servent l'autre morceau, quelles que soient les paroles
        # (2026-10-29, « Champions » de Travis à 0,86, « Open Arms (just SZA) »
        # à 0,71 : Deezer et YouTube lui donnent 216 s, l'ID sert la version
        # avec Travis, 239 s). Simulé sur toute la base : 1 cas actif, juste.
        if page_t is autre and page_a is autre:
            commun = "" if paroles is None else f" ; paroles communes {paroles:.0%}"
            return Arbitrage(
                "les deux ID servent l'autre morceau : retirer celui-ci",
                "retirer_spotify",
                f"Spotify sert « {servi_t.titre} » pour les deux ID : {critere_t} désigne "
                f"« {autre.title} » (#{autre.id}){commun}",
            )
        if page_t is track and page_a is autre:
            return _garde_du_compteur(track, autre, ctx, "Spotify sert le titre de chaque fiche")
        return None

    return juger


def arbitrage_compteur(cas, track, ctx, lire_spotify=None) -> Arbitrage | None:
    """L'arbitrage de la famille « même compteur ». Lecteur injectable ; par
    défaut l'embed Spotify (gardé au brut)."""
    if cas.detecteur != "compteur_partage":
        return None
    if lire_spotify is None:
        from src.utils.spotify_identity import lire_identite_http as lire_spotify
    arbitrage = politique_compteur_partage(lire_spotify)(cas, track, ctx)
    if arbitrage is not None and (cas.detecteur, arbitrage.action) in REGLES_COMPTEUR:
        return arbitrage
    return None


# ── Famille « même ID de catalogue, deux pages » — 2026-10-27 ───────────────

#: Simulée sur la base d'avant les retraits à la main du 2026-10-27 (rejeu du
#: brut, `catalogue_designe`) : 6 ID partagés, 6 tranchés comme l'utilisateur
#: et les témoins (Deuces : 274 s servis contre 338-339 s du long remix).
#: Interrupteur : `revue_auto.REGLES_CATALOGUE_BRANCHEES`.
REGLES_CATALOGUE = frozenset(
    {
        ("id_catalogue_partage", "normal"),
        ("id_catalogue_partage", "retirer_spotify"),
        ("id_catalogue_partage", "delier_deezer"),
        # 2026-11-02 (décision utilisateur) : ce qui dérive d'un ID retiré part
        # avec lui — ISRC et ID Apple de l'autre page (« Higher » de The Game).
        ("identite_derivee", "retirer_identite_derivee"),
    }
)


def politique_catalogue_designe(lire_spotify: Callable, lire_deezer: Callable) -> Callable:
    """Ce que sert le catalogue pour l'ID partagé désigne UNE page
    (`catalogue_designe.page_designee`) : elle le garde (« ✓ Normal »), les
    autres le perdent. Rien de servi, ou aucun critère qui n'en retienne
    qu'une : l'humain. `lire_*` : `id -> lecture brute | None`."""
    from src.utils.catalogue_designe import page_designee, servi_deezer, servi_spotify

    def juger(cas, track, ctx):
        if cas.detecteur != "id_catalogue_partage" or track is None:
            return None
        preuves = cas.preuves or {}
        if preuves.get("meme_enregistrement"):
            return None  # à fusionner : décision humaine, l'ID n'a pas à partir
        if preuves.get("spotify_id"):
            champ, valeur, action = "spotify_id", preuves["spotify_id"], "retirer_spotify"
            if track.spotify_id != valeur:
                return None

            def lire():
                return servi_spotify(lire_spotify(valeur))

        elif preuves.get("deezer_id"):
            champ, valeur, action = "deezer_id", preuves["deezer_id"], "delier_deezer"
            if track.deezer_id is None or int(track.deezer_id) != int(valeur):
                return None

            def lire():
                return servi_deezer(lire_deezer(int(valeur)))

        else:
            return None
        porteuses = [track] + [t for t in ctx.disco if t.id in set(preuves.get("autres") or ())]
        # Une lecture par ID, pas une par porteuse.
        servi = ctx.memo(f"catalogue_servi|{champ}|{valeur}", lire)
        verdict = page_designee(servi, porteuses)
        if verdict is None:
            return None
        critere, gardee = verdict
        if gardee is track:
            return Arbitrage(
                "l'ID est celui de cette page",
                "normal",
                f"le catalogue sert « {servi.titre} » : seule page retenue par {critere}",
            )
        return Arbitrage(
            "l'ID est celui d'une autre page : retirer",
            action,
            f"le catalogue sert « {servi.titre} » : {critere} désigne « {gardee.title} » "
            f"(#{gardee.id})",
        )

    return juger


def arbitrage_catalogue(cas, track, ctx, lire_spotify=None, lire_deezer=None) -> Arbitrage | None:
    """L'arbitrage de la famille « même ID de catalogue ». Lecteurs injectables ;
    par défaut, l'embed Spotify (gardé au brut) et `GET /track` de Deezer."""
    if cas.detecteur == "identite_derivee" and track is not None:
        # Le détecteur exige déjà la preuve : l'ID de l'autre page est refusé ici.
        return Arbitrage(
            "ISRC / ID Apple de l'autre page : retirer",
            "retirer_identite_derivee",
            cas.motif,
        )
    if cas.detecteur != "id_catalogue_partage":
        return None
    if lire_spotify is None:
        from src.utils.spotify_identity import lire_identite_http as lire_spotify
    if lire_deezer is None:
        from src.utils.deezer_identity import lire_piste_http as lire_deezer
    arbitrage = politique_catalogue_designe(lire_spotify, lire_deezer)(cas, track, ctx)
    if arbitrage is not None and (cas.detecteur, arbitrage.action) in REGLES_CATALOGUE:
        return arbitrage
    return None


# ── Familles « audio, LRC, alias, liens Deezer à confirmer » — 2026-10-26 ───

#: Décision utilisateur 2026-10-26 : « trancher tout ce qu'on peut, la vérif
#: manuelle c'est pour les cas que le code ne peut pas trancher ». Chaque
#: politique a été simulée sur toute la base PUIS confrontée à un témoin
#: indépendant de ce qu'elle change ; deux ont été affinées pour cela (BPM :
#: une page SongBPM à la durée des catalogues est le même morceau ; LRC : un
#: retrait exige un LRC qui s'arrête bien avant la fin de la fiche).
#: Interrupteur : `revue_auto.REGLES_ETENDUES_BRANCHEES`.
REGLES_ETENDUES = frozenset(
    {
        ("bpm", "normal"),
        ("bpm", "retirer_songbpm"),
        ("tonalite", "normal"),
        ("lrc_douteux", "retirer_lrc"),
        ("lrc_live", "retirer_lrc"),
        ("doublon_lrc", "retirer_lrc"),
        ("liens_proposes", "confirmer_lien"),
        # 2026-11-06 : formation MusicBrainz + Discogs, « collectif » par défaut.
        ("liens_proposes", "confirmer_formation"),
        # 2026-11-13 (R4) : formation prouvée par les morceaux Genius du membre.
        ("liens_proposes", "confirmer_formation_genius"),
        # 2026-11-22 : noms SÛRS de l'artiste et de ses membres confirmés
        # (`utils.liens_proposes`) — nom civil, autre nom d'un membre, nom d'un
        # membre pris pour un alias de la formation, membre d'une personne.
        ("liens_proposes", "taire_lien"),
        ("liens_proposes", "refuser_lien"),
        ("deezer_link_candidate", "deezer_appliquer"),
        ("deezer_link_candidate", "normal"),
        ("kworb_proposition", "kworb_tiers"),
        # 2026-11-23, décision utilisateur : la variante d'une fiche, sommée à
        # ± 2 s de sa durée indépendante, sinon fiche à part
        # (`juger_variante_kworb`) ; simulée sur les 17 propositions du panneau :
        # 6 sommées, 2 fiches, 9 à l'humain.
        ("kworb_proposition", "kworb_sommer"),
        ("kworb_proposition", "kworb_version"),
        # 2026-10-27 : simulée sur toute la base (41 vidéos partagées sur 90),
        # témoins d'accord (album vu par la recherche, durées de catalogue).
        ("video_partagee", "retirer_video_partagee"),
        # 2026-10-29 : 7 liens sur 11, artiste étranger ou autre remix montrés.
        ("recherche_perimee", "retirer_video_recherche"),
        # 2026-10-29 : « titre + année nue », règle décidée par l'utilisateur
        # (rapport `titre_annee_2026-10-27.md` du studio), simulée sur la copie.
        ("jumelle_annee", "retirer_songbpm"),
        ("jumelle_annee", "retirer_getsongbpm"),
        ("jumelle_annee", "retirer_lrc"),
        ("jumelle_annee", "retirer_video_recherche"),
        # 2026-10-29 : 3 cas sur 6 ; témoins la fiche de la version que Spotify
        # nomme (son propre ID, sa durée) et Deezer / Apple de la fiche.
        ("id_spotify_version", "normal"),
        # 2026-11-02 : pages « Non-Music » (décision utilisateur 2026-11-01),
        # témoin les 10 pages tranchées à la main ; Eminem, 16 / 16.
        ("annotee", "normal"),
        ("annotee", "desactiver_non_musique"),
        ("annotee", "supprimer_non_musique"),
        # 2026-10-29, décision utilisateur (« si on est sûr avec les data […]
        # au moindre doute, on tranche pas ») : versions et titres génériques
        # entrent quand les catalogues suffisent à la preuve.
        ("generique_duree", "normal"),
        ("duree_songbpm", "retirer_songbpm"),
        # 2026-10-29, décision utilisateur : la chaîne d'un autre artiste ET une
        # durée que toutes celles de la fiche démentent (e62, e63).
        ("video_autre_chaine", "retirer_video_recherche"),
        # 2026-10-29 : mesures SongBPM posées par un titre CONTENU avant la
        # garde de pose `cccebc7`, retirées de la fiche que ses catalogues
        # démentent quand ceux de l'autre les confirment (simulée sur la copie).
        # 2026-10-30, décision utilisateur : selon la relation des deux titres,
        # toute la mesure (autre morceau) ou la durée seule (coupe), refusée
        # champ par champ (`mesure_songbpm`) ; simulée sur une copie, chaque
        # décision confrontée aux paroles Genius et aux catalogues.
        ("songbpm_titre_contenu", "retirer_champs_songbpm"),
        # 2026-10-30 : vidéo Genius muette plus de 3 fois plus longue que le
        # morceau (concert, émission, compilation) ; simulée sur la copie,
        # 4 liens, chacun confronté à son titre (aucun n'est le morceau).
        ("video_trop_longue", "retirer_video_recherche"),
        # 2026-11-01 : vidéo de la recherche au titre non équivalent, démentie
        # de plus de 10 s par 2 catalogues lus par ID ; simulée sur la base,
        # 6 vidéos, chacune un autre morceau ou une autre partie.
        ("recherche_dementie", "retirer_video_recherche"),
        # 2026-11-06, décision utilisateur : page de traduction ; 9 fiches, toutes
        # des traductions, ni « Lost in Translation », ni les « (english version) ».
        ("page_traduction", "supprimer_non_musique"),
        # 2026-11-06, décision utilisateur (`revue.VIDEOS_MORTES_BRANCHEES`) : lien
        # d'une vidéo que YouTube a répondu ne pas avoir ; refus de règle.
        ("video_morte", "retirer_video_recherche"),
        # 2026-10-31 (LD1, rapport panneau-familles) : LRC LRCLIB calé sur un
        # audio que 2 catalogues lus par ID confirment ; 3 / 22 sur la copie.
        ("lrc_douteux", "normal"),
        # 2026-10-31 (SP1) : ID Spotify confirmé par Apple ou Deezer lus par
        # leur ID ; 2 / 8 sur la copie (Take 'Em High, Ghetto).
        ("duree_spotify", "normal"),
        # 2026-10-31 (D1) : deux pages au même titre, artistes crédités
        # différents (adresse Genius) et paroles communes < 0,9 ; 6 / 13.
        ("doublon", "normal"),
        # 2026-10-31 : LRC de l'autre fiche sur une fiche que rien ne publie ;
        # simulée sur la base, 5 / 5 cas du panneau (Gyrophare / Garcimore…),
        # témoin la fin du LRC de l'autre fiche à 2-16 s près.
        ("paroles_autre_fiche", "retirer_lrc"),
    }
)


def politique_jumelle_annee(cas, track, _ctx):
    """La donnée qu'une fiche partage avec sa JUMELLE « titre + année nue »
    revient à la jumelle quand la source le dit (`revue.donnee_de_la_jumelle`
    a posé `retirer`) : la fiche la perd, par un refus de règle. Les
    identifiants (ID Spotify, Deezer, ISRC) restent à l'humain."""
    from src.services.revue import ACTIONS_JUMELLE

    preuves = cas.preuves or {}
    if cas.detecteur != "jumelle_annee" or track is None or not preuves.get("retirer"):
        return None
    action = ACTIONS_JUMELLE.get(preuves.get("donnee"))
    if action is None:
        return None
    return Arbitrage("donnée de la jumelle : la retirer de la fiche", action, cas.motif)


def politique_songbpm_titre_contenu(cas, track, _ctx):
    """Les mesures SongBPM qu'une fiche partage avec une autre par un titre
    CONTENU (`revue.songbpm_titre_contenu`) partent quand la page est à
    l'autre (`retirer` posé) : toutes (un autre morceau) ou la durée seule
    (une coupe du même morceau), selon `champs` (décision utilisateur
    2026-10-30). Une version (démo, remix), une relation indécise ou une page
    que rien n'attribue restent à l'humain (au moindre doute, le code ne
    tranche pas)."""
    preuves = cas.preuves or {}
    if cas.detecteur != "songbpm_titre_contenu" or track is None or not preuves.get("retirer"):
        return None
    if preuves.get("champs") == ["duration"]:
        # Une COUPE du même morceau (décision utilisateur 2026-10-30) : le
        # tempo est le même, la durée non.
        return Arbitrage(
            "durée SongBPM de l'autre titre (une coupe du même morceau) : la retirer",
            "retirer_champs_songbpm",
            cas.motif,
        )
    return Arbitrage(
        "mesures SongBPM de l'autre titre : les retirer de la fiche",
        "retirer_champs_songbpm",
        cas.motif,
    )


def politique_recherche_perimee(cas, track, _ctx):
    """Une vidéo de la recherche que le score refuse, pour une raison que les
    données MONTRENT (`revue.RAISONS_MONTREES` : artiste étranger, autre
    remix) : le lien part, par un refus de règle. Une ressemblance de titre
    trop faible reste à l'humain (« Home Sweet Home (Episode 2) » d'A2H, la
    bonne vidéo sous une page Genius au titre générique)."""
    from src.services.revue import RAISONS_MONTREES

    preuves = cas.preuves or {}
    if cas.detecteur != "recherche_perimee" or track is None:
        return None
    if preuves.get("raison") not in RAISONS_MONTREES:
        return None
    return Arbitrage(
        "vidéo d'un autre morceau : retirer le lien",
        "retirer_video_recherche",
        cas.motif,
    )


def politique_video_partagee(cas, _track, _ctx):
    """Une vidéo que plusieurs pages portent revient à la fiche que ses
    témoins désignent (`revue.proprietaire_et_temoin`, qui a posé `garder`) :
    elle quitte les autres, par un refus de règle."""
    preuves = cas.preuves or {}
    if cas.detecteur != "video_partagee" or not preuves.get("garder"):
        return None
    return Arbitrage(
        "vidéo d'une autre page : la retirer des autres fiches",
        "retirer_video_partagee",
        preuves.get("temoin") or f"interprète principal de la vidéo : {preuves.get('principal')}",
    )


#: Familles de version dont la page ne garde pas le clip de sa page
#: principale (décision utilisateur 2026-11-22 : démo, freestyle, édition).
#: Ni un live, ni un instrumental, ni un remix nommé par son titre : leur
#: vidéo peut être la leur.
FAMILLES_SANS_LE_CLIP = frozenset({"demo", "alternate", "snippet"})

#: « Dinos x Adidas / NMD_V3 » : une série ou une publicité, nommée avant le
#: titre du morceau.
_PREFIXE_DE_SERIE = re.compile(r"^(?P<serie>[^/]+?)\s+/\s+(?P<titre>\S.*)$")


def _version_de(q, p, fiches) -> bool:
    """PUR. La page `q` est-elle une VERSION de la page principale `p`, au sens
    du juge des titres : l'original que Genius lui désigne (`socle_designe`) ;
    une démo, une prise alternative ou un extrait au même socle
    (« - Clean(Ish) ») ; une édition de diffusion (« (Clean) ») ou un
    freestyle au même titre de base ; ou « <série de l'artiste> / <titre> »."""
    from src.utils.title_matching import contains_as_words, normalize_name
    from src.utils.version_descriptors import (
        edition_de_diffusion,
        meme_titre_de_base,
        meme_titre_normalise,
        porte_une_version,
        socle_du_titre,
    )
    from src.utils.version_heritage import famille_de_fiche, socle_designe

    if meme_titre_normalise(q.title, p.title):
        return False  # homonymes : rien ne les départage ici
    designe = socle_designe(q, fiches)
    if designe is not None:
        return revue._page(designe) == revue._page(p)
    if porte_une_version(q.title):
        return (
            meme_titre_normalise(socle_du_titre(q.title), p.title)
            and famille_de_fiche(q.title, q.relationships) in FAMILLES_SANS_LE_CLIP
        )
    if meme_titre_de_base(q.title, p.title):
        # Un sous-titre quelconque (« 530 (Haikou LP) ») peut nommer une autre
        # prise : seuls l'édition de diffusion et le freestyle comptent.
        return bool(edition_de_diffusion(q.title) or revue._FREESTYLE_RE.search(q.title or ""))
    m = _PREFIXE_DE_SERIE.match(q.title or "")
    if m is None or not meme_titre_normalise(m["titre"], p.title):
        return False
    serie = normalize_name(m["serie"])
    noms = [q.primary_artist_name, q.artist.name if q.artist else None]
    return any(n and contains_as_words(normalize_name(n), serie) for n in noms)


#: Part des mots d'un sous-titre que le titre de la vidéo doit porter pour le
#: NOMMER (graphies du distributeur : « 50cent » pour « 50 Cent »).
_PART_DU_SOUS_TITRE = 0.6


def _video_nomme_le_sous_titre(q, v) -> bool:
    """PUR. Le titre de la vidéo porte-t-il le SOUS-TITRE qui distingue la page
    `q` (« The Game: My Bitch (Dissin 50cent,Jay-Z,Suge Knight) » : la vidéo
    est celle de « My Bitch (Dissin’ 50 Cent, Jay-Z, Suge Knight) ») ?"""
    from src.utils.title_matching import normalize_name
    from src.utils.version_descriptors import sous_titres

    vus = set(normalize_name(v.title or "").split())
    for sous_titre in sous_titres(q.title):
        mots = [m for m in normalize_name(sous_titre).split() if len(m) >= 3]
        if mots and sum(m in vus for m in mots) / len(mots) >= _PART_DU_SOUS_TITRE:
            return True
    return False


def versions_de_la_page_principale(fiches, v):
    """PUR. `(page principale, [fiches de ses versions])` quand une vidéo que
    plusieurs PAGES portent est le clip d'une page principale que ses autres
    porteuses déclinent — démo, freestyle, édition (décision utilisateur
    2026-11-22 : Dinos « NMD_V3 » et « Dinos x Adidas / NMD_V3 », Eminem « The
    Real Slim Shady » et sa « - Clean(Ish) », Kid Cudi « Day ’N’ Nite
    (Nightmare) » et le remix « Day ‘N’ Nite » que Genius lui rattache). Le
    clip revient à la page principale, jamais à la démo. Sinon None.

    La page principale n'est pas elle-même une version : titre d'original
    (`titre_d_original`) ou original que Genius désigne pour une porteuse.
    Elle doit être UNIQUE, et la vidéo la nommer (`revue.video_nomme`) ; une
    vidéo qui EST la version d'une porteuse (« … (Demo) » posé sur la démo)
    reste à cette porteuse."""
    from src.utils.version_descriptors import meme_version_publiee, porte_une_version
    from src.utils.version_heritage import socle_designe

    pages: dict = {}
    for t in fiches:
        pages.setdefault(revue._page(t), []).append(t)
    if len(pages) < 2:
        return None
    reps = {page: lignes[0] for page, lignes in pages.items()}
    designes = {
        revue._page(d) for t in reps.values() if (d := socle_designe(t, fiches)) is not None
    }
    candidates = [
        p
        for page, p in reps.items()
        if socle_designe(p, fiches) is None
        and not porte_une_version(p.title)
        and (titre_d_original(p.title) or page in designes)
    ]
    trouves = []
    for p in candidates:
        versions = [
            q for page, q in reps.items() if page != revue._page(p) and _version_de(q, p, fiches)
        ]
        if versions:
            trouves.append((p, versions))
    if len(trouves) != 1:
        return None
    p, versions = trouves[0]
    if not revue.video_nomme(p, v):
        return None
    if porte_une_version(v.title) and any(meme_version_publiee(q.title, v.title) for q in versions):
        return None
    if any(_video_nomme_le_sous_titre(q, v) for q in versions):
        return None
    retirees = {revue._page(q) for q in versions}
    return p, [t for t in fiches if revue._page(t) in retirees]


def politique_kworb_tiers(cas, _track, ctx):
    """Une ligne Kworb « remix par un tiers » (★ du rapprochement) que l'en-tête
    Spotify confirme : remix NOMMÉ (le remixeur est dans le titre) ET l'artiste
    crédité par Spotify. La fiche créée tient l'artiste en rôle SECONDAIRE
    (`participation` : ni Timeline ni totaux). Un remix sans remixeur nommé
    (« 2021 Remix Extended », souvent une édition d'un remix déjà en base) ou
    une collaboration restent à l'humain."""
    from src.utils.title_matching import names_match_as_words

    preuves = cas.preuves or {}
    if cas.detecteur != "kworb_proposition" or preuves.get("proposition") != "tiers":
        return None
    if preuves.get("kind") != "remix_named" or not preuves.get("remixer"):
        return None
    artiste = next((t.artist.name for t in ctx.disco if t.artist is not None), None)
    credites = preuves.get("credited") or []
    if not artiste or not any(names_match_as_words(artiste, nom) for nom in credites):
        return None
    return Arbitrage(
        "remix par un tiers : fiche en rôle secondaire",
        "kworb_tiers",
        f"remixeur « {preuves['remixer']} » ; Spotify crédite {', '.join(credites)}",
    )


_CATALOGUES_LUS_PAR_ID = ("deezer", "spotify_web", "apple_music")


def _autre_fichier(version, d_id: int) -> bool:
    """La version nommée (`version`, sa fiche) n'est pas le fichier de l'ID
    (`d_id` s) : plus de `ECART_VERSION_S` d'écart, ou plus
    d'`ECART_FICHIER_S` quand deux catalogues lus par leur ID confirment sa
    durée à `ECART_FICHIER_S` près."""
    ecart = abs(int(version.duration) - d_id)
    if ecart > ECART_VERSION_S:
        return True
    durees = _durees(version)
    confirmee = sum(
        1
        for s in _CATALOGUES_LUS_PAR_ID
        if durees.get(s) and abs(int(durees[s]) - int(version.duration)) <= ECART_FICHIER_S
    )
    return ecart > ECART_FICHIER_S and confirmee >= 2


def politique_id_spotify_version(cas, track, ctx):
    """La page Spotify de l'ID nomme une AUTRE version (« Deep Diving - me n ü
    Remix » pour « Deep Diving », « K-POP » pour « K-POP (Chopped &
    Screwed) ») ; l'ID est pourtant celui de la fiche quand trois choses le
    montrent (2026-10-29) :
      · la version nommée a SA fiche chez l'artiste, avec son propre ID
        Spotify — l'ID de la fiche n'est pas le sien ;
      · sa durée diffère de celle de l'ID de plus de `ECART_VERSION_S` ;
      · un catalogue de la fiche lu par SON identifiant (Deezer, Apple) donne
        la durée de l'ID, à `ECART_FICHIER_S` près.
    ✓ Normal : aucune donnée ne change. Deux versions à la même durée
    (instrumental, édition) restent à l'humain.

    L'écart suffit au-delà d'`ECART_FICHIER_S` quand la durée de la version
    nommée est elle-même confirmée par deux catalogues lus par leur ID : deux
    fichiers distincts, sûrement (Travis « FRANCHISE (REMIX) » 207 s, Deezer du
    remix 207 s ; « FRANCHISE » 202 s chez Deezer, Spotify et ReccoBeats ;
    2026-10-31). Rien n'est modifié, l'action n'est pas destructive."""
    from src.services.revue import _titre_sans_feat

    preuves = cas.preuves or {}
    sert = preuves.get("spotify_sert")
    if cas.detecteur != "id_spotify_version" or track is None or not sert:
        return None
    durees = _durees(track)
    d_id = durees.get("spotify_web")
    temoin = next(
        (
            (s, durees[s])
            for s in ("deezer", "apple_music")
            if durees.get(s) and d_id and abs(int(durees[s]) - int(d_id)) <= ECART_FICHIER_S
        ),
        None,
    )
    if temoin is None:
        return None
    cle = _titre_sans_feat(sert)
    ids_de_la_fiche = {track.spotify_id, *(track.spotify_ids or [])}
    nommees = [t for t in ctx.disco if t is not track and _titre_sans_feat(t.title) == cle]
    if not nommees or not all(
        t.spotify_id
        and t.spotify_id not in ids_de_la_fiche
        and t.duration
        and _autre_fichier(t, int(d_id))
        for t in nommees
    ):
        return None
    autre = nommees[0]
    return Arbitrage(
        "l'ID est celui de la fiche : la version nommée a sa propre fiche",
        "normal",
        f"« {autre.title} » : ID {autre.spotify_id}, {autre.duration} s ; l'ID de la "
        f"fiche {d_id} s = {temoin[0]} {temoin[1]} s",
    )


def _identites(track) -> tuple:
    return (
        str(track.isrc).upper() if track.isrc else None,
        str(track.deezer_id) if track.deezer_id else None,
        track.spotify_id,
    )


def politique_generique_distincts(cas, track, ctx):
    """Deux titres génériques à la même durée (« Interlude » et « Outro » de
    *Premier mandat*, 50 s) : une COÏNCIDENCE quand chaque fiche a ses propres
    ISRC, ID Deezer et ID Spotify, tous distincts, et que sa durée est
    soutenue par au moins trois catalogues solides (2026-10-29). Le moindre
    identifiant manquant ou partagé : l'humain. ✓ Normal, rien ne change."""
    if cas.detecteur != "generique_duree" or track is None:
        return None
    ids_autres = set((cas.preuves or {}).get("autres") or ())
    autres = [t for t in ctx.disco if t.id in ids_autres]
    if not autres or len(autres) != len(ids_autres):
        return None
    fiches = [track, *autres]
    identites = [_identites(t) for t in fiches]
    for rang in range(3):
        valeurs = [i[rang] for i in identites]
        if None in valeurs or len(set(valeurs)) != len(valeurs):
            return None
    for t in fiches:
        vote = voter(_durees(t), t.duration, editions=tuple(t.durees_editions or ()))
        solides = (vote.pour if vote else set()) - {famille(s) for s in FRAGILES}
        if not vote or vote.nature != SOUTENUE or len(solides) < 3:
            return None
    return Arbitrage(
        "durée réelle, partagée par coïncidence : ✓ Normal",
        "normal",
        f"{track.duration} s ; ISRC, ID Deezer et Spotify distincts, durées soutenues "
        "par 3 catalogues ou plus sur chaque fiche",
    )


_SONGBPM_PAR_LE_VOTE = politique_durees(3)


def politique_songbpm_version(cas, track, ctx):
    """La page SongBPM d'une VERSION ou d'un titre générique, démentie par au
    moins trois catalogues solides d'accord entre eux, à plus de
    `ECART_VERSION_S` (« Hasta La Vista (Intro) » 203 s contre 14-16 s ;
    « Niagara Falls […] [ChoppedNotSlopped] » 207 s, la version normale,
    contre 252 s) : la même preuve que pour un original
    (`REGLES_VALIDEES`), étendue le 2026-10-29. Un original passe par
    `arbitrage_valide`."""
    if cas.detecteur != "duree_songbpm" or track is None or titre_d_original(track.title):
        return None
    return _SONGBPM_PAR_LE_VOTE(cas, track, ctx)


#: Au-delà de cette part de paires de mots communes, deux pages au même titre
#: peuvent être la même transcription : la fusion se décide à la main (D1).
PAROLES_DEUX_ENREGISTREMENTS_MAX = 0.9


def artistes_de_la_page(track) -> str | None:
    """Les artistes que la page Genius crédite, tels que son ADRESSE les écrit
    (« kanye-west-and-lauryn-hill » dans `…/Kanye-west-and-lauryn-hill-i-
    cant-wait-lyrics`) : Genius compose l'adresse des interprètes, puis du
    titre. None quand l'adresse ne finit pas par le titre de la fiche."""
    import unicodedata

    m = re.search(r"genius\.com/([^/?#]+)-lyrics", track.genius_url or "", re.I)
    if not m or not track.title:
        return None
    titre = unicodedata.normalize("NFKD", track.title).encode("ascii", "ignore").decode()
    titre = re.sub(r"[^a-z0-9]+", "-", re.sub(r"['’.]", "", titre.lower())).strip("-")
    adresse = m.group(1).lower()
    if not titre or not adresse.endswith("-" + titre):
        return None
    return adresse[: -len(titre) - 1] or None


def politique_doublon(cas, track, ctx):
    """D1 (2026-10-31) : deux pages Genius au même titre sont deux
    ENREGISTREMENTS quand Genius leur crédite des artistes différents (son
    adresse) ET que leurs paroles, présentes des deux côtés, ont moins de
    `PAROLES_DEUX_ENREGISTREMENTS_MAX` de paires de mots communes (« I CAN'T
    WAIT » avec et sans Lauryn Hill, 0,78). ✓ Normal ; des paroles quasi
    identiques ou les mêmes artistes : une fusion possible, l'humain."""
    from src.utils.concordance_paroles import paroles_de_reference, recouvrement_bigrammes

    if cas.detecteur != "doublon" or track is None:
        return None
    ids = set((cas.preuves or {}).get("autres") or ())
    autres = [t for t in ctx.disco if t.id in ids]
    if not autres or len(autres) != len(ids):
        return None
    mes_artistes = artistes_de_la_page(track)
    mes_paroles = paroles_de_reference(track.lyrics.text, track.lyrics.source)
    preuves = []
    for autre in autres:
        siens = artistes_de_la_page(autre)
        if not mes_artistes or not siens or siens == mes_artistes:
            return None
        part = recouvrement_bigrammes(
            mes_paroles, paroles_de_reference(autre.lyrics.text, autre.lyrics.source)
        )
        if part is None or part >= PAROLES_DEUX_ENREGISTREMENTS_MAX:
            return None
        preuves.append(f"« {siens} » contre « {mes_artistes} », paroles communes {part:.2f}")
    return Arbitrage("deux enregistrements : ✓ Normal", "normal", " ; ".join(preuves))


#: Catalogues lus par un identifiant dont l'accord avec SongBPM le protège.
_SOUTIENS_SONGBPM = ("deezer", "spotify_web", "apple_music", "reccobeats")


def _audios_de_la_page(track) -> list:
    """Les vidéos AUDIO que la page Genius lie elle-même, publiées par une
    chaîne « X - Topic » (le fichier du distributeur) et de durée connue."""
    return [
        v
        for v in track.videos or []
        if v.source == "genius_media"
        and v.kind == "audio"
        and v.duration
        and (v.channel or "").endswith(" - Topic")
    ]


def politique_songbpm_audio_de_la_page(cas, track, _ctx):
    """SB1 (2026-10-31) : la durée SongBPM est démentie, de plus de
    `ECART_VERSION_S`, par l'audio Topic que la page Genius lie elle-même, et
    aucun catalogue lu par ID ne la soutient à `ECART_FICHIER_S` : la page
    SongBPM est celle d'une autre version, ses mesures partent (« Cash
    Money » 161 s contre l'audio de la page 138 s). Versions et medleys
    compris (Q3, décision utilisateur 2026-10-31) : le témoin est l'audio que
    la page de la VERSION elle-même lie (« Ego (Live) » 115 s contre 236 s,
    « Material Girls Medley » 238 s contre 279 s)."""
    if cas.detecteur != "duree_songbpm" or track is None:
        return None
    durees = _durees(track)
    sb = durees.get("songbpm")
    audios = _audios_de_la_page(track)
    if not sb or not audios or any(abs(v.duration - sb) <= ECART_VERSION_S for v in audios):
        return None
    if any(
        durees.get(s) and abs(int(durees[s]) - int(sb)) <= ECART_FICHIER_S
        for s in _SOUTIENS_SONGBPM
    ):
        return None
    a = audios[0]
    return Arbitrage(
        "SongBPM d'une autre version : retirer ses mesures",
        "retirer_songbpm",
        f"SongBPM {sb} s ; audio de la page Genius ({a.channel}) {a.duration} s",
    )


def politique_duree_songbpm(cas, track, ctx):
    """Une version démentie par 3 catalogues, ou un original démenti par
    l'audio de sa page."""
    return politique_songbpm_version(cas, track, ctx) or politique_songbpm_audio_de_la_page(
        cas, track, ctx
    )


def politique_spotify_confirme(cas, track, _ctx):
    """La durée de l'ID Spotify, démentie par un témoin, est CONFIRMÉE par un
    autre catalogue lu par SON identifiant (Apple, Deezer) à `ECART_FICHIER_S`
    près : l'ID est le bon, le témoin contraire décrit une autre édition
    (2026-10-31, SP1 : « Take 'Em High » Spotify 179 = Apple 179 contre Deezer
    207 ; « Ghetto » Spotify 219 = Apple 220 contre Deezer 204). ✓ Normal."""
    if cas.detecteur != "duree_spotify" or track is None:
        return None
    durees = _durees(track)
    d = durees.get("spotify_web")
    if not d:
        return None
    temoins = [
        f"{s} {durees[s]} s"
        for s in ("apple_music", "deezer")
        if durees.get(s) and abs(int(durees[s]) - int(d)) <= ECART_FICHIER_S
    ]
    if not temoins:
        return None
    return Arbitrage(
        "ID Spotify confirmé par un autre catalogue : ✓ Normal",
        "normal",
        f"spotify_web {d} s = {', '.join(temoins)}",
    )


def politique_video_autre_chaine(cas, track, _ctx):
    """Le détecteur exige déjà les deux témoins (chaîne d'un artiste étranger,
    durée contredite par toutes celles de la fiche) : le lien part, par un
    refus de règle (« ↩ Rétablir » le rend)."""
    if cas.detecteur != "video_autre_chaine" or track is None:
        return None
    return Arbitrage(
        "vidéo d'un autre artiste : retirer le lien", "retirer_video_recherche", cas.motif
    )


def politique_video_trop_longue(cas, track, _ctx):
    """Une vidéo Genius qui ne nomme pas le morceau et dure plus de
    `revue.RATIO_VIDEO_MUETTE` fois sa durée (`retirer` posé par le
    détecteur) : concert, émission, compilation — le lien part, par un refus
    de règle. Celle qui le nomme reste à l'humain."""
    if cas.detecteur != "video_trop_longue" or track is None:
        return None
    if not (cas.preuves or {}).get("retirer"):
        return None
    return Arbitrage(
        "vidéo bien plus longue que le morceau, qui ne le nomme pas : retirer le lien",
        "retirer_video_recherche",
        cas.motif,
    )


def politique_page_traduction(cas, track, _ctx):
    """Supprimée et notée supprimée, comme une page de prose : désactivée, elle
    resterait une ligne qui partage la vidéo de l'original."""
    if cas.detecteur != "page_traduction" or track is None:
        return None
    return Arbitrage(
        "une traduction, pas un morceau : supprimer", "supprimer_non_musique", "traduction"
    )


def politique_video_morte(cas, track, _ctx):
    """Le détecteur exige la RÉPONSE de YouTube : le lien part, par un refus de
    règle, pour que la discographie ne le repose pas (« ↩ Rétablir » le rend).
    Un choix `manual` reste à l'humain."""
    if cas.detecteur != "video_morte" or track is None:
        return None
    if track.youtube_url_source == "manual":
        return None
    return Arbitrage(
        "YouTube n'a plus cette vidéo : retirer le lien", "retirer_video_recherche", cas.motif
    )


def politique_recherche_dementie(cas, track, _ctx):
    """Le détecteur exige déjà les deux témoins (titre vu non équivalent, durée
    démentie par deux catalogues d'accord) : le lien part, par un refus de
    règle (« ↩ Rétablir » le rend)."""
    if cas.detecteur != "recherche_dementie" or track is None:
        return None
    return Arbitrage(
        "vidéo d'un autre morceau (titre et durée) : retirer le lien",
        "retirer_video_recherche",
        cas.motif,
    )


# ── Pages Genius « Non-Music » sans trace — décision utilisateur 2026-11-01 ──

#: Sections d'une CHANSON (le chant ou le freestyle se garde).
_SECTIONS_CHANT = frozenset(
    {"verse", "couplet", "chorus", "refrain", "hook", "bridge", "pont", "pre-chorus", "post-chorus"}
)
#: Sections et mots de titre d'un SKETCH (désactivé s'il n'est jamais sorti).
_MOTS_SKETCH = ("skit", "interlude", "intro", "outro")
#: Une prose : au moins autant de mots par ligne, en moyenne (un couplet en a
#: 6 à 10 ; « I Know, I Know (Love Everyone) », 71 mots sur une ligne).
_MOTS_PAR_LIGNE_PROSE = 12

_EN_TETE = re.compile(r"^\s*\[([^\]:]*)")


def nature_non_musique(titre: str | None, paroles: str | None) -> str | None:
    """PUR. Ce qu'est une page Genius « Non-Music » sans trace de plateforme
    (règle décidée par l'utilisateur le 2026-11-01, sur 10 pages tranchées à
    la main) : « chant » (couplets, refrain ; un freestyle) → gardée, même sans sortie
    officielle (mixtape, live filmé, CD bonus ; décision utilisateur 2026-11-10) ;
    « sketch » (skit, interlude, intro, outro, au titre ou en section) et
    « sans_texte » → désactivées ; « texte » (un sample, une prose : entretien,
    article, liste, notes de pochette) → supprimée. Des lignes courtes sans
    section allaient à l'humain jusqu'au 2026-11-10 ; décision utilisateur ce
    jour-là (« à supprimer si bien Non-Music ») : un texte qui n'est ni chant ni
    sketch est un « texte », supprimé (« Bostellan », un poème de Dooz Kawa ;
    « Imitation Kery James », un sketch de Thomas N'Gijol)."""
    from src.utils.paroles_genius import texte_utile
    from src.utils.title_matching import contains_as_words

    sections = {
        m.group(1).strip().lower()
        for ligne in (paroles or "").splitlines()
        if (m := _EN_TETE.match(ligne))
    }
    mots_de_section = {mot for s in sections for mot in re.findall(r"[\w-]+", s)}
    titre_bas = (titre or "").lower()  # `contains_as_words` veut des chaînes normalisées
    if mots_de_section & _SECTIONS_CHANT or contains_as_words("freestyle", titre_bas):
        return "chant"
    if any(contains_as_words(m, titre_bas) for m in _MOTS_SKETCH) or (
        mots_de_section & set(_MOTS_SKETCH)
    ):
        return "sketch"
    texte = texte_utile(paroles)
    lignes = [ligne for ligne in texte.splitlines() if ligne.strip()]
    if not lignes:
        return "sans_texte"
    # Un sample, une prose (≥ `_MOTS_PAR_LIGNE_PROSE` mots par ligne : entretien,
    # article, notes de pochette), et depuis le 2026-11-10 tout autre texte qui
    # n'est ni chant ni sketch.
    return "texte"


def politique_non_musique(cas, track, _ctx):
    """🏷️ `annotee` : le sens de `nature_non_musique`."""
    if cas.detecteur != "annotee" or track is None:
        return None
    nature = nature_non_musique(track.title, track.lyrics.text)
    if nature == "chant":
        return Arbitrage("chant ou freestyle : garder", "normal", "sections d'une chanson")
    if nature in ("sketch", "sans_texte"):
        return Arbitrage(
            "jamais sorti : désactiver",
            "desactiver_non_musique",
            "sketch" if nature == "sketch" else "page sans texte",
        )
    if nature == "texte":
        return Arbitrage("un texte, pas un morceau : supprimer", "supprimer_non_musique", "prose")
    return None


_POLITIQUE_PAR_DETECTEUR: dict[str, Callable] = {
    "annotee": politique_non_musique,
    "recherche_dementie": politique_recherche_dementie,
    "video_morte": politique_video_morte,
    "page_traduction": politique_page_traduction,
    "doublon": politique_doublon,
    "duree_spotify": politique_spotify_confirme,
    "video_trop_longue": politique_video_trop_longue,
    "video_autre_chaine": politique_video_autre_chaine,
    "generique_duree": politique_generique_distincts,
    "duree_songbpm": politique_duree_songbpm,
    "id_spotify_version": politique_id_spotify_version,
    "bpm": politique_bpm,
    "tonalite": politique_tonalite,
    "lrc_douteux": politique_lrc,
    "lrc_live": politique_lrc,
    "doublon_lrc": politique_lrc,
    "liens_proposes": politique_alias,
    "deezer_link_candidate": politique_liens_deezer,
    "kworb_proposition": politique_kworb_proposition,
    "video_partagee": politique_video_partagee,
    "recherche_perimee": politique_recherche_perimee,
    "jumelle_annee": politique_jumelle_annee,
    "songbpm_titre_contenu": politique_songbpm_titre_contenu,
    "paroles_autre_fiche": politique_paroles_autre_fiche,
}


def arbitrage_etendu(cas, track, ctx) -> Arbitrage | None:
    """L'arbitrage des familles étendues du 2026-10-26, si sa (détecteur,
    action) est validée."""
    juger = _POLITIQUE_PAR_DETECTEUR.get(cas.detecteur)
    if juger is None:
        return None
    arbitrage = juger(cas, track, ctx)
    if arbitrage is not None and (cas.detecteur, arbitrage.action) in REGLES_ETENDUES:
        return arbitrage
    return None


POLITIQUES: tuple[Politique, ...] = (
    Politique(
        "durees_vote_3",
        ("duree_spotify", "duree_deezer", "duree_isolee", "duree_songbpm", "generique_duree"),
        "Durées : vote des catalogues, 3 catalogues solides d'accord contre la valeur",
        politique_durees(3),
    ),
    Politique(
        "durees_vote_2",
        ("duree_spotify", "duree_deezer", "duree_isolee", "duree_songbpm", "generique_duree"),
        "Durées : vote des catalogues, 2 catalogues solides d'accord contre la valeur",
        politique_durees(2),
    ),
    Politique(
        "liens_deezer",
        ("deezer_link_candidate", "deezer_link_review"),
        "Liens Deezer : ISRC, ou durée indépendante à 2 s + même titre ; vote contre",
        politique_liens_deezer,
    ),
    Politique(
        "bpm_une_voix",
        ("bpm",),
        "BPM : ReccoBeats et SongBPM sont une seule voix ; une seule voix contre",
        politique_bpm,
    ),
    Politique(
        "tonalite_une_voix",
        ("tonalite",),
        "Tonalité : ReccoBeats et SongBPM sont une seule voix ; une seule voix contre",
        politique_tonalite,
    ),
    Politique(
        "lrc",
        ("lrc_douteux", "lrc_live", "doublon_lrc"),
        "LRC : celui d'une autre fiche (≥ 90 %), minuté au-delà de la durée, "
        "ou d'une autre prise",
        politique_lrc,
    ),
    Politique(
        "alias",
        ("liens_proposes",),
        "Alias proposés : MusicBrainz ET Discogs, ou MusicBrainz « nom d'artiste »",
        politique_alias,
    ),
)
