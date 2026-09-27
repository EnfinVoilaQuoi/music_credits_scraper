"""Panneau « À trancher » — niveau FORMEL : les corrections faites sans attendre.

Trois niveaux de confiance (WIP, idée utilisateur du 2026-09-26) : la preuve
FORMELLE corrige seule, l'ambigu attend l'utilisateur, le reste est sain. Un
détecteur n'est formel (`Detecteur.formel`) qu'après mesure sur les vingt-cinq
artistes de la base (2026-09-27) :

- `lrc_autre_fiche` — le LRC colle à ≥ 60 % des paires de mots d'une AUTRE
  fiche et à < 40 % des siennes (démos et références qui avaient reçu le LRC
  de l'original) ;
- `certif_autre_titre` — une certification d'un autre titre, datée avant la
  sortie (18 sur 21, toutes fausses) ;
- `songbpm_original` — une autre prise sans plateforme (démo, référence, live
  inédit, remix) aux durée ET tempo SongBPM de son original (2026-09-28).

Chaque correction passe par l'écrivain qui existe déjà (`retirer_lrc`,
`record_certifications`), pose la MÉMOIRE de refus que relisent les
producteurs (sans elle, le run suivant reposerait la donnée) et est consignée
au journal `revue_corrections` avec de quoi la DÉFAIRE. Rétablir une correction
marque le cas « normal » : la passe suivante ne la refait pas.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.services.revue import CODES_FORMELS, Cas, Revue, analyser
from src.utils.logger import get_logger

logger = get_logger(__name__)

#: Une fiche ne montre qu'un cas par détecteur à la fois (deux certifs d'autres
#: titres : la seconde apparaît une fois la première retirée).
_PASSES_MAX = 25


@dataclass
class BilanCorrections:
    appliquees: list[str] = field(default_factory=list)
    echecs: list[str] = field(default_factory=list)

    def resume(self) -> str:
        if not self.appliquees and not self.echecs:
            return ""
        texte = f"⚡ {len(self.appliquees)} correction(s) automatique(s) (preuve formelle)"
        if self.echecs:
            texte += f", {len(self.echecs)} en échec"
        return texte + " — détail et « ↩ Rétablir » dans « À trancher »."


def cas_surs(revue: Revue) -> list[Cas]:
    return [c for c in revue.actifs if c.detecteur in CODES_FORMELS]


# ── Les corrections ─────────────────────────────────────────────────────────


def _corriger_lrc(dm, track, cas: Cas) -> tuple[str, dict]:
    from src.utils.corrections_fiches import memoriser_lrc_refuse

    lrc = track.lyrics.synced
    if not lrc:
        raise LookupError("la fiche n'a plus de LRC")
    # Mémorisé d'abord : le résolveur ne le retiendra plus, quelle que soit la
    # source qui le resservirait.
    memoriser_lrc_refuse(track, lrc)
    retirees: list[dict] = []
    dm.retirer_lrc(track.id, lrc, retirees=retirees)
    track.lyrics.synced = None
    track.lyrics.synced_source = None
    return (
        f"LRC retiré de « {track.title} » — {cas.motif}",
        {"type": "lrc", "lrc": lrc, "observations": retirees},
    )


def _corriger_certif(dm, track, cas: Cas) -> tuple[str, dict]:
    from src.utils.corrections_fiches import cle_certif, memoriser_certif_refusee

    entree = cas.preuves["certification"]
    # Mémorisée d'abord : `apply_certifications` ne la rattachera plus.
    memoriser_certif_refusee(track, entree)
    cle = cle_certif(entree)
    track.certs.entries = [e for e in track.certs.entries if cle_certif(e) != cle]
    dm.record_certifications(track.id, track.certs.entries, track.certs.album_entries)
    return (
        f"{entree.get('body')} {entree.get('certification')} de « {entree.get('title')} » "
        f"retirée de « {track.title} »",
        {"type": "certif", "entree": entree},
    )


def _corriger_songbpm(dm, track, cas: Cas) -> tuple[str, dict]:
    retirees: list[dict] = []
    n = dm.retirer_mesures_songbpm(track.id, retirees=retirees)
    if not n:
        raise LookupError("plus aucune mesure SongBPM sur la fiche")
    return (
        f"{n} mesure(s) SongBPM retirée(s) de « {track.title} » — {cas.motif}",
        {"type": "observations", "observations": retirees},
    )


_CORRECTEURS = {
    "lrc_autre_fiche": ("retirer_lrc", _corriger_lrc),
    "certif_autre_titre": ("retirer_certif", _corriger_certif),
    "songbpm_original": ("retirer_songbpm", _corriger_songbpm),
}


def corriger(dm, artiste) -> BilanCorrections:
    """Applique toutes les corrections à preuve formelle de l'artiste (sa
    discographie chargée) et les consigne. Idempotent : une correction faite
    ne se représente plus (la donnée est partie, la mémoire la garde partie)."""
    bilan = BilanCorrections()
    tracks = {t.id: t for t in artiste.tracks or []}
    deja: set[tuple[str, str]] = set()
    for _ in range(_PASSES_MAX):
        nouveaux = [c for c in cas_surs(analyser(dm, artiste)) if (c.detecteur, c.cle) not in deja]
        if not nouveaux:
            break
        for cas in nouveaux:
            deja.add((cas.detecteur, cas.cle))
            track = tracks.get(cas.track_id)
            action, corriger_cas = _CORRECTEURS[cas.detecteur]
            if track is None:
                bilan.echecs.append(f"{cas.morceau} : fiche introuvable")
                continue
            try:
                compte_rendu, annulation = corriger_cas(dm, track, cas)
            except (LookupError, KeyError) as e:
                bilan.echecs.append(f"{cas.morceau} : {e}")
                continue
            dm.journaliser_correction(artiste.id, cas, action, compte_rendu, annulation)
            dm.retirer_signalement(artiste.id, cas.detecteur, cas.cle)
            bilan.appliquees.append(compte_rendu)
            logger.info(f"À trancher — correction automatique : {compte_rendu}")
    return bilan


def corriger_apres_run(dm, artiste) -> BilanCorrections:
    """Point d'accroche des runs : ne lève JAMAIS — une correction qui échoue ne
    rend pas incomplet le run qui l'a précédée."""
    try:
        return corriger(dm, artiste)
    except Exception:  # noqa: BLE001 - dernier ressort, trace complète
        logger.exception(f"Corrections automatiques en échec pour {artiste.name}")
        return BilanCorrections(echecs=["exception (voir le log)"])


