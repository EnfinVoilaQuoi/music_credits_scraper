"""Pages Genius SUPPRIMÉES : constat, désactivation, retour (2026-11-23).

Genius supprime des pages : le détail d'API répond 403 (`genius_api`,
constat daté) et la page web sert « Oops! Page not found »
(`genius_scraper_v3.page_introuvable`). Le crawl est une RÉPONSE : l'étape
crédits pose un constat daté (`runtime.ABSENCE_PAGE_GENIUS`, par donnée :
il ne fait pas taire les paroles d'ailleurs), sans rien effacer, et la page
n'est plus relue avant qu'il expire (`settings.delai_absence_jours`).

Trois règles, sur le modèle des pages vides (`services.pages_vides`) :

  · en fin de CYCLE, une page constatée supprimée qui n'a AUCUN identifiant
    de plateforme (ni Deezer, ni Spotify, ni Apple) est désactivée, motif
    `page_supprimee`, origine `regle:page_supprimee` ; réactivée à la main,
    elle ne l'est plus (`load_reactivees_a_la_main`) ;
  · en fin d'étape DISCOGRAPHIE, une fiche désactivée à ce motif dont le
    détail Genius répond de nouveau (200, un morceau) est réactivée et son
    constat levé — l'étape crédits relira la page, et le constat reviendra si
    elle est toujours introuvable ; une désactivation faite à la main (sans
    motif) n'est jamais levée ;
  · pendant la DISCOGRAPHIE, une page NOUVELLE (ID inconnu) au même titre
    qu'UNE seule fiche dont la page est supprimée (désactivée à ce motif, ou
    constat récent) est la page recréée par Genius : elle se rattache à cette
    fiche (`rattacher_page_genius`, qui garde l'historique, les refus, les
    éditions et les identifiants de plateforme ; ce qui venait de l'ancienne
    page — crédits Genius, paroles Genius, anecdote — est à relire) et la fiche
    est réactivée. Plusieurs fiches, ou un titre seulement proche : on ne
    devine pas, la page crée sa fiche comme avant et la paire est journalisée.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, datetime

from src.utils.logger import get_logger

logger = get_logger(__name__)

MOTIF_PAGE_SUPPRIMEE = "page_supprimee"
ORIGINE_REGLE = f"regle:{MOTIF_PAGE_SUPPRIMEE}"


# ── Le constat ───────────────────────────────────────────────────────────────


def pages_constatees(runtime) -> set[int]:
    """Les fiches dont la page Genius a été constatée supprimée dans le délai
    des absences (`runtime.absences_recentes`)."""
    from src.services import runtime as module_runtime

    return set(module_runtime.absences_recentes(runtime, [module_runtime.ABSENCE_PAGE_GENIUS]))


def constater(
    runtime, introuvables: Iterable[int], lues: Iterable[int], maintenant: datetime | None = None
) -> int:
    """Pose le constat des pages servies « Page not found » par ce run et lève
    celui des pages LUES (la page est revenue). Rend le nombre de constats."""
    from src.services import runtime as module_runtime

    le = maintenant or datetime.now()
    poser = [(tid, le) for tid in dict.fromkeys(introuvables) if tid]
    retirer = [tid for tid in dict.fromkeys(lues) if tid]
    module_runtime.constater_absences(runtime, module_runtime.ABSENCE_PAGE_GENIUS, poser, retirer)
    if poser:
        logger.info(f"🚫 Genius : {len(poser)} page(s) introuvable(s), constat daté")
    return len(poser)


# ── La DÉSACTIVATION (fin de cycle) ──────────────────────────────────────────


def identifiants_de_plateforme(track) -> list[str]:
    """Les identifiants de plateforme de la fiche : une seule suffit à la garder."""
    ids = (("Deezer", track.deezer_id), ("Spotify", track.spotify_id))
    ids += (("Apple", track.apple_music_id),)
    return [nom for nom, valeur in ids if valeur]


def a_desactiver(tracks, desactives, supprimees, a_la_main=()) -> list:
    """PUR. Les pages constatées supprimées, sans identifiant de plateforme, ni
    déjà désactivées, ni réactivées à la main."""
    return [
        t
        for t in tracks
        if t.id is not None
        and t.id in supprimees
        and t.id not in desactives
        and t.id not in a_la_main
        and not identifiants_de_plateforme(t)
    ]


def desactiver_pages_supprimees(runtime, artist, tracks) -> int:
    """Désactive (réversible, motif `page_supprimee`, origine
    `regle:page_supprimee`) ce qu'`a_desactiver` désigne, et le journalise.
    Appelée en fin de CYCLE, après identité et streams, qui ont pu donner un
    identifiant à la fiche. Jamais de suppression."""
    disabled = runtime.disabled
    pages = a_desactiver(
        tracks,
        disabled.load_disabled_tracks(artist.name),
        pages_constatees(runtime),
        disabled.load_reactivees_a_la_main(artist.name),
    )
    if not pages:
        return 0
    for t in pages:
        logger.info(
            f"🚫 Page désactivée « {t.title} » (#{t.id}) : supprimée de Genius, "
            "sans identifiant de plateforme"
        )
    disabled.desactiver(artist.name, [t.id for t in pages], MOTIF_PAGE_SUPPRIMEE, ORIGINE_REGLE)
    return len(pages)


# ── Le RETOUR de la même page (fin de discographie) ──────────────────────────


def desactivees_au_motif(motifs: dict[int, dict]) -> set[int]:
    """PUR. Les fiches désactivées par la règle des pages supprimées."""
    return {tid for tid, m in motifs.items() if m.get("motif") == MOTIF_PAGE_SUPPRIMEE}


def a_reactiver(tracks, motifs: dict[int, dict], repond: Callable) -> list:
    """Les fiches désactivées au motif `page_supprimee` dont la page répond de
    nouveau (`repond(track)` vrai : détail Genius en 200, un morceau). Une
    fiche disparue (fusion, suppression) n'est plus dans `tracks`."""
    ids = desactivees_au_motif(motifs)
    return [t for t in tracks if t.id in ids and t.genius_id and repond(t) is True]


