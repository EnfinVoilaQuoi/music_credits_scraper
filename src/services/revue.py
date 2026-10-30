"""Panneau « À trancher » — les DÉTECTEURS (étape 1, 2026-09-27).

Chaque défaut trouvé le 2026-09-26 l'a été par hasard, en creusant autre chose
(LRC d'un autre morceau, vidéos d'un autre artiste, mesures SongBPM d'une autre
version…). À l'échelle d'une discographie de 2 000 fiches, vérifier ligne par
ligne est impossible : on ne montre que ce qu'un détecteur trouve SUSPECT, trié
par IMPACT (streams, ou vues de la vidéo en cause), pour trancher le haut de la
liste.

Règles :
- un détecteur est une fonction PURE `(fiche, contexte) -> (motif, preuves) |
  None` — le contexte (discographie, observations brutes, liens proposés) est lu
  UNE fois à l'ouverture, sans réseau ; le panneau n'écrit rien ;
- il SIGNALE, il ne corrige rien — décision utilisateur (2026-09-27) : une
  donnée n'est retirée que sur preuve, et c'est l'utilisateur qui tranche ;
  SAUF les détecteurs FORMELS (`formel=True`, 2026-09-27) : leur preuve suffit,
  `services/revue_auto` les corrige seul, avec trace et « ↩ Rétablir » ;
- un cas se recalcule à chaque ouverture : une meilleure donnée arrivée par un
  run (une durée YouTube, une page relue) le fait disparaître d'elle-même ;
- chaque règle a été MESURÉE sur de vraies discographies avant d'être gardée
  (« crédit au nom d'un titre » a été jeté : 171 faux cas chez Booba, les
  morceaux « Kalash » ou « The Game » portant le nom d'une personne).

Ce que seuls les RUNS savent (variantes suspectes de Kworb, écarts Deezer, audit
Spotify — oracles réseau) n'entre ici qu'une fois enregistré : étape 3 (WIP).
"""

from __future__ import annotations

import collections
import json
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from difflib import SequenceMatcher

from src.utils.compteurs_partages import (
    PLANCHER,
    lectures,
    meme_compteur,
    titres_parents,
    version_proche,
)
from src.utils.concordance_paroles import (
    MOTS_MIN,
    SEUIL_FAUX,
    SEUIL_FAUX_BIGRAMMES,
    SEUIL_JUSTE,
    SEUIL_JUSTE_BIGRAMMES,
    bigrammes,
    lrc_dementi,
    mots,
    paroles_de_reference,
    recouvrement,
    recouvrement_bigrammes,
)
from src.utils.credits_genius_api import SOURCE_API, SOURCE_SCRAPE
from src.utils.duree_youtube import SOURCE_AUDIO
from src.utils.spotify_identity import (
    CHAMP_ID_PROPOSE,
    remix_d_autres_artistes,
    variante_etrangere,
)
from src.utils.title_matching import (
    cle_album,
    contains_as_words,
    developper_ligatures,
    names_match_as_words,
    normalize_name,
    normalize_title,
)
from src.utils.track_validation import sans_info
from src.utils.version_descriptors import (
    Kind,
    meme_version_publiee,
    parse_variant,
    reeditions_evidentes,
    socle_normalise,
    titre_generique,
)
from src.utils.version_heritage import IndexSocles, famille_de, socle_parmi
from src.utils.vote_durees import (
    ISOLEE,
    MAJORITAIRE,
    SOUTENUE,
    TOLERANCE_S,
    editions_distinctes,
    voter,
)

#: Écart de durée au-delà duquel deux sources ne décrivent plus le même fichier
#: (2 s = même fichier ; 5 s = tolérance inter-plateformes, comme la revue des
#: liens Deezer).
ECART_DUREE_S = 5

#: Sources de durée lues PAR l'identifiant Spotify de la fiche.
_SOURCES_PAR_ID_SPOTIFY = ("spotify_web", "reccobeats")

#: Sources qui ne mesurent rien (reprise, héritage, saisie) : hors des votes.
_SOURCES_NON_MESUREES = frozenset({"legacy", "heritage", "manual"})

#: Tolérance d'accord entre deux BPM, après correction d'octave.
TOLERANCE_BPM = 0.03

#: Vues YouTube au-delà desquelles un écart avec Spotify devient suspect, et
#: rapport qui le rend anormal (Booba « Cruella » : 5,5 M contre 316 k).
VUES_YTM_MIN = 500_000
RAPPORT_YTM_SPOTIFY = 10

#: Recouvrement flou minimal entre le titre d'une fiche et celui d'une vidéo.
SIMILARITE_VIDEO = 0.8

#: Ce qu'un cas DEMANDE (2026-10-19, mesure de la file : 1 464 cas, dont 601
#: crédits 🎫 et 143 vidéos 🎬 déjà sans effet). Seules les DÉCISIONS attendent
#: un humain ; le critère de fin du gel (AUDIT §7) ne compte qu'elles, avec les
#: erreurs de l'audit. Les deux autres natures restent affichées et comptées,
#: à part.
DECISION = "decision"
#: Un run le tranchera (une donnée encore à lire), pas un humain.
RUN = "run"
#: Déjà neutralisé : le cas ne change aucune donnée comptée (des vues exclues
#: des totaux, une ligne Kworb rattachée à rien).
INFORMATION = "information"
NATURES = {DECISION: "à trancher", RUN: "à faire par un run", INFORMATION: "pour information"}


@dataclass(frozen=True)
class Cas:
    detecteur: str
    track_id: int | None
    morceau: str
    motif: str
    impact: int = 0
    preuves: dict = field(default_factory=dict, hash=False, compare=False)
    #: Identité STABLE du cas : la fiche (sa page Genius, sinon son id) + une
    #: EMPREINTE de ses preuves. Un verdict la mémorise ; de nouvelles preuves
    #: (un autre BPM, une autre vidéo) font une autre clé, donc le cas revient.
    cle: str = ""
    #: `DECISION`, `RUN` ou `INFORMATION` — hors de la clé : changer la nature
    #: d'un détecteur ne fait revenir aucun cas déjà tranché.
    nature: str = field(default=DECISION, compare=False)


def cle_du_cas(track, preuves: dict) -> str:
    """Les valeurs VOLATILES (vues, impact) sont hors de l'empreinte — sinon le
    cas reviendrait à chaque run ; un détecteur peut fixer `preuves["empreinte"]`."""
    if track is None:
        ident = "artiste"
    elif track.genius_id:
        ident = f"g{track.genius_id}"
    else:
        ident = f"t{track.id}"
    if "empreinte" in preuves:
        empreinte = str(preuves["empreinte"])
    else:
        stables = {k: v for k, v in preuves.items() if k != "impact"}
        empreinte = json.dumps(stables, sort_keys=True, ensure_ascii=False, default=str)
    return f"{ident}|{empreinte}"


@dataclass(frozen=True)
class Detecteur:
    code: str
    libelle: str
    icone: str
    #: `(fiche, contexte) -> (motif, preuves) | None` ; `preuves["impact"]`
    #: remplace l'impact de la fiche (ex. les vues de la vidéo en cause).
    juger: Callable
    #: La preuve est FORMELLE : le cas se corrige sans attendre l'utilisateur
    #: (`services/revue_auto`, journalisé et rétablissable).
    formel: bool = False
    #: Nature des cas (`DECISION`, `RUN`, `INFORMATION`) ; `nature_du_cas`,
    #: `(fiche, preuves, contexte) -> nature`, la décide cas par cas.
    nature: str = DECISION
    nature_du_cas: Callable | None = None


@dataclass(frozen=True)
class DetecteurArtiste:
    code: str
    libelle: str
    icone: str
    #: `contexte -> [(libellé, motif, preuves)]`
    juger: Callable
    nature: str = DECISION
    #: `(preuves, contexte) -> nature`
    nature_du_cas: Callable | None = None


@dataclass
class Contexte:
    disco: list
    obs: dict = field(default_factory=dict)  # track_id -> [Observation]
    relations_proposees: list = field(default_factory=list)
    #: Divergences d'IDENTITÉ entre lignes sœurs, lues par `analyser`
    #: (`divergences_identite_soeurs`) — le détecteur reste pur.
    soeurs: list = field(default_factory=list)
    #: Places des fiches sur leurs disques (`get_artist_positions`), lues par
    #: `analyser` : preuve d'appoint des 👯.
    positions: dict = field(default_factory=dict)
    #: Index calculés UNE fois par ouverture (un détecteur qui compare chaque
    #: fiche à toute la discographie coûtait des minutes chez Kanye).
    _memo: dict = field(default_factory=dict, repr=False)

    def memo(self, nom: str, fabrique: Callable):
        if nom not in self._memo:
            self._memo[nom] = fabrique()
        return self._memo[nom]


def contexte(tracks, obs=None, relations_proposees=None) -> Contexte:
    return Contexte(list(tracks), obs or {}, list(relations_proposees or []))


def impact(track) -> int:
    """Ce qu'une erreur sur la fiche fausserait : ses streams, toutes plateformes."""
    return (track.streams.spotify_streams or 0) + (track.streams.ytm_streams or 0)


def _mots(texte: str | None) -> str:
    t = developper_ligatures(texte or "")
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode().casefold()
    return " ".join(re.sub(r"[\W_]+", " ", t).split())


def _mesures(ctx: Contexte, track, champ: str) -> dict[str, str]:
    """`{source: valeur}` des observations RÉELLES d'un champ."""
    return {
        o.source: str(o.value)
        for o in ctx.obs.get(track.id, [])
        if o.field == champ and o.source not in _SOURCES_NON_MESUREES and o.value is not None
    }


# ── Durées ──────────────────────────────────────────────────────────────────


def _editions(track) -> tuple:
    """Durées des AUTRES éditions connues de la fiche (`track_editions`), posées
    par `analyser` : elles expliquent un témoin au lieu de l'accuser."""
    return tuple(getattr(track, "durees_editions", ()) or ())


#: ReccoBeats lu par l'ISRC (`reccobeats_resolution`) a lu la piste Spotify de
#: l'ISRC, pas celle de l'ID : quand la page de l'ID donne sa durée, il n'est
#: pas la voix de Spotify (2026-10-27, 6 cas du panneau). Kid Cudi « Erase
#: Me » : page de l'ID 192 s comme YouTube et Discogs, ReccoBeats 199 s par un
#: ISRC d'une autre parution — c'est l'ID qu'on accusait. Interrupteur simulé
#: par `scripts/simuler_durees_panneau.py`.
RECCOBEATS_ISRC_HORS_VOIX = True


def durees_du_vote(track) -> dict:
    """`{source: secondes}` que lisent les détecteurs et les politiques de
    durée : les durées observées, moins ReccoBeats lu par l'ISRC quand la page
    de l'ID Spotify donne la sienne (`RECCOBEATS_ISRC_HORS_VOIX`)."""
    durees = dict(track.durations_observees or {})
    if (
        RECCOBEATS_ISRC_HORS_VOIX
        and durees.get("spotify_web")
        and track.audio.reccobeats_resolution == "isrc"
    ):
        durees.pop("reccobeats", None)
    return durees


def _duree_youtube(track) -> int | None:
    return (track.durations_observees or {}).get(SOURCE_AUDIO)


def duree_songbpm_dementie(track, _ctx=None):
    """L'audio Topic dément la durée SongBPM : la page SongBPM était sans doute
    celle d'une autre version, son BPM et sa tonalité sont suspects aussi (cas
    « Blues (Live at AK Studios) » → page de « Blues »)."""
    yt, sb = _duree_youtube(track), (track.durations_observees or {}).get("songbpm")
    if yt and sb and abs(yt - sb) > ECART_DUREE_S:
        if voter(durees_du_vote(track), yt, editions=_editions(track)).nature == ISOLEE:
            return None  # c'est l'audio YouTube que les catalogues démentent (`duree_isolee`)
        return (
            f"durée SongBPM {sb} s ≠ audio YouTube {yt} s — page d'une autre version ? "
            "(BPM et tonalité SongBPM suspects)",
            {"youtube": yt, "songbpm": sb},
        )
    return None


def id_spotify_propose(track, ctx):
    """Un ID Spotify PROPOSÉ par une source qui n'a plus le droit d'en poser
    (SongBPM, ② de l'étape Identité — 2026-09-28) et que la fiche ne porte pas.
    Fiche sans ID : l'utilisateur peut le poser. Fiche à un AUTRE ID : peut-être
    une autre édition, peut-être l'ID de la fiche est faux — c'est dit, rien
    n'est tranché. Un ID déjà refusé (`retirer_id_spotify`) n'est plus montré."""
    proposes = {
        str(o.value): o.source
        for o in ctx.obs.get(track.id, [])
        if o.field == CHAMP_ID_PROPOSE and o.value
    }
    connus = set(track.get_all_spotify_ids())
    candidats = sorted(sid for sid in proposes if sid not in connus)
    if not candidats:
        return None
    from src.utils.corrections_fiches import ids_refuses

    refuses = ids_refuses(track)
    candidats = [sid for sid in candidats if sid not in refuses]
    if not candidats:
        return None
    sid = candidats[0]
    source = proposes[sid]
    if track.spotify_id:
        motif = f"{source} désigne l'ID Spotify {sid} — la fiche porte {track.spotify_id}"
    else:
        motif = f"{source} propose l'ID Spotify {sid} (fiche sans ID)"
    return motif, {
        "spotify_id_propose": sid,
        "spotify_id_fiche": track.spotify_id,
        "source": source,
    }


#: Durées INDÉPENDANTES d'un identifiant, et l'écart qui les dément : l'audio
#: Topic et la piste YTM sont le fichier du distributeur (97-99 % à ≤ 2 s) ;
#: Deezer est une autre plateforme, dont les éditions diffèrent de quelques
#: secondes (mesuré 2026-09-29 : un écart de 6 s sur 400 fiches, une édition).
_DUREES_TEMOINS = (
    (SOURCE_AUDIO, ECART_DUREE_S),
    ("ytmusic", ECART_DUREE_S),
    ("apple_music", ECART_DUREE_S),
    ("deezer", 10),
)


def duree_spotify_dementie(track, _ctx=None):
    """Une durée indépendante (audio Topic, piste YTM, Deezer) dément celle de
    l'identifiant Spotify : l'ID désigne peut-être un autre enregistrement (ses
    streams et ses mesures avec) — Kanye « New God Flow.1 » (295 s chez Deezer)
    portait l'ID de *New God Flow* (357 s)."""
    if not track.spotify_id:
        return None
    durees = durees_du_vote(track)
    for temoin, ecart in _DUREES_TEMOINS:
        ref = durees.get(temoin)
        for src in _SOURCES_PAR_ID_SPOTIFY:
            d = durees.get(src)
            if ref and d and abs(ref - d) > ecart:
                vote = voter(durees, d, editions=_editions(track))
                if vote.nature in (SOUTENUE, MAJORITAIRE):
                    return None  # le témoin est l'isolé (`duree_isolee`)
                if vote.nature == ISOLEE:
                    motif = (
                        f"durée de l'ID Spotify ({src}) {d} s démentie par "
                        f"{vote.dementie_par()} — autre enregistrement"
                    )
                else:
                    motif = (
                        f"durée de l'ID Spotify ({src}) {d} s ≠ {temoin} {ref} s — "
                        "autre enregistrement ?"
                    )
                return motif, {temoin: ref, src: d, "spotify_id": track.spotify_id}
    return None


def duree_deezer_dementie(track, _ctx=None):
    """L'audio Topic ou la piste YTM dément la durée de l'identifiant Deezer :
    le hit Deezer est peut-être celui d'une autre version."""
    durees = durees_du_vote(track)
    dz = durees.get("deezer")
    if not track.deezer_id or not dz:
        return None
    for temoin, ecart in _DUREES_TEMOINS:
        ref = durees.get(temoin)
        if temoin != "deezer" and ref and abs(ref - dz) > ecart:
            vote = voter(durees, dz, editions=_editions(track))
            if vote.nature in (SOUTENUE, MAJORITAIRE):
                return None  # le témoin est l'isolé (`duree_isolee`)
            if vote.nature == ISOLEE:
                motif = f"durée Deezer {dz} s démentie par {vote.dementie_par()} — autre version"
            else:
                motif = f"durée Deezer {dz} s ≠ {temoin} {ref} s — autre version ?"
            return motif, {temoin: ref, "deezer": dz, "deezer_id": track.deezer_id}
    return None


#: Témoins de durée qu'aucun autre détecteur n'accuse (Spotify et Deezer ont
#: le leur, qui porte l'action sur leur identifiant).
_TEMOINS_ISOLABLES = (SOURCE_AUDIO, "ytmusic", "youtube_officiel", "youtube_video", "apple_music")


def duree_isolee(track, _ctx=None):
    """Un témoin de durée que les autres catalogues démentent : la vidéo, la
    piste YTM ou la fiche Apple est celle d'une autre version (2026-10-13).
    Diam's « Vice Versa » : Spotify, Deezer et Apple à 240 s, l'audio YouTube
    à 277 s — avant le vote, c'est l'ID Spotify qu'on accusait."""
    durees = durees_du_vote(track)
    for source in _TEMOINS_ISOLABLES:
        d = durees.get(source)
        vote = voter(durees, d, editions=_editions(track))
        if vote and vote.nature == ISOLEE:
            return (
                f"durée {source} {d} s démentie par {vote.dementie_par()} — "
                "vidéo ou piste d'une autre version ?",
                {source: d, **vote.contre},
            )
    return None


