"""Règles de COUVERTURE TEMPORELLE, partagées par les trois validateurs de certifs.

Un mois sans certification n'est une anomalie que si l'année est assez fournie
pour qu'un mois vide soit SURPRENANT. Les trois validateurs appliquaient jusqu'ici
le même seuil arbitraire (« au moins 12 certifications dans l'année »), écrit en
trois exemplaires — et trop bas : sur une année à 16 certifications, des mois
vides sont le régime normal, pas un défaut de collecte. Le rapport RIAA
signalait ainsi 17 mois « manquants » sur 1960-1964, et le rapport SNEP 12 sur
1991-1993 et 2014, tous parfaitement explicables par la rareté des
certifications de ces années-là.

Le seuil n'est plus posé au doigt mouillé mais DÉRIVÉ. Sous une répartition
uniforme, la probabilité qu'un mois donné soit vide vaut (11/12)^n, donc le
nombre de mois vides ATTENDUS dans l'année vaut 12·(11/12)^n. On n'analyse les
trous que là où cette espérance descend sous UN mois — soit n ≥ 29.

Mesuré sur les corpus réels le 2026-09-06 :
  · SNEP : 35 mois signalés → 19. Disparaissent exactement les quatre années
    creuses (1991 : 13 certifs, 1992 : 18, 1993 : 17, 2014 : 13) ; restent les
    trous des années fournies (2006 : 114 certifs et 4 mois vides, 2015 : 135
    et 4), qui sont les vrais défauts de collecte connus.
  · RIAA : 17 mois signalés → 6, tous sur 1962-1964 (29 à 42 certifications).
    Ceux-là RESTENT : sous le modèle énoncé ils sont improbables. Monter le
    seuil pour les faire taire reviendrait à régler la règle sur le résultat
    qu'on souhaite lire — c'est le contraire d'une mesure.
"""

from __future__ import annotations

from typing import NamedTuple

#: Espérance de mois vides au-delà de laquelle on renonce à conclure. Un mois :
#: en dessous, un trou est le régime normal de l'année.
_TOLERANCE_MOIS_VIDES = 1.0


def mois_vides_attendus(n_certifications: int) -> float:
    """Nombre de mois vides ATTENDUS pour `n` certifications réparties au hasard."""
    return 12 * (11 / 12) ** max(int(n_certifications), 0)


def annee_assez_dense(n_certifications: int) -> bool:
    """Un mois vide serait-il surprenant cette année-là ?

    False quand la rareté des certifications explique à elle seule les trous :
    les signaler ne dirait rien de la qualité de la collecte.
    """
    return mois_vides_attendus(n_certifications) < _TOLERANCE_MOIS_VIDES


# ─────────────────────────────────────────────── années qui s'EFFONDRENT
#: Une année « s'effondre » quand elle tombe sous le QUART de ses deux bornes.
_FACTEUR_EFFONDREMENT = 4


class Creux(NamedTuple):
    """Une suite d'années consécutives effondrées entre deux années fournies."""

    debut: int
    fin: int
    comptes: tuple[int, ...]
    avant: int
    apres: int

    def decrire(self) -> str:
        periode = str(self.debut) if self.debut == self.fin else f"{self.debut}-{self.fin}"
        return (
            f"{periode} : {', '.join(map(str, self.comptes))} certification(s) par an, "
            f"entre {self.avant} ({self.debut - 1}) et {self.apres} ({self.fin + 1})"
        )


def annees_effondrees(par_annee: dict[int, int]) -> list[Creux]:
    """Les suites d'années dont le volume s'effondre face à leurs DEUX bornes.

    Angle mort que ce module laissait (2026-10-10) : les trous mensuels ne sont
    cherchés que dans les années denses, donc une année qui s'effondre n'est
    jamais regardée — SNEP 2009-2014 (4 à 13 certifications par an entre deux
    années à 128 et 136) passait sans un mot, et il a fallu interroger le site
    à la main pour conclure que la source était vide aussi. Comparer une année à
    ses voisines immédiates ne suffit pas : le creux SNEP dure SIX ans, chaque
    année y a des voisines effondrées. La règle raisonne donc sur une SUITE,
    bornée de part et d'autre par une année dense (`annee_assez_dense`), chacune
    de ses années sous le quart des deux bornes. Le début d'un corpus (SNEP
    1976-1993, rampe avant 1994) n'est pas un effondrement : pas de borne avant.

    Mesuré sur les quatre corpus le 2026-10-10 : SNEP 2009-2014 seulement ;
    RIAA, BRMA et BPI : rien. L'effondrement est NOMMÉ, même légitime — c'est
    une information, pas une anomalie à compter.
    """
    annees = sorted(par_annee)
    creux: list[Creux] = []
    i = 1
    while i < len(annees):
        avant = par_annee[annees[i - 1]]
        seuil = avant / _FACTEUR_EFFONDREMENT
        if not annee_assez_dense(avant) or par_annee[annees[i]] >= seuil:
            i += 1
            continue
        j = i
        while j + 1 < len(annees) and par_annee[annees[j + 1]] < seuil:
            j += 1
        if j + 1 < len(annees) and annees[j + 1] == annees[j] + 1:
            apres = par_annee[annees[j + 1]]
            comptes = tuple(par_annee[annees[k]] for k in range(i, j + 1))
            if annee_assez_dense(apres) and max(comptes) < apres / _FACTEUR_EFFONDREMENT:
                creux.append(Creux(annees[i], annees[j], comptes, avant, apres))
        i = j + 1
    return creux