def reactiver_pages_revenues(runtime, artist, tracks) -> int:
    """Réactive ce qu'`a_reactiver` désigne et lève le constat (l'étape crédits
    relira la page ; toujours introuvable, le constat revient). Une requête de
    détail par fiche désactivée à ce motif : peu nombreuses."""
    disabled, api = runtime.disabled, runtime.genius_api
    if disabled is None or api is None:
        return 0
    motifs = disabled.load_motifs(artist.name)
    if not desactivees_au_motif(motifs):
        return 0
    revenues = a_reactiver(tracks, motifs, lambda t: api.page_repond(t.genius_id, t.id))
    if not revenues:
        return 0
    for t in revenues:
        logger.info(f"♻️ Page réactivée « {t.title} » (#{t.id}) : la page Genius répond de nouveau")
    disabled.reactiver(artist.name, [t.id for t in revenues])
    constater(runtime, (), [t.id for t in revenues])
    return len(revenues)


# ── La page RECRÉÉE sous un autre ID (discographie) ──────────────────────────


def meme_titre(a: str | None, b: str | None) -> bool:
    """PUR. Le même morceau pour le juge des titres : titres égaux une fois
    normalisés, ou même titre de base (sous-titres et éditions de diffusion
    près), sans autre descripteur de version et aux mêmes parties."""
    from src.utils.version_descriptors import autre_partie, meme_titre_normalise, titre_de_base

    if meme_titre_normalise(a, b):
        return True
    ba, bb = titre_de_base(a), titre_de_base(b)
    return bool(ba and bb) and meme_titre_normalise(ba, bb) and not autre_partie(a, b)


def titre_proche(a: str | None, b: str | None) -> bool:
    """PUR. Un titre seulement PROCHE (autre graphie, version voisine) : la paire
    se montre, on ne la rattache pas."""
    from src.utils.version_descriptors import meme_socle, titres_confondus

    return titres_confondus(a, b) or meme_socle(a, b)


def _candidates(page, fiches, juge) -> list:
    from src.utils.version_descriptors import meme_disque, titre_generique

    return [
        f
        for f in fiches
        if juge(page.title, f.title)
        # Un titre générique (« Intro ») n'est le même que sur le même disque.
        and (not titre_generique(page.title) or meme_disque(page.album, f.album))
    ]


@dataclass
class Recreations:
    """`rattacher` : (page neuve, fiche) sûres ; `a_trancher` : (page, [fiches])."""

    rattacher: list = field(default_factory=list)
    a_trancher: list = field(default_factory=list)


