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

import json
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from difflib import SequenceMatcher

from src.utils.concordance_paroles import (
    MOTS_MIN,
    SEUIL_FAUX,
    SEUIL_JUSTE,
    SEUIL_JUSTE_BIGRAMMES,
    bigrammes,
    lrc_dementi,
    mots,
    paroles_de_reference,
    recouvrement,
)
from src.utils.credits_genius_api import SOURCE_API
from src.utils.duree_youtube import SOURCE_AUDIO
from src.utils.spotify_identity import CHAMP_ID_PROPOSE, variante_etrangere
from src.utils.title_matching import cle_album, names_match_as_words
from src.utils.track_validation import sans_info
from src.utils.version_descriptors import Kind, parse_variant, socle_normalise, titre_generique
from src.utils.version_heritage import IndexSocles, famille_de, socle_parmi

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


@dataclass(frozen=True)
class DetecteurArtiste:
    code: str
    libelle: str
    icone: str
    #: `contexte -> [(libellé, motif, preuves)]`
    juger: Callable


@dataclass
class Contexte:
    disco: list
    obs: dict = field(default_factory=dict)  # track_id -> [Observation]
    relations_proposees: list = field(default_factory=list)
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
    t = (texte or "").replace("œ", "oe").replace("Œ", "OE").replace("æ", "ae")
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


def _duree_youtube(track) -> int | None:
    return (track.durations_observees or {}).get(SOURCE_AUDIO)


def duree_songbpm_dementie(track, _ctx=None):
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
    durees = track.durations_observees or {}
    for temoin, ecart in _DUREES_TEMOINS:
        ref = durees.get(temoin)
        for src in _SOURCES_PAR_ID_SPOTIFY:
            d = durees.get(src)
            if ref and d and abs(ref - d) > ecart:
                return (
                    f"durée de l'ID Spotify ({src}) {d} s ≠ {temoin} {ref} s — "
                    "autre enregistrement ?",
                    {temoin: ref, src: d, "spotify_id": track.spotify_id},
                )
    return None


