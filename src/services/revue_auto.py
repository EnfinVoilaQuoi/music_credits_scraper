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

S'y ajoutent les POLITIQUES d'arbitrage validées par l'utilisateur
(`politiques_revue.REGLES_VALIDEES`, 2026-10-20 : durées) : leur cas se
corrige comme un cas formel (retrait SongBPM, retrait de l'ID Spotify d'un
autre enregistrement) ou se marque « ✓ Normal » PAR RÈGLE — note
`regle:<détecteur>`, distincte d'un verdict humain. Interrupteur :
`REGLES_BRANCHEES`, levé le 2026-10-20 après accord sur le passage à blanc.

Chaque correction passe par l'écrivain qui existe déjà (`retirer_lrc`,
`record_certifications`), pose la MÉMOIRE de refus que relisent les
producteurs (sans elle, le run suivant reposerait la donnée) et est consignée
au journal `revue_corrections` avec de quoi la DÉFAIRE. Rétablir une correction
marque le cas « normal » : la passe suivante ne la refait pas.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass, field

from src.services.revue import (
    CODES_FORMELS,
    DECISION,
    Cas,
    Revue,
    analyser,
    charger_contexte,
)
from src.utils.logger import etape_de_flux, get_logger

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


#: Les politiques VALIDÉES (`politiques_revue.REGLES_VALIDEES`) ne
#: s'appliquent qu'interrupteur levé — levé le 2026-10-20, l'utilisateur ayant
#: validé les 36 corrections du passage à blanc (« Je valide pour les 36
#: corrections », `scripts/simuler_regles_validees.py`).
REGLES_BRANCHEES = True

#: Famille « ID Spotify proposé » (`politiques_revue.REGLES_ID_PROPOSE`) :
#: levé le 2026-10-20, l'utilisateur ayant validé les 46 corrections du passage
#: à blanc restreint (« Je valide les 46 »).
REGLES_ID_PROPOSE_BRANCHEES = True

#: Famille « écarts Deezer » (`politiques_revue.REGLES_DEEZER`) : levé le
#: 2026-10-21, l'utilisateur ayant validé les 45 corrections du passage à blanc.
REGLES_DEEZER_BRANCHEES = True

#: Famille « liens Deezer » (délier, `politiques_revue.REGLES_LIENS_DEEZER`) :
#: BAISSÉ tant que l'utilisateur n'a pas validé son passage à blanc.
REGLES_LIENS_DEEZER_BRANCHEES = False

#: Famille « ID Spotify porté par deux fiches » (`politiques_revue.REGLES_ID_PARTAGE`) :
#: levé le 2026-10-26 — simulée sur toute la base (2 cas, tranchés comme
#: l'utilisateur), qui ne veut plus vérifier « des trucs qu'on sait déjà ».
REGLES_ID_PARTAGE_BRANCHEES = True

#: Familles BPM, tonalité, LRC, alias et liens Deezer à confirmer
#: (`politiques_revue.REGLES_ETENDUES`) : levé le 2026-10-26, chaque politique
#: simulée sur toute la base et confrontée à un témoin indépendant.
REGLES_ETENDUES_BRANCHEES = True

#: Actions qui n'ont pas de fiche à elles : la piste Deezer « absente » n'est
#: encore sur aucune.
_SANS_FICHE = frozenset(
    {"deezer_appliquer", "deezer_rattacher_isrc", "confirmer_lien", "kworb_tiers"}
)

#: Correcteurs qui reçoivent l'artiste (ils rejouent l'action du panneau).
_AVEC_ARTISTE = _SANS_FICHE | {"delier_deezer"}

#: Préfixe de la note d'un « ✓ Normal » posé par une règle (et de l'origine
#: d'un refus) — un humain n'en pose jamais.
PREFIXE_REGLE = "regle:"


def cas_surs(revue: Revue) -> list[Cas]:
    return [c for c in revue.actifs if c.detecteur in CODES_FORMELS]


