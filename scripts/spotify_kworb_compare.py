"""Compare les streams vus par Kworb et par le scrape des pages Spotify.

LECTURE SEULE — aucune écriture, aucun appel réseau. Tout est déjà en base : les
deux sources posent leur observation sur le même champ (`spotify_streams`) avec
une provenance différente, donc la comparaison ne coûte rien et reste disponible
en permanence.

À quoi ça sert : décider si `settings.streams_master` doit basculer sur
`spotify_web`. L'écart attendu est un **décalage de fraîcheur** — Kworb porte sa
date « Last updated », parfois vieille de plusieurs mois — et non une divergence
de méthode. Si les écarts sont erratiques plutôt que datés, c'est qu'il se passe
autre chose, et il faut le comprendre avant de basculer.

Usage :
    python scripts/spotify_kworb_compare.py                 # tous les artistes
    python scripts/spotify_kworb_compare.py ISHA            # un artiste
    python scripts/spotify_kworb_compare.py ISHA --limit 40
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, text  # noqa: E402

from src.config import BASE_DIR  # noqa: E402

if hasattr(sys.stdout, "reconfigure") and "pytest" not in sys.modules:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_REQUETE = """
SELECT ar.name AS artiste,
       t.title AS titre,
       MAX(CASE WHEN o.source = 'kworb'       THEN o.value END) AS kworb,
       MAX(CASE WHEN o.source = 'spotify_web' THEN o.value END) AS spotify,
       MAX(CASE WHEN o.source = 'kworb'       THEN o.seen_at END) AS kworb_vu,
       MAX(CASE WHEN o.source = 'spotify_web' THEN o.seen_at END) AS spotify_vu
FROM observations o
JOIN tracks t   ON t.id = o.track_id
JOIN artists ar ON ar.id = t.artist_id
WHERE o.field = 'spotify_streams' AND o.source IN ('kworb', 'spotify_web')
GROUP BY t.id
HAVING kworb IS NOT NULL AND spotify IS NOT NULL
"""


def _entier(valeur):
    """Les observations sont stockées en TEXT : on coerce, ou on écarte."""
    try:
        return int(valeur)
    except (TypeError, ValueError):
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("artiste", nargs="?", help="filtre sur le nom d'artiste")
    parser.add_argument("--limit", type=int, default=30, help="lignes affichées (défaut 30)")
    args = parser.parse_args()

    engine = create_engine(f"sqlite:///{(BASE_DIR / 'data' / 'music_credits.db').as_posix()}")
    with engine.connect() as conn:
        lignes = conn.execute(text(_REQUETE)).mappings().all()

    if args.artiste:
        cible = args.artiste.strip().lower()
        lignes = [r for r in lignes if cible in (r["artiste"] or "").lower()]

    compares = []
    for r in lignes:
        kworb, spotify = _entier(r["kworb"]), _entier(r["spotify"])
        if kworb is None or spotify is None or kworb == 0:
            continue
        compares.append((r, kworb, spotify, (spotify - kworb) / kworb * 100))

    if not compares:
        print(
            "Aucun morceau couvert par les DEUX sources.\n"
            "Lance « Nb Streams » sur un artiste : Kworb et Spotify posent chacun "
            "leur observation, et la comparaison devient disponible."
        )
        return 0

    compares.sort(key=lambda c: abs(c[3]), reverse=True)
    print(f"{len(compares)} morceau(x) vus par les deux sources — écarts décroissants\n")
    print(f"{'ARTISTE':<14} {'TITRE':<32} {'KWORB':>13} {'SPOTIFY':>13} {'ÉCART':>8}  KWORB VU LE")
    print("-" * 104)
    for r, kworb, spotify, ecart in compares[: args.limit]:
        vu = str(r["kworb_vu"] or "")[:10]
        print(
            f"{(r['artiste'] or '')[:13]:<14} {(r['titre'] or '')[:31]:<32} "
            f"{kworb:>13,} {spotify:>13,} {ecart:>7.1f}%  {vu}".replace(",", " ")
        )

    ecarts = [c[3] for c in compares]
    median = sorted(ecarts)[len(ecarts) // 2]
    print(
        f"\nÉcart médian : {median:+.1f}%  |  "
        f"Spotify supérieur sur {sum(1 for e in ecarts if e > 0)}/{len(ecarts)} morceaux"
    )
    print(
        "Lecture : un écart POSITIF et régulier = Kworb est simplement en retard "
        "(comportement attendu).\nDes écarts erratiques, dans les deux sens, "
        "signalent autre chose — à comprendre AVANT de basculer `streams_master`."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
