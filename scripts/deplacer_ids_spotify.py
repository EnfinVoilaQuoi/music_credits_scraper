"""Déplace les IDs Spotify posés sur la MAUVAISE fiche (étape 5, 2026-09-24).

Oracle : la page Kworb de chaque artiste, qui associe un ID à SON titre. Quand
l'ID d'une ligne est porté par une fiche au titre différent alors qu'UNE fiche
porte exactement le titre de la ligne (`update_kworb.rapprocher`, voie
`id_mal_place`), deux cas :

  · DOUBLON probable — titres quasi identiques (censure « Ni**as In Paris » /
    « Niggas in Paris », coquille « Genny & Ciiro » / « Genny & Ciro »), ou fiche
    du titre SANS page Genius (créée par Deezer / Kworb sous un autre libellé) :
    fusion PROPOSÉE, rien n'est touché (décision humaine, fichier de corrections) ;
  · sinon l'ID est DÉPLACÉ : retiré de la fiche porteuse avec ce qui en
    découlait (`clear_track_spotify_id`, trois gestes), posé sur la fiche du
    titre si elle n'en a pas (via le gate d'identité). Le gate refuse ensuite
    de le reposer sur la mauvaise fiche (`fiche_du_titre_spotify`).

    python scripts/deplacer_ids_spotify.py            # dry-run
    python scripts/deplacer_ids_spotify.py --apply    # backup + écriture
"""

import argparse
import difflib
import re
import sys
import time

if sys.platform == "win32" and "pytest" not in sys.modules:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.utils.title_matching import normalize_title


def doublon_evident(a: str, b: str) -> bool:
    """PUR. Deux titres qui ne diffèrent que par une censure (« Ni**as »), une
    coquille ou la ponctuation désignent la même fiche en double."""
    na, nb = normalize_title(a), normalize_title(b)
    if na == nb:
        return True
    for censure, clair in ((a, b), (b, a)):
        if "*" in censure:
            motif = re.escape(normalize_title(censure.replace("*", "_"))).replace("_", ".")
            if re.fullmatch(motif, normalize_title(clair.replace("*", "_"))):
                return True
    # Une coquille garde le nombre de MOTS (« Ciiro » / « Ciro ») ; « Freestyle »
    # / « Freestyle 4 » ressemble à 90 % et désigne deux morceaux.
    mots = lambda t: re.findall(r"\w+", t.lower())  # noqa: E731
    if len(mots(a)) != len(mots(b)):
        return False
    return difflib.SequenceMatcher(None, na, nb).ratio() >= 0.9


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="écrire (défaut : dry-run)")
    args = parser.parse_args()

    from src.scrapers.kworb_scraper import KworbScraper
    from src.utils import update_kworb as uk
    from src.utils.data_manager import DataManager
    from src.utils.kworb_links_manager import KworbLinksManager
    from src.utils.spotify_identity import lire_identite_http, valider_identite

    dm = DataManager()
    if args.apply:
        from src.utils.database_backup import get_backup_manager

        print(f"💾 Backup : {get_backup_manager().create_backup('before_deplacer_ids_spotify')}")
    scraper = KworbScraper()
    compte = {"deplaces": 0, "doublons": 0}
    for nom in dm.get_artist_names():
        artiste = dm.get_artist_by_name(nom)
        page, _ = uk._scrape_validated(scraper, artiste, dm, artiste.spotify_id)
        if not page:
            continue
        index = uk.construire_index(dm.get_artist_tracks(artiste.id))
        decisions = KworbLinksManager().load(artiste.name)
        lignes = []
        for entree in page["entries"]:
            r = uk.rapprocher(entree, index, artiste, decisions, lambda s: None)
            if r.via != "id_mal_place":
                continue
            sid = entree["spotify_id"]
            porteur = index.by_edition_id[sid]
            bonne = r.track
            # Fiche « au bon titre » SANS page Genius (créée par Deezer / Kworb
            # sous un titre écrit autrement) : presque toujours le MÊME morceau
            # que la fiche Genius (« Strass & paillettes » / « Strass et
            # paillettes ») — mesuré 2026-09-24 : 21 cas. Fusion PROPOSÉE.
            if doublon_evident(porteur.title, bonne.title) or (
                not bonne.genius_id and porteur.genius_id
            ):
                compte["doublons"] += 1
                lignes.append(
                    f"   🔁 à fusionner ? « {porteur.title} » ({porteur.id}) / "
                    f"« {bonne.title} » ({bonne.id}"
                    f"{', créée par Deezer/Kworb' if not bonne.genius_id else ''})"
                    f" — {entree['streams']:,} streams"
                )
                continue
            compte["deplaces"] += 1
            pose = ""
            if args.apply:
                dm.clear_track_spotify_id(porteur.id, sid)
                if not bonne.spotify_id and valider_identite(bonne, sid, lire_identite_http):
                    dm.update_track_spotify_id(bonne.id, sid)
                    pose = " (posé sur la bonne fiche)"
            lignes.append(
                f"   🔀 ID de « {bonne.title} » retiré de « {porteur.title} »"
                f"{pose} — {entree['streams']:,} streams"
            )
        if lignes:
            print(f"== {nom}")
            print("\n".join(lignes))
        time.sleep(1)
    print(f"\nTOTAL {compte}")
    if not args.apply:
        print("(dry-run — relancer avec --apply pour écrire)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