def contexte_deezer(dm, artiste) -> tuple[dict, set]:
    """`({ISRC: titre} de toute la base, ID Deezer de l'artiste et de ses
    formations)` — ce que la politique des écarts Deezer consulte."""
    from sqlalchemy import text

    with dm.engine.connect() as conn:
        isrcs = {
            str(i).upper(): t
            for i, t in conn.execute(
                text("SELECT isrc, title FROM tracks WHERE isrc IS NOT NULL")
            ).all()
        }
    ids = {artiste.deezer_id} | {
        t.artist.deezer_id for t in artiste.tracks or [] if t.artist and t.artist.deezer_id
    }
    return isrcs, {int(i) for i in ids if i}


def cas_par_regle(
    revue: Revue,
    tracks: dict,
    ctx,
    *,
    durees: bool = True,
    id_propose: bool = False,
    deezer: tuple | None = None,
    liens_deezer: bool = False,
    id_partage: bool = False,
    etendues: bool = False,
) -> list[tuple[Cas, object]]:
    """`[(cas, arbitrage)]` que tranche une règle (décisions seules) : les
    durées VALIDÉES, et la famille « ID Spotify proposé » si demandée."""
    from src.services.politiques_revue import (
        arbitrage_deezer,
        arbitrage_etendu,
        arbitrage_id_partage,
        arbitrage_id_propose,
        arbitrage_liens_deezer,
        arbitrage_valide,
    )

    sortie = []
    for c in revue.actifs:
        if c.nature != DECISION or c.detecteur in CODES_FORMELS:
            continue
        track = tracks.get(c.track_id)
        arbitrage = None
        if durees:
            arbitrage = arbitrage_valide(c, track, ctx)
        if arbitrage is None and id_propose:
            arbitrage = arbitrage_id_propose(c, track, ctx)
        if arbitrage is None and deezer is not None:
            arbitrage = arbitrage_deezer(c, track, ctx, *deezer)
        if arbitrage is None and liens_deezer:
            arbitrage = arbitrage_liens_deezer(c, track, ctx)
        if arbitrage is None and id_partage:
            arbitrage = arbitrage_id_partage(c, track, ctx)
        if arbitrage is None and etendues:
            arbitrage = arbitrage_etendu(c, track, ctx)
        if arbitrage is not None:
            sortie.append((c, arbitrage))
    return sortie


# ── Les corrections ─────────────────────────────────────────────────────────


def _corriger_lrc(dm, track, cas: Cas) -> tuple[str, dict]:
    from src.utils.corrections_fiches import memoriser_lrc_refuse

    lrc = track.lyrics.synced
    if not lrc:
        raise LookupError("la fiche n'a plus de LRC")
    # Mémorisé d'abord : le résolveur ne le retiendra plus, quelle que soit la
    # source qui le resservirait.
    memoriser_lrc_refuse(track, lrc, origine=f"regle:{cas.detecteur}")
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
    memoriser_certif_refusee(track, entree, origine=f"regle:{cas.detecteur}")
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


def _rendus_a_l_humain(dm, artist_id: int) -> set[tuple[str, str]]:
    """Les cas dont une correction PAR RÈGLE a été défaite (« ↩ Rétablir ») :
    l'humain a dit non, la règle ne les reprend plus."""
    return {
        (c["detecteur"], c["cle"])
        for c in dm.corrections_revue(artist_id, retablies=True)
        if c.get("retablie_at") and str(c.get("action") or "").startswith(PREFIXE_REGLE)
    }


def _corriger_spotify(dm, track, cas: Cas) -> tuple[str, dict]:
    from src.utils.corrections_fiches import memoriser_id_refuse
    from src.utils.spotify_audit import rejeter_spotify_id

    sid = cas.preuves.get("spotify_id")
    if not sid or sid not in {track.spotify_id, *(track.spotify_ids or [])}:
        raise LookupError("la fiche ne porte plus cet ID Spotify")
    from src.utils.track_repository import TrackRepository

    # Le rapport du retrait ne garde que (champ, source) : les VALEURS, de quoi
    # rétablir, se lisent avant.
    liees = TrackRepository.SOURCES_LIEES_A_L_ID_SPOTIFY
    retirees = [
        {
            "track_id": track.id,
            "field": o.field,
            "source": o.source,
            "value": o.value,
            "confidence": o.confidence,
            "seen_at": str(o.seen_at) if o.seen_at else None,
        }
        for o in dm.get_artist_observations(track.artist.id).get(track.id, [])
        if o.source in liees
    ]
    # Mémorisé d'abord, avec son origine : le gate ne le reposera plus.
    memoriser_id_refuse(track, sid, origine=f"{PREFIXE_REGLE}{cas.detecteur}")
    rejeter_spotify_id(dm, track, sid)
    return (
        f"ID Spotify {sid} retiré de « {track.title} » — {cas.motif}",
        {"type": "spotify", "spotify_id": sid, "observations": retirees},
    )