def id_spotify_autre_version(track, _ctx=None):
    """Le titre de la page Spotify porte un descripteur de version que la fiche
    n'a pas, ou l'inverse : « Repose en paix (Remix) » portait l'ID de
    *Repose en paix*, « Waves (OG) » celui de *waves* de Miguel (la signature
    des 73 variantes de 2026-09-21, sur des ID antérieurs au gate). Un remix
    dont le REMIXEUR est crédité sur la page est bien le remix (« Know No
    Better (BROHUG Remix) », titré « Know No Better » chez Spotify)."""
    page = track.spotify_page_title
    if not track.spotify_id or not page:
        return None
    titre, _, credits = page.partition(" • ")
    if not variante_etrangere(track, {"name": titre}):
        # Un remix nu dont la page crédite d'autres artistes que les siens (et
        # pas ses invités) : un autre remix, que le titre seul ne trahit pas.
        autre = remix_d_autres_artistes(track, titre, credits.split(", "))
        if autre is None:
            return None
        inconnus, absents = autre
        return (
            f"l'ID Spotify sert « {titre} », crédité à {', '.join(inconnus)} sans "
            f"{', '.join(absents)} — un autre remix ?",
            {"spotify_id": track.spotify_id, "spotify_sert": titre, "credites": credits},
        )
    remixeur = parse_variant(track.title).remixer
    if remixeur and names_match_as_words(remixeur, credits):
        return None
    return (
        f"l'ID Spotify sert « {titre} » — une autre version ?",
        {"spotify_id": track.spotify_id, "spotify_sert": titre},
    )


def id_spotify_partage(track, ctx):
    """Deux fiches qui ne sont pas des lignes sœurs (pages Genius différentes)
    portent la même édition Spotify : c'est le MÊME enregistrement, deux pages
    Genius pour un morceau (Kanye « Castro » ×2, « SKY CITY » / « Sky City »).
    UN cas par groupe ; un doublon de titre déjà signalé n'en refait pas un."""

    def fabrique():
        par_id: dict[str, list] = {}
        for t in ctx.disco:
            for sid in set(t.spotify_ids or ([t.spotify_id] if t.spotify_id else [])):
                par_id.setdefault(sid, []).append(t)
        return par_id

    index = ctx.memo("par_spotify_id", fabrique)
    for sid in track.spotify_ids or ([track.spotify_id] if track.spotify_id else []):
        autres = [
            t
            for t in index.get(sid, ())
            if t is not track and not (t.genius_id and t.genius_id == track.genius_id)
        ]
        if not autres or any(
            t.id is not None and track.id is not None and t.id < track.id for t in autres
        ):
            continue
        if sid == track.spotify_id and _porteuses_de_l_id(ctx, "spotify_id", sid):
            continue  # `id_catalogue_partage` le juge
        if all(doublon_de_titre(t, ctx) or doublon_de_titre(track, ctx) for t in autres):
            continue
        return (
            f"même ID Spotify que « {autres[0].title} »"
            + (f" ({autres[0].artist.name})" if autres[0].artist else "")
            + " — même enregistrement, deux pages Genius ?",
            {"autres": [t.id for t in autres], "spotify_id": sid},
        )
    return None


#: Colonnes d'identité qu'un catalogue sert (titre, interprète, album, date).
CHAMPS_DU_CATALOGUE = ("spotify_id", "deezer_id")


def _id_du_catalogue(track, champ: str):
    return track.spotify_id if champ == "spotify_id" else track.deezer_id


def _porteuses_de_l_id(ctx, champ: str, valeur) -> list:
    """Les fiches de l'artiste qui portent `valeur` en `champ` PRINCIPAL, quand
    elles sont au moins deux PAGES Genius différentes (ni lignes sœurs, ni
    fiche sans page) et pas une réédition évidente ; [] sinon."""

    def fabrique():
        groupes: dict = {}
        for t in ctx.disco:
            for c in CHAMPS_DU_CATALOGUE:
                v = _id_du_catalogue(t, c)
                if v:
                    groupes.setdefault((c, str(v)), []).append(t)
        retenus = {}
        for cle, ts in groupes.items():
            pages = {t.genius_id for t in ts}
            if len(pages) < 2 or None in pages:
                continue
            if reeditions_evidentes([(t.title, t.album, t.duration) for t in ts]):
                continue
            retenus[cle] = ts
        return retenus

    return ctx.memo("porteuses_par_id_de_catalogue", fabrique).get((champ, str(valeur)), [])


def id_catalogue_partage(track, ctx):
    """Le même ID Spotify ou Deezer PRINCIPAL sur deux pages Genius : la même
    recherche de titre l'a posé sur les deux (« Forever » de Lil Wayne et celui
    de Drake, 2026-10-27). Ce que sert le catalogue pour l'ID désigne la page
    (`politiques_revue.politique_catalogue_designe`) ; UN cas par fiche
    porteuse, pour que chacune garde ou perde l'ID par sa propre correction."""
    for champ in CHAMPS_DU_CATALOGUE:
        valeur = _id_du_catalogue(track, champ)
        porteuses = _porteuses_de_l_id(ctx, champ, valeur) if valeur else []
        if track not in porteuses:
            continue
        autres = [t for t in porteuses if t is not track]
        catalogue = "Spotify" if champ == "spotify_id" else "Deezer"
        return (
            f"même ID {catalogue} que « {autres[0].title} »"
            + (f" et {len(autres) - 1} autre(s)" if len(autres) > 1 else "")
            + " — à quelle page est-il ?",
            {champ: valeur, "autres": [t.id for t in autres]},
        )
    return None


#: Fenêtre de recherche des paires : deux compteurs lus à quelques semaines
#: d'écart restent à 5 % l'un de l'autre ; `meme_compteur` juge ensuite.
_FENETRE_COMPTEUR = 1.05


def _paires_de_compteur(ctx) -> dict:
    """`{track_id: [(autre fiche, recouvrement des paroles)]}` : deux fiches
    (pages Genius et ID Spotify différents, titres parents) dont les compteurs
    Spotify suivent UNE courbe (`compteurs_partages.meme_compteur`)."""
    fiches = sorted(
        (t for t in ctx.disco if t.spotify_id and (t.streams.spotify_streams or 0) >= PLANCHER),
        key=lambda t: t.streams.spotify_streams,
    )
    paires: dict = {}
    for i, a in enumerate(fiches):
        for b in fiches[i + 1 :]:
            if b.streams.spotify_streams > a.streams.spotify_streams * _FENETRE_COMPTEUR:
                break
            if a.spotify_id == b.spotify_id or (a.genius_id and a.genius_id == b.genius_id):
                continue
            if not titres_parents(a.title, b.title):
                continue
            rythme = max(a.streams.spotify_daily_streams or 0, b.streams.spotify_daily_streams or 0)
            if not meme_compteur(
                lectures(ctx.obs.get(a.id)), lectures(ctx.obs.get(b.id)), rythme or None
            ):
                continue
            ra = paroles_de_reference(a.lyrics.text, a.lyrics.source)
            rb = paroles_de_reference(b.lyrics.text, b.lyrics.source)
            rec = recouvrement_bigrammes(ra, rb) if ra and rb else None
            paires.setdefault(a.id, []).append((b, rec))
            paires.setdefault(b.id, []).append((a, rec))
    return paires


def compteur_partage(track, ctx):
    """Deux fiches, deux ID Spotify, UN compteur (règle 2a, 2026-10-27) : il
    entre deux fois dans les totaux. Version proche (même ISRC, instrumental,
    paroles communes) : le compteur ne compte que sur une fiche ; sinon un des
    deux ID est souvent celui de l'autre morceau — ce que sert Spotify le dit
    (`politiques_revue.politique_compteur_partage`). UN cas par fiche."""
    paires = ctx.memo("paires_de_compteur", lambda: _paires_de_compteur(ctx))
    for autre, rec in paires.get(track.id, ()):
        preuve = version_proche(track, autre, rec)
        return (
            f"même compteur Spotify que « {autre.title} » ({autre.streams.spotify_streams:,})"
            + (f" — {preuve}" if preuve else " — paroles différentes : un des ID est-il faux ?"),
            {
                "spotify_id": track.spotify_id,
                "autre": autre.id,
                "autre_titre": autre.title,
                "autre_spotify_id": autre.spotify_id,
                "version_proche": preuve,
                "paroles": None if rec is None else round(rec, 2),
            },
        )
    return None


def generique_meme_duree(track, ctx):
    """Un titre GÉNÉRIQUE (intro, outro, interlude…) à la même durée qu'un autre
    titre générique de l'artiste : une donnée venue d'un rapprochement par titre
    (les 3 intros d'*Autopsie* à 94 s, toutes celles de SongBPM)."""
    if not titre_generique(track.title) or not track.duration:
        return None
    autres = [
        t
        for t in ctx.disco
        if t is not track and titre_generique(t.title) and t.duration == track.duration
    ]
    if autres:
        return (
            f"même durée ({track.duration} s) que « {autres[0].title} »"
            + (f" (+{len(autres) - 1})" if len(autres) > 1 else ""),
            {"duree": track.duration, "autres": [t.id for t in autres]},
        )
    return None


# ── Mesures audio ───────────────────────────────────────────────────────────


def _meme_tempo(a: float, b: float) -> bool:
    return any(abs(a * k - b) <= TOLERANCE_BPM * b for k in (0.5, 1, 2))


#: Source isolée dans 61 des 64 désaccords à trois sources (2026-09-28) : un
#: désaccord À DEUX où elle est seule contre une source fiable s'explique.
SOURCE_PEU_FIABLE = "getsongbpm"


def _desaccord_explique(valeurs: dict, retenue, meme) -> bool:
    """Le désaccord ne demande rien à l'utilisateur (2026-09-28, panneau le
    moins rempli possible) : la valeur RETENUE est celle d'une majorité stricte
    de sources — mesuré, le cas des 64 désaccords à trois sources —, ou celle de
    la source fiable face à GetSongBPM seul contre elle."""
    if retenue is None:
        return False
    d_accord = [s for s, v in valeurs.items() if meme(retenue, v)]
    if len(d_accord) * 2 > len(valeurs):
        return True
    return (
        len(valeurs) == 2
        and SOURCE_PEU_FIABLE in valeurs
        and d_accord == [s for s in valeurs if s != SOURCE_PEU_FIABLE]
    )


def bpm_desaccord(track, ctx):
    """Les sources de BPM se contredisent (hors octave) et le vote a tranché
    SANS le dire (Booba « Lunatic » : GetSongBPM 83, ReccoBeats et SongBPM 130).
    Un désaccord que la majorité ou la fiabilité explique n'est pas montré."""
    bpms = {s: float(v) for s, v in _mesures(ctx, track, "bpm").items()}
    vals = list(bpms.values())
    retenu = float(track.audio.bpm) if track.audio.bpm is not None else None
    if _desaccord_explique(bpms, retenu, _meme_tempo):
        return None
    if len(vals) > 1 and not all(_meme_tempo(vals[0], v) for v in vals[1:]):
        detail = ", ".join(f"{s} {v:g}" for s, v in sorted(bpms.items()))
        return (f"BPM en désaccord : {detail}", {"bpm": bpms})
    return None


def _meme_tonalite(a: tuple[int, int], b: tuple[int, int]) -> bool:
    """Identiques, ou RELATIVES (do majeur / la mineur : même armure, la
    confusion classique des analyseurs — mesuré, elle faisait l'essentiel du
    bruit)."""
    if a == b:
        return True
    (ka, ma), (kb, mb) = a, b
    if ma == mb:
        return False
    majeur, mineur = (ka, kb) if ma == 1 else (kb, ka)
    return (mineur + 3) % 12 == majeur


def tonalite_desaccord(track, ctx):
    """Les sources de tonalité se contredisent (paires COMPLÈTES seulement : une
    tonalité sans mode — SongBPM en omet — n'est pas un désaccord)."""
    cles, modes = _mesures(ctx, track, "key"), _mesures(ctx, track, "mode")
    paires = {}
    for s, k in cles.items():
        try:
            paires[s] = (int(float(k)), int(float(modes[s])))
        except (KeyError, ValueError):
            continue
    vals = list(paires.values())
    retenue = (
        (int(track.audio.key), int(track.audio.mode))
        if track.audio.key is not None and track.audio.mode is not None
        else None
    )
    if _desaccord_explique(paires, retenue, _meme_tonalite):
        return None
    if len(vals) > 1 and not all(_meme_tonalite(vals[0], v) for v in vals[1:]):
        noms = ("do", "do♯", "ré", "mi♭", "mi", "fa", "fa♯", "sol", "la♭", "la", "si♭", "si")
        detail = ", ".join(
            f"{s} {noms[k % 12]} {'majeur' if m else 'mineur'}"
            for s, (k, m) in sorted(paires.items())
        )
        return (f"tonalité en désaccord : {detail}", {"tonalites": paires})
    return None


#: Familles dont la fiche est une AUTRE PRISE que son original — ni la même
#: édition (radio edit, remaster, « Physical Version » : mêmes mesures, même LRC
#: attendus), ni le même beat ou la même voix (instrumental, a cappella).
FAMILLES_AUTRE_PRISE = frozenset(
    {"demo", "reference", "alternate", "snippet", "performance", "remix_named", "remix_bare"}
)


def autre_prise_hors_plateformes(track, ctx):
    """L'original d'une fiche qui est une AUTRE PRISE et n'a AUCUNE trace de
    plateforme (ni ID Spotify ni ID Deezer) — None sinon.

    Une telle fiche (démo, référence, live inédit, remix non officiel) n'a ni
    page SongBPM (SongBPM ne référence que le catalogue Spotify) ni LRC à elle :
    ce qu'elle porte de SongBPM ou de YouTube Music vient d'une recherche qui a
    servi l'original (mesuré 2026-09-28 : 106 fiches aux mesures SongBPM de leur
    original, 34 au LRC de leur original)."""
    if track.spotify_id or track.deezer_id:
        return None
    if famille_de(track.title) not in FAMILLES_AUTRE_PRISE:
        return None
    return socle_parmi(track.title, ctx.disco, ctx.memo("socles", lambda: IndexSocles(ctx.disco)))


def songbpm_de_l_original(track, ctx):
    """FORMEL. Une autre prise hors plateformes dont la durée SongBPM (à 2 s) ET
    le tempo SongBPM sont ceux de son original : la page SongBPM était celle de
    l'original — BPM, tonalité et durée avec (« All Day (Kendrick Lamar
    Reference) » 311 s / 123 bpm, comme *All Day*)."""
    socle = autre_prise_hors_plateformes(track, ctx)
    if socle is None or not socle.duration or not socle.audio.bpm:
        return None
    duree, bpm = _songbpm(ctx, track, "duration"), _songbpm(ctx, track, "bpm")
    try:
        if not duree or not bpm or abs(int(float(duree)) - int(socle.duration)) > 2:
            return None
        if not _meme_tempo(float(bpm), float(socle.audio.bpm)):
            return None
    except ValueError:
        return None
    mesures = {o.field: str(o.value) for o in ctx.obs.get(track.id, []) if o.source == "songbpm"}
    return (
        f"durée {duree} s et tempo {bpm} SongBPM = ceux de « {socle.title} » — une "
        "version sans plateforme n'a pas de page SongBPM à elle",
        {"original": socle.id, "songbpm": mesures},
    )


def songbpm_copie_de_l_original(track, ctx):
    """Une VERSION (live, remix…) dont BPM ET durée SongBPM sont ceux de son
    original : la page était celle de l'original (Freeze « Louisville (Remix) »,
    196 s pour 193 s). Un INSTRUMENTAL n'est pas visé — même beat, même tempo
    attendus (décision utilisateur)."""
    if parse_variant(track.title).kind == Kind.NONE or famille_de(track.title) == "instrumental":
        return None
    if songbpm_de_l_original(track, ctx):
        return None  # formel : `songbpm_de_l_original`
    socle = socle_parmi(track.title, ctx.disco, ctx.memo("socles", lambda: IndexSocles(ctx.disco)))
    if socle is None:
        return None
    v = {f: _songbpm(ctx, track, f) for f in ("bpm", "duration")}
    o = {f: _songbpm(ctx, socle, f) for f in ("bpm", "duration")}
    # Une TONALITÉ lue des deux côtés et différente : deux pages SongBPM
    # distinctes, la coïncidence de tempo et de durée n'est pas une copie
    # (« Jamm - Remix » 5 mineur, « Jamm » 6 majeur, 2026-10-29).
    for f in ("key", "mode"):
        lue, originale = _songbpm(ctx, track, f), _songbpm(ctx, socle, f)
        if lue is not None and originale is not None and lue != originale:
            return None
    if v["bpm"] and v["duration"] and v == o:
        return (
            f"BPM {v['bpm']} et durée {v['duration']} s SongBPM identiques à « {socle.title} » "
            "— page de l'original ?",
            {"songbpm": v, "original": socle.id},
        )
    return None


def _songbpm(ctx, track, champ):
    for o in ctx.obs.get(track.id, []):
        if o.field == champ and o.source == "songbpm":
            return str(o.value)
    return None


# ── Paroles, pages, crédits ─────────────────────────────────────────────────


#: Sous ce nombre de mots distincts, les paroles Genius sont un EXTRAIT (snippet,
#: fragment — « Good Ass Job » : 12 mots pour un LRC de 190) : le recouvrement ne
#: peut ni confirmer ni douter. Le démenti, lui, reste jugé dès 8 mots.
MOTS_MIN_DOUTE = 40

#: Sessions et émissions (décision utilisateur 2026-09-28 : une catégorie « live »
#: qui regroupe versions live, Grünt, COLORS, OKLM, Skyrock). COLORS est lu par le
#: vocabulaire des versions (famille live) ; « OKLM » ou « Planète rap » seuls
#: sont aussi des TITRES (Booba), d'où le contexte exigé.
_SESSION_RE = re.compile(r"gr[uü]nt|#\s*plan[eè]te\s*rap", re.IGNORECASE)
_EMISSION_RE = re.compile(r"plan[eè]te\s*rap|skyrock|oklm", re.IGNORECASE)
_CONTEXTE_RE = re.compile(r"freestyle|session|live|radio|couvre[\s-]*feu", re.IGNORECASE)


