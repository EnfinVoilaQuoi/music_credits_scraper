"""Nature des disques : combler `albums.record_type` pour TOUS les artistes.

`_types_albums_deezer` ne tourne qu'en FIN d'enrichissement, une fois par
artiste. Résultat mesuré le 2026-09-23 : sur les 7 436 morceaux qui portent un
album, **2 457 seulement sont sur un disque typé** — 1 133 sur un disque connu
mais non qualifié, et 3 846 sur un disque absent de la table `albums`, qui
n'est pas un catalogue mais le magasin des streams d'album.

Ce trou a un effet précis : la validation **n'exige pas les paroles
synchronisées** quand elle ne sait pas si le disque est un album ou un single
(« Timestamps non exigés — nature du disque inconnue »). Un ⚠️ qu'on ne sait
pas justifier serait pire, mais personne ne réparait le trou.

Le script ne réimplémente RIEN : il rejoue `album_types.types_albums_deezer`,
la même coroutine que la fin de run, sur les morceaux déjà en base. Faute de
provider ayant tourné, il n'y a pas d'identifiant d'album Deezer sous la main :
c'est la voie « recherche d'album par (artiste, titre) » qui sert, avec sa
correspondance de titre normalisé EXACTE.

⚠️ Une valeur `manual` n'est jamais écrasée (garde-fou de `set_album_record_type`).

⚠️ **Beaucoup de ces disques n'existent pas chez un distributeur** : « Yandhi »,
« Kanye West's Visionary Streams of Consciousness », « Owl Pharaoh » sont des
compilations d'INÉDITS faites par la communauté Genius. Deezer ne les connaît
pas, et c'est le bon résultat — le rapport les compte à part plutôt que de les
présenter comme des échecs.

Usage :
    python scripts/fill_album_types.py                     # inventaire, SANS réseau
    python scripts/fill_album_types.py --apply             # interroge Deezer et écrit
    python scripts/fill_album_types.py --apply --artiste "SCH"
"""

import argparse
import sys

# Fix encodage Windows (règle projet : reconfigure, jamais de re-wrapping)
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.concurrency import async_loop
from src.enrichment.album_types import regrouper, types_albums_deezer
from src.services.runtime import Runtime
from src.utils.database_backup import get_backup_manager
from src.utils.logger import get_logger

logger = get_logger(__name__)


def _albums_sans_type(runtime, artist) -> list[str]:
    """Titres d'albums de cet artiste dont la nature est inconnue."""
    deja = {a["title"]: a for a in runtime.data_manager.get_albums_for_artist(artist.id) or []}
    return [
        t for t in sorted(regrouper(artist.tracks)) if not (deja.get(t) or {}).get("record_type")
    ]


def _charger(runtime, nom: str):
    artist = runtime.data_manager.get_artist_by_name(nom)
    if artist is None:
        return None
    artist.tracks = runtime.data_manager.get_artist_tracks(artist.id)
    return artist


def inventaire(runtime, noms: list[str]) -> dict[str, list[str]]:
    """Ce qu'il y aurait à demander — aucune requête réseau."""
    par_artiste = {}
    for nom in noms:
        artist = _charger(runtime, nom)
        if artist is None or not artist.tracks:
            continue
        manquants = _albums_sans_type(runtime, artist)
        if manquants:
            par_artiste[nom] = manquants
    return par_artiste


async def _remplir(runtime, artist, force: bool):
    enricher, dm = runtime.data_enricher, runtime.data_manager
    deja = {a["title"]: a for a in dm.get_albums_for_artist(artist.id) or []}
    return await types_albums_deezer(
        enricher.deezer_client,
        enricher.http,
        artist.tracks,
        deja,
        lambda title, rt, did: dm.set_album_record_type(artist.id, title, rt, deezer_album_id=did),
        force=force,
        artist_name=artist.name,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="interroge Deezer et écrit")
    parser.add_argument("--artiste", action="append", help="limiter à cet artiste (répétable)")
    parser.add_argument("--force", action="store_true", help="redemander même les disques typés")
    args = parser.parse_args()

    runtime = Runtime.build()
    try:
        noms = args.artiste or runtime.data_manager.get_artist_names()
        par_artiste = inventaire(runtime, noms)
        total = sum(len(v) for v in par_artiste.values())
        print(f"\n{total} disque(s) sans nature, sur {len(par_artiste)} artiste(s) :\n")
        for nom, manquants in sorted(par_artiste.items(), key=lambda kv: -len(kv[1])):
            print(f"   {len(manquants):4}  {nom}")

        if not args.apply:
            print(
                "\nℹ️  INVENTAIRE : aucune requête Deezer n'a été faite."
                "\n   Relance avec --apply (une recherche d'album par disque)."
            )
            return 0
        if not total and not args.force:
            print("\nRien à faire.")
            return 0

        backup = get_backup_manager().create_backup("before_fill_album_types")
        if not backup:
            print("❌ Backup impossible — abandon (règle projet : jamais d'écriture sans backup).")
            return 1
        print(f"\n💾 Backup : {backup}\n")

        renseignes = ignores = 0
        introuvables: list[str] = []
        for nom in sorted(par_artiste) if not args.force else noms:
            artist = _charger(runtime, nom)
            if artist is None or not artist.tracks:
                continue
            print(f"── {nom}")
            bilan = async_loop.run_sync(_remplir(runtime, artist, args.force))
            renseignes += bilan.renseignes
            ignores += bilan.ignores
            introuvables.extend(
                m for m in bilan.motifs if "aucune fiche Deezer" in m or "aucun hit" in m
            )

        print(f"\n✅ {renseignes} disque(s) qualifié(s), {ignores} laissé(s) sans nature.")
        if introuvables:
            print(
                f"\nℹ️  {len(introuvables)} disque(s) que Deezer ne connaît pas — souvent des"
                "\n   compilations d'inédits faites par la communauté Genius. Ce n'est pas"
                "\n   un échec : ces disques n'ont pas de nature à déclarer.\n"
            )
            for motif in introuvables[:20]:
                print(f"   {motif}")
        return 0
    finally:
        try:
            runtime.data_enricher.close()
        except Exception as e:  # noqa: BLE001 - fermeture best-effort
            logger.debug(f"Fermeture de l'enricher : {e}")


if __name__ == "__main__":
    sys.exit(main())
