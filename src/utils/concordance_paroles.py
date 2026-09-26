"""Un texte synchronisé (LRC) est-il bien celui du morceau ? — module PUR.

Mesuré le 2026-09-26 : la recherche YTM retenait le 1ᵉʳ résultat du bon ARTISTE
sans regarder le titre, et le départage par la durée ne voyait rien (la durée
de la fiche venait souvent d'une source trompée de la même façon). Résultat :
**1 146 LRC YTM faux sur 2 279 rejoués (50 %)** — *Intro (A2)* de Booba avec
le LRC de *G5 (Intro)*, *J'me retourne pas* d'A2H avec « Où est le paradis ? ».
LRCLIB aussi, beaucoup moins (66 sur 3 723 : titres courts et banals — *Sky*,
*Up*, *OK*, *Home*) ; 9 anciens LRC sans source chez Isha et PLK (*Mon dernier
mot* portait *Fin de ce monde*, 98 % de mots communs).

L'oracle : le recouvrement de MOTS avec les paroles Genius du morceau. Mots de
plus de 2 lettres, accents retirés, en-têtes « [...] » ignorés ; MAXIMUM des deux
sens, sans quoi des paroles Genius partielles (un extrait) face à un LRC complet
paraîtraient étrangères (*Wolves (BOOTS Reference)*). Seuils calibrés sur la
base : témoin LRCLIB à 95 % ≥ 0,8 ; sous 0,4 tous les exemples lus sont faux.
Le script de mesure est `scripts/oracle_lrc.py`.

Jugeable seulement si chaque texte a au moins `MOTS_MIN` mots distincts : en
dessous (instrumental, snippet, paroles absentes), on ne conclut RIEN — un LRC
non jugeable n'est pas un LRC faux.
"""

from __future__ import annotations

import re
import unicodedata

#: Sous ce recouvrement, le LRC est celui d'un autre morceau.
SEUIL_FAUX = 0.4
#: À partir de ce recouvrement, il est juste ; entre les deux, à trancher.
SEUIL_JUSTE = 0.6
#: Mots distincts exigés de CHAQUE côté pour conclure.
MOTS_MIN = 8

_ENTETE = re.compile(r"\[[^\]]*\]")
_MOT = re.compile(r"[a-z0-9']+")


def mots(texte: str | None) -> set[str]:
    """Les mots distincts d'un texte (LRC ou paroles), comparables entre eux."""
    t = unicodedata.normalize("NFKD", texte or "").encode("ascii", "ignore").decode().lower()
    return {w for w in _MOT.findall(_ENTETE.sub(" ", t)) if len(w) > 2}


def jugeable(paroles: str | None) -> bool:
    """Ces paroles suffisent-elles à juger un LRC ?"""
    return len(mots(paroles)) >= MOTS_MIN


def recouvrement(paroles: str | None, lrc: str | None) -> float | None:
    """Part de mots communs (max des deux sens) ; None si non jugeable."""
    mg, ml = mots(paroles), mots(lrc)
    if len(mg) < MOTS_MIN or len(ml) < MOTS_MIN:
        return None
    commun = len(mg & ml)
    return round(max(commun / len(mg), commun / len(ml)), 2)


def paroles_de_reference(texte: str | None, source: str | None) -> str | None:
    """Les paroles qui peuvent servir d'oracle : GENIUS seulement (ou héritées
    d'une fiche Genius, `heritage:<id>`). Un texte venu de YTM (« Source:
    LyricFind ») sort de la même recherche que le LRC qu'on juge — il serait
    aussi faux que lui, et démentirait un bon LRC LRCLIB."""
    s = (source or "").lower()
    return texte if s.startswith("genius") or s.startswith("heritage") else None


def lrc_dementi(paroles: str | None, lrc: str | None) -> bool:
    """Les paroles Genius DÉMENTENT ce LRC : c'est celui d'un autre morceau.

    False quand on ne peut pas juger — seul un démenti écarte une source."""
    score = recouvrement(paroles, lrc)
    return score is not None and score < SEUIL_FAUX