def session_live(track) -> bool:
    """Version live, session (COLORS) ou freestyle d'émission (Grünt, Planète Rap,
    Skyrock, OKLM) : leurs LRC se jugent à part — un freestyle collectif de vingt
    minutes n'a au mieux qu'un couplet synchronisé quelque part."""
    titre = track.title or ""
    if famille_de(titre) == "performance" or _SESSION_RE.search(titre):
        return True
    return bool(_EMISSION_RE.search(titre) and _CONTEXTE_RE.search(titre))


def _versions_soeurs(a, b) -> bool:
    """Deux VERSIONS d'un même socle dont aucune n'est l'original (« Can't Tell Me
    Nothing (R.O.C. Remix) » / « (Jeezy Remix) ») : peut-être le même morceau
    sous deux pages."""
    if parse_variant(a.title).kind == Kind.NONE or parse_variant(b.title).kind == Kind.NONE:
        return False
    return socle_normalise(a.title) == socle_normalise(b.title)


def _verdict_lrc(track, ctx):
    """`(code, motif, preuves)` d'un LRC que l'oracle ne tranche pas seul, ou
    None — code `lrc` (douteux), `doublon` (le LRC d'une version sœur). Mémorisé :
    trois détecteurs le consultent."""
    cache = ctx.memo("verdict_lrc", dict) if ctx is not None else {}
    if track.id in cache:
        return cache[track.id]
    cache[track.id] = verdict = _juger_lrc(track, ctx)
    return verdict


def _juger_lrc(track, ctx):
    if not track.lyrics.synced:
        return None
    ref = paroles_de_reference(track.lyrics.text, track.lyrics.source)
    if lrc_dementi(ref, track.lyrics.synced) or len(mots(ref)) < MOTS_MIN_DOUTE:
        return None
    source = track.lyrics.synced_source
    trouve = _lrc_autre_candidat(track, ctx) if ctx is not None else None
    if trouve is not None:
        autre, meilleur, propre = trouve
        if _lrc_formel(track, ctx, trouve):
            return None  # formel : `lrc_d_une_autre_fiche`
        preuves = {
            "autre": autre.id,
            "bigrammes": round(propre, 2),
            "bigrammes_autre": round(meilleur, 2),
            "source": source,
        }
        if _versions_soeurs(track, autre):
            return (
                "doublon",
                f"même morceau que « {autre.title} » ? Son LRC ({source or '?'}) y colle à "
                f"{meilleur:.0%}, à {propre:.0%} aux paroles de celle-ci",
                {**preuves, "autres": [autre.id]},
            )
        return (
            "lrc",
            f"LRC ({source or '?'}) peut-être celui de « {autre.title} » "
            f"({meilleur:.0%} de paires communes, {propre:.0%} avec ses propres paroles)",
            preuves,
        )
    score = recouvrement(ref, track.lyrics.synced)
    if score is not None and SEUIL_FAUX <= score < SEUIL_JUSTE:
        return (
            "lrc",
            f"LRC ({source or '?'}) : {score:.0%} de mots communs avec les paroles Genius",
            {"recouvrement": score, "source": source},
        )
    return None


def lrc_douteux(track, ctx=None):
    """LRC ni démenti (écarté d'office) ni confirmé (≥ 0,6) par les paroles
    Genius, ni reconnu comme celui d'une autre fiche : ce que l'oracle ne sait
    pas trancher seul. Les sessions live ont leur propre détecteur."""
    verdict = _verdict_lrc(track, ctx)
    if verdict and verdict[0] == "lrc" and not session_live(track):
        return verdict[1], verdict[2]
    return None


def lrc_session_live(track, ctx=None):
    """Les LRC douteux des versions live et des freestyles d'émission (Grünt,
    COLORS, OKLM, Skyrock, Planète Rap), rangés à part."""
    verdict = _verdict_lrc(track, ctx)
    if verdict and verdict[0] == "lrc" and session_live(track):
        return verdict[1], verdict[2]
    return None


def doublon_par_lrc(track, ctx):
    """Deux versions sœurs dont l'une porte le LRC qui colle aux paroles de
    l'autre : peut-être le même morceau sous deux pages — envoyé au traitement
    des doublons (fusion), le LRC pouvant aussi être retiré."""
    verdict = _verdict_lrc(track, ctx)
    if verdict and verdict[0] == "doublon":
        return verdict[1], verdict[2]
    return None


#: Écart minimal de paires communes entre l'autre fiche et la fiche elle-même
#: pour attribuer un LRC à l'autre (mesuré 2026-09-27 : 91 cas sur 230, démos et
#: références qui avaient reçu le LRC de l'original).
ECART_AUTRE_FICHE = 0.3
#: Au-delà, la fiche partage trop de son propre texte avec ce LRC pour que la
#: preuve soit formelle — lives, remix, démos qui reprennent le refrain, doublons
#: de casse (83 cas mesurés entre 0,4 et 0,6) : à trancher.
PROPRE_MAX_FORMEL = 0.4
#: … sauf quand le LRC colle NETTEMENT mieux à l'autre fiche (2026-09-28) :
#: au moins 70 % de ses paires, 25 points au-dessus des siennes, et moins de
#: 60 % avec les siennes. Relu sur la base entière : les 22 cas ainsi rendus
#: formels étaient TOUS une autre prise portant le LRC de son original (démo,
#: remix, « Version 1 », live, remaster, medley — le plus bas : 71 % contre 41 %).
AUTRE_MIN_NET, ECART_MIN_NET, PROPRE_MAX_NET = 0.70, 0.25, 0.60
#: Une paire de mots présente dans plus de fiches que cela (« i m », « you
#: know ») ne sert pas à désigner des candidates : l'index ne garde que les
#: paires rares, et seules les meilleures candidates sont comparées en entier.
_DF_MAX = 40
_CANDIDATES = 10


def _part_commune(a: set, b: set) -> float:
    return max(len(a & b) / len(a), len(a & b) / len(b)) if a and b else 0.0


def _references_bigrammes(ctx) -> dict:
    """`{track_id: (texte, bigrammes)}` des paroles Genius JUGEABLES de la
    discographie, calculé une fois par ouverture."""

    def fabrique():
        index = {}
        for t in ctx.disco:
            ref = paroles_de_reference(t.lyrics.text, t.lyrics.source)
            if ref and len(mots(ref)) >= MOTS_MIN:
                index[t.id] = (ref.strip(), bigrammes(ref))
        return index

    return ctx.memo("bigrammes", fabrique)


def _index_bigrammes(ctx) -> dict:
    """`{paire rare: [track_id]}` — sans lui, comparer chaque LRC à toutes les
    paroles de Kanye West coûtait 15 s à l'ouverture."""

    def fabrique():
        index: dict = {}
        for tid, (_texte, paires) in _references_bigrammes(ctx).items():
            for b in paires:
                index.setdefault(b, []).append(tid)
        return {b: tids for b, tids in index.items() if len(tids) <= _DF_MAX}

    return ctx.memo("index_bigrammes", fabrique)


def _titre_sans_feat(titre: str | None) -> str:
    """« Cavaliero (Feat. Koba LaD) » et « CAVALIERO » : la même fiche en double,
    pas une autre fiche."""
    return cle_doublon(re.sub(r"[\(\[]\s*(?:feat|ft)\b[^\)\]]*[\)\]]", "", titre or "", flags=re.I))


def _lrc_autre_candidat(track, ctx):
    """`(autre fiche, part commune avec elle, part commune avec la sienne)` quand
    le LRC colle nettement mieux aux paroles d'une autre fiche ; None sinon.
    Mémorisé : deux détecteurs le consultent."""
    cache = ctx.memo("lrc_autre", dict)
    if track.id not in cache:
        cache[track.id] = _chercher_lrc_autre(track, ctx)
    return cache[track.id]


def _chercher_lrc_autre(track, ctx):
    lrc = track.lyrics.synced
    refs = _references_bigrammes(ctx)
    if not lrc or track.id not in refs or len(mots(lrc)) < MOTS_MIN:
        return None
    texte, siens = refs[track.id]
    paires = bigrammes(lrc)
    propre = _part_commune(siens, paires)
    if propre >= SEUIL_JUSTE_BIGRAMMES:
        return None
    par_id = ctx.memo("par_id", lambda: {t.id: t for t in ctx.disco})
    index = _index_bigrammes(ctx)
    votes: dict[int, int] = {}
    for b in paires:
        for tid in index.get(b, ()):
            votes[tid] = votes.get(tid, 0) + 1
    moi = _titre_sans_feat(track.title)
    meilleur, autre = 0.0, None
    for tid in sorted(votes, key=votes.get, reverse=True)[: _CANDIDATES + 2]:
        t = par_id[tid]
        if t is track or (track.genius_id and t.genius_id == track.genius_id):
            continue
        texte_autre, les_siens = refs[tid]
        if texte_autre == texte or _titre_sans_feat(t.title) == moi:
            continue
        part = _part_commune(les_siens, paires)
        if part > meilleur:
            meilleur, autre = part, t
    if autre is None or meilleur < SEUIL_JUSTE_BIGRAMMES or meilleur - propre < ECART_AUTRE_FICHE:
        return None
    return autre, meilleur, propre


def _part_du_lrc(autre, track, ctx) -> float:
    """Part des paires du LRC que les paroles de l'autre fiche EXPLIQUENT. La
    part commune retient le meilleur des deux sens : un EXTRAIT (« Manque de
    sommeil » de barely afk, huit lignes du refrain de *way back* samplées)
    y fait 100 % contre le LRC entier du morceau (2026-09-29 : six corrections
    fautives sur 25, toutes sous 45 %)."""
    paires = bigrammes(track.lyrics.synced)
    les_siens = _references_bigrammes(ctx)[autre.id][1]
    return len(les_siens & paires) / len(paires) if paires else 0.0


def _lrc_formel(track, ctx, trouve) -> bool:
    """Le LRC est-il FORMELLEMENT celui de l'autre fiche ? Oui sous 40 % de
    paires avec ses propres paroles ; oui aussi, au-delà, quand la fiche est une
    AUTRE PRISE hors plateformes et que l'autre fiche est SON original (une démo,
    un live inédit n'ont aucun LRC à eux — le refrain commun fait monter le
    recouvrement sans rien prouver)."""
    autre, meilleur, propre = trouve
    if propre < PROPRE_MAX_FORMEL or autre is autre_prise_hors_plateformes(track, ctx):
        return True
    # Deux versions SŒURS (aucune n'est l'original) posent une question de
    # fusion : elles restent au détecteur `doublon_lrc`.
    return (
        meilleur >= AUTRE_MIN_NET
        and _part_du_lrc(autre, track, ctx) >= AUTRE_MIN_NET
        and meilleur - propre >= ECART_MIN_NET
        and propre < PROPRE_MAX_NET
        and not _versions_soeurs(track, autre)
    )


def lrc_d_une_autre_fiche(track, ctx):
    """FORMEL. Le LRC colle aux paroles d'une AUTRE fiche de l'artiste (≥ 60 %
    de paires de mots communes), et à moins de 40 % aux siennes : c'est le LRC
    de l'autre (Kanye « Ghost Town (Demo) » portait celui de *Ghost Town*,
    « Get Off Me » celui de *Can't Tell Me Nothing*). Une fiche aux MÊMES
    paroles (version héritée, page sœur) ou au même titre n'est pas une autre
    fiche."""
    trouve = _lrc_autre_candidat(track, ctx)
    if trouve is None or not _lrc_formel(track, ctx, trouve):
        return None
    autre, meilleur, propre = trouve
    from src.utils.corrections_fiches import empreinte_lrc

    return (
        f"LRC de « {autre.title} » ({meilleur:.0%} de paires de mots communes, "
        f"{propre:.0%} avec ses propres paroles)",
        {
            "autre": autre.id,
            "bigrammes": round(propre, 2),
            "bigrammes_autre": round(meilleur, 2),
            "source": track.lyrics.synced_source,
            "empreinte": empreinte_lrc(track.lyrics.synced),
        },
    )


#: Part des paires de mots d'un texte de REPLI (paroles hors Genius, ou LRC
#: d'une fiche sans paroles Genius) qu'expliquent les paroles Genius d'une
#: autre fiche, à partir de laquelle ce texte est peut-être le sien (WIP du
#: 2026-10-12 : A2H « Pardonne moi » portait les paroles Musixmatch et le LRC
#: YTM de « Pardonnez-moi », 100 %).
PAROLES_AUTRE_MIN = 0.8
#: … et ce texte doit couvrir au moins cette part des paires de l'autre : une
#: page qui contient TOUT un concert (« Free Larry Hoover Benefit Concert »)
#: explique n'importe quel morceau joué, sans en être un (mesuré le 2026-10-30).
PAROLES_AUTRE_COUVERTES_MIN = 0.5
#: Ressemblance des titres (hors groupes entre parenthèses) au-delà de laquelle
#: deux fiches sont deux graphies (« Coal lla » / « Caol Ila », « Heaven At
#: Night » / « Heaven at Nite », « La 5ème saison » / « La Cinquième Saison » :
#: 0,75 ; les titres étrangers signalés le 2026-10-30 restent sous 0,56).
GRAPHIE_TITRE_MIN = 0.75


def _titres_parents(a: str | None, b: str | None) -> bool:
    """Même titre une fois les groupes entre parenthèses retirés, l'un contenu
    dans l'autre (mots entiers : « Fais les backs (Live) » / « 22h31 - Fais
    les backs » ; parenthèses comprises : « DCR (Dolce Camara Remix) » /
    « Dolce Camara (Solo) »), ou deux graphies proches."""
    from src.utils.version_descriptors import meme_socle, titres_confondus

    if meme_socle(a, b) or titres_confondus(a, b) or _titre_sans_feat(a) == _titre_sans_feat(b):
        return True
    na = normalize_title(re.sub(r"\s*[\(\[][^)\]]*[\)\]]", "", a or "")) or normalize_title(a)
    nb = normalize_title(re.sub(r"\s*[\(\[][^)\]]*[\)\]]", "", b or "")) or normalize_title(b)
    if not na or not nb:
        return False
    if na == nb or contains_as_words(na, nb) or contains_as_words(nb, na):
        return True
    if contains_as_words(na, normalize_title(b)) or contains_as_words(nb, normalize_title(a)):
        return True
    return SequenceMatcher(None, na, nb).ratio() >= GRAPHIE_TITRE_MIN


def _relation_legitime(track, autre) -> bool:
    """Deux fiches dont le texte PEUT être commun : ligne sœur, version ou
    graphie du même morceau, relation Genius dans un sens ou l'autre (remix,
    reprise, interpolation, sample…), contenant et contenu."""
    from src.utils.contenants import ids_contenus

    if track.genius_id and autre.genius_id == track.genius_id:
        return True
    if _titres_parents(track.title, autre.title):
        return True
    if autre.id in ids_contenus(track) or track.id in ids_contenus(autre):
        return True
    for a, b in ((track, autre), (autre, track)):
        for rel in a.relationships or []:
            if rel.get("track_id") is not None and rel.get("track_id") == b.id:
                return True
            if b.genius_url and rel.get("url") == b.genius_url:
                return True
            if rel.get("title") and normalize_title(rel["title"]) == normalize_title(b.title):
                return True
    return False


def _textes_de_repli(track, refs) -> list[tuple[str, str]]:
    """`[(libellé, texte)]` que rien ne juge : les paroles d'une source autre
    que Genius, et le LRC d'une fiche sans paroles Genius jugeables (avec
    elles, `lrc_d_une_autre_fiche` le juge déjà)."""
    textes = []
    if track.id in refs:
        return textes
    if track.lyrics.text and paroles_de_reference(track.lyrics.text, track.lyrics.source) is None:
        textes.append((f"paroles {track.lyrics.source or '?'}", track.lyrics.text))
    if track.lyrics.synced:
        textes.append((f"LRC {track.lyrics.synced_source or '?'}", track.lyrics.synced))
    return [(lib, t) for lib, t in textes if len(mots(t)) >= MOTS_MIN]


def paroles_d_une_autre_fiche(track, ctx):
    """Une fiche SANS paroles Genius jugeables porte des paroles de repli
    (Musixmatch, LyricFind, YTM) ou un LRC dont au moins 80 % des paires de
    mots sont dans les paroles Genius d'une AUTRE fiche de l'artiste — sans
    relation qui l'explique (`_relation_legitime`). Rien ne juge ce texte : un
    DÉTECTEUR, pas une règle (des vols probables : « Pardonne moi » ←
    « Pardonnez-moi », Shurik'n « Interlude » ← « Samuraï »). Le refus existe
    (`refuser_paroles_ytm`, retrait du LRC)."""
    refs = _references_bigrammes(ctx)
    textes = _textes_de_repli(track, refs)
    if not textes:
        return None
    par_id = ctx.memo("par_id", lambda: {t.id: t for t in ctx.disco})
    index = _index_bigrammes(ctx)
    meilleur, autre, quels = 0.0, None, []
    for libelle, texte in textes:
        paires = bigrammes(texte)
        if not paires:
            continue
        votes: dict[int, int] = {}
        for b in paires:
            for tid in index.get(b, ()):
                votes[tid] = votes.get(tid, 0) + 1
        for tid in sorted(votes, key=votes.get, reverse=True)[:_CANDIDATES]:
            t = par_id[tid]
            if t is track or t.id == track.id:
                continue
            les_siens = refs[tid][1]
            commun = len(les_siens & paires)
            part = commun / len(paires)
            if (
                part < PAROLES_AUTRE_MIN
                or commun / len(les_siens) < PAROLES_AUTRE_COUVERTES_MIN
                or _relation_legitime(track, t)
            ):
                continue
            if autre is not None and t.id == autre.id:
                meilleur = max(meilleur, part)
                quels.append(libelle)
            elif part > meilleur:
                meilleur, autre, quels = part, t, [libelle]
    if autre is None:
        return None
    return (
        f"{' et '.join(sorted(set(quels)))} : ceux de « {autre.title} » ? "
        f"({meilleur:.0%} de leurs paires de mots dans ses paroles Genius)",
        {
            "autre": autre.id,
            "part": round(meilleur, 2),
            "textes": sorted(set(quels)),
            "empreinte": f"autre:{autre.genius_id or autre.id}",
        },
    )


