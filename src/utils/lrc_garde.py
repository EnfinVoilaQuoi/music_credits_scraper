"""Garde des paroles SYNCHRONISÉES : un LRC appartient à UN fichier audio
(CORRECTIFS 1.5 / 1.7 / 1.8, décisions utilisateur du 2026-10-09).

L'oracle de concordance (`concordance_paroles`) juge un LRC contre les paroles
Genius DE LA FICHE ; sans paroles jugeables il ne dit rien, et LRCLIB / YTM
rendent alors le morceau voisin (« Fin » ← « Histoire sans fin », « Love » ←
« NO LOVE » : 51 fiches mesurées). Cette garde regarde les AUTRES fiches de
l'artiste — ce que le résolveur, qui ne voit qu'un morceau, ne peut pas faire.

Quatre refus, du plus net au plus fin :

- **instrumental** (titre ou constat) : aucun LRC, jamais ;
- **trop long** : un LRC minuté au-delà de la durée CONFIRMÉE de la fiche
  (deux catalogues indépendants) est celui d'un autre audio (2026-10-13) ;
- **version** : le LRC exact d'une autre fiche du MÊME socle n'est gardé que
  pour le même enregistrement (remaster, explicit/clean, album version, bonus,
  version promo) — retiré pour radio edit, live, acoustique, remix, chopped,
  démo, prise alternative, snippet, référence, sped up/slowed, a cappella. La
  durée ne CONFIRME rien (elle peut venir du même faux rapprochement que le LRC) ;
- **autre morceau** : le LRC exact d'une fiche au titre NON équivalent, que les
  paroles de cette fiche-là confirment (≥ 0,6 de paires) et que celles de la
  fiche visée ne confirment pas.

Module PUR : `GardeLrc(tracks)` s'indexe une fois ; `noter(track)` y ajoute un
LRC retenu en cours de run (sans quoi deux fiches du même run se le
partageraient).
"""

from __future__ import annotations

import re

from src.utils.concordance_paroles import jugeable, paroles_de_reference, recouvrement_bigrammes
from src.utils.version_descriptors import meme_socle, parse_variant, titres_confondus
from src.utils.version_heritage import est_instrumental, famille_de
from src.utils.vote_durees import FRAGILES, HORS_VOTE, famille

#: Familles d'un MÊME enregistrement : le minutage de l'original y vaut.
MEME_ENREGISTREMENT = frozenset({"edition", "remaster"})

#: Seuil de « ces paroles confirment ce LRC » (paires de mots, cf. revue).
CONFIRME = 0.6

_RADIO_EDIT = re.compile(r"\bradio\s+(?:edit|version|mix)\b", re.IGNORECASE)


def _cle(lrc: str | None) -> str:
    return "\n".join(ligne.strip() for ligne in (lrc or "").strip().splitlines() if ligne.strip())


def _confirme(track, lrc: str) -> bool:
    ref = paroles_de_reference(track.lyrics.text, track.lyrics.source)
    return bool(ref and jugeable(ref) and (recouvrement_bigrammes(ref, lrc) or 0) >= CONFIRME)


def sans_lrc(track) -> bool:
    """Un instrumental (constaté ou au titre) n'a AUCUN LRC."""
    return bool(track.lyrics.instrumental) or est_instrumental(track.title)


def _autre_enregistrement(titre: str) -> bool:
    """La fiche est-elle une AUTRE prise ou un audio transformé ? Le radio edit
    n'est pas une « version » pour `parse_variant` (même fiche que l'original),
    mais il est COUPÉ : le minutage diverge après la première coupe."""
    famille = famille_de(titre)
    if famille is not None:
        return famille not in MEME_ENREGISTREMENT
    edition = parse_variant(titre).edition or ""
    return bool(_RADIO_EDIT.search(edition))


#: Au-delà, deux durées ne sont pas le même fichier audio (2 s = même fichier,
#: 5 s = même enregistrement d'une plateforme à l'autre, cf. écarts Deezer).
ECART_DUREE_S = 5


