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