def _marquer_normal(dm, artist_id: int, cas: Cas) -> tuple[str, dict]:
    dm.trancher_cas(
        artist_id,
        cas.detecteur,
        cas.cle,
        morceau=cas.morceau,
        motif=cas.motif,
        note=f"{PREFIXE_REGLE}{cas.detecteur}",
    )
    return (f"« {cas.morceau} » marqué normal par règle — {cas.motif}", {"type": "verdict"})


def _poser_id(dm, track, cas: Cas) -> tuple[str, dict]:
    from src.utils.spotify_identity import valider_identite

    sid = cas.preuves["spotify_id_propose"]
    if track.spotify_id:
        raise LookupError("la fiche porte déjà un ID Spotify")
    # Revalidé AU MOMENT D'ÉCRIRE (gate strict) : tout site qui pose un ID passe
    # par le gate (`TestToutProducteurPasseParLeGate`), la politique n'en tient pas lieu.
    if not valider_identite(track, sid, lire_album=lambda _sid: None):
        raise LookupError(f"l'ID {sid} ne passe plus le contrôle d'identité")
    if not dm.update_track_spotify_id(track.id, sid, source=cas.preuves.get("source") or "regle"):
        raise LookupError(f"l'ID {sid} n'a pas pu être posé")
    track.spotify_id = sid
    return (
        f"ID Spotify {sid} posé sur « {track.title} »",
        {"type": "pose_spotify", "spotify_id": sid},
    )


def _refuser_id(dm, track, cas: Cas) -> tuple[str, dict]:
    from src.utils.corrections_fiches import memoriser_id_refuse

    sid = cas.preuves["spotify_id_propose"]
    memoriser_id_refuse(track, sid, origine=f"{PREFIXE_REGLE}{cas.detecteur}")
    return (
        f"ID Spotify {sid} refusé pour « {track.title} »",
        {"type": "refus_spotify", "spotify_id": sid},
    )


def _confirmer_lien(_dm, _track, cas: Cas, *, artiste) -> tuple[str, dict]:
    """Confirme un alias proposé — l'action du panneau ; rétablir le remet
    « proposé »."""
    from src.services import revue_actions

    ctx = _contexte_action(_dm, artiste)
    compte_rendu = revue_actions._statut_lien("confirmed")(ctx, cas)
    return compte_rendu, {
        "type": "lien",
        "nom": cas.preuves["nom"],
        "kind": cas.preuves["kind"],
    }


def _kworb_tiers(dm, _track, cas: Cas, *, artiste) -> tuple[str, dict]:
    """Crée la fiche d'un remix par un tiers — la décision du panneau, telle
    quelle (`kworb_decisions.appliquer`, page Spotify lue). Rétablir supprime
    la fiche créée et rend la ligne à la proposition."""
    from src.services import revue_actions
    from src.utils.title_matching import normalize_title

    ctx = _contexte_action(dm, artiste)
    compte_rendu = revue_actions._kworb_decision("tiers")(ctx, cas)
    titre = cas.preuves["kworb_title"]
    decision = (
        revue_actions._kworb_links().load(artiste.name)["decisions"].get(normalize_title(titre), {})
    )
    return compte_rendu, {
        "type": "fiche_kworb",
        "kworb_title": titre,
        "creee": decision.get("track_id"),
    }


def _contexte_action(dm, artiste):
    from src.services import revue_actions

    tracks = {t.id: t for t in artiste.tracks or []}
    return revue_actions.ContexteAction(dm, artiste, tracks, renvois={})


