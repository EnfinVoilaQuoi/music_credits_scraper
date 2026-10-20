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


def _dementie(durees: dict, source: str, editions, minimum: int):
    """La valeur de `source` est démentie FORMELLEMENT : au moins `minimum`
    catalogues SOLIDES d'autres familles la contredisent, ils s'accordent entre
    eux (à la tolérance du vote), aucun autre catalogue ne la soutient. Rend la
    durée commune des contradicteurs, sinon None."""
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
    return dict(track.durations_observees or {})


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
                    return None  # une autre édition ? l'humain
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
            from src.utils.version_descriptors import Kind, parse_variant, titre_generique

            if parse_variant(track.title).kind != Kind.NONE or titre_generique(track.title):
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
    from src.utils.version_descriptors import parse_variant, titres_equivalents

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
    variante = parse_variant(piste.get("title") or "")
    if variante.edition and titres_equivalents(variante.socle, track.title):
        # Décision utilisateur 2026-10-19 : radio edit, version radio = UNE
        # fiche, la parution en ÉDITION avec SA durée — l'écart ne dément rien.
        return Arbitrage(
            "rattacher en ÉDITION (sa durée propre)",
            "deezer_appliquer",
            f"« {variante.edition} » de « {track.title} »",
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


def politique_lrc(cas, track, ctx):
    """LRC douteux : celui d'une AUTRE fiche (≥ 90 % de ses paires de mots,
    quelle que soit la part des siennes) se retire ; un LRC minuté au-delà de
    la durée de la fiche (+ 5 s) aussi. 👯 LRC d'une version sœur : deux PRISES
    différentes selon la taxonomie (« R.O.C. Remix » / « Jeezy Remix ») ne
    sont pas le même morceau — le LRC est celui de l'autre, il se retire ; même
    prise : une fusion, l'humain."""
    from src.utils.lrc_garde import ECART_DUREE_S, fin_du_lrc
    from src.utils.version_descriptors import meme_prise, parse_variant

    if track is None or not track.lyrics.synced:
        return None
    preuves = cas.preuves or {}
    autre = preuves.get("bigrammes_autre")
    if cas.detecteur == "doublon_lrc":
        titre_autre = next((t.title for t in ctx.disco if t.id == preuves.get("autre")), None)
        if titre_autre and not meme_prise(parse_variant(track.title), parse_variant(titre_autre)):
            return Arbitrage(
                "deux prises différentes : le LRC est celui de l'autre, le retirer",
                "retirer_lrc",
                f"« {track.title} » ≠ « {titre_autre} » (taxonomie), LRC à "
                f"{(autre or 0):.0%} de l'autre",
            )
        return None
    if autre is not None and autre >= LRC_AUTRE_MIN:
        return Arbitrage(
            "LRC d'une autre fiche : le retirer",
            "retirer_lrc",
            f"{autre:.0%} des paires de mots de l'autre fiche",
        )
    fin = fin_du_lrc(track.lyrics.synced)
    if fin and track.duration and fin > int(track.duration) + ECART_DUREE_S:
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
    from src.utils.version_descriptors import parse_variant, titre_generique
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
            v = parse_variant(titre)
            fam = famille_de(titre)
            if fam in (None, "edition"):
                from src.utils.version_descriptors import Kind, nature_d_edition

                # Kind NONE : édition de DIFFUSION (« Radio Edit », « Explicit »).
                nature = "diffusion" if v.kind == Kind.NONE else nature_d_edition(v)
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
    from src.utils.version_descriptors import Kind, parse_variant, titre_generique

    if not titre:
        return False
    if re.search(r"[\(\[]| - ", titre) or _MOTS_AMBIGUS.search(titre):
        return False
    return parse_variant(titre).kind == Kind.NONE and not titre_generique(titre)


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
