"""Passage à BLANC des politiques d'arbitrage automatique du panneau « À trancher ».

Règle projet « simuler avant d'activer » : aucune politique de
`src/services/politiques_revue.py` n'est branchée. Ce script les applique, sur
une COPIE de la base (sauvegarde SQLite en ligne ; la vraie base n'est que
lue), à toutes les DÉCISIONS actives du panneau, et écrit la liste de ce que
chacune trancherait, et dans quel sens — pour que l'utilisateur valide famille
par famille. Aucune écriture, aucun réseau : le gate d'identité Spotify relit
les pages embed déjà gardées au brut (`data/brut/brut.db`, ouvert en lecture
seule) ; `--reseau` lit par un GET nu les pages jamais gardées (aucune
écriture) ; une page illisible laisse le cas non jugé.

    python scripts/simuler_politiques_revue.py [--sortie rapport.md] [--artiste A2H]
"""

from __future__ import annotations

import argparse
import collections
import shutil
import sqlite3
import sys
import tempfile
import zlib
from contextlib import closing
from datetime import date
from pathlib import Path

from src.config import DATA_DIR

BASE = Path(DATA_DIR) / "music_credits.db"
BRUT = Path(DATA_DIR) / "brut" / "brut.db"


def copier_la_base(copie: Path) -> None:
    with (
        closing(sqlite3.connect(f"file:{BASE.as_posix()}?mode=ro", uri=True)) as source,
        closing(sqlite3.connect(copie)) as cible,
    ):
        source.backup(cible)


def lecteur_embed(reseau: bool):
    """`spotify_id -> identité | None` : la page embed gardée au brut (ouvert en
    LECTURE SEULE) ; avec `reseau`, une page jamais gardée est lue par un GET
    nu (rien n'est écrit, ni au brut ni en base). None : page illisible."""
    import time

    import requests

    from src.scrapers.spotify_id_scraper_v2 import _UA, SpotifyIDScraper

    conn = (
        sqlite3.connect(f"file:{BRUT.as_posix()}?mode=ro", uri=True, check_same_thread=False)
        if BRUT.exists()
        else None
    )
    cache: dict = {}

    def lire(sid: str):
        if sid in cache:
            return cache[sid]
        url = f"https://open.spotify.com/embed/track/{sid}"
        ligne = (
            conn
            and conn.execute(
                "SELECT corps FROM brut WHERE source = 'spotify_embed' AND requete = ? "
                "ORDER BY derniere_lecture DESC LIMIT 1",
                (url,),
            ).fetchone()
        )
        texte = zlib.decompress(ligne[0]).decode("utf-8") if ligne else None
        if texte is None and reseau:
            time.sleep(1)
            try:
                r = requests.get(url, timeout=15, headers={"User-Agent": _UA})
                texte = r.text if r.ok else None
            except requests.RequestException:
                texte = None
        cache[sid] = SpotifyIDScraper._identite_depuis_embed(texte) if texte else None
        return cache[sid]

    return lire


def gate_strict(lire):
    """Le gate d'identité STRICT (titres non tranchés), lecteur injecté ; le
    disque d'un titre générique n'est pas lu (page complète : un navigateur)."""
    from src.utils.spotify_identity import valider_identite

    return lambda track, sid: bool(valider_identite(track, sid, lire, lire_album=lambda _sid: None))