def _deezer_appliquer(dm, _track, cas: Cas, *, artiste) -> tuple[str, dict]:
    """Crée la fiche d'une piste « absente », ou rattache une parution à
    confirmer — l'action du panneau, telle quelle (`creer_lignes`)."""
    from src.services import revue_actions

    ctx = _contexte_action(dm, artiste)
    piste = cas.preuves["ecart"]["piste"]
    compte_rendu = revue_actions._deezer_appliquer(ctx, cas)
    if cas.detecteur == "deezer_link_candidate":
        return compte_rendu, {
            "type": "lien_deezer",
            "track_id": cas.track_id,
            "ecart": cas.preuves["ecart"],
        }
    return compte_rendu, {"type": "fiche_deezer", "deezer_id": piste.get("id")}


def _deezer_rattacher_isrc(dm, _track, cas: Cas, *, artiste) -> tuple[str, dict]:
    from src.services import revue_actions

    ctx = _contexte_action(dm, artiste)
    isrc = str(cas.preuves["ecart"]["piste"].get("isrc") or "").upper()
    fiche = next((t for t in ctx.tracks.values() if t.isrc and str(t.isrc).upper() == isrc), None)
    compte_rendu = revue_actions._deezer_rattacher_isrc(ctx, cas)
    return compte_rendu, {
        "type": "lien_deezer",
        "track_id": fiche.id if fiche else None,
        "ecart": cas.preuves["ecart"],
    }


def _delier_deezer(dm, track, cas: Cas, *, artiste) -> tuple[str, dict]:
    """Délie l'ID Deezer (`duree_deezer`) ou la parution à revoir
    (`deezer_link_review`) — l'action du panneau ; les observations Deezer de
    la fiche sont lues AVANT, de quoi rétablir."""
    from src.services import revue_actions

    did = cas.preuves.get("deezer_id") or cas.preuves.get("piste_deezer")
    if not did:
        raise LookupError("aucun ID Deezer à délier")
    retirees = [
        {
            "track_id": track.id,
            "field": o.field,
            "source": o.source,
            "value": o.value,
            "confidence": o.confidence,
            "seen_at": str(o.seen_at) if o.seen_at else None,
        }
        for o in dm.get_artist_observations(track.artist.id).get(track.id, [])
        if o.source == "deezer"
    ]
    ctx = _contexte_action(dm, artiste)
    if cas.detecteur == "duree_deezer":
        compte_rendu = revue_actions._delier_deezer(ctx, cas)
    else:
        compte_rendu = revue_actions._deezer_appliquer(ctx, cas)
    # Le geste du panneau mémorise le refus sans origine : on le réinscrit
    # comme refus d'une RÈGLE, distinct d'un refus humain.
    from src.utils.corrections_fiches import memoriser_deezer_refuse, oublier_deezer_refuse

    oublier_deezer_refuse(track, int(did))
    memoriser_deezer_refuse(track, int(did), origine=f"{PREFIXE_REGLE}{cas.detecteur}")
    return compte_rendu, {
        "type": "delie_deezer",
        "deezer_id": int(did),
        "avait_l_id": track.deezer_id is not None and int(track.deezer_id) == int(did),
        "observations": retirees,
        "ecart": cas.preuves.get("ecart"),
    }


#: Correcteurs des RÈGLES, par action de la politique.
_CORRECTEURS_DE_REGLE = {
    "retirer_lrc": _corriger_lrc,
    "confirmer_lien": _confirmer_lien,
    "kworb_tiers": _kworb_tiers,
    "delier_deezer": _delier_deezer,
    "deezer_appliquer": _deezer_appliquer,
    "deezer_rattacher_isrc": _deezer_rattacher_isrc,
    "retirer_songbpm": _corriger_songbpm,
    "retirer_spotify": _corriger_spotify,
    "poser_id": _poser_id,
    "refuser_id": _refuser_id,
}

_CORRECTEURS = {
    "lrc_autre_fiche": ("retirer_lrc", _corriger_lrc),
    "certif_autre_titre": ("retirer_certif", _corriger_certif),
    "songbpm_original": ("retirer_songbpm", _corriger_songbpm),
}