_HORODATAGE = re.compile(r"\[(\d+):(\d{1,2})(?:[.:]\d+)?\]")


def duree_confirmee(track) -> int | None:
    """PUR. La durée de la fiche si DEUX catalogues indépendants la donnent
    (±3 s), sinon None : une durée isolée peut être celle du faux rapprochement
    que le LRC trahit (« Swagger Like Us » à 167 s)."""
    try:
        duree = int(track.duration)
    except (TypeError, ValueError):
        return None
    familles = set()
    for source, valeur in (getattr(track, "durations_observees", None) or {}).items():
        if source in FRAGILES or source in HORS_VOTE or not valeur:
            continue
        if abs(int(valeur) - duree) <= 3:
            familles.add(famille(source))
    return duree if len(familles) >= 2 else None


def fin_du_lrc(lrc: str | None) -> int | None:
    """PUR. Le dernier horodatage d'un LRC, en secondes."""
    fins = [int(m) * 60 + int(s) for m, s in _HORODATAGE.findall(lrc or "")]
    return max(fins) if fins else None


def _durees_dementent(track, autre) -> bool:
    try:
        a, b = int(track.duration), int(autre.duration)
    except (TypeError, ValueError):
        return False
    return abs(a - b) > ECART_DUREE_S


class GardeLrc:
    def __init__(self, tracks=()):
        self._par_lrc: dict[str, list] = {}
        for t in tracks:
            self.noter(t)

    def noter(self, track) -> None:
        cle = _cle(track.lyrics.synced)
        if not cle:
            return
        porteurs = self._par_lrc.setdefault(cle, [])
        if not any(t is track for t in porteurs):  # identité, pas l'égalité de dataclass
            porteurs.append(track)

    def motif(self, track, lrc: str | None) -> str | None:
        """Pourquoi ce LRC n'est pas celui de `track`, ou None s'il peut l'être."""
        if not lrc:
            return None
        if sans_lrc(track):
            return "instrumental : aucun LRC"
        # Un LRC ne court pas après la fin du morceau : au-delà de la durée
        # CONFIRMÉE, il minute un autre audio (30 fiches mesurées le 2026-10-13 :
        # « Pharaon revient » 66 s portait un LRC de 8 min 34).
        duree, fin = duree_confirmee(track), fin_du_lrc(lrc)
        if duree and fin and fin > duree + ECART_DUREE_S:
            return f"LRC minuté jusqu'à {fin} s, fiche de {duree} s (durée confirmée)"
        for autre in self._par_lrc.get(_cle(lrc), []):
            if autre is track or (track.id is not None and autre.id == track.id):
                continue
            if track.genius_id and autre.genius_id == track.genius_id:
                continue  # ligne sœur : le même enregistrement chez un autre artiste
            if meme_socle(track.title, autre.title):
                if _autre_enregistrement(track.title) and not _autre_enregistrement(autre.title):
                    return f"LRC de l'original « {autre.title} » (autre enregistrement)"
                if (
                    famille_de(track.title) is not None
                    and famille_de(autre.title) is None
                    and _durees_dementent(track, autre)
                ):
                    # « X Version » range en « édition » (*Version* l'emporte) :
                    # « PABLO (Stem Player Version) » 372 s, « Cold (Yeezus Tour
                    # Version) »… La durée ne CONFIRME jamais, elle DÉMENT ici
                    # (décision utilisateur 2026-10-09).
                    return (
                        f"LRC de « {autre.title} » ({autre.duration} s), "
                        f"fiche de {track.duration} s"
                    )
                continue
            if titres_confondus(track.title, autre.title):
                continue  # doublon de fiche : affaire de fusion, pas de LRC
            if _confirme(autre, lrc) and not _confirme(track, lrc):
                return f"LRC de « {autre.title} », que ses paroles confirment"
        return None