def page_sans_info(track, _ctx=None):
    """Page Genius lue qui ne dit rien (🕳️) : morceau poubelle ou vrai inédit."""
    if sans_info(track):
        return ("page Genius lue sans aucune information (🕳️)", {})
    return None


def page_annotee_sans_trace(track, _ctx=None):
    """URL Genius `-annotated` (le tag « Non-Music ») sans aucune trace de
    plateforme : c'est ainsi qu'on a trouvé les pages poubelles du 2026-09-26
    (29 supprimées sur 47). Le tag seul couvre aussi de vrais skits. Une page
    qui ne dit rien (`sans_info`) est déjà montrée pour information (🕳️,
    décision utilisateur 2026-10-26) : elle n'est pas une décision de plus
    (« NICE THAT », « Real shit », 2026-10-29)."""
    if (
        (track.genius_url or "").endswith("-annotated")
        and not track.spotify_id
        and not track.deezer_id
        and not track.videos
        and not sans_info(track)
    ):
        return ("page Genius « Non-Music » sans trace de plateforme ni vidéo", {})
    return None


def credits_api_seuls(track, _ctx=None):
    """Page lue, et des crédits que SEULE l'API Genius donne (🎫) : la page ne
    les porte pas. Souvent légitimes (Kid Cudi « Programmer »), mais un grand
    nombre trahirait un parseur qui perd des lignes."""
    if not track.last_scraped:
        return None
    api = [c for c in track.credits if c.source == SOURCE_API]
    if api:
        noms = ", ".join(f"{c.name} ({c.role.value})" for c in api[:3])
        return (
            f"{len(api)} crédit(s) 🎫 que la page ne confirme pas : {noms}"
            + ("…" if len(api) > 3 else ""),
            {"credits": [(c.name, c.role.value) for c in api]},
        )
    return None


def nature_credits_api(track, _preuves, _ctx) -> str:
    """Sans AUCUN crédit scrapé, la section crédits n'a rien donné : l'étape
    Crédits la relira (`est_manquant(CREDITS_GENIUS)` ne regarde que la source
    `genius`) — un run, pas une décision. À côté de crédits scrapés, ce sont
    les compléments que la page ne montre pas, GARDÉS en 🎫 par décision
    utilisateur (2026-09-26) : une information."""
    if any(c.source == SOURCE_SCRAPE for c in track.credits):
        return INFORMATION
    return RUN


def cle_doublon(titre: str | None) -> str:
    """Le titre à la casse, aux accents et à la ponctuation près — mais AVEC ses
    descripteurs : « Heartless » et « Heartless (Live) » sont deux fiches
    légitimes (`normalize_title` les confondait : 465 faux doublons chez Kanye)."""
    return _mots(titre).replace(" ", "")


def _interprete(track) -> str:
    """L'interprète de la fiche : depuis e36 le titre n'est plus une identité,
    deux « Heartless » (l'original, une reprise d'un tiers) sont légitimes."""
    return cle_doublon(track.primary_artist_name) if track.is_featuring else ""


def _index_doublons(disco) -> dict[tuple, list]:
    index: dict[tuple, list] = {}
    for t in disco:
        if not t.secondary_role:
            index.setdefault((cle_doublon(t.title), _interprete(t)), []).append(t)
    return index


def _disques_distincts(a, b) -> bool:
    """Deux fiches rangées sur deux disques DIFFÉRENTS (ni sans album, ni
    éditions d'un même disque — « BULLY » / « BULLY - DELUXE ») : deux morceaux
    au même titre, pas un doublon (« Intro » de College Dropout et de
    Graduation, « OUTSIDE » de 2016 et de JACKBOYS 2 — relevé le 2026-09-28)."""
    ca, cb = cle_album(a.album), cle_album(b.album)
    return bool(ca and cb) and not (ca.startswith(cb) or cb.startswith(ca))


def _paroles_distinctes(a, b) -> bool:
    """Les paroles Genius des deux pages DÉMENTENT le doublon : moins de
    `SEUIL_FAUX_BIGRAMMES` de paires de mots communes (l'oracle des LRC : deux
    morceaux différents y tombent à 99 %). Deux morceaux au même titre, pas un
    doublon (« Famous » de *The Life of Pablo* et celui avec Playboi Carti et
    Future, 3 % ; 2026-10-29). Sans paroles jugeables, rien n'est dit."""
    ra = paroles_de_reference(a.lyrics.text, a.lyrics.source)
    rb = paroles_de_reference(b.lyrics.text, b.lyrics.source)
    paires = recouvrement_bigrammes(ra, rb) if ra and rb else None
    return paires is not None and paires < SEUIL_FAUX_BIGRAMMES


def doublon_de_titre(track, ctx):
    """Deux fiches de l'artiste au même titre, à la casse et à la ponctuation
    près (Booba « 3G » / « 3 G ») : doublon à fusionner, ou deux morceaux
    distincts à renommer. UN cas par groupe, porté par la fiche d'identifiant le
    plus bas (une paire en faisait deux, 2026-09-28)."""
    if track.secondary_role:
        return None
    cle = (cle_doublon(track.title), _interprete(track))
    autres = [
        t
        for t in ctx.memo("doublons", lambda: _index_doublons(ctx.disco)).get(cle, [])
        if t is not track
        and not (t.genius_id and track.genius_id and t.genius_id == track.genius_id)
        and not _disques_distincts(t, track)
        and not _paroles_distinctes(t, track)
    ]
    if autres and any(
        t.id is not None and track.id is not None and t.id < track.id for t in autres
    ):
        return None
    if cle[0] and autres:
        return (
            f"même titre que « {autres[0].title} »"
            + (f" ({autres[0].album})" if autres[0].album else " (sans album)")
            + (f" (+{len(autres) - 1})" if len(autres) > 1 else "")
            + (f" — celle-ci : {track.album}" if track.album else ""),
            {"autres": [t.id for t in autres]},
        )
    return None


def doublon_inedit(track, ctx):
    """« X (Unreleased) » à côté de « X » : normalement fusionnés en fin de run
    discographie (`inedits.doublons_inedits`) — un reste ici est un run
    interrompu."""
    from src.utils.inedits import doublons_inedits

    paires = ctx.memo("inedits", lambda: {id(a): g for a, g in doublons_inedits(ctx.disco)})
    gardee = paires.get(id(track))
    if gardee is not None:
        return (f"inédit en double de « {gardee.title} »", {"garde": gardee.id})
    return None


# ── Vidéos, streams, dates ──────────────────────────────────────────────────


def _video_couvre(socle: str, titre_video: str) -> bool:
    if not socle:
        return True
    if set(socle.split()) <= set(titre_video.split()):
        return True
    if socle.replace(" ", "") in titre_video.replace(" ", ""):
        return True  # « N°10 » / « N° 10 »
    mots_socle = socle.split()
    if len(mots_socle) >= 4 and "".join(m[0] for m in mots_socle) in titre_video.split():
        # Le SIGLE du titre (« LMLVSB » pour « La mort leur va si bien »),
        # mot entier, à partir de quatre mots (2026-09-28 : faux positifs relevés
        # en échantillon ; en deçà, deux ou trois lettres se trouvent partout).
        return True
    n = len(socle)
    fenetres = (titre_video[i : i + n] for i in range(max(1, len(titre_video) - n + 1)))
    return max((SequenceMatcher(None, socle, f).ratio() for f in fenetres), default=0) >= (
        SIMILARITE_VIDEO
    )


def _une_lettre_pres(a: str, b: str) -> bool:
    """Distance d'édition ≤ 1 (« vu » / « vue »), même initiale."""
    if a == b:
        return True
    if not a or not b or a[0] != b[0] or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b, strict=True)) == 1
    court, long_ = (a, b) if len(a) < len(b) else (b, a)
    return any(long_[:i] + long_[i + 1 :] == court for i in range(len(long_)))


def _mots_a_une_lettre(socle: str, titre_video: str) -> bool:
    """Chaque mot du titre (≥ 3 mots) se retrouve dans la vidéo à une lettre
    près : « Vu D'Ici » / « Vue d'ici » (2026-09-28, faux positifs relevés)."""
    mots_socle, mots_video = socle.split(), titre_video.split()
    return len(mots_socle) >= 3 and all(
        any(_une_lettre_pres(m, v) for v in mots_video) for m in mots_socle
    )


_FREESTYLE_RE = re.compile(r"\bfreestyles?\b", re.IGNORECASE)


def _freestyle_d_emission(track, titre_video: str) -> bool:
    """Un freestyle (ou une session d'émission) n'a pas de « titre » que la
    vidéo reprendrait : « Gros freestyle de L'Entourage en live dans Planète
    Rap ! » est publié « L'Entourage - Freestyle [Part. 1] #PlanèteRap ». Une
    vidéo de freestyle ou de l'émission le couvre (2026-09-28)."""
    titre = track.title or ""
    if not (_FREESTYLE_RE.search(titre) or session_live(track)):
        return False
    return bool(_FREESTYLE_RE.search(titre_video) or _EMISSION_RE.search(titre_video))


_SESSION_NOMMEE_RE = re.compile(
    r"tiny\s*desk|colors|concert|show|tour|unplugged|storytellers|sessions?", re.IGNORECASE
)


def _titres_alternatifs(track) -> list[str]:
    """Le contenu d'une parenthèse qui n'est PAS un descripteur de version est
    un titre alternatif : « Bigger Than You (Do It Alone) », « Intro (Table
    d'écoute) ». Deux mots au moins (un mot seul se trouve partout)."""
    alternatifs = []
    for contenu in re.findall(r"[\(\[]([^)\]]+)[\)\]]", track.title or ""):
        mots_p = _mots(contenu)
        if (
            len(mots_p.split()) >= 2
            and parse_variant(f"x ({contenu})").kind == Kind.NONE
            # Un nom de SESSION n'est pas un titre : « Temptations (Tiny Desk
            # Home) » était couvert par le Tiny Desk de Ty Dolla $ign.
            and not _CONTEXTE_RE.search(contenu)
            and not _SESSION_NOMMEE_RE.search(contenu)
        ):
            alternatifs.append(mots_p)
    return alternatifs


def video_nomme(track, v) -> bool:
    """Le titre de la vidéo `v` nomme-t-il le morceau `track` ?"""
    socle = _mots(parse_variant(track.title).socle)
    # Une parenthèse qui n'est pas un descripteur fait partie du titre Genius
    # (« Pursuit of Happiness (Nightmare) » : le clip officiel ne la porte pas) —
    # sauf sur un titre générique, qu'elle seule distingue (« Intro (A2) »).
    nu = _mots(re.sub(r"\s*[\(\[][^)\]]*[\)\]]", "", track.title or ""))
    titre = _mots(v.title)
    if titre_generique(nu):
        # … mais la vidéo que la page Genius du morceau désigne elle-même, et
        # qui porte le mot générique (« Intro (0.9) » → « Intro », « Outro
        # (A2) » → « Booba - Outro (Son Officiel) »), est bien la sienne.
        if v.source == "genius_media" and set(nu.split()) <= set(titre.split()):
            return True
        nu = socle
    variantes = [socle, *_parties_de_medley(track)]
    if _FREESTYLE_RE.search(track.title or ""):
        # « Rap contenders freestyle » est publié « Rap Contenders » : le mot
        # « freestyle » manque souvent à la vidéo, le reste la nomme.
        sans = _mots(_FREESTYLE_RE.sub(" ", parse_variant(track.title).socle))
        if sans:
            variantes.append(sans)
    return (
        any(_video_couvre(x, titre) or _mots_a_une_lettre(x, titre) for x in variantes)
        or _video_couvre(nu, titre)
        or any(_video_couvre(alt, titre) for alt in _titres_alternatifs(track))
        or _freestyle_d_emission(track, v.title)
    )


def _parties_de_medley(track) -> list[str]:
    """« Solitaire / Leçon de vie » : la vidéo d'un medley en nomme une partie."""
    parties = [_mots(p) for p in (track.title or "").split("/")]
    return [p for p in parties if p] if len(parties) > 1 else []


def _proprietaires_des_videos(ctx) -> dict[str, list]:
    """`{video_id: [fiches qui la portent]}` — mémorisé par ouverture."""

    def fabrique():
        par_video: dict[str, list] = {}
        for t in ctx.disco:
            for v in t.videos or []:
                if v.video_id:
                    par_video.setdefault(v.video_id, []).append(t)
        return par_video

    return ctx.memo("proprietaires_videos", fabrique)


def _nombre_de_pages(proprietaires: dict, video_id: str) -> int:
    """Combien d'ENREGISTREMENTS portent la vidéo : les lignes sœurs (même
    page Genius, chez un groupe et chez son membre, réunis par la
    discographie) n'en font qu'un — compter les lignes en faisait un partage."""
    return len({_page(t) for t in proprietaires.get(video_id, ())})


def raison_du_refus_de_recherche(track, recherche: dict) -> str:
    """Pourquoi le score refuse ce que la recherche a vu : « artiste » (aucun
    artiste du résultat n'est un artiste de la fiche — « Outro » de Dimeh
    Todo sur celui de Di-Meh), « version » (un remixeur étranger à la fiche —
    « In Common (Kaskade Remix) » sur le remix avec Travis), sinon « titre »
    (ressemblance, titre générique sans disque, partie) : l'humain."""
    from src.utils.title_matching import noms_de_meme_graphie
    from src.utils.version_heritage import remixeur_etranger
    from src.youtube.youtube_searcher import formes_publiees, invites_nommes

    titre = recherche.get("titre") or ""
    connus = [
        n
        for n in (
            track.artist.name if track.artist else None,
            track.primary_artist_name,
            *(track.featured_artists or "").split(","),
        )
        if n and n.strip()
    ]
    vus = [*(recherche.get("artistes") or []), *invites_nommes(titre)]
    if vus and not any(noms_de_meme_graphie(v, c) for v in vus for c in connus):
        return "artiste"
    if any(remixeur_etranger(track.title, forme, connus) for forme in formes_publiees(titre)):
        return "version"
    return "titre"


#: Les refus que les données MONTRENT : une règle retire le lien.
RAISONS_MONTREES = ("artiste", "version")


def recherche_perimee(track, _ctx=None):
    """Une vidéo posée par la RECHERCHE que le score courant refuse : ses vues
    ne comptent déjà plus (`vues_comptees`, décision 2026-10-20) ; reste à
    retirer le lien, ou à le valider (✔️ : il passe en `manual`). Ce qui était
    une famille de l'audit (2026-10-29) : le panneau le tranche. UN cas par
    fiche, une raison montrée d'abord."""
    from src.services.vues_comptees import recherche_valide

    refusees = [
        (raison_du_refus_de_recherche(track, v.recherche), v)
        for v in track.videos or []
        if v.source == "search_auto" and v.recherche and not recherche_valide(track, v.recherche)
    ]
    if not refusees:
        return None
    raison, v = min(refusees, key=lambda rv: (rv[0] not in RAISONS_MONTREES, -(rv[1].views or 0)))
    vus = ", ".join(v.recherche.get("artistes") or []) or "?"
    return (
        f"la recherche a vu « {v.recherche.get('titre')} » ({vus}) : le score le refuse "
        f"({raison}), ses vues ne comptent pas",
        {
            "video_id": v.video_id,
            "track_ids": [track.id],
            "raison": raison,
            "impact": v.views or 0,
        },
    )


#: Sources de durée de la fiche tirées d'une VIDÉO : elles ne jugent pas une
#: vidéo (l'une d'elles peut venir de celle-ci).
_DUREES_DE_VIDEO = frozenset({"youtube_video", "youtube_officiel"})
#: Écart au-delà duquel une durée dément la vidéo : celui des actions
#: DESTRUCTIVES (`politiques_revue.ECART_VERSION_S`) — « Banlieue » de 113 et
#: la vidéo de Rim'K (membre du groupe), 9 s d'écart, restent à l'humain.
_ECART_LIEN_ETRANGER_S = 10


