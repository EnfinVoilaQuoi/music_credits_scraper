"""Critère de fin du gel des fonctionnalités, en quatre volets (décision utilisateur
du 2026-11-22, PROJET « Gel des fonctionnalités ») :

1. **invariants** à zéro, sans tolérance (familles `audit.INVARIANTS`) ;
2. **régressions** à zéro : cas nouveaux sur une fiche déjà jugée ou relue, tels
   que les fins de run les consignent (`audit_par_artiste/hausses.jsonl`) ;
3. **délai** des décisions du panneau : aucun cas de plus de 14 j, et chaque cycle
   en ferme plus qu'il n'en ouvre. L'âge d'un cas est celui de sa première
   apparition dans les audits datés de la BASE ENTIÈRE (`audit_*.json`) : rien de
   nouveau à tenir, l'historique est déjà là ;
4. **échantillon témoin** (`scripts/echantillon_temoin.py --depouiller`) : taux
   d'erreur réel sous un seuil par donnée. Sans fiche dépouillée, ou sans seuil
   décidé, le volet est « non mesuré » — jamais tenu pour atteint.

Le critère est tenu quand les quatre le sont deux semaines de suite (le compte
rendu montre l'état d'aujourd'hui ; la durée se lit en le relançant). Les cas
« à faire par un run » et « pour information » n'y comptent pas.

Fonctions PURES : l'appelant (`scripts/critere_fin_du_gel.py`) lit les fichiers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from src.services.audit import DECISION, ERREUR, INVARIANTS, identite_du_cas

#: Âge maximal d'une décision du panneau (décision utilisateur 2026-11-22).
DELAI_DECISION_JOURS = 14
#: Fenêtre des régressions comptées : celle du critère, deux semaines.
FENETRE_REGRESSIONS_JOURS = 14
BASE_ENTIERE = "base entière"


def cle_cas(cas: dict) -> tuple:
    """L'identité d'un cas d'un audit à l'autre, celle des hausses de fin de run
    (`audit.identite_du_cas` : famille et fiches ; sans fiche, le libellé). Sur
    la clé (famille, artiste, fiches), tous les cas sans fiche d'un artiste —
    `deezer_version`, `deezer_absent`… — n'en faisaient qu'un (2026-11-27 :
    93 cas comptés 23)."""
    return identite_du_cas(cas)


def _date(texte: str) -> datetime:
    return datetime.fromisoformat(str(texte)[:19])


@dataclass
class Volet:
    nom: str
    tenu: bool | None  # None = non mesuré
    valeur: str
    detail: list[str] = field(default_factory=list)


def volet_invariants(audit: dict) -> Volet:
    cas = [c for c in audit.get("cas", []) if c.get("famille") in INVARIANTS]
    erreurs = [c for c in cas if c.get("nature", ERREUR) == ERREUR]
    par_famille: dict[str, int] = {}
    for c in erreurs:
        par_famille[c["famille"]] = par_famille.get(c["famille"], 0) + 1
    return Volet(
        "invariants à zéro",
        not erreurs,
        str(len(erreurs)),
        [f"{f} : {n}" for f, n in sorted(par_famille.items())],
    )


def volet_regressions(hausses: list[dict], maintenant: datetime) -> Volet:
    depuis = maintenant - timedelta(days=FENETRE_REGRESSIONS_JOURS)
    nouveaux: list[tuple[str, dict]] = []
    for ligne in hausses:
        try:
            quand = _date(ligne.get("date", ""))
        except ValueError:
            continue
        if quand < depuis:
            continue
        for cas in ligne.get("nouveaux") or []:
            if cas.get("nature", ERREUR) in (ERREUR, DECISION):
                nouveaux.append((ligne["date"][:10], cas))
    detail = [f"{d} {c.get('artiste')} — {c.get('famille')}" for d, c in nouveaux[-10:]]
    return Volet(
        f"régressions à zéro ({FENETRE_REGRESSIONS_JOURS} j)",
        not nouveaux,
        str(len(nouveaux)),
        detail,
    )


def premieres_apparitions(audits: list[dict]) -> dict[tuple, datetime]:
    """{cle_cas: date du premier audit de la base entière qui le montre}."""
    vu: dict[tuple, datetime] = {}
    for audit in sorted(audits, key=lambda a: a.get("date", "")):
        if audit.get("perimetre") != BASE_ENTIERE:
            continue
        quand = _date(audit["date"])
        for cas in audit.get("cas", []):
            vu.setdefault(cle_cas(cas), quand)
    return vu


def volet_delai(audits: list[dict]) -> Volet:
    """Âge des décisions du DERNIER audit de la base entière, et bilan du cycle
    (décisions ouvertes contre fermées depuis l'audit précédent)."""
    entiers = sorted(
        (a for a in audits if a.get("perimetre") == BASE_ENTIERE),
        key=lambda a: a.get("date", ""),
    )
    if not entiers:
        return Volet("décisions au délai", None, "aucun audit de la base entière")
    dernier = entiers[-1]
    maintenant = _date(dernier["date"])
    vu = premieres_apparitions(entiers)

    def decisions(audit):
        return {cle_cas(c) for c in audit.get("cas", []) if c.get("nature") == DECISION}

    actuelles = decisions(dernier)
    vieilles = sorted(
        (k for k in actuelles if (maintenant - vu[k]).days > DELAI_DECISION_JOURS),
        key=lambda k: vu[k],
    )
    detail = [f"{len(vieilles)} / {len(actuelles)} décisions de plus de {DELAI_DECISION_JOURS} j"]
    par_famille: dict[str, int] = {}
    for k in vieilles:
        par_famille[k[0]] = par_famille.get(k[0], 0) + 1
    detail += [f"  {f} : {n}" for f, n in sorted(par_famille.items(), key=lambda x: -x[1])]
    cycle_tenu = True
    if len(entiers) > 1:
        avant = decisions(entiers[-2])
        ouvertes, fermees = len(actuelles - avant), len(avant - actuelles)
        cycle_tenu = fermees > ouvertes
        detail.append(
            f"cycle depuis {entiers[-2]['date'][:10]} : {fermees} fermées, {ouvertes} ouvertes"
        )
    return Volet("décisions au délai", not vieilles and cycle_tenu, str(len(vieilles)), detail)


def volet_temoin(depouillement: dict | None, seuils: dict[str, float] | None) -> Volet:
    """`depouillement` : la sortie de `echantillon_temoin.depouiller` ; `seuils` :
    {donnée: taux maximal}, à décider (aucun n'est fixé au 2026-11-26)."""
    nom = "échantillon témoin sous les seuils"
    if not depouillement or not (depouillement.get("total") or {}).get("verifiees"):
        return Volet(nom, None, "non mesuré (aucune fiche de contrôle dépouillée)")
    if not seuils:
        return Volet(nom, None, "non mesuré (seuils par donnée non décidés)")
    depasse = []
    for donnee, seuil in sorted(seuils.items()):
        taux = (depouillement.get(donnee) or {}).get("taux")
        if taux is None:
            depasse.append(f"{donnee} : non vérifiée")
        elif taux > seuil:
            depasse.append(f"{donnee} : {taux:.1%} > {seuil:.1%}")
    return Volet(nom, not depasse, str(len(depasse)), depasse)


def rapport(volets: list[Volet]) -> str:
    marque = {True: "✓", False: "✗", None: "?"}
    lignes = ["Critère de fin du gel (décision du 2026-11-22)"]
    for v in volets:
        lignes.append(f"  {marque[v.tenu]} {v.nom} : {v.valeur}")
        lignes += [f"      {d}" for d in v.detail]
    tenus = [v.tenu for v in volets]
    if all(t is True for t in tenus):
        fin = "TENU aujourd'hui — à tenir deux semaines de suite"
    elif any(t is False for t in tenus):
        fin = "NON TENU"
    else:
        fin = "INCOMPLET (volets non mesurés)"
    lignes.append(f"  → {fin}")
    return "\n".join(lignes)
