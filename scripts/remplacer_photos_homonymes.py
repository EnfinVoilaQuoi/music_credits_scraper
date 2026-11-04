"""Remplace les photos prises chez un homonyme par celle du profil Deezer prouvé.

    python scripts/remplacer_photos_homonymes.py --rapport <photos_personnes_*.json>
    python scripts/remplacer_photos_homonymes.py --rapport <…> --apply
    python scripts/remplacer_photos_homonymes.py --rapport <…> --apply --inclure "Sap.jpg,Paco.jpg"

À blanc (défaut) : classe les cas `homonyme` de `mesurer_photos_personnes.py` dont le fichier
montre le profil le moins suivi, télécharge les photos candidates À PART, écrit le plan et des
planches avant / après. `data/images/artistes/` n'est pas touché.

`--apply` : relit le plan, remplace les cas sûrs (plus `--inclure`, moins `--exclure`) ; chaque
ancien fichier part d'abord dans `data/images/artistes_remplacees/<date>/`, ce qui rend le geste
réversible. Un fichier qui a changé depuis le passage à blanc n'est pas touché. La base n'est pas
ouverte : `artists.image_path` désigne le même chemin.
"""

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from PIL import Image, ImageDraw, ImageFont

from src.config import ARTIST_IMAGES_DIR, IMAGES_DIR
from src.utils.image_downloader import download_image

HOMONYME = "homonyme"
SUR, DOUTE, SANS_PHOTO = "sur", "doute", "sans_photo"
# En deçà, l'écart de fans ne départage pas deux profils au même nom.
RAPPORT_DE_FANS_MIN = 10
FANS_PREUVE_MIN = 100
PAUSE_ENTRE_IMAGES = 0.1

COTE = 120
COLONNES = 6
LIGNES_PAR_PLANCHE = 8
HAUT_DE_CASE = 44


def _plie(nom: str) -> str:
    return nom.casefold().strip()


def preuve_retenue(ligne: dict) -> dict:
    """Le profil prouvé le plus suivi."""
    return max(ligne["preuves"], key=lambda p: p["nb_fan"] or 0)


def fichier_moins_suivi(ligne: dict) -> bool:
    """Le fichier montre-t-il le profil le moins suivi ? Sinon, le profil prouvé est plutôt un
    doublon Deezer de la même personne, et la photo en place est sans doute la bonne."""
    return (ligne["resultat"]["nb_fan"] or 0) < (preuve_retenue(ligne)["nb_fan"] or 0)


def classer(ligne: dict) -> tuple[str, str]:
    """(`sur` | `doute`, raison) : ne passe seul que ce qu'aucun signal ne contredit."""
    preuve = preuve_retenue(ligne)
    fans_preuve = preuve["nb_fan"] or 0
    fans_fichier = ligne["resultat"]["nb_fan"] or 0
    if len({p["id"] for p in ligne["preuves"]}) > 1:
        return DOUTE, "plusieurs profils prouvés"
    if fans_preuve < FANS_PREUVE_MIN:
        return DOUTE, f"profil prouvé à {fans_preuve} fans"
    if fans_preuve < RAPPORT_DE_FANS_MIN * max(fans_fichier, 1):
        return DOUTE, f"écart de fans faible ({fans_preuve} contre {fans_fichier})"
    noms = {_plie(n) for n in ligne["noms"]}
    if _plie(preuve["nom"]) not in noms and _plie(ligne["resultat"]["nom"]) in noms:
        return DOUTE, f"le fichier porte le nom exact, la preuve s'écrit « {preuve['nom']} »"
    return SUR, ""


def empreinte(chemin: Path) -> str:
    return hashlib.sha256(chemin.read_bytes()).hexdigest()


def preparer(
    lignes: list[dict],
    images: Path,
    candidates: Path,
    telecharger: Callable[[str, Path], Path | None] = download_image,
    pause: float = 0.0,
) -> list[dict]:
    """Le plan : une entrée par cas, candidate téléchargée. N'écrit que dans `candidates`."""
    plan = []
    cas = [x for x in lignes if x["verdict"] == HOMONYME and fichier_moins_suivi(x)]
    for ligne in sorted(cas, key=lambda x: x["fichier"].casefold()):
        actuel = images / ligne["fichier"]
        if not actuel.exists():
            continue
        groupe, raison = classer(ligne)
        preuve = preuve_retenue(ligne)
        candidate = telecharger(preuve["photo"], candidates / ligne["fichier"])
        if pause:
            time.sleep(pause)
        if candidate is None:
            groupe, raison = SANS_PHOTO, "le profil prouvé n'a pas de photo"
        elif empreinte(candidate) == empreinte(actuel):
            groupe, raison = SANS_PHOTO, "même image que le fichier en place"
        plan.append(
            {
                "fichier": ligne["fichier"],
                "groupe": groupe,
                "raison": raison,
                "preuve": {k: preuve[k] for k in ("id", "nom", "nb_fan")},
                "fichier_montre": {k: ligne["resultat"][k] for k in ("id", "nom", "nb_fan")},
                "empreinte_actuelle": empreinte(actuel),
                "candidate": candidate.name if candidate else None,
            }
        )
    return plan


def a_remplacer(plan: list[dict], inclure: set[str], exclure: set[str]) -> list[dict]:
    """Les cas sûrs, plus ceux que l'humain inclut, moins ceux qu'il exclut."""
    connus = {e["fichier"]: e for e in plan}
    inconnus = sorted((inclure | exclure) - set(connus))
    if inconnus:
        raise ValueError(f"Pas dans le plan : {', '.join(inconnus)}")
    sans_photo = sorted(f for f in inclure if connus[f]["groupe"] == SANS_PHOTO)
    if sans_photo:
        raise ValueError(f"Rien pour les remplacer : {', '.join(sans_photo)}")
    return [
        e
        for e in plan
        if e["fichier"] not in exclure and (e["groupe"] == SUR or e["fichier"] in inclure)
    ]