def video_d_une_autre_chaine(track, ctx=None):
    """Une vidéo que la fiche porte SEULE, dont la chaîne (e63, « X - Topic » ou
    officielle déclarée) nomme un artiste ÉTRANGER à la fiche (ni son
    principal, ni un invité, ni l'artiste de la ligne) ET dont la durée (e62)
    contredit, de plus de `_ECART_LIEN_ETRANGER_S`, TOUTES les durées de la fiche
    — au moins un catalogue lu par ID parmi elles, aucune source hors vidéo
    d'accord : le morceau d'un autre (« Viser la victoire » d'Admiral T portait
    « Promis à la victoire » de Kery James). La chaîne seule ne prouve rien :
    groupe, label, artiste en tête de la parution (320 vidéos sur 409 à la
    durée des catalogues, mesuré le 2026-10-29) ; sans durée à comparer, ou
    une durée d'accord (« Welcome To The World », Discogs 185 s = la vidéo
    de Kevin Rudolf 184 s, l'ID Spotify à 254 s peut être le faux), rien."""
    if not track.videos:
        return None
    proprietaires = _proprietaires_des_videos(ctx) if ctx is not None else {}
    observees = track.durations_observees or {}
    catalogues = [int(d) for s in _DUREES_PAR_ID if (d := observees.get(s))]
    hors_video = [
        int(d) for s, d in observees.items() if d and s not in _DUREES_DE_VIDEO and s != "legacy"
    ]
    if not catalogues:
        return None
    for v in track.videos:
        if not v.duration or not v.channel:
            continue
        if len({_page(t) for t in proprietaires.get(v.video_id, [track])}) > 1:
            continue  # partagée : `video_partagee` la juge
        chaine = _chaine_de([track], v.video_id)
        if chaine is None or _place_de_l_artiste(track, chaine[1]) != "étranger":
            continue
        if any(abs(d - v.duration) <= _ECART_LIEN_ETRANGER_S for d in hors_video):
            continue
        return (
            f"vidéo de « {chaine[0]} » ({v.duration} s) : ni l'artiste ni la durée de la "
            f"fiche ({', '.join(f'{d} s' for d in sorted(set(catalogues)))})",
            {
                "video_id": v.video_id,
                "track_ids": [track.id],
                "chaine": chaine[0],
                "duree_video": v.duration,
                "impact": v.views or 0,
            },
        )
    return None


#: Une vidéo Genius qui ne NOMME pas le morceau et dure plus de 3 fois sa
#: durée n'est pas le morceau : concert, émission, compilation. Mesuré sur la
#: copie du 2026-10-30 (7 877 vidéos `genius_media` à durée connue) : les
#: vidéos muettes d'une seule page au-delà de 3,8 × sont toutes de cette sorte
#: (Tiny Desk entier 6,1 ×, « 1 son en 1H » de Booska-P 4,3 ×, « Freshmen
#: Adjustment Mixtape Tracks 1-5 » 3,9 ×, set de Rolling Loud 3,8 ×), puis
#: rien jusqu'à 1,8 × (« I Luv The Hood », le morceau lui-même, que le titre
#: nomme mal) : 3 est au milieu de l'écart.
RATIO_VIDEO_MUETTE = 3
#: Une vidéo qui NOMME le morceau peut être un clip long : court métrage, film
#: (« WTP » de Teyana Taylor 3,1 ×, « Runaway (Full-length Film) » 3,8 ×,
#: « Neverland (Short Film) » 2,4 ×). Au-delà de 4 ×, titre et durée se
#: contredisent (version longue, original samplé — « Sinnerman » de Nina
#: Simone sur le morceau d'une soul mix) : l'humain.
RATIO_VIDEO_NOMMEE = 4
#: Durées de la fiche qui ne jugent pas une vidéo : celles tirées des vidéos
#: (l'une peut venir d'elle) et `legacy` (contredit, ne soutient pas).
_DUREES_HORS_JUGE = _DUREES_DE_VIDEO | {"youtube", "legacy"}


def video_trop_longue(track, ctx=None):
    """Une vidéo posée par la page Genius (`genius_media`), qu'aucune autre
    page ne porte, BIEN plus longue que le morceau : plus de
    `RATIO_VIDEO_MUETTE` fois la plus longue de ses durées indépendantes
    (catalogues, SongBPM, Discogs, hit YTM — ni vidéo, ni legacy) quand son
    titre ne nomme pas le morceau — le concert, l'émission, la compilation où
    il passe (« BlackBird » de Planète Rap, 58 min, sur « LEGO » de B.B.
    Jacques) : `retirer`, une règle retire le lien. Plus de
    `RATIO_VIDEO_NOMMEE` fois quand le titre le nomme : titre et durée se
    contredisent, l'humain. La vidéo d'un disque ENTIER (comptée à l'album,
    `contenants.video_de_disque`), une page qui en CONTIENT d'autres (medley,
    album entier), une vidéo portée par plusieurs pages (`video_partagee`) ou
    une fiche sans durée indépendante : rien."""
    from src.utils.contenants import est_contenant, video_de_disque

    if not track.videos or est_contenant(track):
        return None
    durees = []
    for source, d in (track.durations_observees or {}).items():
        if d and source not in _DUREES_HORS_JUGE:
            try:
                durees.append(int(float(d)))
            except (TypeError, ValueError):
                continue
    reference = max(durees, default=0)
    if reference <= 0:
        return None
    proprietaires = _proprietaires_des_videos(ctx) if ctx is not None else {}
    for v in sorted(track.videos, key=lambda x: -(x.views or 0)):
        if v.source != "genius_media" or not v.duration:
            continue
        if len({_page(t) for t in proprietaires.get(v.video_id, [track])}) > 1:
            continue  # partagée : `video_partagee` la juge
        if video_de_disque(v.title, track.album):
            continue
        nommee = video_nomme(track, v)
        ratio = v.duration / reference
        if ratio <= (RATIO_VIDEO_NOMMEE if nommee else RATIO_VIDEO_MUETTE):
            continue
        fois = f"{ratio:.1f}".replace(".", ",")
        if nommee:
            motif = (
                f"vidéo « {v.title} » ({v.duration} s) : {fois} fois la durée du morceau "
                f"({reference} s), mais elle le nomme — version longue, film, autre enregistrement ?"
            )
        else:
            motif = (
                f"vidéo « {v.title} » ({v.duration} s) : {fois} fois la durée du morceau "
                f"({reference} s), et elle ne le nomme pas — concert, émission ou compilation"
            )
        return (
            motif,
            {
                "video_id": v.video_id,
                "track_ids": [track.id],
                "duree_video": v.duration,
                "duree_fiche": reference,
                "retirer": not nommee,
                "impact": v.views or 0,
            },
        )
    return None


def video_etrangere(track, ctx=None):
    """Une vidéo rattachée dont le titre ne nomme pas le morceau : peut-être
    celle d'un autre (Kanye « Heartless - Recorded At RAK Studios » → vidéo de
    Dermot Kennedy, 918 k vues, comptées dans les streams YouTube). Une vidéo
    PORTÉE PAR PLUSIEURS FICHES se juge une fois, au niveau de l'artiste
    (`video_partagee`) — un « album entier » faisait un cas par titre. La vidéo
    du disque ENTIER (« FULL EP ») n'en est pas un : reconnue, comptée à
    l'album (`contenants.video_de_disque`, 2026-10-29)."""
    from src.utils.contenants import video_de_disque

    proprietaires = _proprietaires_des_videos(ctx) if ctx is not None else {}
    suspectes = [
        v
        for v in track.videos or []
        if v.title
        and _nombre_de_pages(proprietaires, v.video_id) < 2
        and not video_nomme(track, v)
        and not video_de_disque(v.title, track.album)
    ]
    if not suspectes:
        return None
    v = max(suspectes, key=lambda x: x.views or 0)
    return (
        f"vidéo « {v.title} » ({v.video_id}) ne nomme pas le morceau",
        {"video_id": v.video_id, "titre": v.title, "impact": v.views or 0},
    )


def nature_video_etrangere(track, preuves, _ctx) -> str:
    """Un lien posé par la page Genius qui ne nomme pas le morceau ne compte
    déjà pas ses vues (`vues_comptees`, décision utilisateur 2026-10-10) : le
    cas n'est qu'une information. Venue d'une autre provenance (notre
    recherche, la page d'album YTM), la vidéo COMPTE : à trancher."""
    v = next((x for x in track.videos or [] if x.video_id == preuves.get("video_id")), None)
    return INFORMATION if v is not None and v.source == "genius_media" else DECISION


#: « Artiste - Titre » : la tête d'un titre de vidéo, avant le premier tiret.
_TETE_DE_VIDEO_RE = re.compile(r"^(.+?)\s+[-–—]\s+")
#: Les invités de la tête (« Kid Cudi feat Arie Dixon & … ») ne sont pas
#: l'interprète principal.
_INVITES_DE_TETE_RE = re.compile(r"\s+(?:feat\.?|ft\.?|featuring|avec|w/)\s.*$", re.IGNORECASE)


def interprete_principal_de_la_video(v) -> str | None:
    """L'interprète PRINCIPAL d'une vidéo : le premier artiste que la recherche
    a vu (e60) ; sinon la tête « Artiste - Titre » de son titre, invités
    coupés. None si le titre n'a pas cette forme (« KING », « Moon - Kanye
    West » donne « Moon », qui ne nomme personne)."""
    if v.recherche and v.recherche.get("artistes"):
        return v.recherche["artistes"][0]
    m = _TETE_DE_VIDEO_RE.match(v.title or "")
    return _INVITES_DE_TETE_RE.sub("", m.group(1)) if m else None


def _page(track):
    """L'enregistrement d'une fiche : sa page Genius (les lignes sœurs n'en
    font qu'un), sinon la fiche elle-même."""
    return track.genius_id or f"fiche:{track.id}"


#: Plusieurs interprètes dans la queue d'un titre (« Moon - Kanye West, Sunday
#: Service ») : elle ne désigne personne.
_PLUSIEURS_INTERPRETES_RE = re.compile(r",|&|\s+x\s+|/", re.IGNORECASE)
#: Mots d'édition d'un titre d'album (« Cruis'n USA (Deluxe) », « The
#: Graduates EP ») : la vidéo nomme le disque sans eux.
_EDITION_D_ALBUM_RE = re.compile(r"\s*[\(\[][^)\]]*[\)\]]|\s+(?:ep|deluxe)\s*$", re.IGNORECASE)


def _elue(fiches, predicat):
    """La page que `predicat` désigne SEULE parmi `fiches`, sinon None."""
    elues = {_page(t) for t in fiches if predicat(t)}
    return elues.pop() if len(elues) == 1 else None


def _principal_de_la_fiche(t) -> str:
    return normalize_name(t.primary_artist_name or (t.artist.name if t.artist else ""))


def _par_interprete(fiches, v):
    """L'interprète principal de la vidéo (`interprete_principal_de_la_video`)
    désigne sa fiche. Sinon, pour un titre « Titre - Artiste » (« ON MY OWN -
    CONSEQUENCE ft. KID CUDI », « Sky High - KiD CuDi », 2026-10-29), la
    QUEUE, invités coupés — si elle ne nomme qu'un interprète."""
    principal = interprete_principal_de_la_video(v)
    if principal:
        cible = normalize_name(principal)
        elues = {_page(t) for t in fiches if contains_as_words(_principal_de_la_fiche(t), cible)}
        if elues or (v.recherche and v.recherche.get("artistes")):
            return (elues.pop(), principal) if len(elues) == 1 else None
    m = _TETE_DE_VIDEO_RE.match(v.title or "")
    if not m:
        return None
    queue = _INVITES_DE_TETE_RE.sub("", re.sub(r"[\(\[][^)\]]*[\)\]]", "", v.title[m.end() :]))
    if not queue.strip() or _PLUSIEURS_INTERPRETES_RE.search(queue):
        return None
    cible = normalize_name(queue)
    page = _elue(fiches, lambda t: contains_as_words(_principal_de_la_fiche(t), cible))
    return (page, queue.strip()) if page else None


def _lien_de(t, video_id):
    return next((x for x in t.videos or [] if x.video_id == video_id), None)


def _album_nu(album: str | None) -> str:
    return normalize_title(_EDITION_D_ALBUM_RE.sub("", album or "")) if album else ""


def _temoins_du_proprietaire(fiches, v) -> list[tuple[str, str]]:
    """`[(page désignée, témoin)]` : ce qui, dans la base, désigne la page à qui
    revient la vidéo (tri des 47 `video_partagee` du 2026-10-29)."""
    temoins = []
    if (par := _par_interprete(fiches, v)) is not None:
        temoins.append((par[0], f"interprète principal de la vidéo : {par[1]}"))
    # Un lien posé À LA MAIN sur une seule page (« Reborn » sur *KIDS SEE
    # GHOSTS*, et non le remix de Dirty Nice que la page d'album YTM porte).
    manuelle = _elue(
        fiches, lambda t: (lien := _lien_de(t, v.video_id)) is not None and lien.source == "manual"
    )
    if manuelle:
        temoins.append((manuelle, "lien posé à la main"))
    # L'album nommé par le titre de la vidéo (« Low (Cruis'n Usa) »), s'il n'est
    # pas le titre du morceau lui-même (un single éponyme se nommerait partout).
    titre_video = normalize_title(v.title or "")
    nomme = _elue(
        fiches,
        lambda t: len(a := _album_nu(t.album)) >= 4
        and a != normalize_title(t.title or "")
        and contains_as_words(a, titre_video),
    )
    if nomme:
        temoins.append((nomme, "album nommé par la vidéo"))
    # Ce que la recherche a vu, sur l'une quelconque des lignes qui portent la
    # vidéo (« Double Discours - Live » : sur la ligne de *Jihad* seulement).
    vus = {
        (lien.recherche or {}).get("album")
        for t in fiches
        if (lien := _lien_de(t, v.video_id)) is not None and (lien.recherche or {}).get("album")
    }
    if len({_album_nu(a) for a in vus}) == 1:
        vu = vus.pop()
        recherche = _elue(fiches, lambda t: _album_nu(t.album) == _album_nu(vu))
        if recherche:
            temoins.append((recherche, f"album vu par la recherche : {vu}"))
    seule = _elue(fiches, lambda t: video_nomme(t, v))
    if seule:
        temoins.append((seule, "seule page que la vidéo nomme"))
    if (par_duree := _par_la_duree(fiches, v.video_id)) is not None:
        temoins.append(par_duree)
    if (par_chaine := _par_la_chaine(fiches, v.video_id)) is not None:
        temoins.append(par_chaine)
    if (par_album := _par_l_album_ytm(fiches, v.video_id)) is not None:
        temoins.append(par_album)
    return temoins


def _chaine_de(fiches, video_id) -> tuple[str, str] | None:
    """`(nom de la chaîne, artiste qu'elle nomme)` de la vidéo (e63), lue sur
    l'une quelconque des lignes qui la portent, ou None."""
    from src.utils.chaines_youtube import artiste_de_la_chaine

    for t in fiches:
        lien = _lien_de(t, video_id)
        if lien is not None and lien.channel:
            artiste = artiste_de_la_chaine(lien.channel, lien.channel_id)
            return (lien.channel, artiste) if artiste else None
    return None


def _place_de_l_artiste(t, artiste: str) -> str:
    """Où `artiste` (celui d'une chaîne) se tient sur la fiche `t` :
    « principal », « lié » (invité, ou artiste de la ligne), ou « étranger »."""
    cible = normalize_name(artiste)
    if not cible:
        return "lié"  # un nom qui ne se compare pas ne prouve rien

    def nomme(nom: str | None) -> bool:
        n = normalize_name(nom or "")
        return bool(n) and (contains_as_words(n, cible) or contains_as_words(cible, n))

    if nomme(t.primary_artist_name or (t.artist.name if t.artist else "")):
        return "principal"
    # Sur la REPRISE d'un tiers (`secondary_role` « Cover »), l'artiste de la
    # ligne est l'auteur de l'original, pas un interprète : sa chaîne ne lie
    # pas la vidéo à la reprise (« Ghost Town » de Justice Der, ligne de
    # Kanye, portait l'audio « Kanye West - Topic » de l'original).
    ligne = t.artist.name if t.artist and t.secondary_role != "Cover" else ""
    if nomme(ligne) or any(nomme(a) for a in t.featured_artists_list):
        return "lié"
    return "étranger"


def _par_la_chaine(fiches, video_id):
    """`(page, témoin)` : la chaîne de la vidéo (e63, « X - Topic » ou chaîne
    officielle déclarée) nomme l'interprète principal d'UNE page, et aucun
    artiste — principal, invité, artiste de la ligne — des autres (2026-10-29 :
    « Castro » de « Yo Gotti - Topic » → Yo Gotti, pas CyHi ; « Hiver » de
    « aladin 135 - Topic » → Aladin 135, pas PLK)."""
    chaine = _chaine_de(fiches, video_id)
    if chaine is None:
        return None
    nom, artiste = chaine
    places: dict = {}
    for t in fiches:
        places.setdefault(_page(t), set()).add(_place_de_l_artiste(t, artiste))
    principales = [p for p, s in places.items() if "principal" in s]
    if len(principales) != 1:
        return None
    if any(s != {"étranger"} for p, s in places.items() if p != principales[0]):
        return None
    return principales[0], f"chaîne de l'interprète : {nom}"


#: Sources de durée lues par l'IDENTIFIANT d'un catalogue : jamais par la
#: vidéo elle-même (ni `youtube`, l'audio Topic, ni la recherche YTM).
_DUREES_PAR_ID = ("deezer", "apple_music", "spotify_web", "reccobeats")
#: Écart au-delà duquel une page n'est pas l'enregistrement de la vidéo (la
#: tolérance du vote, `vote_durees.TOLERANCE_S`).
_ECART_AUTRE_PAGE_S = TOLERANCE_S


def _par_la_duree(fiches, video_id):
    """`(page, témoin)` : la durée de la VIDÉO (`track_videos.duration`, e62)
    est celle des catalogues d'UNE page, à 2 s, et toutes les autres pages
    ont des catalogues à plus de `_ECART_AUTRE_PAGE_S` (2026-10-29 : « KING »
    157 s, VULTURES 1 157 s, BULLY 126 s ; « I CAN'T WAIT » 128 s, BULLY
    127 s, DELUXE 137 s). Une page sans durée de catalogue ne se juge pas :
    rien."""
    duree = next(
        (
            lien.duration
            for t in fiches
            if (lien := _lien_de(t, video_id)) is not None and lien.duration
        ),
        None,
    )
    if not duree:
        return None
    proches = set()
    for t in fiches:
        catalogues = [
            int(d)
            for s in _DUREES_PAR_ID
            if (d := (t.durations_observees or {}).get(s)) is not None
        ]
        if not catalogues:
            return None
        if all(abs(d - duree) <= 2 for d in catalogues):
            proches.add(_page(t))
        elif not all(abs(d - duree) > _ECART_AUTRE_PAGE_S for d in catalogues):
            return None  # ni elle, ni nettement une autre : rien ne se dit
    if len(proches) != 1:
        return None
    return next(iter(proches)), f"durée de la vidéo ({duree} s) = ses catalogues seuls"


