"""Recherche de certifications PAR ARTISTE, sur les trois corps à la fois.

Trois sources, trois façons d'être interrogées — et ce n'est pas un choix
d'architecture, c'est ce que chaque site permet :

  · SNEP  — filtre serveur `?interprete=`, qui rend un CSV portant TOUS les
    paliers du titre (vérifié : 47TER « On avait dit » sort avec son Or de 2020
    et son Platine de 2021, deux lignes distinctes).
  · RIAA  — recherche `?ar=`, dépliée par la timeline (`get_details=True`), qui
    donne l'échelle DATÉE de chaque titre.
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

Et **une certification ne compte qu'une fois** même quand deux noms la ramènent :
« IAM feat. Shurik'N » répond aux deux recherches, ce qui gonflerait le total
d'un artiste précisément là où il collabore le plus avec son groupe.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from src.utils.logger import get_logger

logger = get_logger(__name__)

#: Sources réellement interrogeables par un nom d'artiste, dans l'ordre
#: d'affichage. BRMA n'y est pas : voir le docstring du module.
#:
#: La BPI est la mieux lotie des quatre : elle expose un ANNUAIRE d'ids
#: (`/artists?q=`), donc le rapprochement de noms se fait chez elle et non chez
#: nous. Corollaire à connaître : une entité BPI est la chaîne de crédit
#: FACTURÉE (« SIGALA FT ELLA HENDERSON » est une entité distincte de
#: « SIGALA »), ce qui rejoint exactement le problème que `noms_de_recherche`
#: traite pour les groupes.
SOURCES_INTERROGEABLES = ("SNEP", "RIAA", "BPI")

#: Au-delà, le récapitulatif d'un corps est tronqué. Un rapport qu'on ne lit
#: pas ne vaut pas mieux qu'un compteur — et 120 titres tiennent déjà de la
#: consultation, pas du coup d'œil.
MAX_TITRES_AFFICHES = 120


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


def _cle_certification(cert: dict) -> tuple:
    """Ce qui fait qu'une certification est LA MÊME, vue depuis deux noms."""
    return (
        cert["body"],
        cert["artist_name"].upper().strip(),
        cert["title"].upper().strip(),
        cert["category"],
        cert["certification"].strip(),
        (cert["certification_date"] or "")[:10],
    )


def certifications(noms: Iterable[str], matcher=None) -> list[dict]:
    """Les certifications de ces noms, dédoublonnées, chacune sachant d'où elle vient.

    Le rapprochement se fait par MOT ENTIER, via le chercheur unique de
    `cert_matcher` — surtout pas une seconde implémentation : « SCH » comme
    sous-chaîne ramène Marco Schuitmaker, et deux chercheurs finiraient par
    diverger comme l'ont fait le validateur et le nettoyeur RIAA.

    Chaque certification rendue porte en plus `noms_correspondants` : les noms
    de la recherche qui l'ont ramenée. Une ligne créditée « IAM feat. Shurik'N »
    en porte DEUX — c'est ce qui permet de dire d'où vient quoi sans compter la
    ligne deux fois.
    """
    if matcher is None:
        from src.utils.cert_matcher import get_cert_matcher

        matcher = get_cert_matcher()

    par_cle: dict[tuple, dict] = {}
    for nom in noms:
        for cert in matcher.get_artist_certifications(nom):
            cle = _cle_certification(cert)
            connue = par_cle.get(cle)
            if connue is None:
                par_cle[cle] = {**cert, "noms_correspondants": [nom]}
            elif nom not in connue["noms_correspondants"]:
                connue["noms_correspondants"].append(nom)
    return list(par_cle.values())


def nouveautes(avant: Iterable[dict], apres: Iterable[dict]) -> list[dict]:
    """Les certifications présentes APRÈS et absentes AVANT : l'apport du run."""
    connues = {_cle_certification(c) for c in avant}
    return [c for c in apres if _cle_certification(c) not in connues]