def pages_recreees(nouveaux, existants, supprimees: set[int]) -> Recreations:
    """PUR. Les pages NOUVELLES (ID qu'aucune fiche ne porte) qui reprennent une
    fiche dont la page est supprimée (`supprimees`). Un rattachement exige UNE
    fiche au même titre (`meme_titre`), réciproquement une seule page pour
    elle ; le reste — plusieurs fiches, titre seulement proche — est à
    trancher par l'humain."""
    connus = {t.genius_id for t in existants if t.genius_id}
    fiches = [t for t in existants if t.id in supprimees and t.genius_id]
    sortie = Recreations()
    if not fiches:
        return sortie
    pages = [t for t in nouveaux if t.genius_id and t.genius_id not in connus]
    sures: dict[int, list] = {}
    for page in pages:
        exactes = _candidates(page, fiches, meme_titre)
        if len(exactes) == 1:
            sures.setdefault(exactes[0].id, []).append((page, exactes[0]))
            continue
        proches = exactes or _candidates(page, fiches, titre_proche)
        if proches:
            sortie.a_trancher.append((page, proches))
    for paires in sures.values():
        if len(paires) == 1:
            sortie.rattacher.append(paires[0])
        else:
            for page, fiche in paires:
                sortie.a_trancher.append((page, [fiche]))
    return sortie


def _date(track) -> str | None:
    d = track.release_date
    if d is None:
        return None
    return d.strftime("%Y-%m-%d") if isinstance(d, date) else str(d)


def rattacher_pages_recreees(runtime, artist, nouveaux, existants) -> list[str]:
    """Rattache chaque page recréée sûre à sa fiche (`rattacher_page_genius`),
    réactive la fiche et lève son constat. Rend « page → fiche » par
    rattachement ; journalise les paires à trancher. Les fiches rattachées
    portent ensuite le nouvel ID : l'appelant RECHARGE la discographie avant de
    fusionner."""
    disabled, dm = runtime.disabled, runtime.data_manager
    # Rien à rapprocher sans page neuve ni fiche au même titre (aucune lecture
    # des constats pour un run sans page nouvelle).
    connus = {t.genius_id for t in existants if t.genius_id}
    neuves = [t for t in nouveaux if t.genius_id and t.genius_id not in connus]
    if not any(
        meme_titre(p.title, f.title) or titre_proche(p.title, f.title)
        for p in neuves
        for f in existants
        if f.genius_id
    ):
        return []
    # Les fiches à la page supprimée : désactivées par la règle, ou constatées
    # et actives (une identité les garde). Une fiche désactivée À LA MAIN
    # reste hors jeu : la décision de l'utilisateur ne se lève pas seule.
    supprimees = pages_constatees(runtime)
    if disabled is not None:
        a_la_main = disabled.load_disabled_tracks(artist.name)
        par_regle = desactivees_au_motif(disabled.load_motifs(artist.name))
        supprimees = (supprimees - a_la_main) | par_regle
    if not supprimees:
        return []
    trouve = pages_recreees(nouveaux, existants, supprimees)
    for page, fiches in trouve.a_trancher:
        logger.warning(
            f"⚠️ « {page.title} » (page Genius #{page.genius_id}) : page recréée ? "
            f"{len(fiches)} fiche(s) à la page supprimée au titre proche "
            f"({', '.join(f'« {f.title} » #{f.id}' for f in fiches)}) — aucune rattachée, "
            "à trancher"
        )
    faits, reactivees = [], []
    for page, fiche in trouve.rattacher:
        ok = dm.rattacher_page_genius(
            fiche.id,
            genius_id=page.genius_id,
            genius_url=page.genius_url,
            album=page.album or fiche.album,
            date_observee=_date(page),
            is_featuring=page.is_featuring,
            primary_artist_name=page.primary_artist_name,
            secondary_role=page.secondary_role,
        )
        if not ok:
            continue
        logger.info(
            f"🔗 Page Genius recréée « {page.title} » (#{page.genius_id}) rattachée à "
            f"« {fiche.title} » (#{fiche.id}), dont la page #{fiche.genius_id} est supprimée"
        )
        faits.append(f"{page.title} → {fiche.title}")
        reactivees.append(fiche.id)
    if reactivees:
        if disabled is not None:
            disabled.reactiver(artist.name, set(reactivees) & par_regle)
        constater(runtime, (), reactivees)
    return faits
