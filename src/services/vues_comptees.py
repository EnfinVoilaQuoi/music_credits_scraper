"""Quelles vidéos comptent leurs vues dans les streams YouTube Music d'une fiche
(décision utilisateur 2026-10-10).

Le lien posé sur la page Genius est une provenance PROTÉGÉE, mais ses
contributeurs y mettent parfois la vidéo où le morceau a été MONTRÉ, pas une
publication du morceau : la pub G-SHOCK (2,8 M de vues) liée au « Skit - Limits »
de Médine, dont la vraie piste fait 52 000 vues ; le podcast où Kanye dévoile
« NORTH'S SIDE » ; le sketch où OrelSan joue « Batfreestyle ». Mesuré : 205
liens Genius sur 6 250 ne nomment pas leur morceau, 258 M de vues.

Règle : une vidéo liée par Genius qui ne NOMME pas le morceau (le juge du
détecteur 🎬, `revue.video_nomme`) reste affichée, mais ses vues ne comptent pas
— sauf validation humaine : ✔️ dans la fiche (le lien passe en `manual`, une
autre provenance que celle-ci) ou « ✓ Normal » dans le panneau (verdict mémorisé
sur le cas 🎬 de cette vidéo). Les autres provenances ne sont pas concernées :
la chaîne de l'artiste et notre recherche (seuil 0,90) n'en produisent presque
pas (11 et 2, surtout des graphies — « BLKKK SKKKN HEAD »).
"""

from __future__ import annotations

from types import SimpleNamespace

from src.services.revue import cle_du_cas, video_nomme

DETECTEUR = "video_etrangere"


def cles_validees(dm, artist_id: int) -> set[str]:
    """Les cas 🎬 marqués « ✓ Normal » pour cet artiste."""
    return {cle for (det, cle) in dm.verdicts_revue(artist_id) if det == DETECTEUR}


def vue_comptee(fiche, video_id: str, titre: str | None, source: str | None, valides) -> bool:
    if source != "genius_media" or not titre:
        return True  # titre inconnu : on ne conclut pas
    video = SimpleNamespace(title=titre, source=source)
    if video_nomme(fiche, video):
        return True
    return cle_du_cas(fiche, {"video_id": video_id, "titre": titre}) in valides


def videos_exclues(fiche, videos, valides) -> set[str]:
    """`video_id` des vidéos de `fiche` dont les vues ne comptent pas.
    `videos` : objets à `video_id`, `title`, `source` (`TrackVideo`)."""
    return {
        v.video_id
        for v in videos
        if v.video_id and not vue_comptee(fiche, v.video_id, v.title, v.source, valides)
    }


def apres_verdict(dm, cas) -> None:
    """« ✓ Normal » (ou son annulation) sur un cas 🎬 change ce qui compte : le
    total de la fiche suit tout de suite, sans attendre le prochain run YTM.
    Seulement si la vidéo a des vues mesurées — sinon rien ne change, et un
    recalcul viderait un total ancien qu'aucune vidéo mesurée n'explique."""
    if cas.detecteur == DETECTEUR and cas.track_id and cas.preuves.get("impact"):
        dm.recalculer_vues_ytm(cas.track_id)