@etape_de_flux(
    "Corrections automatiques",
    verdict=lambda r: (not r.echecs, f"{len(r.echecs)} échec(s)"),
)
def corriger(dm, artiste) -> BilanCorrections:
    """Applique toutes les corrections à preuve formelle de l'artiste (sa
    discographie chargée) et les consigne. Idempotent : une correction faite
    ne se représente plus (la donnée est partie, la mémoire la garde partie)."""
    bilan = BilanCorrections()
    tracks = {t.id: t for t in artiste.tracks or []}
    deja: set[tuple[str, str]] = set()
    for _ in range(_PASSES_MAX):
        ctx = charger_contexte(dm, artiste)
        revue = analyser(dm, artiste, ctx)
        a_faire = [(c, None) for c in cas_surs(revue)]
        if (
            REGLES_BRANCHEES
            or REGLES_ID_PROPOSE_BRANCHEES
            or REGLES_DEEZER_BRANCHEES
            or REGLES_LIENS_DEEZER_BRANCHEES
            or REGLES_ID_PARTAGE_BRANCHEES
            or REGLES_ETENDUES_BRANCHEES
        ):
            rendus = _rendus_a_l_humain(dm, artiste.id)
            a_faire += [
                (c, a)
                for c, a in cas_par_regle(
                    revue,
                    tracks,
                    ctx,
                    durees=REGLES_BRANCHEES,
                    id_propose=REGLES_ID_PROPOSE_BRANCHEES,
                    deezer=contexte_deezer(dm, artiste) if REGLES_DEEZER_BRANCHEES else None,
                    liens_deezer=REGLES_LIENS_DEEZER_BRANCHEES,
                    id_partage=REGLES_ID_PARTAGE_BRANCHEES,
                    etendues=REGLES_ETENDUES_BRANCHEES,
                )
                if (c.detecteur, c.cle) not in rendus
            ]
        nouveaux = [(c, a) for c, a in a_faire if (c.detecteur, c.cle) not in deja]
        if not nouveaux:
            break
        for cas, arbitrage in nouveaux:
            deja.add((cas.detecteur, cas.cle))
            track = tracks.get(cas.track_id)
            if arbitrage is None:
                action, corriger_cas = _CORRECTEURS[cas.detecteur]
            elif arbitrage.action == "normal":
                action = f"{PREFIXE_REGLE}normal"

                def corriger_cas(_dm, _track, c):
                    return _marquer_normal(_dm, artiste.id, c)

            else:
                action = f"{PREFIXE_REGLE}{arbitrage.action}"
                correcteur = _CORRECTEURS_DE_REGLE[arbitrage.action]
                if arbitrage.action in _AVEC_ARTISTE:
                    correcteur = functools.partial(correcteur, artiste=artiste)

                def corriger_cas(_dm, _track, c, f=correcteur, arb=arbitrage):
                    compte_rendu, annulation = f(_dm, _track, c)
                    return f"{compte_rendu} [{arb.raison}]", annulation

            sans_fiche = arbitrage is not None and arbitrage.action in _SANS_FICHE
            if track is None and not sans_fiche:
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
    rend pas incomplet le run qui l'a précédée.

    D'abord les portiers dont la RÈGLE a changé depuis leur passage sur
    l'artiste sont rejoués (`regles`, 2026-10-12) : une règle changée dans le
    code n'écrit rien, rien d'autre ne la ferait repartir."""
    try:
        from src.services import regles

        for compte_rendu in regles.rejouer_perimes(dm, artiste):
            logger.info(f"🔁 {compte_rendu} ({artiste.name})")
    except Exception:  # noqa: BLE001 - dernier ressort, trace complète
        logger.exception(f"Rejeu des règles changées en échec pour {artiste.name}")
    try:
        return corriger(dm, artiste)
    except Exception:  # noqa: BLE001 - dernier ressort, trace complète
        logger.exception(f"Corrections automatiques en échec pour {artiste.name}")
        return BilanCorrections(echecs=["exception (voir le log)"])


# ── Rétablir ────────────────────────────────────────────────────────────────


def retablir(dm, artiste, correction: dict) -> str:
    """Défait une correction du journal et marque son cas « normal » (sinon la
    passe suivante la referait). Lève si la fiche n'est plus là."""
    from src.utils.corrections_fiches import (
        oublier_certif_refusee,
        oublier_id_refuse,
        oublier_lrc_refuse,
    )

    annulation = correction["annulation"]
    track = next((t for t in artiste.tracks or [] if t.id == correction["track_id"]), None)
    # Une piste Deezer « absente » n'avait pas de fiche : l'annulation la retrouve.
    if track is None and annulation.get("type") not in (
        "fiche_deezer",
        "lien_deezer",
        "lien",
        "fiche_kworb",
    ):
        raise LookupError("la fiche n'est plus dans la discographie chargée")
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
    elif annulation.get("type") == "spotify":
        sid = annulation["spotify_id"]
        oublier_id_refuse(track, sid)
        dm.update_track_spotify_id(track.id, sid, source="retabli")
        dm.restaurer_observations(annulation.get("observations") or [])
        compte_rendu = f"ID Spotify {sid} rétabli sur « {track.title} »"
    elif annulation.get("type") == "pose_spotify":
        from src.utils.spotify_audit import rejeter_spotify_id

        rejeter_spotify_id(dm, track, annulation["spotify_id"])
        compte_rendu = f"ID Spotify {annulation['spotify_id']} retiré de « {track.title} »"
    elif annulation.get("type") == "refus_spotify":
        oublier_id_refuse(track, annulation["spotify_id"])
        compte_rendu = f"refus de l'ID {annulation['spotify_id']} levé pour « {track.title} »"
    elif annulation.get("type") == "fiche_deezer":
        creee = next(
            (
                t
                for t in dm.get_artist_tracks(artiste.id)
                if t.deezer_id and int(t.deezer_id) == int(annulation["deezer_id"])
            ),
            None,
        )
        if creee is None:
            raise LookupError("la fiche créée n'est plus là")
        dm.delete_track(creee.id)
        compte_rendu = f"fiche « {creee.title} » (créée par règle) supprimée"
    elif annulation.get("type") == "lien_deezer":
        from src.services import ecarts_deezer

        fiche = next((t for t in artiste.tracks or [] if t.id == annulation.get("track_id")), None)
        if fiche is None:
            raise LookupError("la fiche rattachée n'est plus dans la discographie")
        e = ecarts_deezer.ecart_depuis(annulation["ecart"], {fiche.id: fiche})
        e.nature, e.existing_track = "link_review", fiche
        compte_rendu = ecarts_deezer.delier(dm, artiste, e)
    elif annulation.get("type") == "delie_deezer":
        from src.utils.corrections_fiches import oublier_deezer_refuse

        did = annulation["deezer_id"]
        oublier_deezer_refuse(track, did)
        if annulation.get("ecart"):
            # La parution revient (`creer_lignes`, nature « link ») : elle
            # renseigne aussi la fiche (ID, ISRC, durée) comme au premier lien.
            from src.services import ecarts_deezer

            e = ecarts_deezer.ecart_depuis(annulation["ecart"], {track.id: track})
            e.nature, e.existing_track, e.matched_by = "link", track, "retabli"
            ecarts_deezer.creer_lignes(dm, artiste, [e])
        elif annulation.get("avait_l_id"):
            dm.fill_track_identities(track.id, deezer_id=did)
        dm.restaurer_observations(annulation.get("observations") or [])
        compte_rendu = f"lien Deezer {did} rétabli sur « {track.title} »"
    elif annulation.get("type") == "fiche_kworb":
        from src.services import revue_actions

        if annulation.get("creee"):
            dm.delete_track(annulation["creee"])
        revue_actions._kworb_links().oublier_decision(artiste.name, annulation["kworb_title"])
        compte_rendu = f"« {annulation['kworb_title']} » : fiche retirée, proposition rendue"
    elif annulation.get("type") == "lien":
        if not dm.set_relation_status(
            artiste.id, annulation["nom"], annulation["kind"], "proposed"
        ):
            raise LookupError(f"lien « {annulation['nom']} » introuvable")
        compte_rendu = f"« {annulation['nom']} » rendu « proposé »"
    elif annulation.get("type") == "verdict":
        # Défaire le « ✓ Normal » d'une règle, c'est RENDRE le cas à l'humain :
        # le verdict part, rien ne le remplace, et la règle ne le reprend plus
        # (`_rendus_a_l_humain` lit le journal des corrections rétablies).
        dm.annuler_verdict(artiste.id, correction["detecteur"], correction["cle"])
        dm.marquer_correction_retablie(correction["id"])
        compte_rendu = f"« {correction['morceau']} » rendu à l'humain (règle défaite)"
        logger.info(f"À trancher — {compte_rendu}")
        return compte_rendu
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