def bilan_de(certs: Iterable[dict]) -> dict:
    """Les COMPTEURS d'une liste de certifications, par corps.

    Rend, par corps (SNEP / BRMA / RIAA / RIAA Latin) : le nombre de
    certifications, le nombre de titres distincts, et combien de ces titres
    portent au moins DEUX paliers — c'est cette dernière colonne qui dit si on a
    l'historique ou seulement le palier le plus haut.
    """
    par_corps: dict[str, dict] = {}
    for cert in certs:
        entree = par_corps.setdefault(cert["body"], {"total": 0, "paliers": {}})
        entree["total"] += 1
        titre = cert["title"].upper().strip()
        entree["paliers"].setdefault(titre, set()).add(cert["certification"].strip())

    return {
        corps: {
            "certifications": brut["total"],
            "titres": len(brut["paliers"]),
            "titres_multi_paliers": sum(1 for v in brut["paliers"].values() if len(v) > 1),
        }
        for corps, brut in par_corps.items()
    }


def bilan_local(noms: Iterable[str], matcher=None) -> dict:
    """Les compteurs pour ces noms — lecture SEULE du magasin, sans réseau.

    À appeler APRÈS les récupérations pour montrer le résultat, et avant pour
    mesurer ce que le run a apporté.
    """
    return bilan_de(certifications(noms, matcher))


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
            f"{corps} : {d['certifications']} certification(s), {d['titres']} titre(s), {detail}"
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


def repartition(certs: Iterable[dict], noms: Iterable[str]) -> str:
    """Combien de certifications chaque nom cherché a ramenées.

    Les totaux ne s'additionnent PAS forcément : une ligne créditée « IAM feat.
    Shurik'N » répond aux deux recherches et compte pour chacune. C'est voulu —
    la question « qu'a rapporté le groupe ? » veut cette ligne des deux côtés —
    mais il faut le DIRE, sinon la somme des parts dépasse le tout et le rapport
    a l'air faux.
    """
    noms = list(noms)
    if len(noms) < 2:
        return ""
    certs = list(certs)
    parts = [f"{nom} {sum(1 for c in certs if nom in c['noms_correspondants'])}" for nom in noms]
    partagees = sum(1 for c in certs if len(c["noms_correspondants"]) > 1)
    ligne = "Répartition : " + " · ".join(parts)
    if partagees:
        ligne += f"  (dont {partagees} créditée(s) à plusieurs de ces noms)"
    return ligne


def _ordre_palier(niveau: str) -> tuple:
    """Où se place un palier dans une échelle qui MONTE.

    Deux composantes, parce que les libellés en portent deux : le palier de
    base (Or < Platine < Diamant) et son multiplicateur. Le rang de base vient
    de `cert_matcher._RANK` — le référentiel partagé, où le PLUS PETIT nombre
    est le plus haut palier, d'où le signe. Le multiplicateur, lui, doit être
    lu comme un NOMBRE : trié en texte, « 10x » se range avant « 2x ».
    """
    from src.utils.cert_matcher import _RANK

    lvl = (niveau or "").strip().lower()
    multiplicateur = 1
    if (m := re.match(r"(\d+)\s*x\s+(.*)", lvl)) is not None:
        multiplicateur, lvl = int(m.group(1)), m.group(2).strip()
    return (-_RANK.get(lvl, 99), multiplicateur)