def _par_l_album_ytm(fiches, video_id):
    """`(page, témoin)` : la vidéo est, pour UNE page seulement, la piste de sa
    page d'album YouTube Music (`ytm_album`), et sa durée (e62) est celle des
    catalogues de cette page, à 2 s — les autres n'ont aucun catalogue à moins
    de `_ECART_AUTRE_PAGE_S` (une page SANS catalogue ne dément rien : c'est
    ce qui distingue ce témoin de `_par_la_duree`). « 530 » : l'audio
    « ¥$ - Topic » (288 s) de *VULTURES 2* (288 s), posée aussi par Genius sur
    « 530 (Haikou LP) », sans catalogue (2026-10-30). Un titre fait de signes
    seuls (« * (Part. 1) ») ne se rapproche pas sûrement d'une piste d'album
    (« * * * * * » posée sur la Part. 1, 39 s, au lieu de la Part. 3) : rien."""
    album = {
        _page(t)
        for t in fiches
        if (lien := _lien_de(t, video_id)) is not None and lien.source == "ytm_album"
    }
    if len(album) != 1:
        return None
    page = album.pop()
    duree = next(
        (
            lien.duration
            for t in fiches
            if (lien := _lien_de(t, video_id)) is not None and lien.duration
        ),
        None,
    )
    if not duree:
        return None
    for t in fiches:
        catalogues = [
            int(d)
            for s in _DUREES_PAR_ID
            if (d := (t.durations_observees or {}).get(s)) is not None
        ]
        if _page(t) == page:
            if not _titre_sans_parentheses(t) or not catalogues:
                return None
            if not all(abs(d - duree) <= 2 for d in catalogues):
                return None
        elif any(abs(d - duree) <= _ECART_AUTRE_PAGE_S for d in catalogues):
            return None
    return page, f"piste de l'album YouTube Music, durée ({duree} s) = ses catalogues"


def partage_a_juger(fiches) -> bool:
    """Une vidéo portée par `fiches` est-elle un partage à juger ? Il faut
    deux PAGES (les lignes sœurs n'en font qu'une), pas toutes sur le même
    disque (clip double compté au niveau de l'album, décision utilisateur
    2026-10-10). Le seul juge : le panneau et l'audit l'appellent tous deux."""
    pages = {_page(t) for t in fiches}
    albums = {cle_album(t.album) for t in fiches if t.album}
    return len(pages) >= 2 and not (all(t.album for t in fiches) and len(albums) == 1)


def _titre_sans_parentheses(track) -> str:
    return _mots(re.sub(r"\s*[\(\[][^)\]]*[\)\]]", "", track.title or ""))


def pages_homonymes(fiches) -> bool:
    """Deux des fiches portent-elles le même titre, l'un CONTENU dans l'autre
    (parenthèses ôtées) : « KING » / « KING », « 530 » / « 530 (Haikou LP) »,
    « Jeunes entrepreneurs » / « Booska Jeunes entrepreneurs » ? Une vidéo qui
    les nomme toutes ne départage rien. Un titre fait de signes seuls (« * »,
    « *** » de *Chambre 140*) n'a pas de mots : il vaut tous les autres."""
    titres = [_titre_sans_parentheses(t) for t in fiches]
    return any(
        not a or not b or contains_as_words(a, b) or contains_as_words(b, a)
        for i, a in enumerate(titres)
        for b in titres[i + 1 :]
    )


def proprietaire_et_temoin(fiches, v):
    """`(fiche, témoin)` à qui revient une vidéo que plusieurs PAGES portent,
    sinon `(None, None)`. Le motif dominant des vidéos partagées est
    l'HOMONYME : « Selfish » de Slum Village (feat. Kanye) posé aussi sur le
    « Selfish » de *DONDA 2*. Les témoins (`_temoins_du_proprietaire`) doivent
    tous désigner la MÊME page, et la vidéo nommer la fiche gardée. Rien ne
    départage (deux pages du même interprète, titre sans tête), des témoins
    en désaccord, ou toutes les fiches sur le même disque (clip double, compté
    au niveau de l'album) : None. Simulé sur toute la base le 2026-10-27
    (interprète seul : 41 des 90 vidéos partagées tranchées, aucune démentie
    par les témoins) ; les quatre autres témoins le 2026-10-29."""
    if not partage_a_juger(fiches):
        return None, None
    temoins = _temoins_du_proprietaire(fiches, v)
    if not temoins or len({page for page, _ in temoins}) != 1:
        return None, None
    gagnante = next(t for t in fiches if _page(t) == temoins[0][0])
    if not video_nomme(gagnante, v):
        return None, None
    return gagnante, " ; ".join(temoin for _, temoin in temoins)


def proprietaire_de_la_video(fiches, v):
    """La fiche à qui revient la vidéo (`proprietaire_et_temoin`), sinon None."""
    return proprietaire_et_temoin(fiches, v)[0]


def video_partagee(ctx):
    """Une vidéo portée par PLUSIEURS fiches qui n'en nomme pas au moins une
    (album entier, EP complet, film, freestyle collectif mal rattaché) : UN
    cas, avec les fiches concernées — ses vues sont comptées sur chacune.
    Quand les témoins désignent SA fiche (`proprietaire_et_temoin`), le cas porte la fiche à garder (`garder`)
    et les autres (`track_ids`) : une règle le tranche. Une vidéo qui nomme
    toutes ses fiches n'est un cas que si elles sont HOMONYMES
    (`pages_homonymes`) : rien ne dit laquelle elle est (`homonymes`) ; un
    clip double qui nomme deux titres distincts n'en est pas un.

    C'est le SEUL juge des vidéos partagées : l'audit compte ses cas
    (`revue:video_partagee`), il n'a plus sa famille (2026-10-29 : 33 erreurs
    à l'audit, 11 informations au panneau pour les mêmes vidéos)."""
    cas = []
    for video_id, fiches in _proprietaires_des_videos(ctx).items():
        if len(fiches) < 2 or not partage_a_juger(fiches):
            continue
        v = next(x for x in fiches[0].videos if x.video_id == video_id)
        if not v.title:
            continue
        # Ce que la recherche a vu est sur la ligne qu'elle a posée.
        vue = next(
            (x for t in fiches for x in t.videos or [] if x.video_id == video_id and x.recherche),
            v,
        )
        gagnante, temoin = proprietaire_et_temoin(fiches, vue)
        if gagnante is not None:
            autres = [t for t in fiches if _page(t) != _page(gagnante)]
            noms = ", ".join(f"« {t.title} » (#{t.id})" for t in autres)
            cas.append(
                (
                    v.title,
                    f"{temoin} : elle revient à « {gagnante.title} » "
                    f"(#{gagnante.id}), pas à {noms}",
                    {
                        "video_id": video_id,
                        "kind": video_id,
                        "track_ids": [t.id for t in autres],
                        "garder": gagnante.id,
                        "temoin": temoin,
                        "impact": v.views or 0,
                    },
                )
            )
            continue
        muettes = [t for t in fiches if not video_nomme(t, v)]
        if not muettes:
            if pages_homonymes(fiches):
                noms = ", ".join(f"« {t.title} » (#{t.id})" for t in fiches[:4])
                cas.append(
                    (
                        v.title,
                        f"vidéo portée par {len(fiches)} pages au même titre ({noms}) : "
                        "rien dans la base ne dit laquelle elle est",
                        {
                            "video_id": video_id,
                            "kind": video_id,
                            "track_ids": [t.id for t in fiches],
                            "homonymes": True,
                            "impact": v.views or 0,
                        },
                    )
                )
            continue
        noms = ", ".join(f"« {t.title} »" for t in muettes[:4])
        if len(muettes) > 4:
            noms += f" et {len(muettes) - 4} autre(s)"
        cas.append(
            (
                v.title,
                f"vidéo portée par {len(fiches)} fiches, ne nomme pas {noms}",
                {
                    "video_id": video_id,
                    "kind": video_id,
                    "track_ids": [t.id for t in muettes],
                    "impact": v.views or 0,
                },
            )
        )
    return cas


def nature_video_partagee(preuves, ctx) -> str:
    """Une vidéo que deux fiches d'un MÊME artiste portent n'est comptée chez
    aucune des deux (`_recalculer_vues_ytm`) : information. Portée par une
    seule fiche d'un artiste (l'autre ligne est d'un groupe, d'un invité), ses
    vues comptent chez lui : à trancher. Une vidéo dont le propriétaire est
    désigné est une DÉCISION, qu'une règle prend (`politiques_revue`)."""
    if preuves.get("garder"):
        return DECISION
    fiches = _proprietaires_des_videos(ctx).get(preuves.get("video_id"), [])
    artistes = [t.artist.id if t.artist else None for t in fiches]
    if fiches and all(a is not None and artistes.count(a) > 1 for a in artistes):
        return INFORMATION
    return DECISION


def soeurs_identite_divergente(ctx):
    """Deux lignes du MÊME enregistrement (même page Genius, artistes
    différents) portent deux identités différentes — deux ID Spotify, deux
    liens YouTube, un « inédit » d'un côté seulement. Aucune n'est « la plus
    récente » : l'une des deux est fausse, l'utilisateur tranche (décision
    2026-10-12). La synchronisation ne fait que combler, elle ne verra jamais
    ce cas."""
    cas = []
    for d in ctx.soeurs:
        detail = " · ".join(f"{a} : {v}" for a, v in sorted(d["valeurs"].items()))
        cas.append(
            (
                d["titre"],
                f"{d['colonne']} différent selon la ligne — {detail}",
                {
                    "kind": f"{d['colonne']}:{'|'.join(sorted(map(str, d['valeurs'].values())))}",
                    "track_ids": [d["track_id"]],
                },
            )
        )
    return cas


def _vues_expliquees(track, ctx) -> bool:
    """Chaque vidéo qui compte des vues est-elle LA publication du morceau ?
    Elle le nomme, aucune autre fiche ne la porte, et elle est dans sa version
    — ou c'est la reprise que la page décrit (« FourfiveSeconds » de Nicole
    Cross : Kanye y est l'auteur, la vidéo « … (Nicole Cross Official Cover
    Video) » est bien la sienne). Le rapport YouTube / Spotify seul ne prouve
    rien : un clip d'un autre où l'artiste est invité, un titre d'avant le
    streaming font ×10 à juste titre (22 des 24 cas du 2026-10-27, contrôlés
    vidéo par vidéo). Sans vidéo, les vues viennent des compteurs du run : rien
    ne les explique."""
    proprietaires = _proprietaires_des_videos(ctx) if ctx is not None else {}
    avec_vues = [v for v in track.videos or [] if v.views]
    if not avec_vues:
        return False
    for v in avec_vues:
        if not v.title or _nombre_de_pages(proprietaires, v.video_id) > 1:
            return False
        if not video_nomme(track, v):
            return False
        if meme_version_publiee(track.title, v.title):
            continue
        interprete = track.primary_artist_name
        reprise = re.search(r"(?i)\bcover\b", v.title) and not re.search(
            r"(?i)\bcover\b", track.title or ""
        )
        if not (reprise and interprete and contains_as_words(interprete, v.title)):
            return False
    return True


def vues_youtube_anormales(track, ctx=None):
    """Vues YouTube dix fois au-dessus des streams Spotify : une vidéo d'un
    autre morceau, ou d'un autre artiste, gonfle peut-être le total."""
    sp, yt = track.streams.spotify_streams or 0, track.streams.ytm_streams or 0
    if sp and yt > VUES_YTM_MIN and yt > RAPPORT_YTM_SPOTIFY * sp:
        if _vues_expliquees(track, ctx):
            return None
        return (
            f"{yt:,} vues YouTube pour {sp:,} streams Spotify (×{yt // sp})".replace(",", " "),
            {"ytm": yt, "spotify": sp, "empreinte": ""},
        )
    return None


def _jour(valeur) -> date | None:
    try:
        return date.fromisoformat(str(valeur)[:10])
    except ValueError:
        return None


def _certifs_avant_sortie(track):
    sortie = _jour(track.release_date) if track.release_date else None
    if sortie is None:
        return
    for e in track.certs.reelles:
        certif = _jour(e.get("certification_date") or "")
        if certif and certif < sortie:
            yield e, certif, sortie


_PARENTHESE = re.compile(r"[\(\[]([^\)\]]*)[\)\]]")


def _socle_et_parentheses(titre: str | None) -> tuple[str, frozenset]:
    """« Pursuit Of Happiness (Nightmare) (Prime Day Show) » → (« pursuitofhappiness »,
    {« nightmare », « primedayshow »})."""
    titre = titre or ""
    entre = frozenset(cle_doublon(m) for m in _PARENTHESE.findall(titre) if cle_doublon(m))
    return cle_doublon(_PARENTHESE.sub(" ", titre)), entre


def certif_d_une_autre_oeuvre(track, entree) -> bool:
    """PUR. La certification désigne-t-elle PREUVE À L'APPUI une autre œuvre ?

    - titres différents hors parenthèses : oui (« FATHER » ← « FATHER STRETCH MY
      HANDS PT. 1 », « Nemesis » ← « NERO NEMESIS ») ;
    - même titre, et la FICHE porte une parenthèse que la certif n'a pas : oui,
      c'est la certif de l'original sur une version (« Jesus Walks (Orchestral) »
      ← « JESUS WALKS ») ;
    - même titre, et la CERTIF porte une parenthèse de plus : NON — un sous-titre
      (Kid Cudi « Day 'N' Nite » ← « DAY 'N' NITE (NIGHTMARE) », le bon morceau :
      c'est sa date de sortie qui est fausse). Mesuré 2026-09-27, 4 certifs
      retirées à tort avant cette règle."""
    socle_f, entre_f = _socle_et_parentheses(track.title)
    socle_c, entre_c = _socle_et_parentheses(entree.get("title"))
    if socle_f != socle_c:
        return True
    return entre_c < entre_f


def certif_avant_sortie(track, _ctx=None):
    """Une certification datée AVANT la sortie du morceau, au MÊME titre : la
    date de sortie est peut-être fausse (Kid Cudi « Day 'N' Nite », sortie
    lue en 2020 pour un Or de 2009). À trancher. Un AUTRE titre relève de
    `certif_d_un_autre_titre`, formel."""
    for e, certif, sortie in _certifs_avant_sortie(track):
        if not certif_d_une_autre_oeuvre(track, e):
            return (
                f"{e.get('body', '?')} {e.get('certification', '?')} du {certif:%d/%m/%Y} "
                f"avant la sortie ({sortie:%d/%m/%Y}) — « {e.get('title', '?')} »",
                {"certification": e, "sortie": str(sortie)},
            )
    return None


def certif_d_un_autre_titre(track, _ctx=None):
    """FORMEL. Une certification datée AVANT la sortie, et d'un AUTRE titre :
    le rattachement par mots l'a prise pour ce morceau (« FATHER » ← « FATHER
    STRETCH MY HANDS PT. 1 », « KING » ← l'album « JESUS IS KING », une version
    ← son original). Mesuré 2026-09-27 : 18 cas sur 21, tous faux."""
    for e, certif, sortie in _certifs_avant_sortie(track):
        if certif_d_une_autre_oeuvre(track, e):
            return (
                f"{e.get('body', '?')} {e.get('certification', '?')} de « {e.get('title', '?')} » "
                f"du {certif:%d/%m/%Y}, avant la sortie ({sortie:%d/%m/%Y}) — un autre titre",
                {"certification": e, "sortie": str(sortie)},
            )
    return None


# ── Titre + année nue : la donnée de la jumelle ─────────────────────────────


#: Sources de durée assez SOLIDES pour qu'une règle RETIRE une donnée sur leur
#: foi : les catalogues. Une vidéo (« Tout ira bien (2017) » de Booba, vidéo de
#: 195 s face à 186 s) ou SongBPM ne suffisent pas — « on ne retire que ce
#: qu'on peut montrer » ; leur cas va à l'humain.
TEMOINS_DE_RETRAIT = frozenset(
    {"manual", "deezer", "apple_music", "spotify_web", "ytmusic", "discogs"}
)


def _durees_jumelles(track, jumelle, ctx, exclues):
    """(durée indépendante de la fiche, de la jumelle) — `titre_annee`, les
    valeurs que l'une copie de l'autre écartées, les catalogues seuls."""
    from src.utils.titre_annee import ORDRE_DES_TEMOINS, garde_parmi

    fragiles = {s for s in ORDRE_DES_TEMOINS if s not in TEMOINS_DE_RETRAIT}
    garde = garde_parmi(track, [jumelle], set(exclues) | fragiles)
    if garde is None:
        return None, None
    return garde.duree, garde.jumelles[0][2]


def _a_qui(duree_donnee, d_fiche, d_jumelle):
    """« fiche », « jumelle », « meme » (un seul enregistrement : rien à
    trancher) ou None (indécidable) — par la durée que la donnée porte."""
    from src.utils.titre_annee import TOLERANCE

    if d_fiche is not None and d_jumelle is not None and abs(d_fiche - d_jumelle) <= TOLERANCE:
        return "meme"
    if duree_donnee is None or d_fiche is None or d_jumelle is None:
        return None
    a_la_fiche = abs(duree_donnee - d_fiche) <= TOLERANCE
    a_la_jumelle = abs(duree_donnee - d_jumelle) <= TOLERANCE
    if a_la_jumelle and not a_la_fiche:
        return "jumelle"
    if a_la_fiche and not a_la_jumelle:
        return "fiche"
    return None


