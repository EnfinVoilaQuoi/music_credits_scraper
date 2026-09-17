"""Ce que le site SNEP montre ENCORE, ligne par ligne du brut.

Mesuré le 2026-09-14 sur trois années lues entièrement (2024-2026) : **323 de
nos 4 152 lignes locales étaient absentes du site**. Mais 305 d'entre elles
(94 %) sont de l'HISTOIRE que le site efface et que nous sommes les seuls à
garder — depuis 2024 au moins, le SNEP ne conserve que le palier COURANT d'une
œuvre, l'Or disparaît quand le Platine arrive (272 cas à crédit identique, 33
où le crédit d'artiste a été réécrit en montant : « GIMS, DAMSO » → « GIMS &
DAMSO »). Aligner le brut sur le site aurait donc DÉTRUIT la donnée que le
magasin a pour valeur d'accumuler.

Ce qui est réellement faux chez nous tient en 8 lignes sur trois ans : deux
rétrogradations (MAES *Pure* « Triple Diamant » corrigé en Triple Platine, JUL
*MIMI* Platine → Or) et six disparitions pures. Ce module ne sait distinguer
les deux populations que par l'ŒUVRE : une ligne locale est RETIRÉE si le site
n'en montre plus aucun palier au moins égal pour la même œuvre — clé (titre,
date de sortie), jamais le crédit d'artiste, que le SNEP réécrit.

Le RAPPROCHEMENT se fait par SQUELETTE alphanumérique (`squelette_libelle`, la
règle des fantômes), jamais par `normalize_text` (2026-09-17) : le site sert
encore les titres anciens avec un « ? » à la place de l'apostrophe et de l'œ
(« THAT?S THE WAY IT IS », « AU C?UR DU STADE »), pages ET export, là où le
brut porte la forme restaurée. `normalize_text` garde l'apostrophe et supprime
le « ? » — « THAT'S » ≠ « THATS » —, si bien qu'un backfill de 1987-2026 avait
« confirmé » 153 retraits, dont 119 sur un libellé à apostrophe ou ligature, et
ajouté 108 doublons « ? » comme nouveautés. La clé du SIDECAR, elle, reste
`cle_ligne` (identité de la ligne telle qu'écrite) : seule la comparaison
change, aucune marque à migrer.

Le résultat vit dans un SIDECAR (`certif-.vues.json`) et non dans une colonne
du brut : le brut garde le format natif du SNEP et `_row_key` l'indexe par la
fin. Une marque est POSÉE OU EFFACÉE à chaque année relue entièrement (même
règle que `partial` dans le sidecar de fraîcheur) : un rescrape qui retrouve
la ligne la réhabilite. Le brut ne perd jamais rien ; c'est le CLEAN qui
exclut les lignes marquées, et c'est lui seul que lit `cert_matcher`.
"""

from __future__ import annotations

import csv
import io
import json
import re
from collections.abc import Callable, Iterable
from datetime import date
from pathlib import Path
from typing import NamedTuple

from src.utils.cert_normalize import RANG_PALIERS, normalize_text, squelette_libelle
from src.utils.logger import get_logger

logger = get_logger(__name__)

_SEPARATEUR_CLE = "␟"  # « symbol for unit separator » : jamais dans un titre


def champs(ligne: str) -> list[str]:
    """Les champs d'une ligne du brut, guillemets CSV RÉSOLUS.

    Le brut mélange deux écritures d'un même titre : l'export CSV du SNEP
    quote un champ qui contient des guillemets (`"… (FROM ""SING"" …)"`), le
    parseur de page les écrit nus. Un `split(";")` voit deux titres — mesuré :
    8 faux « retraits » sur 323, et un fabricant de doublons latent.
    """
    try:
        return next(csv.reader(io.StringIO(ligne), delimiter=";"))
    except (csv.Error, StopIteration):
        return ligne.split(";")


def cle_ligne(f: list[str]) -> tuple:
    """Clé d'une ligne du brut : artiste, titre, puis les 4 derniers champs
    (catégorie, palier, sortie, constat) indexés par la FIN — le label, seul
    champ de longueur variable, peut contenir le séparateur."""
    return (
        normalize_text(f[0]),
        normalize_text(f[1]),
        f[-4].strip(),
        f[-3].strip(),
        f[-2].strip(),
        f[-1].strip(),
    )