# ── Rétablir ────────────────────────────────────────────────────────────────


def retablir(dm, artiste, correction: dict) -> str:
    """Défait une correction du journal et marque son cas « normal » (sinon la
    passe suivante la referait). Lève si la fiche n'est plus là."""
    from src.utils.corrections_fiches import oublier_certif_refusee, oublier_lrc_refuse

    track = next((t for t in artiste.tracks or [] if t.id == correction["track_id"]), None)
    if track is None:
        raise LookupError("la fiche n'est plus dans la discographie chargée")
    annulation = correction["annulation"]
    if annulation.get("type") == "lrc":
        lrc = annulation["lrc"]
        oublier_lrc_refuse(track, lrc)
        dm.restaurer_observations(annulation.get("observations") or [])
        # Même arbitrage que partout : la colonne suit les observations.
        for tid in {o["track_id"] for o in annulation.get("observations") or []} | {track.id}:
            dm.reverifier_lrc(tid)
        track.lyrics.synced = lrc
        compte_rendu = f"LRC rétabli sur « {track.title} »"
    elif annulation.get("type") == "certif":
        entree = annulation["entree"]
        oublier_certif_refusee(track, entree)
        track.certs.entries = [*track.certs.entries, entree]
        dm.record_certifications(track.id, track.certs.entries, track.certs.album_entries)
        compte_rendu = (
            f"{entree.get('body')} {entree.get('certification')} rétablie sur « {track.title} »"
        )
    elif annulation.get("type") == "observations":
        n = dm.restaurer_observations(annulation.get("observations") or [])
        compte_rendu = f"{n} mesure(s) rétablie(s) sur « {track.title} »"
    else:
        raise ValueError(f"correction de type inconnu : {annulation.get('type')!r}")
    dm.marquer_correction_retablie(correction["id"])
    dm.trancher_cas(
        artiste.id,
        correction["detecteur"],
        correction["cle"],
        morceau=correction["morceau"],
        motif=correction["motif"],
        note="correction automatique rétablie",
    )
    logger.info(f"À trancher — {compte_rendu}")
    return compte_rendu