def _partages_avec(track, jumelle, ctx):
    """`[(donnée, preuves, à qui)]` que la fiche et sa jumelle portent toutes
    deux. « à qui » : « jumelle » (la donnée revient à la jumelle : la fiche la
    perd), « fiche », « meme », ou None (indécidable)."""
    from src.utils.corrections_fiches import empreinte_lrc
    from src.utils.lrc_garde import CHAMP_DUREE_AUDIO, fin_du_lrc
    from src.utils.titre_annee import (
        EXCLUES_LRC,
        EXCLUES_SONGBPM,
        EXCLUES_YOUTUBE,
        garde_parmi,
    )

    sortie = []
    for source, champs in (
        ("songbpm", ("bpm", "key", "mode", "duration")),
        ("getsongbpm", ("bpm", "key", "mode")),
    ):
        a = {c: _mesures(ctx, track, c).get(source) for c in champs}
        b = {c: _mesures(ctx, jumelle, c).get(source) for c in champs}
        if (a.get("bpm") or a.get("key")) and a == b:
            d_fiche, d_jumelle = _durees_jumelles(track, jumelle, ctx, EXCLUES_SONGBPM)
            duree = int(float(a["duration"])) if a.get("duration") else None
            sortie.append((source, {"mesures": a}, _a_qui(duree, d_fiche, d_jumelle)))
    lrc = track.lyrics.synced
    # Le LRC de la jumelle : sa colonne, ou un LRC qu'une de ses sources lui a
    # servi (#11846 « Used To Love U (2003) » porte le LRC YTM de « Used to
    # Love U », dont la colonne garde celui de LRCLIB).
    lrc_jumelle = {
        empreinte_lrc(str(o.value))
        for o in ctx.obs.get(jumelle.id, [])
        if o.field == "lyrics_synced" and o.value
    } | ({empreinte_lrc(jumelle.lyrics.synced)} if jumelle.lyrics.synced else set())
    if lrc and empreinte_lrc(lrc) in lrc_jumelle:
        empreinte = empreinte_lrc(lrc)
        sources = {
            o.source
            for o in ctx.obs.get(track.id, [])
            if o.field == "lyrics_synced" and empreinte_lrc(str(o.value)) == empreinte
        }
        audios = _mesures(ctx, track, CHAMP_DUREE_AUDIO)
        audio = next((int(float(audios[s])) for s in sorted(sources) if audios.get(s)), None)
        d_fiche, d_jumelle = _durees_jumelles(track, jumelle, ctx, EXCLUES_LRC)
        a_qui = _a_qui(audio, d_fiche, d_jumelle)
        fin = fin_du_lrc(lrc)
        # Un LRC qui court au-delà d'une fiche ne peut pas être le sien.
        if a_qui is None and fin and d_fiche is not None and d_jumelle is not None:
            if d_fiche + 2 < fin <= d_jumelle + 2:
                a_qui = "jumelle"
            elif d_jumelle + 2 < fin <= d_fiche + 2:
                a_qui = "fiche"
        sortie.append(("lrc", {"empreinte": empreinte, "audio": audio, "fin": fin}, a_qui))
    garde_f = garde_parmi(track, [jumelle], EXCLUES_YOUTUBE)
    garde_j = garde_parmi(jumelle, [track], EXCLUES_YOUTUBE)
    des_jumelles = {v.video_id for v in jumelle.videos or [] if v.video_id}
    for v in track.videos or []:
        if not v.video_id or v.video_id not in des_jumelles:
            continue
        a_qui = None
        if garde_f is not None and garde_j is not None and v.title:
            refus_f, refus_j = garde_f.motif_video(v.title), garde_j.motif_video(v.title)
            if refus_f and not refus_j:
                a_qui = "jumelle"
            elif refus_j and not refus_f:
                a_qui = "fiche"
        sortie.append(
            ("video", {"video_id": v.video_id, "titre": v.title, "impact": v.views or 0}, a_qui)
        )
    for champ in ("spotify_id", "deezer_id", "isrc"):
        valeur = getattr(track, champ)
        if valeur and str(valeur) == str(getattr(jumelle, champ) or ""):
            sortie.append((champ, {champ: valeur}, None))
    return sortie


def _vu_de_l_autre_cote(jumelle, track, donnee, ctx) -> bool:
    """La jumelle voit-elle aussi ce partage ? Un LRC qu'une source de la
    jumelle lui a servi sans qu'il tienne sa colonne ne se voit que de la
    fiche qui le porte : le cas y reste."""
    return any(d == donnee for d, _, _ in _partages_avec(jumelle, track, ctx))


#: Ce que la fiche perd, par donnée, quand la source la donne à la jumelle :
#: l'action de la politique (`politiques_revue.politique_jumelle_annee`).
ACTIONS_JUMELLE = {
    "songbpm": "retirer_songbpm",
    "getsongbpm": "retirer_getsongbpm",
    "lrc": "retirer_lrc",
    "video": "retirer_video_recherche",
}


def donnee_de_la_jumelle(track, ctx):
    """« Suzy » / « Suzy 2003 » (2026-10-29, `titre_annee`) : la fiche et sa
    JUMELLE (même artiste, titre qui ne diffère que par une année finale nue)
    portent la MÊME donnée — mesures SongBPM ou GetSongBPM, LRC, vidéo, ID
    Spotify / Deezer, ISRC. C'est le rattrapage des données posées avant la
    règle, ou avant que la jumelle existe.

    Un cas par fiche, celle qui PERD la donnée quand la source dit à qui elle
    revient (durée de la donnée compatible avec la seule jumelle, LRC qui court
    au-delà de la fiche, titre de vidéo à l'année de la jumelle) : `retirer` est
    posé, une règle tranche. Deux fiches d'un même enregistrement (durées
    indépendantes à ± 2 s) ne font aucun cas : partager est juste. Sinon le
    cas va à l'humain, une fois par paire (sur la fiche au plus petit id, ou
    sur la seule qui voie le partage) ;
    une homonymie exacte (« Suzy (Version radio) ») n'est pas de son ressort."""
    from src.utils.titre_annee import jumelles

    titres = ctx.memo("titres_disco", lambda: [(t.id, t.title) for t in ctx.disco])
    par_id = ctx.memo("fiches_par_id", lambda: {t.id: t for t in ctx.disco})
    indecis = []
    for jid, _ in jumelles(track.id, track.title, titres):
        jumelle = par_id.get(jid)
        if jumelle is None:
            continue
        for donnee, preuves, a_qui in _partages_avec(track, jumelle, ctx):
            preuves = {**preuves, "donnee": donnee, "jumelle": jumelle.id}
            if a_qui == "jumelle":
                quoi = "la vidéo" if donnee == "video" else donnee
                return (
                    f"{quoi} de « {jumelle.title} » (#{jumelle.id}) : la source la lui "
                    f"donne, pas à « {track.title} »",
                    {**preuves, "retirer": True, "track_ids": [track.id], "garder": jumelle.id},
                )
            if a_qui is None and (
                track.id < jumelle.id or not _vu_de_l_autre_cote(jumelle, track, donnee, ctx)
            ):
                indecis.append((donnee, preuves, jumelle))
    if indecis:
        donnee, preuves, jumelle = indecis[0]
        return (
            f"{donnee} partagé(e) avec « {jumelle.title} » (#{jumelle.id}) — les sources "
            "ne disent pas à qui il revient",
            preuves,
        )
    return None


# ── SongBPM posé par un titre CONTENU (avant la garde `cccebc7`) ────────────


_CHAMPS_SONGBPM = ("bpm", "key", "mode", "duration")


def _signatures_songbpm(ctx) -> dict:
    """`{mesures SongBPM: [fiches]}` de la discographie (BPM ou tonalité lus)."""

    def fabrique():
        groupes = collections.defaultdict(list)
        for t in ctx.disco:
            m = tuple(_mesures(ctx, t, c).get("songbpm") for c in _CHAMPS_SONGBPM)
            if m[0] or m[1]:
                groupes[m].append(t)
        return groupes

    return ctx.memo("signatures_songbpm", fabrique)


def _juge_songbpm():
    """Le juge de la pose SongBPM, PUR (`titre_contenu_non_corrobore` n'ouvre
    aucun navigateur) : construit sans son `__init__`, qui journalise."""
    from src.scrapers.songbpm_scraper_v2 import SongBPMScraper

    return SongBPMScraper.__new__(SongBPMScraper)


def _tient_la_page(track, autre, duree_page) -> str | None:
    """« confirme », « contraire » ou None : ce que les CATALOGUES de la fiche
    (`TEMOINS_DE_RETRAIT`) disent de la durée de la page SongBPM. Une valeur que
    l'autre fiche porte aussi de la même source (copiée, ou lue sur la même
    page) n'est pas un témoin. « confirme » : toutes à ± 2 s ; « contraire » :
    toutes au-delà de `ECART_VERSION_S` (deux éditions d'un même enregistrement
    diffèrent encore en deçà) et aucune durée indépendante — vidéo comprise — à
    ± 2 s. Entre les deux, ou sans catalogue : None."""
    from src.services.politiques_revue import ECART_VERSION_S
    from src.utils.titre_annee import EXCLUES_SONGBPM, ORDRE_DES_TEMOINS, TOLERANCE, _duree

    obs, chez_l_autre = track.durations_observees or {}, autre.durations_observees or {}

    def propres(sources):
        for s in sources:
            d = _duree(obs.get(s))
            if d is not None and d != _duree(chez_l_autre.get(s)):
                yield abs(d - duree_page)

    ecarts = [e for s in ORDRE_DES_TEMOINS if s in TEMOINS_DE_RETRAIT for e in propres([s])]
    if not ecarts:
        return None
    if all(e <= TOLERANCE for e in ecarts):
        return "confirme"
    independants = propres(s for s in ORDRE_DES_TEMOINS if s not in EXCLUES_SONGBPM)
    if all(e > ECART_VERSION_S for e in ecarts) and all(e > TOLERANCE for e in independants):
        return "contraire"
    return None


def songbpm_titre_contenu(track, ctx):
    """Les mesures SongBPM posées par un titre CONTENU dans l'autre, avant la
    garde de pose `cccebc7` (2026-10-29) : deux fiches de l'artiste aux mesures
    SongBPM IDENTIQUES dont l'un des titres contient l'autre avec un mot en plus
    (« Maybe » / « Maybe So », « Tree » / « That Tree ») — le juge de la pose
    (`titre_contenu_non_corrobore`) les refuserait aujourd'hui sans durée
    indépendante à ± 2 s.

    La fiche que ses catalogues CONTREDISENT perd les mesures quand ceux de
    l'autre les CONFIRMENT (`retirer` posé : une règle tranche ; la garde de
    pose, rejouée au run suivant, refuse la même page faute de durée
    concordante). Deux fiches que leurs catalogues confirment sont un même
    enregistrement : aucun cas. Le reste (une fiche sans catalogue, une vidéo
    pour seule contradiction, un écart d'édition) va à l'humain, une fois par
    paire : sur la fiche que rien ne confirme, sinon au plus petit id. Sans
    durée SongBPM, rien ne dit à qui la page revient : aucun cas (7 paires
    « Jail » / « Jail, Pt. 2 » au BPM égal, 2026-10-29). Les jumelles « titre
    + année » sont du ressort de `jumelle_annee`."""
    from src.utils.titre_annee import _duree, jumelles

    mesures = tuple(_mesures(ctx, track, c).get("songbpm") for c in _CHAMPS_SONGBPM)
    duree_page = _duree(mesures[3])
    autres = [t for t in _signatures_songbpm(ctx).get(mesures, ()) if t.id != track.id]
    if duree_page is None or not autres:
        return None
    titres = ctx.memo("titres_disco", lambda: [(t.id, t.title) for t in ctx.disco])
    des_jumelles = {jid for jid, _ in jumelles(track.id, track.title, titres)}
    juge = ctx.memo("juge_songbpm", _juge_songbpm)
    lues = dict(zip(_CHAMPS_SONGBPM, mesures, strict=True))
    indecis = []
    for autre in autres:
        if autre.id in des_jumelles:
            continue
        page = {"title": autre.title, "duration": duree_page}
        if not juge.titre_contenu_non_corrobore(page, track.title, None, None):
            continue  # titres égaux, annotation, ou aucun ne contient l'autre
        fiche = _tient_la_page(track, autre, duree_page)
        cote = _tient_la_page(autre, track, duree_page)
        preuves = {
            "songbpm": lues,
            "autre": autre.id,
            "fiche": fiche or "aucune",
            "l_autre": cote or "aucune",
        }
        if fiche == "contraire" and cote == "confirme":
            return (
                f"mesures SongBPM de « {autre.title} » (#{autre.id}, {duree_page} s, que ses "
                f"catalogues confirment) : ceux de « {track.title} » la démentent",
                {**preuves, "retirer": True, "track_ids": [track.id], "garder": autre.id},
            )
        if "confirme" == fiche == cote or (fiche == "confirme" and cote == "contraire"):
            continue
        if (cote == "confirme") or (fiche != "confirme" and track.id < autre.id):
            indecis.append((autre, preuves))
    if indecis:
        autre, preuves = indecis[0]
        return (
            f"mesures SongBPM partagées avec « {autre.title} » (#{autre.id}), un titre contenu "
            f"dans l'autre — les catalogues ne disent pas à qui elles reviennent "
            f"(ici : {preuves['fiche']}, là : {preuves['l_autre']})",
            preuves,
        )
    return None


# ── Détecteurs d'ARTISTE ────────────────────────────────────────────────────


def liens_proposes(ctx):
    """Alias et formations PROPOSÉS par MusicBrainz / Discogs, pas encore
    arbitrés dans la fenêtre « Groupes »."""
    return [
        (
            r.related_name,
            f"{'formation' if r.kind != 'alias' else 'alias'} proposé"
            + (f" ({r.detail})" if r.detail else "")
            + " — à arbitrer dans « Groupes »",
            {"nom": r.related_name, "kind": r.kind},
        )
        for r in ctx.relations_proposees
    ]


DETECTEURS: tuple[Detecteur, ...] = (
    Detecteur(
        "video_etrangere",
        "Vidéo qui ne nomme pas le morceau",
        "🎬",
        video_etrangere,
        nature_du_cas=nature_video_etrangere,
    ),
    Detecteur(
        "recherche_perimee", "Vidéo de la recherche refusée par le score", "🔎", recherche_perimee
    ),
    Detecteur("vues_ytm", "Vues YouTube anormales", "📈", vues_youtube_anormales),
    Detecteur("duree_songbpm", "Durée SongBPM démentie par YouTube", "⏱️", duree_songbpm_dementie),
    Detecteur("duree_spotify", "Durée de l'ID Spotify démentie", "🎧", duree_spotify_dementie),
    Detecteur("duree_deezer", "Durée de l'ID Deezer démentie", "🎧", duree_deezer_dementie),
    Detecteur("duree_isolee", "Durée d'un témoin démentie par les catalogues", "⏱️", duree_isolee),
    Detecteur(
        "id_spotify_version", "ID Spotify d'une autre version", "🔀", id_spotify_autre_version
    ),
    Detecteur("id_spotify_partage", "Même ID Spotify, deux pages", "👯", id_spotify_partage),
    Detecteur(
        "id_catalogue_partage", "Même ID de catalogue, deux pages", "🪪", id_catalogue_partage
    ),
    Detecteur("compteur_partage", "Même compteur Spotify, deux fiches", "🧮", compteur_partage),
    Detecteur(
        "jumelle_annee", "Donnée partagée avec la jumelle à l'année", "📅", donnee_de_la_jumelle
    ),
    Detecteur(
        "songbpm_titre_contenu",
        "Mesures SongBPM d'un titre contenu",
        "🪆",
        songbpm_titre_contenu,
    ),
    Detecteur("id_spotify_propose", "ID Spotify proposé (SongBPM)", "🆔", id_spotify_propose),
    Detecteur(
        "songbpm_original",
        "Version sans plateforme aux mesures SongBPM de l'original",
        "🪞",
        songbpm_de_l_original,
        formel=True,
    ),
    Detecteur(
        "songbpm_copie", "Version aux mesures de l'original", "🪞", songbpm_copie_de_l_original
    ),
    Detecteur("bpm", "BPM en désaccord", "🥁", bpm_desaccord),
    Detecteur("tonalite", "Tonalité en désaccord", "🎹", tonalite_desaccord),
    Detecteur("lrc_autre_fiche", "LRC d'une autre fiche", "📝", lrc_d_une_autre_fiche, formel=True),
    Detecteur(
        "paroles_autre_fiche",
        "Paroles ou LRC de repli d'une autre fiche",
        "📝",
        paroles_d_une_autre_fiche,
    ),
    Detecteur("lrc_douteux", "LRC douteux (40-60 %)", "📝", lrc_douteux),
    Detecteur(
        "lrc_live", "LRC douteux — live, Grünt, COLORS, OKLM, Skyrock", "🎤", lrc_session_live
    ),
    Detecteur("generique_duree", "Titre générique, durée partagée", "🔁", generique_meme_duree),
    Detecteur(
        "certif_autre_titre",
        "Certification d'un autre titre",
        "🏆",
        certif_d_un_autre_titre,
        formel=True,
    ),
    Detecteur("certif_date", "Certification avant la sortie", "🏆", certif_avant_sortie),
    Detecteur("doublon", "Doublon de titre", "👯", doublon_de_titre),
    Detecteur("doublon_lrc", "Même morceau ? (LRC d'une version sœur)", "👯", doublon_par_lrc),
    Detecteur("doublon_inedit", "Inédit en double", "👻", doublon_inedit),
    Detecteur(
        "credits_api",
        "Crédits 🎫 non confirmés",
        "🎫",
        credits_api_seuls,
        nature_du_cas=nature_credits_api,
    ),
    Detecteur("annotee", "Page « Non-Music » sans trace", "🏷️", page_annotee_sans_trace),
    Detecteur(
        "video_autre_chaine",
        "Vidéo d'un autre artiste (chaîne et durée)",
        "📺",
        video_d_une_autre_chaine,
    ),
    Detecteur(
        "video_trop_longue",
        "Vidéo Genius bien plus longue que le morceau",
        "📏",
        video_trop_longue,
    ),
    # Pour INFORMATION (décision utilisateur 2026-10-26, « la vérif manuelle,
    # c'est pour les cas que le code ne peut pas trancher ») : une page qui ne
    # porte rien (ni paroles, ni plateforme, ni album, ni vidéo, ni tiers
    # crédité) ne pèse sur aucune donnée ni aucun visuel — trancher « poubelle
    # ou inédit » n'y changerait rien. 75 fiches au 2026-10-26.
    Detecteur("sans_info", "Page Genius sans info", "🕳️", page_sans_info, nature=INFORMATION),
)