def _paliers(certs: list[dict]) -> list[str]:
    """L'échelle d'un titre, du plus ancien au plus récent, en lignes lisibles.

    Les paliers sont GROUPÉS PAR DATE, et un groupe de trois ou plus est
    résumé « 2x → 11x Platino ». Ce n'est pas de la cosmétique : le corpus
    historique RIAA déroule l'échelle de façon SYNTHÉTIQUE — Despacito porte
    dix paliers latins au même 12/04/2017, trente-quatre en tout — et les
    aligner sur une ligne donne un pavé illisible qui cache l'information
    utile, à savoir que ces paliers ont été enregistrés d'un seul coup.
    """
    par_date: dict[str, list[str]] = {}
    for c in certs:
        par_date.setdefault((c["certification_date"] or "?")[:10], []).append(c["certification"])

    etapes = []
    for date in sorted(par_date):
        niveaux = sorted(set(par_date[date]), key=_ordre_palier)
        if len(niveaux) >= 3:
            etapes.append(f"{niveaux[0]} → {niveaux[-1]} ({len(niveaux)} paliers) {date}")
        else:
            etapes.append(f"{', '.join(niveaux)} {date}")

    # Repli à la ligne plutôt qu'une ligne à rallonge : au-delà, le rapport
    # devient un mur et on cesse de le lire.
    lignes, courante = [], ""
    for etape in etapes:
        bloc = etape if not courante else f"{courante}  →  {etape}"
        if len(bloc) > 86 and courante:
            lignes.append(courante + "  →")
            courante = etape
        else:
            courante = bloc
    if courante:
        lignes.append(courante)
    return lignes


def recap(certs: Iterable[dict], nouvelles: Iterable[dict] = ()) -> str:
    """Le récapitulatif : quelles certifications, sur quels titres, à quelles dates.

    Des compteurs ne disent pas ce qu'on a. « SNEP : 16 certifications » se lit
    sans rien apprendre ; la liste des titres avec leur échelle datée, si — et
    c'est justement l'échelle qui était la question (« un titre Platine deux ans
    après sa sortie a sûrement eu un Or avant »).

    `nouvelles` = les certifications que le run vient d'apporter, marquées d'un
    « + » et remontées en tête de leur corps.
    """
    certs = list(certs)
    if not certs:
        return "Aucune certification connue pour ces noms."

    cles_neuves = {_cle_certification(c) for c in nouvelles}

    # Regroupement par corps, puis par ŒUVRE (un titre d'un artiste crédité,
    # dans une catégorie) : c'est l'unité dont on veut voir l'échelle.
    par_corps: dict[str, dict[tuple, list]] = {}
    for cert in certs:
        oeuvre = (cert["artist_name"].strip(), cert["title"].strip(), cert["category"])
        par_corps.setdefault(cert["body"], {}).setdefault(oeuvre, []).append(cert)

    blocs = []
    for corps in sorted(par_corps):
        oeuvres = par_corps[corps]
        n_certs = sum(len(v) for v in oeuvres.values())
        n_multi = sum(1 for v in oeuvres.values() if len({c["certification"] for c in v}) > 1)
        entete = f"{corps} — {n_certs} certification(s) · {len(oeuvres)} titre(s)"
        if n_multi:
            entete += f" · {n_multi} avec historique"
        lignes = [entete]

        def rang(item):
            """Les nouveautés d'abord, puis la certification la plus récente."""
            _oeuvre, groupe = item
            neuve = any(_cle_certification(c) in cles_neuves for c in groupe)
            recente = max((c["certification_date"] or "") for c in groupe)
            return (not neuve, recente == "", [-ord(ch) for ch in recente])

        ordonnees = sorted(oeuvres.items(), key=rang)
        for artiste, titre, categorie in [o for o, _ in ordonnees][:MAX_TITRES_AFFICHES]:
            groupe = oeuvres[(artiste, titre, categorie)]
            marque = "+" if any(_cle_certification(c) in cles_neuves for c in groupe) else " "
            lignes.append(f"  {marque} {titre} — {artiste} [{categorie}]")
            lignes.extend(f"      {ligne}" for ligne in _paliers(groupe))
        if len(ordonnees) > MAX_TITRES_AFFICHES:
            lignes.append(f"  … et {len(ordonnees) - MAX_TITRES_AFFICHES} autre(s) titre(s)")
        blocs.append("\n".join(lignes))

    return "\n\n".join(blocs)


__all__ = [
    "MAX_TITRES_AFFICHES",
    "SOURCES_INTERROGEABLES",
    "bilan_de",
    "bilan_local",
    "certifications",
    "evolution",
    "noms_de_recherche",
    "nouveautes",
    "recap",
    "repartition",
    "resume_bilan",
]
