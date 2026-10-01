"""Verdict d'une recherche de certifications PAR ARTISTE — un vocabulaire pour les quatre.

« Aucune certification trouvée » disait jusqu'au 2026-10-01 deux choses
opposées : la source a répondu qu'elle n'avait rien sous ce nom (un artiste
jamais certifié au Royaume-Uni, c'est le cas NORMAL de la plupart des rappeurs
français), ou la page n'a jamais été lue (Cloudflare, gabarit changé, réseau).
Les deux sortaient en code 1 et la fenêtre les affichait en ❌ ÉCHEC — si bien
qu'un vrai échec se noyait dans les absences légitimes.

Trois issues, et la règle qui les sépare est celle d'`absent` en observabilité :
**une absence ne se conclut que sur un constat POSITIF de la source**. Chaque
site dit « rien » à sa manière, et c'est ce message qu'on exige :

  · RIAA — bloc `.tw-gnp-no-results` (« No matching results ») ;
  · SNEP — « Désolé, aucun résultat ne correspond à vos critères de sélection » ;
  · BPI  — annuaire `/artists?q=` qui rend un corps VIDE en 200, liste qui porte
    « No certified awards found » ;
  · BRMA — pas de requête (Ultratop n'a pas de recherche par artiste), lecture du
    corpus local : l'absence y vaut ce que vaut le corpus, et on donne sa date.

Une page sans résultat ET sans ce message (défi Cloudflare, refonte) est un
ÉCHEC — jamais une absence, le seul verdict capable de rendre une panne muette.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

__all__ = ["CODE_ECHEC", "Issue", "Verdict", "conclure"]

#: Code de sortie d'un script dont au moins un nom n'a pas pu être lu. Une
#: absence légitime sort en 0 : la recherche a ABOUTI, elle n'a juste rien rendu.
CODE_ECHEC = 1


class Issue(StrEnum):
    CERTIFIE = "certifie"  # la source a rendu des certifications sous ce nom
    ABSENT = "absent"  # la source a DIT n'avoir rien sous ce nom
    ECHEC = "echec"  # pas de réponse exploitable : on ne sait pas


@dataclass(frozen=True)
class Verdict:
    nom: str
    issue: Issue
    #: Certifications rendues par la source (CERTIFIE seulement).
    lignes: int = 0
    #: Ce qui a été constaté — le message de la source, ou la cause de l'échec.
    motif: str = ""

    def texte(self) -> str:
        if self.issue is Issue.CERTIFIE:
            return f"✅ {self.nom} : {self.lignes} certification(s)" + (
                f" ({self.motif})" if self.motif else ""
            )
        if self.issue is Issue.ABSENT:
            return f"∅ {self.nom} : aucune certification" + (
                f" ({self.motif})" if self.motif else ""
            )
        return f"❌ {self.nom} : {self.motif or 'échec'}"


def conclure(verdicts: Iterable[Verdict]) -> tuple[int, str]:
    """(code de sortie, ligne de bilan) d'une recherche sur plusieurs noms.

    La ligne est imprimée EN DERNIER par le script : c'est elle que
    `services.certifs.ligne_bilan` reprend dans la fenêtre, préfixée du nom de
    la source — d'où son absence ici. Un seul nom en échec
    rend le run non nul — avant, « au moins un nom a rendu quelque chose »
    suffisait, ce qui n'avait de sens que tant qu'une absence passait pour un
    échec ; maintenant qu'elles sont séparées, taire un échec derrière le
    succès d'un autre nom serait un run partiel annoncé complet.
    """
    verdicts = list(verdicts)
    code = CODE_ECHEC if any(v.issue is Issue.ECHEC for v in verdicts) else 0
    return code, " · ".join(v.texte() for v in verdicts) or "aucun nom demandé"
