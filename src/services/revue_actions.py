"""Panneau « À trancher », étape 2 : les ACTIONS par type de cas (2026-09-27).

Décision utilisateur : « À trancher » est la porte d'entrée UNIQUE, verdicts ET
solutions. Une action n'écrit AUCUNE règle nouvelle : elle appelle l'écrivain
qui existe déjà pour ce geste (rejet d'une vidéo, retrait d'un ID Spotify,
retrait des mesures d'une source, statut d'un alias…). Ce qui demande une
fenêtre (la fusion et ses conflits, le choix groupe/collectif, la création
d'une piste Deezer) passe par un RENVOI que l'appelant fournit — ce module
reste sans GUI.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from src.services.revue import Cas
from src.utils.logger import get_logger

logger = get_logger(__name__)

#: Détecteurs dont les preuves portent l'ID Spotify à retirer.
_CODES_ID_SPOTIFY = ("kworb_variante", "spotify_audit", "duree_spotify", "kworb_id_partage")
_CODES_SONGBPM = ("songbpm_copie", "duree_songbpm", "songbpm_original")
_CODES_DOUBLON = ("doublon", "doublon_inedit", "doublon_lrc")


@dataclass
class ContexteAction:
    data_manager: object
    artiste: object
    #: `{track_id: Track}` de la discographie chargée.
    tracks: dict
    #: Renvois vers une fenêtre : `fusion(t1, t2)`, `groupes()`, `ecarts_deezer()`.
    renvois: dict[str, Callable] = field(default_factory=dict)


@dataclass(frozen=True)
class Action:
    code: str
    libelle: str
    executer: Callable  # (ContexteAction, Cas) -> str (compte rendu)
    #: Question posée avant d'agir (None : geste sans perte).
    confirmation: str | None = None
    #: Le cas est-il RÉSOLU une fois l'action faite ? (un renvoi ne l'est pas :
    #: la décision se prend dans la fenêtre ouverte).
    resout: bool = True
    #: L'action touche le RÉSEAU (page Spotify d'une ligne créée) : l'appelant
    #: l'exécute hors du fil de l'interface.
    reseau: bool = False


def _fiche(ctx: ContexteAction, cas: Cas):
    track = ctx.tracks.get(cas.track_id)
    if track is None:
        raise LookupError(f"fiche #{cas.track_id} introuvable dans la discographie chargée")
    return track


# ── Les gestes ──────────────────────────────────────────────────────────────


def _rejeter_video(ctx: ContexteAction, cas: Cas) -> str:
    from src.utils.youtube_integration import reject_youtube_link

    track = _fiche(ctx, cas)
    vid = cas.preuves["video_id"]
    reject_youtube_link(
        ctx.data_manager, track, f"https://www.youtube.com/watch?v={vid}", ctx.artiste.name
    )
    return f"vidéo {vid} retirée de « {track.title} »"


def _retirer_spotify(ctx: ContexteAction, cas: Cas) -> str:
    from src.utils.corrections_fiches import memoriser_id_refuse
    from src.utils.spotify_audit import rejeter_spotify_id

    track = _fiche(ctx, cas)
    sid = cas.preuves["spotify_id"]
    rapport = rejeter_spotify_id(ctx.data_manager, track, sid)
    # Mémorisé : le gate d'identité le refusera au prochain run (Kworb, scraper).
    memoriser_id_refuse(track, sid)
    return (
        f"ID {sid} retiré de « {track.title} », avec "
        f"{len(rapport.get('observations_retirees', []))} observation(s) qui en découlaient"
    )


def _retirer_songbpm(ctx: ContexteAction, cas: Cas) -> str:
    track = _fiche(ctx, cas)
    n = ctx.data_manager.retirer_mesures_songbpm(track.id)
    return f"{n} mesure(s) SongBPM retirée(s) de « {track.title} »"


def _ecarter_source(source: str):
    def executer(ctx: ContexteAction, cas: Cas) -> str:
        track = _fiche(ctx, cas)
        n = ctx.data_manager.retirer_mesures_source(track.id, source)
        return f"{n} mesure(s) {source} retirée(s) de « {track.title} »"

    return executer


def _retirer_certif(ctx: ContexteAction, cas: Cas) -> str:
    from src.utils.corrections_fiches import cle_certif, memoriser_certif_refusee

    track = _fiche(ctx, cas)
    entree = cas.preuves["certification"]
    # Mémorisée d'abord : `apply_certifications` ne la rattachera plus.
    memoriser_certif_refusee(track, entree)
    cle = cle_certif(entree)
    track.certs.entries = [e for e in track.certs.entries if cle_certif(e) != cle]
    ctx.data_manager.record_certifications(track.id, track.certs.entries, track.certs.album_entries)
    return f"{entree.get('body')} {entree.get('certification')} retirée de « {track.title} »"


def _retirer_lrc(ctx: ContexteAction, cas: Cas) -> str:
    from src.utils.corrections_fiches import memoriser_lrc_refuse

    track = _fiche(ctx, cas)
    lrc = track.lyrics.synced
    if not lrc:
        raise LookupError("la fiche n'a plus de LRC")
    # Mémorisé : le résolveur ne le retiendra plus (même texte, quelle que soit
    # la source qui le resservirait).
    memoriser_lrc_refuse(track, lrc)
    n = ctx.data_manager.retirer_lrc(track.id, lrc)
    track.lyrics.synced = None
    return f"LRC retiré de « {track.title} » ({n} observation(s))"


def _deezer_appliquer(ctx: ContexteAction, cas: Cas) -> str:
    """Crée la fiche, rattache ou délie — exactement ce que fait la fenêtre des
    écarts, via `creer_lignes`, sur l'écart reconstruit depuis le signalement."""
    from src.services import ecarts_deezer

    ecart = ecarts_deezer.ecart_depuis(cas.preuves["ecart"], ctx.tracks)
    if ecart.nature in ("link_candidate", "link_review") and ecart.existing_track is None:
        raise LookupError("la fiche concernée n'est plus dans la discographie")
    (compte,) = ecarts_deezer.creer_lignes(
        ctx.data_manager,
        ctx.artiste,
        [ecart],
        lire_piste=ctx.renvois.get("lire_piste_deezer"),
    ) or ["rien"]
    return compte