def rejouer_deezer(runtime, art) -> set | None:
    """Les `(nature, piste)` que le classement Deezer d'AUJOURD'HUI produit,
    rejoué hors ligne depuis le brut (aucun réseau, aucune écriture en base) ;
    None sans ID Deezer, ou si une réponse manque au brut (rejeu incomplet)."""
    from src.concurrency import async_loop
    from src.persistence import brut
    from src.services import ecarts_deezer

    if not art.deezer_id:
        return None
    enricher = runtime.data_enricher
    try:
        with brut.rejeu() as compte:
            bilan = async_loop.run_sync(
                ecarts_deezer.detecter_async(
                    enricher.deezer_client,
                    enricher.http,
                    runtime.data_manager,
                    art,
                    deezer_id=int(art.deezer_id),
                )
            )
    except (OSError, ValueError, RuntimeError) as e:
        print(f"   rejeu Deezer de {art.name} impossible : {e}")
        return None
    if compte["absentes"] or not bilan.complete:
        return None
    return {(e.nature, e.piste.id) for e in bilan.ecarts}


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--artiste", action="append", help="limiter à cet artiste (répétable)")
    p.add_argument(
        "--sortie",
        type=Path,
        default=Path(DATA_DIR) / "diagnostics" / f"politiques_revue_{date.today()}.md",
    )
    p.add_argument(
        "--reseau",
        action="store_true",
        help="lire les pages embed absentes du brut (GET nu, rien n'est écrit)",
    )
    a = p.parse_args(argv)

    dossier = Path(tempfile.mkdtemp(prefix="politiques_revue_"))
    copie = dossier / "copie.db"
    try:
        copier_la_base(copie)
        import src.utils.data_manager as module_dm

        module_dm.DATABASE_URL = f"sqlite:///{copie.as_posix()}"
        dm = module_dm.DataManager()
        if Path(dm.db_path).resolve() != copie.resolve():
            print(f"❌ la base ouverte n'est pas la copie : {dm.db_path}")
            return 1
        from src.services.runtime import Runtime, fermer

        runtime = Runtime.build()
        try:
            lignes = simuler(runtime.data_manager, a.artiste, reseau=a.reseau, runtime=runtime)
        finally:
            fermer(runtime)
        dm.engine.dispose()
    finally:
        shutil.rmtree(dossier, ignore_errors=True)
    a.sortie.parent.mkdir(parents=True, exist_ok=True)
    a.sortie.write_text("\n".join(lignes) + "\n", encoding="utf-8")
    print("\n".join(lignes[:60]))
    print(f"\n→ {a.sortie}")
    return 0


def simuler(dm, noms=None, *, reseau: bool = False, runtime=None) -> list[str]:
    from src.services import politiques_revue as pol
    from src.services import revue

    with closing(sqlite3.connect(dm.db_path)) as c:
        isrcs = {
            str(i).upper(): t
            for i, t in c.execute("SELECT isrc, title FROM tracks WHERE isrc IS NOT NULL")
        }
    lire = lecteur_embed(reseau)
    politiques = [
        *pol.POLITIQUES,
        pol.Politique(
            "id_spotify_propose",
            ("id_spotify_propose",),
            "ID Spotify proposé : fiche sans ID, gate strict + durée indépendante à 2 s",
            pol.politique_id_propose(lire, gate_strict(lire)),
        ),
        pol.Politique(
            "kworb",
            ("kworb_proposition", "kworb_a_confirmer"),
            "Kworb : la proposition ★ qui ne crée rien (variante, morceau existant)",
            pol.politique_kworb,
        ),
    ]
    tranches = collections.defaultdict(list)  # code -> [(artiste, cas, arbitrage)]
    pourquoi = collections.Counter()
    non_tranches = collections.defaultdict(list)
    decisions = collections.Counter()
    for nom in noms or dm.get_artist_names():
        art = dm.get_artist_by_name(nom)
        art.tracks = dm.discographie_reunie(art)
        ctx = revue.charger_contexte(dm, art)
        par_id = {t.id: t for t in art.tracks}
        ids_deezer = {art.deezer_id} | {
            t.artist.deezer_id for t in art.tracks if t.artist and t.artist.deezer_id
        }
        deezer = pol.Politique(
            "ecarts_deezer",
            ("deezer_absent", "deezer_album_absent", "deezer_version", "deezer_apparition"),
            "Écarts Deezer, par cas typique",
            pol.politique_ecarts_deezer(
                isrcs,
                {int(i) for i in ids_deezer if i},
                rejouer_deezer(runtime, art) if runtime else None,
            ),
        )
        for cas in revue.analyser(dm, art, ctx).actifs:
            if cas.nature != revue.DECISION:
                continue
            decisions[cas.detecteur] += 1
            track = par_id.get(cas.track_id)
            if cas.detecteur == "id_spotify_propose":
                lue = lire(cas.preuves.get("spotify_id_propose")) is not None
                pourquoi["page embed lue" if lue else "page embed illisible ou jamais lue"] += 1
            for politique in [*politiques, deezer]:
                if cas.detecteur not in politique.detecteurs:
                    continue
                arbitrage = politique.juger(cas, track, ctx)
                if arbitrage is None:
                    non_tranches[politique.code].append((art.name, cas))
                else:
                    tranches[politique.code].append((art.name, cas, arbitrage))
    return rapport(politiques, tranches, non_tranches, decisions, pourquoi)


