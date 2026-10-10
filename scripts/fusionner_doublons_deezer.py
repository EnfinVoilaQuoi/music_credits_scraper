"""Fusionne les fiches créées depuis Deezer avec la page Genius du même morceau
(CORRECTIFS 2.2, première partie).

Avant la phase 1, `discographie.fusionner` ne rattachait une page Genius à une
fiche SANS page Genius que par `title.lower().strip()` : l'apostrophe ’/', le
« œ », « (feat. X) », « (Live 2006) » / « - Live 2006 » ou « 3 G » / « 3G »
suffisaient à créer un doublon (22 paires mesurées le 2026-10-09). Même
critère que le correctif (`version_descriptors.titres_confondus`, jamais un
titre générique) ; la fiche GENIUS est gardée, l'autre y est fusionnée par
`DataManager.merge_tracks` (crédits, observations, vidéos, ID, parutions
réunis ; vues YouTube Music recalculées).

Une paire est AMBIGUË — plusieurs pages Genius au titre équivalent pour une
même fiche Deezer, ou l'inverse — et alors listée sans être fusionnée.

    python scripts/fusionner_doublons_deezer.py            # dry-run (défaut)
    python scripts/fusionner_doublons_deezer.py --apply    # backup + écriture
"""

from __future__ import annotations

import argparse
import collections
import sys

from src.utils.data_manager import DataManager
from src.utils.database_backup import get_backup_manager
from src.utils.title_matching import normalize_title
from src.utils.version_descriptors import titre_generique, titres_confondus


def _compact(titre: str) -> str:
    return normalize_title(titre or "").replace(" ", "")


def _preuve(s, g) -> bool:
    """Même titre à la graphie près, ou un identifiant commun : deux remix d'un
    même morceau sont « équivalents » pour `titres_confondus` (« Nougat (Grace
    Kim Remix Extended) » / « (Grace Kim Remix) ») sans être le même
    enregistrement."""
    return _compact(s.title) == _compact(g.title) or bool(
        (s.deezer_id and str(s.deezer_id) == str(g.deezer_id))
        or (s.isrc and g.isrc and str(s.isrc).upper() == str(g.isrc).upper())
        or (s.spotify_id and s.spotify_id == g.spotify_id)
    )


def paires(dm) -> tuple[list, list]:
    """(paires sûres (garde, absorbée, artiste), cas ambigus)."""
    sures, ambigus = [], []
    for nom in dm.get_artist_names():
        artiste = dm.get_artist_by_name(nom)
        tracks = dm.get_artist_tracks(artiste.id)
        avec = [t for t in tracks if t.genius_id and not titre_generique(t.title)]
        sans = [t for t in tracks if not t.genius_id and not titre_generique(t.title)]
        prises: dict[int, list] = collections.defaultdict(list)
        retenues = []
        for s in sans:
            pages = [g for g in avec if titres_confondus(s.title, g.title)]
            if not pages:
                continue
            fortes = [g for g in pages if _preuve(s, g)]
            if len(fortes) == 1:
                retenues.append((fortes[0], s))
                prises[fortes[0].id].append(s)
            else:
                ambigus.append((s, pages, nom))
        for g, s in retenues:
            if len(prises[g.id]) == 1:
                sures.append((g, s, nom))
            else:
                ambigus.append((s, [g], nom))
    return sures, ambigus


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--apply", action="store_true", help="écrit (backup automatique avant)")
    a = p.parse_args(argv)
    dm = DataManager()
    sures, ambigus = paires(dm)
    for garde, absorbee, nom in sures:
        print(
            f"  [{nom}] #{absorbee.id} « {absorbee.title} » → #{garde.id} « {garde.title} » "
            f"(g{garde.genius_id})"
        )
    for s, pages, nom in ambigus:
        cibles = ", ".join(f"#{g.id} « {g.title} »" for g in pages)
        print(f"  ❓ [{nom}] #{s.id} « {s.title} » : {cibles} — non fusionné")
    print(f"\n{len(sures)} fusion(s) sûre(s), {len(ambigus)} ambiguë(s)")
    if not a.apply:
        print("(dry-run — relancer avec --apply pour écrire)")
        return 0
    backup = get_backup_manager().create_backup("before_fusionner_doublons_deezer")
    if not backup:
        print("❌ Backup impossible — abandon (règle projet : jamais d'écriture sans backup).")
        return 1
    print(f"💾 Backup : {backup}")
    faites = sum(1 for garde, absorbee, _ in sures if dm.merge_tracks(garde.id, absorbee.id))
    print(f"✅ {faites} fusion(s) faite(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