def cle_texte(cle: tuple) -> str:
    return _SEPARATEUR_CLE.join(cle)


def _cle_comparable(f: list[str]) -> tuple:
    """La même ligne, vue par le site : artiste et titre réduits à leur
    SQUELETTE, pour qu'une écriture corrompue (« LET?S TALK ») et sa forme
    restaurée (« LET'S TALK ») se reconnaissent. C'est la clé de COMPARAISON ;
    `cle_ligne` reste celle du sidecar."""
    return (
        squelette_libelle(f[0]),
        squelette_libelle(f[1]),
        f[-4].strip(),
        f[-3].strip(),
        f[-2].strip(),
        f[-1].strip(),
    )


def cles_oeuvre(f: list[str]) -> tuple[tuple, tuple]:
    """L'ŒUVRE certifiée, par deux clés dont UNE suffit : (titre, sortie) et
    (titre, artiste).

    Le SNEP réécrit le crédit d'artiste en montant un palier (33 cas sur 3 ans :
    feats ajoutés, « , » devenu « & », ordre changé) — la date de sortie tient
    alors. Mais il corrige aussi des dates de sortie (Theodora *Miss Kitoko* :
    Or sorti « 12/03/2025 », Platine sorti « 12/03/2026 ») — l'artiste tient
    alors. Exiger les deux marquait retiré un Or dont le Platine est sur le
    site ; n'exiger que le titre rapprocherait « La nuit » de SCH de celle de
    PLK. Le résidu (titre réécrit, « Argent sale - A COLORS SHOW » → « Argent
    sale ») tombe du côté RETRAIT : rare, et réversible.
    """
    titre = squelette_libelle(f[1])
    return (titre, f[-2].strip()), (titre, squelette_libelle(f[0]))


def _rang(palier: str) -> int:
    return RANG_PALIERS.get(palier.strip().lower(), 99)


class Reconciliation(NamedTuple):
    vues: set[tuple]  # lignes locales retrouvées telles quelles sur le site
    remplacees: set[tuple]  # absentes, mais un palier ≥ existe pour l'œuvre
    retirees: set[tuple]  # absentes, et plus rien d'au moins égal pour l'œuvre


def _classer(f: list[str], site_cles: set, paliers_par_oeuvre: dict) -> str:
    if _cle_comparable(f) in site_cles:
        return "vue"
    # Rang bas = palier haut : un palier ≥ sur le site = rang ≤ au nôtre.
    rangs = [r for c in cles_oeuvre(f) for r in paliers_par_oeuvre.get(c, [])]
    return "remplacee" if any(r <= _rang(f[-3]) for r in rangs) else "retiree"


def _indexer(lignes_site: Iterable[str]) -> tuple[set, dict]:
    site = [f for f in map(champs, lignes_site) if len(f) >= 7]
    site_cles = {_cle_comparable(f) for f in site}
    paliers_par_oeuvre: dict[tuple, list[int]] = {}
    for f in site:
        for cle in cles_oeuvre(f):
            paliers_par_oeuvre.setdefault(cle, []).append(_rang(f[-3]))
    return site_cles, paliers_par_oeuvre


def reconcilier(lignes_locales: list[str], lignes_site: list[str]) -> Reconciliation:
    """Confronte les lignes locales à ce que le site montre (union des années
    lues ENTIÈREMENT — `BilanAnnee.complete` : sur une lecture coupée, chaque
    page manquante « retirerait » trente lignes).

    Fonction PURE. Ce qu'elle rend en `retirees` n'est qu'une liste de
    CANDIDATES : le classement par année du site n'est pas fiable ligne à ligne
    (mesuré le 2026-09-14 : 14 lignes du lot du 20/08/2026 absentes de
    `?annee=2026` et pourtant rendues par `?interprete=`), d'où `confirmer`.
    """
    site_cles, paliers = _indexer(lignes_site)
    vues, remplacees, retirees = set(), set(), set()
    for ligne in lignes_locales:
        f = champs(ligne)
        if len(f) < 7:
            continue
        verdict = _classer(f, site_cles, paliers)
        {"vue": vues, "remplacee": remplacees, "retiree": retirees}[verdict].add(cle_ligne(f))
    return Reconciliation(vues, remplacees, retirees)


