"""Rattrapage : les invités d'un album Deezer posés sur une piste qui ne les crédite pas.

    python scripts/retirer_feats_d_album.py                 # à blanc (défaut)
    python scripts/retirer_feats_d_album.py --apply         # backup + écriture
    python scripts/retirer_feats_d_album.py --base copie.db # sur une autre base

Jusqu'au 2026-11-06, une fiche créée depuis Deezer par le correcteur (sans lecture de
`/track/{id}`) prenait les contributeurs de l'ALBUM : « 28 décembre 1977 » du live de
Kery James avait dix invités, dont Diam's, qui recevait une ligne à son nom.

Preuve exigée pour chaque crédit : fiche sans page Genius, feat de source `deezer`,
nom absent des contributeurs de la piste, présent dans ceux de son album, album de
plusieurs pistes. Deezer ne répond pas : la fiche est laissée, à relire au prochain
passage. La ligne donnée à un de ces faux invités (même ID Deezer, sans page) est
supprimée ; les autres crédits sont retirés de la fiche.

Ces mêmes fiches n'ont jamais lu les invités de LEUR piste : toutes les fiches sans page
au même ID sont relues (`ecarts_deezer.relire_contributeurs`), leurs vrais invités ajoutés.
"""

import argparse
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import text


@dataclass
class Fiche:
    id: int
    artiste: str
    titre: str
    deezer_id: int
    is_featuring: bool
    feats: list[str] = field(default_factory=list)
    faux: list[str] = field(default_factory=list)

    @property
    def a_supprimer(self) -> bool:
        """La ligne n'existe que parce que son artiste était crédité à tort."""
        from src.utils.title_matching import normalize_name

        return self.is_featuring and normalize_name(self.artiste) in {
            normalize_name(n) for n in self.faux
        }


def faux_invites(feats, de_la_piste, de_l_album, nb_pistes) -> list[str]:
    """PUR. Les feats que seul l'album crédite, sur un disque de plusieurs pistes."""
    from src.utils.title_matching import normalize_name

    if not nb_pistes or nb_pistes < 2:
        return []
    piste = {normalize_name(n) for n in de_la_piste}
    album = {normalize_name(n) for n in de_l_album}
    return [n for n in feats if normalize_name(n) not in piste and normalize_name(n) in album]


def lire_fiches(dm) -> list[Fiche]:
    fiches: dict[int, Fiche] = {}
    with dm.engine.connect() as conn:
        for tid, artiste, titre, deezer_id, featuring, nom in conn.execute(
            text(
                "SELECT t.id, a.name, t.title, t.deezer_id, t.is_featuring, c.name "
                "FROM credits c JOIN tracks t ON t.id = c.track_id "
                "JOIN artists a ON a.id = t.artist_id "
                "WHERE c.source = 'deezer' AND c.role = 'Featured Artist' "
                "AND t.genius_id IS NULL AND t.deezer_id IS NOT NULL ORDER BY t.id, c.id"
            )
        ):
            fiche = fiches.setdefault(
                tid, Fiche(tid, artiste, titre, int(deezer_id), bool(featuring))
            )
            fiche.feats.append(nom)
    return list(fiches.values())


def lire_album_http(album_id: int) -> dict | None:
    """`GET /album/{id}` : contributeurs du disque et nombre de pistes."""
    import requests

    try:
        resp = requests.get(f"https://api.deezer.com/album/{int(album_id)}", timeout=15)
        data = resp.json() if resp.ok else None
    except (requests.RequestException, ValueError):
        return None
    if not isinstance(data, dict) or data.get("error") or not data.get("id"):
        return None
    return data


def juger(fiches: list[Fiche], lire_piste, lire_album, pause: float = 0.2) -> list[Fiche]:
    """Remplit `faux` ; rend les fiches à réparer. Sans réponse, la fiche est laissée."""
    a_faire = []
    for fiche in fiches:
        piste = lire_piste(fiche.deezer_id)
        album_id = ((piste or {}).get("album") or {}).get("id")
        album = lire_album(album_id) if album_id else None
        if album is None:
            print(f"  ⚠️ sans réponse de Deezer : #{fiche.id} « {fiche.titre} » laissée")
            continue
        fiche.faux = faux_invites(
            fiche.feats,
            [c.get("name") or "" for c in piste.get("contributors") or []],
            [c.get("name") or "" for c in album.get("contributors") or []],
            album.get("nb_tracks"),
        )
        if fiche.faux:
            a_faire.append(fiche)
        time.sleep(pause)
    return a_faire


def rapport(a_faire: list[Fiche]) -> None:
    print(f"{len(a_faire)} fiche(s) à réparer")
    for f in a_faire:
        geste = "SUPPRIMÉE" if f.a_supprimer else f"{len(f.faux)} crédit(s) retiré(s)"
        print(f"  [{f.artiste}] #{f.id} « {f.titre} » — {geste} : {', '.join(f.faux)}")