@dataclass
class Bilan:
    remplaces: int = 0
    changes: int = 0
    deja_archives: int = 0


def appliquer(entrees: list[dict], images: Path, candidates: Path, archive: Path) -> Bilan:
    """Archive puis remplace, fichier par fichier ; un fichier qui a bougé reste tel quel."""
    bilan = Bilan()
    archive.mkdir(parents=True, exist_ok=True)
    for entree in entrees:
        actuel = images / entree["fichier"]
        candidate = candidates / entree["candidate"]
        if not actuel.exists() or empreinte(actuel) != entree["empreinte_actuelle"]:
            bilan.changes += 1
            continue
        garde = archive / entree["fichier"]
        if garde.exists():
            # Jamais écrasée : c'est la seule copie de l'image d'origine.
            bilan.deja_archives += 1
        else:
            shutil.copy2(actuel, garde)
        temporaire = actuel.with_name(actuel.name + ".part")
        shutil.copyfile(candidate, temporaire)
        os.replace(temporaire, actuel)
        bilan.remplaces += 1
    return bilan


def _police(corps: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype("arial.ttf", corps)
    except OSError:
        return ImageFont.load_default()


def planches(
    entrees: list[dict], images: Path, candidates: Path, dossier: Path, prefixe: str
) -> list[Path]:
    """Pages de cases « avant | après » pour le contrôle à l'œil."""
    police = _police(13)
    largeur_case = 2 * COTE + 12
    hauteur_case = COTE + HAUT_DE_CASE + 10
    par_page = COLONNES * LIGNES_PAR_PLANCHE
    ecrits = []
    for debut in range(0, len(entrees), par_page):
        page = entrees[debut : debut + par_page]
        lignes = (len(page) + COLONNES - 1) // COLONNES
        toile = Image.new("RGB", (COLONNES * largeur_case, lignes * hauteur_case), "white")
        dessin = ImageDraw.Draw(toile)
        for rang, e in enumerate(page):
            x = (rang % COLONNES) * largeur_case + 4
            y = (rang // COLONNES) * hauteur_case + 2
            dessin.text((x, y), Path(e["fichier"]).stem[:32], fill="black", font=police)
            dessin.text(
                (x, y + 15),
                f"{e['fichier_montre']['nb_fan']} > {e['preuve']['nb_fan']} fans",
                fill="#555555",
                font=police,
            )
            for decalage, chemin in (
                (0, images / e["fichier"]),
                (COTE, candidates / e["candidate"]),
            ):
                vignette = Image.open(chemin).convert("RGB").resize((COTE, COTE))
                toile.paste(vignette, (x + decalage, y + HAUT_DE_CASE - 8))
        chemin = dossier / f"{prefixe}_{debut // par_page + 1}.png"
        toile.save(chemin)
        ecrits.append(chemin)
    return ecrits


def _liste(valeur: str) -> set[str]:
    return {x.strip() for x in valeur.split(",") if x.strip()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--rapport", type=Path, required=True, help="JSON de la mesure")
    parser.add_argument("--travail", type=Path, help="défaut : à côté du rapport")
    parser.add_argument("--apply", action="store_true", help="archive puis remplace")
    parser.add_argument("--inclure", default="", help="cas douteux à remplacer aussi (a.jpg,b.jpg)")
    parser.add_argument("--exclure", default="", help="cas sûrs à laisser (a.jpg,b.jpg)")
    args = parser.parse_args(argv)

    travail = args.travail or args.rapport.parent / f"remplacement_{args.rapport.stem[-10:]}"
    candidates = travail / "candidates"
    chemin_plan = travail / "plan.json"
    images = Path(ARTIST_IMAGES_DIR)

    if args.apply:
        if not chemin_plan.exists():
            print(f"❌ Pas de plan ({chemin_plan}) : lancer d'abord le passage à blanc.")
            return 1
        plan = json.loads(chemin_plan.read_text(encoding="utf-8"))
        try:
            entrees = a_remplacer(plan, _liste(args.inclure), _liste(args.exclure))
        except ValueError as exc:
            print(f"❌ {exc}")
            return 1
        archive = Path(IMAGES_DIR) / "artistes_remplacees" / date.today().isoformat()
        bilan = appliquer(entrees, images, candidates, archive)
        print(f"✅ {bilan.remplaces} photos remplacées ; anciennes dans {archive}")
        if bilan.changes:
            print(f"   ⚠️ {bilan.changes} fichiers changés depuis le plan : laissés tels quels")
        return 0

    lignes = json.loads(args.rapport.read_text(encoding="utf-8"))
    candidates.mkdir(parents=True, exist_ok=True)
    plan = preparer(lignes, images, candidates, pause=PAUSE_ENTRE_IMAGES)
    chemin_plan.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    for groupe in (SUR, DOUTE, SANS_PHOTO):
        print(f"{groupe:11} {sum(e['groupe'] == groupe for e in plan)}")
    for groupe in (SUR, DOUTE):
        entrees = [e for e in plan if e["groupe"] == groupe]
        for chemin in planches(entrees, images, candidates, travail, f"planche_{groupe}"):
            print(f"   planche : {chemin}")
    print("À blanc : `data/images/artistes/` n'a pas été touché.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