_SEPARATEURS_DE_CREDIT = re.compile(r"\s*(?:,|&| feat\.?\s| x | and )\s*", re.IGNORECASE)


def artiste_principal(credit: str) -> str:
    """Le premier nom d'un crédit SNEP, pour interroger `?interprete=` (qui
    fait un `contains` : « TRINIX » rend bien « TRINIX, MARIANA FROES »)."""
    return _SEPARATEURS_DE_CREDIT.split(credit, maxsplit=1)[0].strip() or credit.strip()


def confirmer(
    r: Reconciliation, lignes_locales: list[str], chercher: Callable[[str], list[str] | None]
) -> Reconciliation:
    """Seconde route pour chaque candidate au retrait : la page filtrée par
    ARTISTE, qui n'est pas servie depuis le même cache que le classement par
    année. Une candidate qui y figure est VUE ; si un palier ≥ y figure pour
    l'œuvre, REMPLACÉE ; sinon seulement, RETIRÉE. Une recherche qui échoue
    (`chercher` rend None) laisse la candidate INDÉCISE : ni retirée, ni vue —
    on ne conclut pas sur une absence qu'on n'a pas pu vérifier.
    """
    if not r.retirees:
        return r
    par_cle = {cle_ligne(f): f for f in map(champs, lignes_locales) if len(f) >= 7}
    resultats: dict[str, list[str] | None] = {}
    vues, remplacees, retirees = set(r.vues), set(r.remplacees), set()
    for cle in sorted(r.retirees):
        f = par_cle.get(cle)
        if f is None:
            continue
        nom = artiste_principal(f[0])
        if nom not in resultats:
            resultats[nom] = chercher(nom)
        page = resultats[nom]
        if page is None:
            continue
        verdict = _classer(f, *_indexer(page))
        {"vue": vues, "remplacee": remplacees, "retiree": retirees}[verdict].add(cle)
    return Reconciliation(vues, remplacees, retirees)


# ---------------------------------------------------------------------------
# Sidecar
# ---------------------------------------------------------------------------


def chemin_sidecar(brut: Path) -> Path:
    return brut.with_name(brut.stem + ".vues.json")


def lire(brut: Path) -> dict[str, dict]:
    p = chemin_sidecar(brut)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.exception(f"Sidecar illisible : {p}")
        return {}


def _ecrire(brut: Path, vues: dict[str, dict]) -> None:
    chemin_sidecar(brut).write_text(
        json.dumps(vues, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8"
    )


def enregistrer(brut: Path, r: Reconciliation, *, le: date | None = None) -> int:
    """Pose `vue_le` sur ce qui est vu, `retiree_le` sur ce qui a disparu, et
    EFFACE `retiree_le` sur ce qui est revu. Rend le nombre de lignes retirées
    à ce jour pour l'année confrontée."""
    jour = (le or date.today()).isoformat()
    vues = lire(brut)
    for cle in r.vues | r.remplacees:
        entree = vues.setdefault(cle_texte(cle), {})
        entree["vue_le"] = jour
        # Une ligne remplacée est de l'histoire, pas un retrait : elle reste dans
        # le clean. On note seulement qu'elle ne se voit plus telle quelle.
        entree["remplacee"] = cle in r.remplacees
        entree.pop("retiree_le", None)
    for cle in r.retirees:
        entree = vues.setdefault(cle_texte(cle), {})
        # La PREMIÈRE date d'absence est l'information ; on ne la rajeunit pas.
        entree.setdefault("retiree_le", jour)
        # Un `remplacee` d'un passage antérieur survivait au retrait : l'entrée
        # disait à la fois « histoire conservée » et « retirée ».
        entree.pop("remplacee", None)
    _ecrire(brut, vues)
    return len(r.retirees)


def cles_retirees(brut: Path) -> set[tuple]:
    """Les clés de ligne marquées retirées — ce que le clean doit exclure."""
    return {tuple(k.split(_SEPARATEUR_CLE)) for k, v in lire(brut).items() if v.get("retiree_le")}
