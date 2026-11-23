"""La fenêtre Santé des sources, sans widget (icône « santé » de la barre du haut).

Deux informations de nature différente, qui ne se mélangent JAMAIS (même
règle que la fenêtre CustomTkinter) : la SONDE (la source répondait-elle au
moment de la vérification ?) et l'USAGE RÉEL (ce que les runs ont vu). Les
sources sont rangées par UTILITÉ (crédits, audio, certifs, streams, médias) :
une source qui sert deux familles apparaît dans les deux.

Le calcul vit dans `observability.rollup` et `utils.source_health` ; ici, les
seuls libellés et l'assemblage des lignes.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from src.observability import rollup
from src.observability.registry import FAMILY_LABELS, FAMILY_ORDER

LIBELLES_STATUT = {"ok": "OK", "degraded": "Dégradé", "broken": "Cassé", "unknown": "Inconnu"}
LIBELLES_NATURE = {
    "timeout": "délai dépassé",
    "unreachable": "injoignable",
    "throttled": "cadence (429)",
    "blocked": "anti-bot",
    "auth": "authentification",
    "parse": "structure changée",
    "crash": "scrape cassé",
}
#: Fenêtres de temps proposées : libellé → jours (None = tout).
FENETRES = {"7 jours": 7, "30 jours": 30, "Tout": None}


@dataclass(frozen=True)
class LigneSante:
    cle: str
    source: str
    statut: str  # ok | degraded | broken | unknown
    sonde: str
    sonde_detail: str
    appels: str
    echecs: str
    en_echec: bool
    nature: str
    derniere_erreur: str


@dataclass(frozen=True)
class Famille:
    cle: str
    titre: str
    agregat: str
    lignes: tuple[LigneSante, ...]


def _sonde(etat: Mapping | None) -> tuple[str, str, str]:
    """(statut, texte, infobulle) du dernier état sondé."""
    if not etat:
        return "unknown", "—", "Jamais vérifiée"
    statut = etat.get("status", "unknown")
    texte = LIBELLES_STATUT.get(statut, statut)
    if etat.get("latency_ms") is not None:
        texte += f" · {etat['latency_ms']} ms"
    detail = [f"Vérifiée le {(etat.get('last_checked') or '').replace('T', ' ')}".strip()]
    if etat.get("message"):
        detail.append(str(etat["message"]))
    if statut != "ok" and etat.get("last_ok"):
        detail.append(f"Dernier OK : {str(etat['last_ok']).replace('T', ' ')}")
    return statut, texte, "\n".join(detail)


def _usage(vue: rollup.SourceUsageView | None, note: str) -> tuple[str, str, bool, str, str]:
    """(appels, échecs, en échec ?, nature, dernière erreur)."""
    if vue is None or not (vue.calls or vue.indeterminate or vue.blocked_expected):
        return "—", "—", False, "", note
    appels = f"{vue.calls:,}".replace(",", " ")
    if vue.absent:
        appels += f" (dont {vue.absent} sans donnée)"
    echecs = f"{vue.comm_failures} ({vue.failure_rate:.1%})" if vue.comm_failures else "0"
    nature = LIBELLES_NATURE.get(vue.dominant_issue, vue.dominant_issue)
    if vue.blocked_expected:
        # Le contournement anti-bot reste VISIBLE sans être alarmant.
        suffixe = f"{vue.blocked_expected} anti-bot contournés"
        nature = f"{nature} · {suffixe}" if nature else suffixe
    derniere = (
        f"{vue.last_failure.replace('T', ' ')} — {vue.last_failure_msg}"
        if vue.last_failure
        else note
    )
    return appels, echecs, bool(vue.comm_failures), nature, derniere


def familles(
    sources: Sequence,
    sante: Mapping[str, Mapping],
    vues: Mapping[str, rollup.SourceUsageView],
) -> list[Famille]:
    """Les sections du panneau, dans l'ordre d'affichage. `sources` : les
    `SourceSpec` (`utils.source_health.SOURCES`)."""
    par_famille: dict[str, list] = {f: [] for f in FAMILY_ORDER}
    for spec in sources:
        for f in spec.families:
            par_famille.setdefault(f, []).append(spec)
    agregats = {
        s.family: s
        for s in rollup.summarize_by_family(vues, {spec.key: spec.families for spec in sources})
    }
    res = []
    for f in FAMILY_ORDER:
        lignes = []
        for spec in par_famille.get(f, []):
            statut, sonde, detail = _sonde(sante.get(spec.key))
            appels, echecs, en_echec, nature, erreur = _usage(
                vues.get(spec.key), spec.usage_note or ""
            )
            lignes.append(
                LigneSante(
                    spec.key,
                    spec.label,
                    statut,
                    sonde,
                    detail,
                    appels,
                    echecs,
                    en_echec,
                    nature,
                    erreur,
                )
            )
        a = agregats.get(f)
        if a is not None and a.calls:
            agregat = f"{a.calls:,} appels · {a.failure_rate:.1%} d'échec".replace(",", " ")
        else:
            agregat = "aucun appel sur la période"
        if a is not None and a.indeterminate:
            agregat += f" · ⚠ {a.indeterminate} indéterminés"
        res.append(Famille(f, FAMILY_LABELS[f], agregat, tuple(lignes)))
    return res


def bandeau(episodes) -> str:
    """Le bandeau des pannes LOCALES (réseau de la machine), vide sans elles."""
    if not episodes:
        return ""
    dernier = episodes[-1]
    quand = dernier.start.strftime("%d/%m à %H:%M")
    return (
        f"⚠ Connexion locale instable le {quand} — {len(dernier.sources)} sources touchées "
        "simultanément : leurs échecs sont écartés du taux."
    )


def sources_cassees(sante: Mapping[str, Mapping]) -> int:
    """Le compte de la pastille de l'icône santé : sources cassées à la sonde."""
    return sum(1 for etat in sante.values() if (etat or {}).get("status") == "broken")