def _kworb_links():
    from src.utils.kworb_links_manager import KworbLinksManager

    return KworbLinksManager()


def _date_kworb(cas: Cas):
    from datetime import datetime

    brute = cas.preuves.get("kworb_date")
    try:
        return datetime.fromisoformat(brute) if brute else None
    except ValueError:
        return None


def _kworb_meme_morceau(ctx: ContexteAction, cas: Cas) -> str:
    s = cas.preuves
    if not ctx.data_manager.record_spotify_streams(
        s["track_id"],
        s["streams"],
        "kworb",
        updated_at=_date_kworb(cas),
        daily_streams=s.get("daily"),
    ):
        raise RuntimeError("streams non écrits")
    _kworb_links().confirm(ctx.artiste.name, s["kworb_title"], s["track_id"])
    return f"« {s['kworb_title']} » lié à « {s.get('db_title')} »"


def _kworb_autre_morceau(ctx: ContexteAction, cas: Cas) -> str:
    _kworb_links().reject(ctx.artiste.name, cas.preuves["kworb_title"])
    return f"« {cas.preuves['kworb_title']} » : pas ce morceau (mémorisé)"


def _kworb_decision(voie: str):
    def executer(ctx: ContexteAction, cas: Cas) -> str:
        from src.scrapers.spotify_web_scraper import lire_identite_page
        from src.services import kworb_decisions

        proposition = {k: v for k, v in cas.preuves.items() if k not in ("kworb_date", "empreinte")}
        return kworb_decisions.appliquer(
            ctx.data_manager,
            ctx.artiste,
            proposition,
            voie,
            _date_kworb(cas),
            lire_page=lire_identite_page,
            links=_kworb_links(),
        )

    return executer


def _statut_lien(statut: str):
    def executer(ctx: ContexteAction, cas: Cas) -> str:
        nom, kind = cas.preuves["nom"], cas.preuves["kind"]
        if not ctx.data_manager.set_relation_status(ctx.artiste.id, nom, kind, statut):
            raise LookupError(f"lien « {nom} » introuvable")
        return f"« {nom} » {'confirmé' if statut == 'confirmed' else 'refusé'}"

    return executer


def _renvoi(nom: str, *args_de_cas: Callable):
    def executer(ctx: ContexteAction, cas: Cas) -> str:
        ouvrir = ctx.renvois.get(nom)
        if ouvrir is None:
            raise LookupError(f"aucune fenêtre « {nom} » disponible ici")
        ouvrir(*(f(ctx, cas) for f in args_de_cas))
        return ""

    return executer


def _autre_fiche(ctx: ContexteAction, cas: Cas):
    autre = cas.preuves.get("garde") or (cas.preuves.get("autres") or [None])[0]
    if autre not in ctx.tracks:
        raise LookupError("l'autre fiche du doublon n'est plus dans la discographie")
    return ctx.tracks[autre]


# ── Le catalogue ────────────────────────────────────────────────────────────


def _action_retirer_lrc() -> Action:
    return Action(
        "retirer_lrc",
        "✖️ Retirer ce LRC",
        _retirer_lrc,
        "Retirer ce LRC ? Il ne sera plus retenu, même si une source le ressert.",
    )


