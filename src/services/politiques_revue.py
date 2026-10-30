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

import re
from collections.abc import Callable
from dataclasses import dataclass

from src.services import revue
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
            # autre chose. Rien n'est retiré ; l'action reste à définir.
            juste = next(s for s in presents if s not in faux)
            return Arbitrage(
                f"{faux[0]} {durees[faux[0]]} s démentie ; {juste} {durees[juste]} s = les "
                "catalogues : ID d'une autre édition, ou lecture fausse"
                + (" — elle tient la colonne" if _colonne_impactee(track, faux[0]) else ""),
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


def politique_id_propose(lire: Callable, gate: Callable) -> Callable:
    """Une fiche SANS ID (une fiche à un AUTRE ID n'est jamais tranchée : autre
    édition ou ID faux, l'humain). `lire(sid)` rend l'identité de la page embed
    (None : jamais lue, non jugé) ; `gate(track, sid)` est le gate d'identité
    STRICT (artiste, durée, version, titre). Refusé par le gate : refuser
    l'ID. Accepté ET une durée INDÉPENDANTE (un catalogue lu par un autre
    identifiant) confirme à 2 s la durée de l'ID : poser l'ID. Accepté sans
    durée indépendante : l'humain."""

    def juger(cas, track, _ctx):
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
            # Le gate compare la durée de Spotify à la COLONNE de la fiche : si
            # deux catalogues indépendants confirment celle de Spotify, c'est la
            # colonne (souvent une autre édition Deezer) qui dément, pas l'ID —
            # Kanye « Comfy » : Spotify 212 s, YTM 212, ReccoBeats 211, Deezer
            # 206 en colonne (2026-10-21). L'humain tranche.
            servie = identite.get("duration")
            confirment = [
                s
                for s, d in durees.items()
                if s not in FRAGILES
                and s not in ("legacy", "heritage", "manual", "youtube_video", "deezer")
                and d
                and servie
                and abs(d - servie) <= ECART_FICHIER_S
            ]
            if len({famille(s) for s in confirment}) >= 2:
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
            return None
        return Arbitrage(
            "poser l'ID",
            "poser_id",
            f"{servi} ; gate strict passé ; {duree_id} s = {', '.join(accord)} "
            f"(durées de la fiche : {temoins})",
        )

    return juger


# ── Liens Deezer ────────────────────────────────────────────────────────────


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
    return None


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
            if fam in ("instrumental", "a_cappella"):
                return Arbitrage(
                    "instrumental / a cappella : ne pas créer de fiche",
                    "normal",
                    f"famille {fam}",
                )
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
            return None
        return None

    return juger


#: Les signalements Kworb qui désignent une LIGNE par son titre.
_TITRE_KWORB = ("kworb_title", "titre_kworb")


def politique_kworb(cas, _track, _ctx, rejoues: dict | None = None):
    """Une proposition Kworb « ★ » qui ne crée rien : rattacher comme VARIANTE
    au socle connu (`rendition` : ID gardé à part, jamais sommé) ou au morceau
    qui EST déjà ce remix (`existant`). Les voies qui créent une fiche ou
    somment des streams (`collab`, `tiers`, `edition`) restent à l'humain.
    `rejoues` : `{titre Kworb: verdict}` du rapprochement d'AUJOURD'HUI, rejoué
    hors ligne depuis le brut (`update_kworb.rapprochements`) — une ligne
    désormais rattachée sans suggestion rend le signalement périmé."""
    preuves = cas.preuves or {}
    titre = next((preuves[k] for k in _TITRE_KWORB if preuves.get(k)), None)
    verdict = (rejoues or {}).get(titre) if titre else None
    if verdict and verdict.get("fiche") and not verdict.get("suggestion"):
        return Arbitrage(
            "le rapprochement d'aujourd'hui rattache la ligne : un run Kworb le retire",
            "run",
            f"« {titre} » → « {verdict['fiche']['titre']} » ({verdict.get('via')})",
        )
    propose = preuves.get("proposition")
    if propose == "rendition" and preuves.get("parent_track_id"):
        return Arbitrage(
            "variante rattachée au socle (jamais sommée)",
            "kworb_rendition",
            f"socle « {preuves.get('parent_title')} »",
        )
    if propose == "existant" and len(preuves.get("existants") or []) == 1:
        return Arbitrage("rattacher au morceau existant", "kworb_existant", "un seul existant")
    return None


# ── Alias proposés ──────────────────────────────────────────────────────────


def politique_alias(cas, _track, ctx):
    """Un ALIAS proposé (jamais une formation : sa nature groupe / collectif se
    tranche dans la fenêtre Groupes) est confirmé quand MusicBrainz ET Discogs
    le donnent, ou quand MusicBrainz le type « nom d'artiste ». Une page
    d'alias Discogs sans type reste à l'humain : nom civil (« Eli Yaffa ») ou
    nom douteux (« Global » chez Médine), rien ne les distingue. Prudence : un
    alias confirmé devient un nom de RECHERCHE (certifications, collectifs) et
    un profil Spotify connu."""
    preuves = cas.preuves or {}
    if preuves.get("kind") != "alias":
        return None
    rel = next(
        (
            r
            for r in ctx.relations_proposees
            if r.kind == "alias" and r.related_name == preuves.get("nom")
        ),
        None,
    )
    if rel is None:
        return None
    sources = set((rel.source or "").split("+"))
    if {"discogs", "musicbrainz"} <= sources:
        return Arbitrage("confirmer l'alias", "confirmer_lien", "MusicBrainz ET Discogs")
    if "musicbrainz" in sources and rel.detail == "Artist name":
        return Arbitrage(
            "confirmer l'alias", "confirmer_lien", "MusicBrainz : nom d'artiste (Artist name)"
        )
    return None


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


#: Mots d'un titre AMBIGU (version, prise, titre générique) — en mots entiers.
_MOTS_AMBIGUS = re.compile(
    r"\b(?:intro|outro|interlude|skit|prelude|prélude|remix|rmx|mix|live|edit|version|"
    r"demo|démo|acoustic|acoustique|instrumental|chopped|screwed|slowed|sped|reprise|"
    r"freestyle|session|snippet|extended|radio)\b",
    re.IGNORECASE,
)


def titre_d_original(titre: str | None) -> bool:
    """Le titre est celui d'un ORIGINAL sans ambiguïté — PRUDENT à dessein : un
    groupe entre parenthèses ou crochets, un « - » suffixe, un mot de version
    ou de titre générique suffisent à le rendre à l'humain (« Nuit
    (Interlude) », « … [ChoppedNotSlopped] » que `parse_variant` ne lit pas)."""
    from src.utils.version_descriptors import porte_une_version, titre_generique

    if not titre:
        return False
    if re.search(r"[\(\[]| - ", titre) or _MOTS_AMBIGUS.search(titre):
        return False
    return not porte_une_version(titre) and not titre_generique(titre)


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
    return None


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


# ── Famille « ID Spotify proposé » — VALIDÉE le 2026-10-20 ─────────────────

#: Validée par l'utilisateur sur le passage à blanc restreint du 2026-10-20
#: (46 corrections : 40 poses, 6 refus) — `revue_auto.REGLES_ID_PROPOSE_BRANCHEES`.
REGLES_ID_PROPOSE = frozenset(
    {("id_spotify_propose", "poser_id"), ("id_spotify_propose", "refuser_id")}
)


def arbitrage_id_propose(cas, track, ctx, lire=None, gate=None) -> Arbitrage | None:
    """L'arbitrage de la famille « ID Spotify proposé », restreint aux
    ORIGINAUX sans ambiguïté (`titre_d_original`). `lire` (page embed) et
    `gate` (gate d'identité STRICT) sont injectables ; par défaut, la page est
    lue (réseau, gardée au brut) et le gate est celui des runs."""
    if cas.detecteur != "id_spotify_propose" or track is None:
        return None
    if not titre_d_original(track.title):
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
        if not titre_d_original(servi):
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


def arbitrage_deezer(cas, track, ctx, isrcs_connus: dict, ids_artiste: set):
    """L'arbitrage de la famille « écarts Deezer », restreint aux originaux
    sans ambiguïté — le titre de la PISTE Deezer et celui de la fiche."""
    if not any(cas.detecteur == d for d, _ in REGLES_DEEZER):
        return None
    par_isrc = _rattacher_par_isrc(cas, ctx, isrcs_connus, ids_artiste)
    if par_isrc is not None:
        return par_isrc
    piste = ((cas.preuves or {}).get("ecart") or {}).get("piste") or {}
    if not titre_d_original(piste.get("title") or cas.morceau):
        return None
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
    if arbitrage is not None and (cas.detecteur, arbitrage.action) in REGLES_DEEZER:
        return arbitrage
    return None


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
        ("deezer_link_candidate", "deezer_appliquer"),
        ("deezer_link_candidate", "normal"),
        ("kworb_proposition", "kworb_tiers"),
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
    (instrumental, édition) restent à l'humain."""
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
        and abs(int(t.duration) - int(d_id)) > ECART_VERSION_S
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


_POLITIQUE_PAR_DETECTEUR: dict[str, Callable] = {
    "video_trop_longue": politique_video_trop_longue,
    "video_autre_chaine": politique_video_autre_chaine,
    "generique_duree": politique_generique_distincts,
    "duree_songbpm": politique_songbpm_version,
    "id_spotify_version": politique_id_spotify_version,
    "bpm": politique_bpm,
    "tonalite": politique_tonalite,
    "lrc_douteux": politique_lrc,
    "lrc_live": politique_lrc,
    "doublon_lrc": politique_lrc,
    "liens_proposes": politique_alias,
    "deezer_link_candidate": politique_liens_deezer,
    "kworb_proposition": politique_kworb_tiers,
    "video_partagee": politique_video_partagee,
    "recherche_perimee": politique_recherche_perimee,
    "jumelle_annee": politique_jumelle_annee,
    "songbpm_titre_contenu": politique_songbpm_titre_contenu,
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
