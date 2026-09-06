"""Recherche de certifications PAR ARTISTE, sur les trois corps à la fois.

Trois sources, trois façons d'être interrogées — et ce n'est pas un choix
d'architecture, c'est ce que chaque site permet :

  · SNEP  — filtre serveur `?interprete=`, qui rend un CSV portant TOUS les
    paliers du titre (vérifié : 47TER « On avait dit » sort avec son Or de 2020
    et son Platine de 2021, deux lignes distinctes).
  · RIAA  — recherche `?ar=`, dépliée par la timeline (`get_details=True`), qui
    donne l'échelle DATÉE de chaque titre. Elle vit sur DEUX onglets, classique
    et latin, tous deux interrogés (cf. `scrape_by_artist`).
  · BRMA  — Ultratop n'expose aucune recherche par artiste qui nous soit
    accessible : ses pages sont derrière Cloudflare et seules les pages
    `/or-platine/{année}/{catégorie}` passent. La contribution de BRMA est donc
    une LECTURE du corpus déjà collecté — qui, lui, est complet et continu
    (1995-2026, chaque année balayée). Ce n'est pas un repli honteux : c'est
    dire ce qu'on sait, plutôt que de simuler une requête qu'on ne peut pas
    faire.

Le point commun aux trois : **plusieurs noms**. Un membre de groupe est crédité
sous son nom ET sous celui de sa formation (Shurik'N chez IAM, Swing à L'Or du
Commun) ; chercher un seul des deux ampute la moitié de sa discographie
certifiée. Les noms viendront des relations d'artistes (lot 3) ; en attendant
l'appelant les fournit, et la fonction ne fait AUCUNE hypothèse sur leur origine.
"""

from __future__ import annotations

from collections.abc import Iterable

from src.utils.logger import get_logger

logger = get_logger(__name__)

#: Sources réellement interrogeables par un nom d'artiste, dans l'ordre
#: d'affichage. BRMA n'y est pas : voir le docstring du module.
SOURCES_INTERROGEABLES = ("SNEP", "RIAA")


def noms_de_recherche(principal: str, alias: Iterable[str] = ()) -> list[str]:
    """Noms à interroger pour cet artiste : lui-même, puis ses formations.

    Dédoublonné SANS tenir compte de la casse ni des espaces de bord, mais la
    forme CONSERVÉE est la première rencontrée — c'est elle qui part dans l'URL,
    et le SNEP comme la RIAA cherchent sur le libellé.

    L'ordre est stable et le principal vient toujours en tête : les rapports se
    lisent dans cet ordre, et un ordre qui bouge d'un run à l'autre rendrait
    deux exécutions incomparables.
    """
    sortie: list[str] = []
    vus: set[str] = set()
    for brut in (principal, *alias):
        nom = (brut or "").strip()
        if not nom:
            continue
        cle = nom.casefold()
        if cle not in vus:
            vus.add(cle)
            sortie.append(nom)
    return sortie


def bilan_local(noms: Iterable[str], matcher=None) -> dict:
    """Ce que le magasin de certifications porte pour ces noms, par corps.

    Lecture SEULE, sans réseau : à appeler APRÈS les récupérations pour montrer
    le résultat, et avant pour montrer le point de départ.

    Le rapprochement se fait par MOT ENTIER, via le chercheur unique de
    `cert_matcher` — surtout pas une seconde implémentation : « SCH » comme
    sous-chaîne ramène Marco Schuitmaker, et deux chercheurs finiraient par
    diverger comme l'ont fait le validateur et le nettoyeur RIAA.

    Rend, par corps (SNEP / BRMA / RIAA / RIAA Latin) : le nombre de
    certifications, le nombre de titres distincts, et combien de ces titres
    portent au moins DEUX paliers — c'est cette dernière colonne qui dit si on a
    l'historique ou seulement le palier le plus haut.
    """
    if matcher is None:
        from src.utils.cert_matcher import get_cert_matcher

        matcher = get_cert_matcher()

    par_corps: dict[str, dict] = {}
    for nom in noms:
        for cert in matcher.get_artist_certifications(nom):
            # Clés du format de `cert_matcher._format` : `body` (SNEP / BRMA /
            # RIAA / RIAA Latin) et `certification` (le niveau).
            corps = cert["body"]
            titre = cert["title"].upper().strip()
            niveau = cert["certification"].strip()
            entree = par_corps.setdefault(corps, {"total": 0, "paliers": {}})
            entree["total"] += 1
            entree["paliers"].setdefault(titre, set()).add(niveau)

    resultat = {}
    for corps, brut in par_corps.items():
        paliers = brut["paliers"]
        resultat[corps] = {
            "certifications": brut["total"],
            "titres": len(paliers),
            "titres_multi_paliers": sum(1 for v in paliers.values() if len(v) > 1),
        }
    return resultat


def resume_bilan(bilan: dict) -> str:
    """Rend le bilan en quelques lignes lisibles, ou dit franchement qu'il est vide."""
    if not bilan:
        return "Aucune certification connue pour ces noms."
    lignes = []
    for corps in sorted(bilan):
        d = bilan[corps]
        detail = (
            f"{d['titres_multi_paliers']} avec plusieurs paliers"
            if d["titres_multi_paliers"]
            else "aucun historique de paliers"
        )
        lignes.append(
            f"{corps} : {d['certifications']} certification(s), "
            f"{d['titres']} titre(s), {detail}"
        )
    return "\n".join(lignes)


def evolution(avant: dict, apres: dict) -> str:
    """Ce que la récupération a CHANGÉ, corps par corps.

    Un total après coup ne dit pas si le run a servi à quelque chose : « 12
    certifications » se lit pareil qu'on en ait rapporté 12 ou zéro. C'est
    l'écart qui informe.
    """
    corps_vus = sorted(set(avant) | set(apres))
    gains = []
    for corps in corps_vus:
        a = avant.get(corps, {}).get("certifications", 0)
        b = apres.get(corps, {}).get("certifications", 0)
        if b != a:
            gains.append(f"{corps} {a} → {b} ({b - a:+d})")
    return " · ".join(gains) if gains else "aucune certification nouvelle"


__all__ = [
    "SOURCES_INTERROGEABLES",
    "bilan_local",
    "evolution",
    "noms_de_recherche",
    "resume_bilan",
]