def actions_pour(cas: Cas) -> list[Action]:
    """Les actions proposées pour un cas (hors « ✓ Normal », toujours là)."""
    d = cas.detecteur
    if d == "video_etrangere":
        return [
            Action(
                "rejeter_video",
                "✖️ Rejeter la vidéo",
                _rejeter_video,
                "Rejeter cette vidéo ? Ses vues ne seront plus comptées pour ce morceau.",
            )
        ]
    if d in _CODES_ID_SPOTIFY and cas.preuves.get("spotify_id"):
        return [
            Action(
                "retirer_spotify",
                "✖️ Retirer l'ID Spotify",
                _retirer_spotify,
                f"Retirer l'ID {cas.preuves['spotify_id']} ? Les streams et mesures qui en "
                "découlent partent avec lui, et il ne sera plus reposé.",
            )
        ]
    if d in _CODES_SONGBPM:
        return [
            Action(
                "retirer_songbpm",
                "✖️ Retirer SongBPM",
                _retirer_songbpm,
                "Retirer BPM, tonalité et durée donnés par SongBPM pour ce morceau ?",
            )
        ]
    if d in ("bpm", "tonalite"):
        sources = sorted((cas.preuves.get("bpm") or cas.preuves.get("tonalites") or {}).keys())
        return [
            Action(
                f"ecarter_{s}",
                f"✖️ Écarter {s}",
                _ecarter_source(s),
                f"Retirer BPM et tonalité mesurés par {s} pour ce morceau ?",
            )
            for s in sources
        ]
    if d in _CODES_DOUBLON:
        fusion = Action(
            "fusionner",
            "🔀 Fusionner…",
            _renvoi("fusion", _fiche, _autre_fiche),
            resout=False,
        )
        if d == "doublon_lrc":
            # Pas le même morceau après examen : c'est alors le LRC qui est faux.
            return [fusion, _action_retirer_lrc()]
        return [fusion]
    if d == "liens_proposes":
        refuser = Action("refuser_lien", "✖️ Refuser", _statut_lien("refused"))
        if cas.preuves.get("kind") == "alias":
            return [Action("confirmer_lien", "✔️ Confirmer", _statut_lien("confirmed")), refuser]
        # Une formation demande sa nature (groupe / collectif) : la fenêtre Groupes.
        return [Action("groupes", "👥 Arbitrer…", _renvoi("groupes"), resout=False), refuser]
    if d in ("lrc_douteux", "lrc_autre_fiche", "lrc_live"):
        return [_action_retirer_lrc()]
    if d in ("certif_date", "certif_autre_titre") and cas.preuves.get("certification"):
        return [
            Action(
                "retirer_certif",
                "✖️ Retirer la certification",
                _retirer_certif,
                "Retirer cette certification de ce morceau ? Elle ne lui sera plus rattachée.",
            )
        ]
    if d == "kworb_a_confirmer":
        return [
            Action("kworb_meme", "✔️ Même morceau", _kworb_meme_morceau),
            Action("kworb_autre", "✖️ Autre morceau", _kworb_autre_morceau),
        ]
    if d == "kworb_proposition":
        from src.services import kworb_decisions

        propose = cas.preuves.get("proposition")
        return [
            Action(
                f"kworb_{v}",
                ("★ " if v == propose else "") + kworb_decisions.LIBELLES[v],
                _kworb_decision(v),
                reseau=v in ("collab", "tiers"),
            )
            for v in kworb_decisions.voies_possibles(cas.preuves)
        ]
    if d.startswith("deezer_") and cas.preuves.get("ecart"):
        libelles = {
            "deezer_link_candidate": "🔗 Rattacher",
            "deezer_link_review": "✂️ Délier",
        }
        libelle = libelles.get(d, "✚ Créer la fiche")
        return [
            Action(
                "deezer_appliquer",
                libelle,
                _deezer_appliquer,
                f"{libelle.split(' ', 1)[1]} « {cas.morceau} » ?",
                reseau=d not in libelles,  # une fiche créée relit la piste chez Deezer
            ),
            Action("ecarts_deezer", "💿 Écarts Deezer…", _renvoi("ecarts_deezer"), resout=False),
        ]
    if d.startswith("deezer_"):
        return [
            Action("ecarts_deezer", "💿 Écarts Deezer…", _renvoi("ecarts_deezer"), resout=False)
        ]
    return []


def executer(action: Action, ctx: ContexteAction, cas: Cas) -> str:
    """Exécute l'action ; si elle RÉSOUT le cas, le signalement éventuel quitte la
    liste sans attendre le prochain run. Lève en cas d'échec (l'appelant l'affiche)."""
    compte_rendu = action.executer(ctx, cas)
    if action.resout:
        ctx.data_manager.retirer_signalement(ctx.artiste.id, cas.detecteur, cas.cle)
        logger.info(f"À trancher — {action.code} : {compte_rendu}")
    return compte_rendu
