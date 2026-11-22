"""La fenêtre Certifications (icône 🏅 de la barre du haut), sans widget.

Décision utilisateur 2026-11-22 : la fenêtre des certifs (MàJ des bases SNEP,
BRMA, RIAA, BPI ; recherche par artiste) migre dans ce bouton, avec une
pastille quand une base a besoin d'une MàJ ; le run certifs d'UN artiste reste
dans Run Complet. Le panneau sera retravaillé par l'utilisateur : ici l'état
de chaque base et ce qui manque, rien de plus.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

from src.utils.cert_normalize import drapeau

PAYS = {"SNEP": "France", "BRMA": "Belgique", "RIAA": "USA", "BPI": "UK"}

#: Au-delà, une base est « à mettre à jour » (pastille) — le même seuil que la
#: liste de publications du studio pour un run de certifs (< 30 j).
DELAI_MAJ_JOURS = 30


def _date(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(str(iso)).replace(tzinfo=None)
    except (TypeError, ValueError):
        return None


def _jour(iso: str | None) -> str:
    d = _date(iso)
    return d.strftime("%d/%m/%Y %H:%M") if d else ""


def a_mettre_a_jour(frais: Mapping, maintenant: datetime | None = None) -> bool:
    """Pas de données, une MàJ partielle, jamais de MàJ globale, ou la
    dernière il y a plus de `DELAI_MAJ_JOURS`."""
    if not frais.get("available"):
        return True
    if frais.get("partial"):
        return True
    derniere = _date(frais.get("last_global"))
    if derniere is None:
        return True
    return (maintenant or datetime.now()) - derniere > timedelta(days=DELAI_MAJ_JOURS)


@dataclass(frozen=True)
class LigneBase:
    source: str
    libelle: str
    etat: str
    derniere_maj: str
    recup_artiste: str
    certifications: str
    trous: str
    a_mettre_a_jour: bool
    detail: str = ""


def ligne(
    source: str,
    frais: Mapping,
    trous: Mapping | None = None,
    maintenant: datetime | None = None,
) -> LigneBase:
    libelle = f"{drapeau(source)} {source} ({PAYS.get(source, '')})".strip()
    besoin = a_mettre_a_jour(frais, maintenant)
    if not frais.get("available"):
        etat, detail = "Pas de données", ""
    elif frais.get("partial"):
        etat, detail = "Partielle", str(frais["partial"])
    elif not frais.get("last_global"):
        etat, detail = "MàJ globale jamais tracée", ""
    else:
        etat, detail = ("À mettre à jour" if besoin else "À jour"), ""
    if trous is None:
        texte_trous = ""
    elif trous.get("erreur"):
        texte_trous = f"non calculés : {trous['erreur']}"
    else:
        n = len(trous.get("gaps") or [])
        texte_trous = f"{n} période(s) manquante(s)" if n else "aucun"
    compte = frais.get("count")
    return LigneBase(
        source,
        libelle,
        etat,
        _jour(frais.get("last_global")),
        _jour(frais.get("last_artist")),
        "" if compte is None else f"{compte:,}".replace(",", " "),
        texte_trous,
        besoin,
        detail,
    )


def detail_trous(source: str, trous: Mapping) -> str:
    """Le détail des périodes manquantes d'une base (infobulle)."""
    if trous.get("erreur"):
        return f"{source} : {trous['erreur']}"
    gaps = trous.get("gaps") or []
    lignes = [f"{source} : {trous.get('total', 0)} certifications"]
    if trous.get("date_range"):
        lignes.append(f"Période : {trous['date_range']}")
    lignes += [f"• {g}" for g in gaps[:10]]
    if len(gaps) > 10:
        lignes.append(f"… et {len(gaps) - 10} autre(s)")
    return "\n".join(lignes)
