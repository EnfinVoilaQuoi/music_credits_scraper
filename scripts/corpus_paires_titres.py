"""Corpus de paires de titres RÉELLES et leurs verdicts (chantier D, 2026-10-12).

Fige ce que répondent les prédicats de titre (`version_descriptors`) sur des
paires tirées de la base, pour que `tests/test_corpus_paires_titres.py` voie
TOUTE paire dont le verdict change. Changer une règle exprès = relancer ce
script avec --ecrire : le diff git du fixture nomme les paires touchées.

Trois sources de paires (titres seulement — aucune parole) :
  · fiche ↔ fiche d'un même artiste : même socle, même graphie compacte, ou
    titres proches (≥ 0,85) qu'on pourrait confondre (« Seul » / « Seuil ») ;
  · fiche ↔ titre de ses vidéos (échantillon stable) ;
  · titre Kworb ↔ fiche CONFIRMÉS à la main (table `kworb_decisions`).

    python scripts/corpus_paires_titres.py            # résumé, rien écrit
    python scripts/corpus_paires_titres.py --ecrire   # réécrit le fixture
"""

import argparse
import collections
import difflib
import hashlib
import json
import sys
from pathlib import Path

if sys.platform == "win32" and "pytest" not in sys.modules:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FIXTURE = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "paires_titres.json"
#: Taille de l'échantillon vidéo ↔ fiche (stable : tri par empreinte).
VIDEOS_MAX = 4000
SEUIL_PROCHES = 0.85


def verdicts(a: str, b: str, alb_a=None, alb_b=None) -> dict:
    """Les réponses des prédicats de titre sur une paire — `a` est la fiche."""
    from src.utils import version_descriptors as vd

    return {
        "equivalents": vd.titres_equivalents(a, b),
        "socle": vd.meme_socle(a, b),
        "confondus": vd.titres_confondus(a, b),
        "version": vd.meme_version(a, b),
        "version_publiee": vd.meme_version_publiee(a, b),
        "doublons": vd.doublons_evidents([(a, alb_a), (b, alb_b)]),
    }


def _empreinte(*parts) -> str:
    return hashlib.sha1("\x1f".join(str(p) for p in parts).encode("utf-8")).hexdigest()


def paires_de_la_base(dm) -> list[dict]:
    from sqlalchemy import text

    from src.utils.title_matching import normalize_title
    from src.utils.version_descriptors import parse_variant

    paires: dict[tuple, dict] = {}

    def ajouter(source, a, b, alb_a=None, alb_b=None):
        if not a or not b or a == b and alb_a == alb_b:
            return
        cle = (source, a, b, alb_a, alb_b)
        paires.setdefault(
            cle, {"source": source, "a": a, "b": b, "album_a": alb_a, "album_b": alb_b}
        )

    with dm.engine.connect() as c:
        fiches = c.execute(
            text("SELECT artist_id, title, album FROM tracks WHERE title <> ''")
        ).all()
        videos = c.execute(
            text(
                "SELECT DISTINCT t.title, v.title FROM track_videos v "
                "JOIN tracks t ON t.id = v.track_id WHERE v.title <> ''"
            )
        ).all()
        kworb = c.execute(
            text(
                "SELECT k.titre, t.title FROM kworb_decisions k JOIN tracks t ON t.id = k.track_id "
                "WHERE k.nature = 'lien' AND k.statut = 'confirme'"
            )
        ).all()

    par_artiste = collections.defaultdict(list)
    for artist_id, titre, album in fiches:
        par_artiste[artist_id].append((titre, album))
    for liste in par_artiste.values():
        groupes = collections.defaultdict(list)
        for titre, album in liste:
            n = normalize_title(titre).replace(" ", "")
            groupes[("socle", normalize_title(parse_variant(titre).socle).replace(" ", ""))].append(
                (titre, album)
            )
            groupes[("debut", n[:3])].append((titre, album))
        for (genre, _), membres in groupes.items():
            membres = sorted(set(membres), key=lambda x: (x[0], x[1] or ""))
            for i, (a, alb_a) in enumerate(membres):
                for b, alb_b in membres[i + 1 :]:
                    if genre == "socle":
                        ajouter("fiches", a, b, alb_a, alb_b)
                        continue
                    na, nb = normalize_title(a), normalize_title(b)
                    if difflib.SequenceMatcher(None, na, nb).ratio() >= SEUIL_PROCHES:
                        ajouter("fiches", a, b, alb_a, alb_b)

    videos = sorted(videos, key=lambda p: _empreinte(*p))[:VIDEOS_MAX]
    for fiche, video in videos:
        ajouter("video", fiche, video)
    for titre_kworb, fiche in kworb:
        ajouter("kworb_confirme", fiche, titre_kworb)
    return sorted(
        paires.values(),
        key=lambda p: (p["source"], p["a"], p["b"], str(p["album_a"]), str(p["album_b"])),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ecrire", action="store_true", help="réécrire le fixture")
    args = parser.parse_args()

    from src.utils.data_manager import DataManager

    paires = paires_de_la_base(DataManager())
    for p in paires:
        p["verdicts"] = verdicts(p["a"], p["b"], p["album_a"], p["album_b"])
    compte = collections.Counter(p["source"] for p in paires)
    print(f"{len(paires)} paires : {dict(compte)}")
    for q in paires[0]["verdicts"]:
        print(f"  {q:16} oui {sum(p['verdicts'][q] for p in paires)}")
    if args.ecrire:
        # Une paire par LIGNE : le diff git d'une règle changée nomme les paires.
        lignes = (json.dumps(p, ensure_ascii=False, separators=(",", ":")) for p in paires)
        FIXTURE.write_text("[\n" + ",\n".join(lignes) + "\n]\n", encoding="utf-8")
        print(f"→ {FIXTURE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