def reparer(dm, a_faire: list[Fiche]) -> tuple[int, int]:
    """`(fiches supprimées, crédits retirés)`. Les lignes données d'abord."""
    supprimees = retires = 0
    for f in sorted(a_faire, key=lambda x: not x.a_supprimer):
        if f.a_supprimer:
            dm.delete_track(f.id)
            supprimees += 1
            continue
        for nom in f.faux:
            retires += dm.forget_credit(f.id, nom, "Featured Artist")
    return supprimees, retires


def fiches_sans_page(dm) -> list[tuple[int, int]]:
    """`[(track_id, artist_id)]` des fiches créées depuis Deezer, sans page Genius."""
    with dm.engine.connect() as conn:
        return [
            (r[0], r[1])
            for r in conn.execute(
                text(
                    "SELECT id, artist_id FROM tracks "
                    "WHERE genius_id IS NULL AND deezer_id IS NOT NULL ORDER BY id"
                )
            )
        ]


def lecteur_en_memoire(lire_piste, pause: float = 0.2):
    """Une piste n'est lue qu'une fois : à blanc, puis à l'écriture."""
    lues: dict[int, dict | None] = {}

    def lire(deezer_id: int) -> dict | None:
        if deezer_id not in lues or lues[deezer_id] is None:
            lues[deezer_id] = lire_piste(deezer_id)
            time.sleep(pause)
        return lues[deezer_id]

    return lire


def apercu_des_invites(dm, lire_piste) -> list[str]:
    """À blanc : les vrais invités que la relecture ajouterait, après la réparation."""
    from src.services.ecarts_deezer import feats_manquants

    lignes = []
    fiches = dict(fiches_sans_page(dm))
    for artiste in dm.artistes_de_la_base():
        if artiste.id not in set(fiches.values()):
            continue
        for t in dm.get_artist_tracks(artiste.id):
            detail = lire_piste(int(t.deezer_id)) if t.id in fiches else None
            manquants = feats_manquants(t, artiste, detail) if detail else []
            if manquants:
                noms = ", ".join(c.name for c in manquants)
                lignes.append(f"  [{artiste.name}] #{t.id} « {t.title} » + {noms}")
    return lignes


def relire_tout(dm, lire_piste) -> list[str]:
    """Lève le drapeau sur les fiches sans page, puis les relit artiste par artiste."""
    from src.services import ecarts_deezer

    fiches = fiches_sans_page(dm)
    ecarts_deezer.noter_relecture(dm, fiches, "rattrapage du 2026-11-06")
    comptes = []
    artistes = {artist_id for _, artist_id in fiches}
    for artiste in dm.artistes_de_la_base():
        if artiste.id in artistes:
            comptes += ecarts_deezer.relire_contributeurs(dm, artiste, lire_piste)
    return comptes


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="écrit (backup automatique avant)")
    parser.add_argument("--base", type=Path, help="autre base que data/music_credits.db")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    import src.utils.data_manager as module_dm
    from src.utils.database_backup import DatabaseBackupManager, get_backup_manager
    from src.utils.deezer_identity import lire_piste_http
    from src.utils.logger import configurer_journaux

    configurer_journaux()
    if args.base:
        module_dm.DATABASE_URL = f"sqlite:///{args.base.resolve().as_posix()}"
    dm = module_dm.DataManager()
    print(f"Base : {dm.db_path}")

    lire_piste = lecteur_en_memoire(lire_piste_http)
    a_faire = juger(lire_fiches(dm), lire_piste, lire_album_http)
    rapport(a_faire)
    if not args.apply:
        apercu = apercu_des_invites(dm, lire_piste)
        print(f"\nRelecture : {len(fiches_sans_page(dm))} fiche(s), {len(apercu)} à compléter")
        print("\n".join(apercu))
        print("\n(à blanc — relancer avec --apply pour écrire)")
        return 0
    sauvegarde = (
        DatabaseBackupManager(
            db_path=str(dm.db_path), backup_dir=str(Path(dm.db_path).parent / "backups")
        )
        if args.base
        else get_backup_manager()
    )
    backup = sauvegarde.create_backup("before_retirer_feats_d_album")
    if not backup:
        print("❌ Backup impossible — abandon (règle projet : jamais d'écriture sans backup).")
        return 1
    print(f"💾 Backup : {backup}")
    supprimees, retires = reparer(dm, a_faire)
    print(f"✅ {supprimees} fiche(s) supprimée(s), {retires} crédit(s) retiré(s)")
    comptes = relire_tout(dm, lire_piste)
    print("\n".join(comptes))
    print(f"✅ relecture : {len(comptes)} ligne(s) de compte rendu")
    return 0


if __name__ == "__main__":
    sys.exit(main())
