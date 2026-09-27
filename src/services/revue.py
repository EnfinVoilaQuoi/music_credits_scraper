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
    SEUIL_FAUX,
    SEUIL_JUSTE,
    paroles_de_reference,
    recouvrement,
)
from src.utils.credits_genius_api import SOURCE_API
from src.utils.duree_youtube import SOURCE_AUDIO
from src.utils.track_validation import sans_info
from src.utils.version_descriptors import Kind, parse_variant, titre_generique
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


def duree_spotify_dementie(track, _ctx=None):
    """L'audio Topic dément la durée de l'identifiant Spotify : l'ID désigne
    peut-être un autre enregistrement (ses streams et ses mesures avec)."""
    yt = _duree_youtube(track)
    if not yt or not track.spotify_id:
        return None
    for src in _SOURCES_PAR_ID_SPOTIFY:
        d = (track.durations_observees or {}).get(src)
        if d and abs(yt - d) > ECART_DUREE_S:
            return (
                f"durée de l'ID Spotify ({src}) {d} s ≠ audio YouTube {yt} s — "
                "autre enregistrement ?",
                {"youtube": yt, src: d, "spotify_id": track.spotify_id},
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


def bpm_desaccord(track, ctx):
    """Les sources de BPM se contredisent (hors octave) et le vote a tranché
    SANS le dire (Booba « Lunatic » : GetSongBPM 83, ReccoBeats et SongBPM 130)."""
    bpms = {s: float(v) for s, v in _mesures(ctx, track, "bpm").items()}
    vals = list(bpms.values())
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
    if len(vals) > 1 and not all(_meme_tonalite(vals[0], v) for v in vals[1:]):
        noms = ("do", "do♯", "ré", "mi♭", "mi", "fa", "fa♯", "sol", "la♭", "la", "si♭", "si")
        detail = ", ".join(
            f"{s} {noms[k % 12]} {'majeur' if m else 'mineur'}"
            for s, (k, m) in sorted(paires.items())
        )
        return (f"tonalité en désaccord : {detail}", {"tonalites": paires})
    return None


def songbpm_copie_de_l_original(track, ctx):
    """Une VERSION (live, remix…) dont BPM ET durée SongBPM sont ceux de son
    original : la page était celle de l'original (Freeze « Louisville (Remix) »,
    196 s pour 193 s). Un INSTRUMENTAL n'est pas visé — même beat, même tempo
    attendus (décision utilisateur)."""
    if parse_variant(track.title).kind == Kind.NONE or famille_de(track.title) == "instrumental":
        return None
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


def lrc_douteux(track, _ctx=None):
    """LRC ni démenti (< 0,4, écarté d'office) ni confirmé (≥ 0,6) par les
    paroles Genius : la tranche que l'oracle ne sait pas trancher seul."""
    if not track.lyrics.synced:
        return None
    ref = paroles_de_reference(track.lyrics.text, track.lyrics.source)
    score = recouvrement(ref, track.lyrics.synced)
    if score is not None and SEUIL_FAUX <= score < SEUIL_JUSTE:
        return (
            f"LRC ({track.lyrics.synced_source or '?'}) : {score:.0%} de mots communs "
            "avec les paroles Genius",
            {"recouvrement": score, "source": track.lyrics.synced_source},
        )
    return None


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


def doublon_de_titre(track, ctx):
    """Deux fiches de l'artiste au même titre, à la casse et à la ponctuation
    près (Booba « 3G » / « 3 G ») : doublon à fusionner, ou deux morceaux
    distincts à renommer."""
    if track.secondary_role:
        return None
    cle = (cle_doublon(track.title), _interprete(track))
    autres = [
        t
        for t in ctx.memo("doublons", lambda: _index_doublons(ctx.disco)).get(cle, [])
        if t is not track
        and not (t.genius_id and track.genius_id and t.genius_id == track.genius_id)
    ]
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
    n = len(socle)
    fenetres = (titre_video[i : i + n] for i in range(max(1, len(titre_video) - n + 1)))
    return max((SequenceMatcher(None, socle, f).ratio() for f in fenetres), default=0) >= (
        SIMILARITE_VIDEO
    )


def video_etrangere(track, _ctx=None):
    """Une vidéo rattachée dont le titre ne nomme pas le morceau : peut-être
    celle d'un autre (Kanye « Heartless - Recorded At RAK Studios » → vidéo de
    Dermot Kennedy, 918 k vues, comptées dans les streams YouTube)."""
    socle = _mots(parse_variant(track.title).socle)
    # Une parenthèse qui n'est pas un descripteur fait partie du titre Genius
    # (« Pursuit of Happiness (Nightmare) » : le clip officiel ne la porte pas) —
    # sauf sur un titre générique, qu'elle seule distingue (« Intro (A2) »).
    nu = _mots(re.sub(r"\s*[\(\[][^)\]]*[\)\]]", "", track.title or ""))
    if titre_generique(nu):
        nu = socle

    def couvre(v) -> bool:
        titre = _mots(v.title)
        return _video_couvre(socle, titre) or _video_couvre(nu, titre)

    suspectes = [v for v in track.videos or [] if v.title and not couvre(v)]
    if not suspectes:
        return None
    v = max(suspectes, key=lambda x: x.views or 0)
    return (
        f"vidéo « {v.title} » ({v.video_id}) ne nomme pas le morceau",
        {"video_id": v.video_id, "titre": v.title, "impact": v.views or 0},
    )


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


def certif_avant_sortie(track, _ctx=None):
    """Une certification datée AVANT la sortie du morceau : la date de sortie
    est fausse, ou la certification appartient à un autre titre (63 sur 1 841
    mesurées le 2026-09-27)."""
    sortie = _jour(track.release_date) if track.release_date else None
    if sortie is None:
        return None
    for e in track.certs.reelles:
        certif = _jour(e.get("certification_date") or "")
        if certif and certif < sortie:
            return (
                f"{e.get('body', '?')} {e.get('certification', '?')} du {certif:%d/%m/%Y} "
                f"avant la sortie ({sortie:%d/%m/%Y}) — « {e.get('title', '?')} »",
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
    Detecteur(
        "songbpm_copie", "Version aux mesures de l'original", "🪞", songbpm_copie_de_l_original
    ),
    Detecteur("bpm", "BPM en désaccord", "🥁", bpm_desaccord),
    Detecteur("tonalite", "Tonalité en désaccord", "🎹", tonalite_desaccord),
    Detecteur("lrc_douteux", "LRC douteux (40-60 %)", "📝", lrc_douteux),
    Detecteur("generique_duree", "Titre générique, durée partagée", "🔁", generique_meme_duree),
    Detecteur("certif_date", "Certification avant la sortie", "🏆", certif_avant_sortie),
    Detecteur("doublon", "Doublon de titre", "👯", doublon_de_titre),
    Detecteur("doublon_inedit", "Inédit en double", "👻", doublon_inedit),
    Detecteur("credits_api", "Crédits 🎫 non confirmés", "🎫", credits_api_seuls),
    Detecteur("annotee", "Page « Non-Music » sans trace", "🏷️", page_annotee_sans_trace),
    Detecteur("sans_info", "Page Genius sans info", "🕳️", page_sans_info),
)

DETECTEURS_ARTISTE: tuple[DetecteurArtiste, ...] = (
    DetecteurArtiste("liens_proposes", "Alias / formations proposés", "👥", liens_proposes),
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
            cas.append(Cas(d.code, None, libelle, motif, 0, preuves, cle))
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


def tous_les_detecteurs() -> list:
    return [*DETECTEURS, *DETECTEURS_ARTISTE, *DETECTEURS_DE_RUN]


def par_detecteur(cas: list[Cas]) -> dict[str, int]:
    compte: dict[str, int] = {}
    for c in cas:
        compte[c.detecteur] = compte.get(c.detecteur, 0) + 1
    return compte