def _generique(sens: str) -> str:
    """Le sens sans ses valeurs (durées, titres) : pour regrouper."""
    import re

    sens = re.sub(r"« [^»]* »", "« … »", sens)
    return re.sub(r"\d+", "N", sens.split(" : ")[0])[:90]


def rapport(politiques, tranches, non_tranches, decisions, pourquoi) -> list[str]:
    codes = [p.code for p in politiques] + ["ecarts_deezer"]
    libelles = {p.code: p.libelle for p in politiques}
    libelles["ecarts_deezer"] = "Écarts Deezer, par cas typique"
    total = sum(decisions.values())
    lignes = [
        f"# Passage à blanc des politiques « À trancher » — {date.today()}",
        "",
        "Aucune politique n'est active. Copie de la base, décisions actives "
        f"seulement : **{total}** cas.",
        "",
        "| politique | cas visés | tranchés | reste à l'humain |",
        "|---|---:|---:|---:|",
    ]
    for code in codes:
        t, n = len(tranches[code]), len(non_tranches[code])
        lignes.append(f"| `{code}` | {t + n} | {t} | {n} |")
    for variante in ("durees_vote_3", "durees_vote_2"):
        exclue = {"durees_vote_3", "durees_vote_2"} - {variante}
        uniques = {
            (nom, cas.detecteur, cas.cle)
            for code in codes
            if code not in exclue
            for nom, cas, _ in tranches[code]
        }
        lignes += [
            "",
            f"Si TOUTES les politiques étaient validées (durées : `{variante}`) : "
            f"{len(uniques)} tranchées, **{total - len(uniques)}** décisions restent "
            "à l'humain.",
        ]
    reste = collections.Counter(decisions)
    for _nom, det, _cle in uniques:  # la dernière variante : la plus large
        reste[det] -= 1
    lignes += ["", "Ce qui resterait, par détecteur :", ""]
    lignes += [f"- {n} × `{det}`" for det, n in reste.most_common() if n > 0]
    lignes += ["", "ID Spotify proposés — pages embed :", ""]
    lignes += [f"- {n} × {r}" for r, n in pourquoi.most_common()]
    for code in codes:
        lignes += ["", f"## `{code}` — {libelles[code]}", ""]
        par_fiche = collections.defaultdict(list)
        for nom, cas, arb in tranches[code]:
            if cas.track_id:
                par_fiche[(nom, cas.track_id)].append((cas, arb))
        doubles = [v for v in par_fiche.values() if len(v) > 1]
        if doubles:
            lignes.append("Fiches tranchées par plusieurs cas (à relire ensemble) :")
            for v in doubles:
                lignes.append(
                    f"- {v[0][0].morceau} : "
                    + " / ".join(f"{c.detecteur} → {a.sens}" for c, a in v)
                )
            lignes.append("")
        sens = collections.Counter(
            (cas.detecteur, _generique(arb.sens), arb.action or "—")
            for _, cas, arb in tranches[code]
        )
        for (det, s, action), n in sorted(sens.items(), key=lambda x: (x[0][0], -x[1])):
            lignes.append(f"- {n} × `{det}` → {s} (action : `{action}`)")
        lignes += ["", "| artiste | détecteur | morceau | sens | preuve |", "|---|---|---|---|---|"]
        for nom, cas, arb in sorted(tranches[code], key=lambda x: (x[1].detecteur, x[0])):
            lignes.append(
                f"| {nom} | {cas.detecteur} | {cas.morceau[:50]} | {arb.sens} | {arb.raison} |"
            )
        if non_tranches[code]:
            lignes += ["", f"Non tranchés ({len(non_tranches[code])}) :", ""]
            for nom, cas in sorted(non_tranches[code], key=lambda x: (x[1].detecteur, x[0])):
                lignes.append(f"- {nom} · {cas.detecteur} · {cas.morceau[:50]} — {cas.motif[:110]}")
    return lignes


if __name__ == "__main__":
    sys.exit(main())