DETECTEURS_ARTISTE: tuple[DetecteurArtiste, ...] = (
    DetecteurArtiste("liens_proposes", "Alias / formations proposés", "👥", liens_proposes),
    DetecteurArtiste(
        "video_partagee",
        "Vidéo portée par plusieurs fiches",
        "🎞️",
        video_partagee,
        nature_du_cas=nature_video_partagee,
    ),
    DetecteurArtiste(
        "soeurs_identite", "Lignes sœurs : identité divergente", "🧬", soeurs_identite_divergente
    ),
)


def _appoint_des_places(track, preuves: dict, ctx: Contexte) -> str:
    """La place des deux fiches sur un même disque, ajoutée au MOTIF d'un 👯
    (2026-10-14) — preuve d'appoint, jamais un cas à elle seule, et hors de la
    clé du cas : un 👯 déjà tranché le reste. Seuls deux numéros d'une même
    source comparable (Deezer, jamais Genius) parlent (`positions_disque`)."""
    from src.utils.positions_disque import appoint_emplacement

    a = ctx.positions.get(track.id) or []
    for autre in preuves.get("autres") or []:
        dit = appoint_emplacement(a, ctx.positions.get(autre) or []) if a else None
        if dit:
            return f" — 📀 {dit}"
    return ""


def detecter(ctx: Contexte, detecteurs=DETECTEURS, detecteurs_artiste=DETECTEURS_ARTISTE):
    """Tous les cas, triés par impact décroissant."""
    cas = []
    for d in detecteurs:
        for t in ctx.disco:
            verdict = d.juger(t, ctx)
            if verdict:
                motif, preuves = verdict
                if d.icone == "👯":
                    motif += _appoint_des_places(t, preuves, ctx)
                poids = preuves.get("impact", impact(t))
                nature = d.nature_du_cas(t, preuves, ctx) if d.nature_du_cas else d.nature
                cle = cle_du_cas(t, preuves)
                cas.append(Cas(d.code, t.id, t.title, motif, poids, preuves, cle, nature))
    for d in detecteurs_artiste:
        for libelle, motif, preuves in d.juger(ctx):
            cle = f"artiste|{libelle}|{preuves.get('kind', '')}"
            nature = d.nature_du_cas(preuves, ctx) if d.nature_du_cas else d.nature
            poids = preuves.get("impact", 0)
            cas.append(Cas(d.code, None, libelle, motif, poids, preuves, cle, nature))
    return sorted(cas, key=lambda c: (-c.impact, c.detecteur, c.morceau))


#: Détecteurs dont les cas viennent des RUNS (oracles réseau), enregistrés dans
#: `revue_signalements` : ils n'ont pas de fonction ici, seulement un libellé.
DETECTEURS_DE_RUN: tuple[DetecteurArtiste, ...] = (
    DetecteurArtiste("kworb_variante", "Kworb : ID d'une autre version", "🔀", None),
    DetecteurArtiste("kworb_id_mal_place", "Kworb : ID sur une autre fiche", "🔀", None),
    DetecteurArtiste("kworb_id_partage", "Kworb : ID porté par deux fiches", "⚠️", None),
    DetecteurArtiste("kworb_doublon", "Kworb : doublon évident", "🔁", None),
    DetecteurArtiste("kworb_ecartee", "Kworb : ligne écartée", "⤫", None),
    DetecteurArtiste("kworb_flou", "Kworb : rapprochement flou", "≈", None),
    # Ses streams ne sont rattachés à rien : rien de faux n'est compté.
    DetecteurArtiste(
        "kworb_non_relie", "Kworb : introuvable en base", "❔", None, nature=INFORMATION
    ),
    DetecteurArtiste("kworb_a_confirmer", "Kworb : même morceau ?", "≟", None),
    DetecteurArtiste("kworb_proposition", "Kworb : variante / remix à classer", "🎚️", None),
    DetecteurArtiste("deezer_absent", "Deezer : morceau absent", "✚", None),
    DetecteurArtiste("deezer_album_absent", "Deezer : disque absent", "💿", None),
    DetecteurArtiste("deezer_version", "Deezer : version absente", "🎚️", None),
    DetecteurArtiste("deezer_apparition", "Deezer : apparition chez un autre", "👥", None),
    DetecteurArtiste("deezer_link_candidate", "Deezer : parution à confirmer", "❓", None),
    DetecteurArtiste("deezer_link_review", "Deezer : lien à revoir", "🔓", None),
    DetecteurArtiste("spotify_audit", "Spotify : identifiant démenti", "🔍", None),
    DetecteurArtiste("spotify_homonymes", "Spotify : homonymes de l'artiste", "👤", None),
    DetecteurArtiste("discogs_contredit", "Discogs : MusicBrainz contredit", "💽", None),
)
CODES_KWORB = tuple(d.code for d in DETECTEURS_DE_RUN if d.code.startswith("kworb_"))
#: Les propositions EN ATTENTE, tirées des `suggestions` du run (pas d'`a_trancher`).
CODES_PROPOSITIONS_KWORB = ("kworb_a_confirmer", "kworb_proposition")


def enregistrer_audit_spotify(data_manager, artiste, ecarts, *, retires=()) -> int:
    """Les écarts d'un audit Spotify COMPLET deviennent des signalements
    (« 🔍 Vérifier les identifiants »), moins ceux qui viennent d'être retirés."""
    par_id = {t.id: t for t in artiste.tracks or []}
    enleves = {(e["track_id"], e["spotify_id"]) for e in retires}
    cas = []
    for e in ecarts:
        if (e["track_id"], e["spotify_id"]) in enleves:
            continue
        marque = (
            "🚨 "
            if e["artiste_etranger"] or e.get("artiste_homonyme")
            else ("🔀 " if e.get("variante_etrangere") else "")
        )
        track = par_id.get(e["track_id"])
        preuves = {"spotify_id": e["spotify_id"], "spotify_sert": e["spotify"]["name"]}
        motif = f"{marque}{e['motif']} — Spotify sert « {e['spotify']['name']} »"
        if track is None:
            cas.append(
                Cas(
                    "spotify_audit",
                    e["track_id"],
                    e["titre"],
                    motif,
                    0,
                    preuves,
                    f"t{e['track_id']}|{e['spotify_id']}",
                )
            )
        else:
            cas.append(
                cas_de_run("spotify_audit", track, e["titre"], motif, preuves, impact(track))
            )
    return data_manager.remplacer_signalements(artiste.id, "spotify_audit", cas)


def enregistrer_homonymes_spotify(data_manager, artiste, homonymes) -> int:
    """Les profils Spotify HOMONYMES vus par un audit complet (`profils_homonymes`)
    : l'ID Spotify de l'artiste n'est pas connu, Spotify crédite son nom sous
    plusieurs URI. À trancher : poser le bon ID de l'artiste — le garde
    d'identité refusera alors les morceaux de l'autre (`artiste_homonyme`)."""
    cas = []
    for h in homonymes:
        if h["artiste"] != artiste.name:
            continue
        detail = " ; ".join(
            f"spotify:artist:{uri} ({len(titres)} : {', '.join(titres[:3])}"
            + ("…" if len(titres) > 3 else "")
            + ")"
            for uri, titres in h["uris"].items()
        )
        cas.append(
            cas_de_run(
                "spotify_homonymes",
                None,
                h["artiste"],
                f"{len(h['uris'])} profils Spotify au nom « {h['artiste']} » — {detail}",
                {"uris": sorted(h["uris"])},
            )
        )
    return data_manager.remplacer_signalements(artiste.id, "spotify_homonymes", cas)


def cas_de_run(detecteur: str, track, morceau: str, motif: str, preuves, impact=0) -> Cas:
    """Un cas trouvé par un RUN, avec la même clé stable que les autres."""
    if track is None:
        cle = f"artiste|{morceau}|" + json.dumps(preuves, sort_keys=True, default=str)
    else:
        cle = cle_du_cas(track, preuves)
    return Cas(
        detecteur, track.id if track is not None else None, morceau, motif, impact, preuves, cle
    )


@dataclass
class Revue:
    #: Cas à trancher, triés par impact.
    actifs: list[Cas]
    #: Cas que l'utilisateur a marqués normaux (consultables, rétablissables).
    masques: list[Cas]


#: Signalements Kworb qui disent « cet ID Spotify est porté par plusieurs
#: fiches » : ils ne valent que tant que c'est vrai.
_CODES_ID_PARTAGE = ("kworb_id_partage", "kworb_doublon")


def ids_spotify_encore_partages(tracks) -> set[str]:
    """Les ID Spotify que plusieurs fiches portent AUJOURD'HUI — même index que
    le run Kworb. Une fusion faite depuis (A2H « J'observe et je retiens »,
    « L'histoire est simple », « Pornostar », runs du 2026-10-01) laissait le
    signalement de la fiche gardée au panneau jusqu'au run suivant."""
    from src.utils.update_kworb import construire_index

    index = construire_index(tracks)
    return set(index.ids_partages) | set(index.doublons)


#: Écarts Deezer qui disent « cette piste n'est sur aucune fiche ».
_CODES_DEEZER_PISTE_ORPHELINE = (
    "deezer_absent",
    "deezer_version",
    "deezer_apparition",
    "deezer_link_candidate",
)


def _signalement_deezer_resolu(
    s, par_id: dict, ids_deezer: set, editions_deezer: frozenset = frozenset()
) -> bool:
    """Un écart Deezer que l'état PRÉSENT dément (2026-10-12, A2H) : la piste
    « absente » est aujourd'hui l'ID Deezer d'une fiche (« Yatch Music » —
    l'ID de « Yacht Music », perdu par une fusion puis rétabli) ; le « lien à
    revoir » n'a plus de durée indépendante qui le contredise (« Pardonne
    moi » : la durée SongBPM, d'un autre morceau, a été retirée).

    Une page notée en ÉDITION d'une fiche (`editions_deezer`) n'est plus
    orpheline non plus ; et un rattachement PROPOSÉ à une fiche tombe quand
    la page est déjà à une AUTRE (R3, 2026-10-27 : les pages de #12092,
    rattachées à #8199 par la règle du panneau le 2026-10-26)."""
    preuves = s["preuves"] or {}
    if s["detecteur"] in _CODES_DEEZER_PISTE_ORPHELINE:
        piste = preuves.get("piste_deezer")
        if piste is not None and piste in editions_deezer:
            return True
        if s["track_id"] is not None:
            # Un rattachement PROPOSÉ à une fiche : résolu si c'est elle qui
            # porte la piste — et sans objet si c'est une autre.
            track = par_id.get(s["track_id"])
            return track is not None and piste is not None and piste in ids_deezer
        return piste in ids_deezer
    if s["detecteur"] == "deezer_link_review":
        from types import SimpleNamespace

        from src.services.ecarts_deezer import _lien_a_revoir

        track = par_id.get(s["track_id"])
        duree = ((preuves.get("ecart") or {}).get("piste") or {}).get("duration")
        if track is None or duree is None:
            return False
        piste = preuves.get("piste_deezer")
        if piste is not None and track.deezer_id != piste:
            return True  # le lien a été délié ou remplacé depuis (A2H « Blues »)
        return _lien_a_revoir(SimpleNamespace(duration=duree), track) is None
    return False


def _cas_des_signalements(
    signalements, ids_disco: set, tracks=None, editions_deezer: frozenset = frozenset()
) -> list[Cas]:
    """Un signalement dont la fiche a disparu depuis le run (fusion,
    suppression) n'a plus d'objet ; avec `tracks`, ceux que l'état présent
    dément non plus (`_signalement_resolu`). `editions_deezer` = pages Deezer
    notées en édition d'une fiche (`track_editions`)."""
    partages = None
    if tracks is not None and any(s["detecteur"] in _CODES_ID_PARTAGE for s in signalements):
        partages = ids_spotify_encore_partages(tracks)
    par_id = {t.id: t for t in tracks or []}
    ids_deezer = {t.deezer_id for t in tracks or [] if t.deezer_id}

    def vivant(s) -> bool:
        if s["track_id"] is not None and s["track_id"] not in ids_disco:
            return False
        if partages is not None and s["detecteur"] in _CODES_ID_PARTAGE:
            return (s["preuves"] or {}).get("spotify_id") in partages
        if tracks is not None:
            return not _signalement_deezer_resolu(s, par_id, ids_deezer, editions_deezer)
        return True

    return [
        Cas(
            s["detecteur"],
            s["track_id"],
            s["morceau"] or "",
            (s["motif"] or "") + _vote_de_parution(s, par_id),
            s["impact"] or 0,
            s["preuves"] or {},
            s["cle"],
            nature_de(s["detecteur"]),
        )
        for s in signalements
        if vivant(s)
    ]


def _vote_de_parution(s, par_id: dict) -> str:
    """Le vote des catalogues sur une PARUTION Deezer à confirmer, ajouté au
    motif (2026-10-13) : confirmée par deux catalogues, le rattachement est
    sûr ; démentie par deux, c'est une autre version."""
    if s["detecteur"] != "deezer_link_candidate":
        return ""
    track = par_id.get(s["track_id"])
    preuves = s["preuves"] or {}
    duree = ((preuves.get("ecart") or {}).get("piste") or {}).get("duration")
    if track is None or not duree:
        return ""
    vote = voter({**durees_du_vote(track), "deezer": duree}, duree, editions=_editions(track))
    if vote.nature == SOUTENUE and len(vote.pour) >= 2:
        return f" — durée confirmée par {', '.join(sorted(vote.pour))} : rattachement sûr"
    if vote.nature == ISOLEE:
        return f" — durée Deezer démentie par {vote.dementie_par()} : autre version probable"
    return ""


def charger_contexte(data_manager, artiste) -> Contexte:
    """Le contexte des détecteurs, lu UNE fois (lectures seules) — celui
    qu'`analyser` juge, et que les politiques d'arbitrage relisent."""
    tracks = artiste.tracks or []
    durees_editions = data_manager.get_artist_edition_durations(artiste.id)
    for t in tracks:
        t.durees_editions = editions_distinctes(
            durees_editions.get(t.id, ()), (t.durations_observees or {}).get("deezer")
        )
    ctx = contexte(
        tracks,
        data_manager.get_artist_observations(artiste.id),
        data_manager.get_artist_relations(artiste.id, status="proposed"),
    )
    ctx.soeurs = data_manager.divergences_identite_soeurs(artiste.id)
    ctx.positions = data_manager.get_artist_positions(artiste.id)
    return ctx


def analyser(data_manager, artiste, ctx: Contexte | None = None) -> Revue:
    """Détecteurs hors ligne + signalements des runs, moins ce que
    l'utilisateur a déjà tranché. Lectures seules, aucune écriture."""
    tracks = artiste.tracks or []
    ctx = ctx or charger_contexte(data_manager, artiste)
    cas = detecter(ctx)
    cas += _cas_des_signalements(
        data_manager.signalements_revue(artiste.id),
        {t.id for t in tracks},
        tracks,
        data_manager.get_artist_edition_deezer_ids(artiste.id),
    )
    cas.sort(key=lambda c: (-c.impact, c.detecteur, c.morceau))
    tranches = data_manager.verdicts_revue(artiste.id)
    return Revue(
        actifs=[c for c in cas if (c.detecteur, c.cle) not in tranches],
        masques=[c for c in cas if (c.detecteur, c.cle) in tranches],
    )


CODES_FORMELS = frozenset(d.code for d in DETECTEURS if d.formel)


def tous_les_detecteurs() -> list:
    return [*DETECTEURS, *DETECTEURS_ARTISTE, *DETECTEURS_DE_RUN]


def par_detecteur(cas: list[Cas]) -> dict[str, int]:
    compte: dict[str, int] = {}
    for c in cas:
        compte[c.detecteur] = compte.get(c.detecteur, 0) + 1
    return compte


def nature_de(code: str) -> str:
    """La nature PAR DÉFAUT des cas d'un détecteur (un détecteur à
    `nature_du_cas` peut en donner une autre cas par cas)."""
    d = next((d for d in tous_les_detecteurs() if d.code == code), None)
    return d.nature if d is not None else DECISION


def par_nature(cas: list[Cas]) -> dict[str, int]:
    """`{nature: nombre}`, les trois natures toujours présentes."""
    compte = dict.fromkeys(NATURES, 0)
    for c in cas:
        compte[c.nature] += 1
    return compte