def duree_deezer_dementie(track, _ctx=None):
    """L'audio Topic ou la piste YTM dément la durée de l'identifiant Deezer :
    le hit Deezer est peut-être celui d'une autre version."""
    durees = track.durations_observees or {}
    dz = durees.get("deezer")
    if not track.deezer_id or not dz:
        return None
    for temoin, ecart in _DUREES_TEMOINS:
        ref = durees.get(temoin)
        if temoin != "deezer" and ref and abs(ref - dz) > ecart:
            return (
                f"durée Deezer {dz} s ≠ {temoin} {ref} s — autre version ?",
                {temoin: ref, "deezer": dz, "deezer_id": track.deezer_id},
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
        return None
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
        if all(doublon_de_titre(t, ctx) or doublon_de_titre(track, ctx) for t in autres):
            continue
        return (
            f"même ID Spotify que « {autres[0].title} »"
            + (f" ({autres[0].artist.name})" if autres[0].artist else "")
            + " — même enregistrement, deux pages Genius ?",
            {"autres": [t.id for t in autres], "spotify_id": sid},
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


def page_sans_info(track, _ctx=None):
    """Page Genius lue qui ne dit rien (🕳️) : morceau poubelle ou vrai inédit."""
    if sans_info(track):
        return ("page Genius lue sans aucune information (🕳️)", {})
    return None


def page_annotee_sans_trace(track, _ctx=None):
    """URL Genius `-annotated` (le tag « Non-Music ») sans aucune trace de
    plateforme : c'est ainsi qu'on a trouvé les pages poubelles du 2026-09-26
    (29 supprimées sur 47). Le tag seul couvre aussi de vrais skits."""
    if (
        (track.genius_url or "").endswith("-annotated")
        and not track.spotify_id
        and not track.deezer_id
        and not track.videos
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


def video_etrangere(track, ctx=None):
    """Une vidéo rattachée dont le titre ne nomme pas le morceau : peut-être
    celle d'un autre (Kanye « Heartless - Recorded At RAK Studios » → vidéo de
    Dermot Kennedy, 918 k vues, comptées dans les streams YouTube). Une vidéo
    PORTÉE PAR PLUSIEURS FICHES se juge une fois, au niveau de l'artiste
    (`video_partagee`) — un « album entier » faisait un cas par titre."""
    proprietaires = _proprietaires_des_videos(ctx) if ctx is not None else {}
    suspectes = [
        v
        for v in track.videos or []
        if v.title and len(proprietaires.get(v.video_id, ())) < 2 and not video_nomme(track, v)
    ]
    if not suspectes:
        return None
    v = max(suspectes, key=lambda x: x.views or 0)
    return (
        f"vidéo « {v.title} » ({v.video_id}) ne nomme pas le morceau",
        {"video_id": v.video_id, "titre": v.title, "impact": v.views or 0},
    )


def video_partagee(ctx):
    """Une vidéo portée par PLUSIEURS fiches qui n'en nomme pas au moins une
    (album entier, EP complet, film, freestyle collectif mal rattaché) : UN
    cas, avec les fiches concernées — ses vues sont comptées sur chacune."""
    cas = []
    for video_id, fiches in _proprietaires_des_videos(ctx).items():
        if len(fiches) < 2:
            continue
        v = next(x for x in fiches[0].videos if x.video_id == video_id)
        if not v.title:
            continue
        muettes = [t for t in fiches if not video_nomme(t, v)]
        if not muettes:
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


def vues_youtube_anormales(track, _ctx=None):
    """Vues YouTube dix fois au-dessus des streams Spotify : une vidéo d'un
    autre morceau, ou d'un autre artiste, gonfle peut-être le total."""
    sp, yt = track.streams.spotify_streams or 0, track.streams.ytm_streams or 0
    if sp and yt > VUES_YTM_MIN and yt > RAPPORT_YTM_SPOTIFY * sp:
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
    Detecteur("video_etrangere", "Vidéo qui ne nomme pas le morceau", "🎬", video_etrangere),
    Detecteur("vues_ytm", "Vues YouTube anormales", "📈", vues_youtube_anormales),
    Detecteur("duree_songbpm", "Durée SongBPM démentie par YouTube", "⏱️", duree_songbpm_dementie),
    Detecteur("duree_spotify", "Durée de l'ID Spotify démentie", "🎧", duree_spotify_dementie),
    Detecteur("duree_deezer", "Durée de l'ID Deezer démentie", "🎧", duree_deezer_dementie),
    Detecteur(
        "id_spotify_version", "ID Spotify d'une autre version", "🔀", id_spotify_autre_version
    ),
    Detecteur("id_spotify_partage", "Même ID Spotify, deux pages", "👯", id_spotify_partage),
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
    Detecteur("credits_api", "Crédits 🎫 non confirmés", "🎫", credits_api_seuls),
    Detecteur("annotee", "Page « Non-Music » sans trace", "🏷️", page_annotee_sans_trace),
    Detecteur("sans_info", "Page Genius sans info", "🕳️", page_sans_info),
)

DETECTEURS_ARTISTE: tuple[DetecteurArtiste, ...] = (
    DetecteurArtiste("liens_proposes", "Alias / formations proposés", "👥", liens_proposes),
    DetecteurArtiste("video_partagee", "Vidéo portée par plusieurs fiches", "🎞️", video_partagee),
)


def detecter(ctx: Contexte, detecteurs=DETECTEURS, detecteurs_artiste=DETECTEURS_ARTISTE):
    """Tous les cas, triés par impact décroissant."""
    cas = []
    for d in detecteurs:
        for t in ctx.disco:
            verdict = d.juger(t, ctx)
            if verdict:
                motif, preuves = verdict
                poids = preuves.get("impact", impact(t))
                cas.append(
                    Cas(d.code, t.id, t.title, motif, poids, preuves, cle_du_cas(t, preuves))
                )
    for d in detecteurs_artiste:
        for libelle, motif, preuves in d.juger(ctx):
            cle = f"artiste|{libelle}|{preuves.get('kind', '')}"
            cas.append(Cas(d.code, None, libelle, motif, preuves.get("impact", 0), preuves, cle))
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
    DetecteurArtiste("kworb_non_relie", "Kworb : introuvable en base", "❔", None),
    DetecteurArtiste("kworb_a_confirmer", "Kworb : même morceau ?", "≟", None),
    DetecteurArtiste("kworb_proposition", "Kworb : variante / remix à classer", "🎚️", None),
    DetecteurArtiste("deezer_absent", "Deezer : morceau absent", "✚", None),
    DetecteurArtiste("deezer_album_absent", "Deezer : disque absent", "💿", None),
    DetecteurArtiste("deezer_version", "Deezer : version absente", "🎚️", None),
    DetecteurArtiste("deezer_apparition", "Deezer : apparition chez un autre", "👥", None),
    DetecteurArtiste("deezer_link_candidate", "Deezer : parution à confirmer", "❓", None),
    DetecteurArtiste("deezer_link_review", "Deezer : lien à revoir", "🔓", None),
    DetecteurArtiste("spotify_audit", "Spotify : identifiant démenti", "🔍", None),
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
        marque = "🚨 " if e["artiste_etranger"] else ("🔀 " if e.get("variante_etrangere") else "")
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


def _cas_des_signalements(signalements, ids_disco: set) -> list[Cas]:
    """Un signalement dont la fiche a disparu depuis le run (fusion,
    suppression) n'a plus d'objet."""
    return [
        Cas(
            s["detecteur"],
            s["track_id"],
            s["morceau"] or "",
            s["motif"] or "",
            s["impact"] or 0,
            s["preuves"] or {},
            s["cle"],
        )
        for s in signalements
        if s["track_id"] is None or s["track_id"] in ids_disco
    ]


def analyser(data_manager, artiste) -> Revue:
    """Détecteurs hors ligne + signalements des runs, moins ce que
    l'utilisateur a déjà tranché. Quatre lectures, aucune écriture."""
    tracks = artiste.tracks or []
    cas = detecter(
        contexte(
            tracks,
            data_manager.get_artist_observations(artiste.id),
            data_manager.get_artist_relations(artiste.id, status="proposed"),
        )
    )
    cas += _cas_des_signalements(
        data_manager.signalements_revue(artiste.id), {t.id for t in tracks}
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
