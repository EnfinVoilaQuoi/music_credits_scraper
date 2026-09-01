"""Harnais avant/après des planches Bubble : étalon gelé vs code courant.

L'outil du « cliquet » : un jeu FIXE de planches témoins (`data/bubble_witness.json`,
créé annoté au premier lancement) est régénéré avec le code courant, puis posé
côte à côte avec l'étalon gelé (`exports/_etalon/`) dans un HTML autonome. Toute
modification du moteur se juge là, à l'œil — l'audit (mêmes règles que
`scripts/bubble_prod.py --audit`) n'est que consultatif.

Usage :
    python scripts/bubble_compare.py               # régénère + compare.html
    python scripts/bubble_compare.py --open        # idem, et ouvre le HTML
    python scripts/bubble_compare.py --freeze      # gèle le rendu courant comme étalon

`--freeze` ne se lance qu'APRÈS validation visuelle par l'utilisateur : l'étalon
est la référence qu'on ne dégrade plus.
"""

import argparse
import base64
import json
import subprocess
import sys
from pathlib import Path

# Encodage Windows (le package est installé via `pip install -e .` : aucun hack sys.path).
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.config import DATA_DIR, EXPORTS_DIR
from src.dataviz.bubble_audit import check_spec
from src.dataviz.bubble_feat import generate_bubble_feat
from src.dataviz.bubble_prod import _safe_dirname, generate_bubble_prod
from src.dataviz.bubble_style_io import load_style
from src.dataviz.style_io import strip_comments
from src.utils.data_manager import DataManager

WITNESS_FILENAME = "bubble_witness.json"

# Jeu de témoins par défaut : la planche signalée (mammouth en solo sur M.A.N),
# une dense, une moyenne, deux autres artistes, et deux planches feat.
_DEFAULT_WITNESSES: tuple[dict, ...] = (
    {"artist": "Josman", "album": "M.A.N (Black Roses & Lost Feelings)", "kind": "prod"},
    {"artist": "Josman", "album": "SPLIT", "kind": "prod"},
    {"artist": "Josman", "album": "Matrix", "kind": "prod"},
    {"artist": "Django", "album": "ATHANOR", "kind": "prod"},
    {"artist": "Isha", "album": "Labrador bleu", "kind": "prod"},
    {"artist": "Josman", "album": "M.A.N (Black Roses & Lost Feelings)", "kind": "feat"},
    {"artist": "Django", "album": "S/O le Flem", "kind": "feat"},
)

_WITNESS_HEADER = [
    "// Planches témoins du harnais bubble_compare : le MÊME jeu à chaque",
    "// comparaison, c'est ce qui rend les avant/après comparables entre eux.",
    '// Une entrée = {"artist": ..., "album": ..., "kind": "prod"|"feat"}.',
    "// Les lignes // sont des commentaires, retirés à la lecture.",
]

_CSS = (
    "body{margin:0;font-family:Arial,sans-serif;background:#f5f5f5;color:#222}"
    "h1{font-size:20px;padding:18px 20px 4px;margin:0}"
    "p.note{padding:0 20px 10px;margin:0;font-size:14px;color:#555}"
    ".wit{max-width:1500px;margin:0 auto 26px;padding:0 16px}"
    "h2{font-size:16px;margin:0 0 8px}"
    ".pair{display:grid;grid-template-columns:1fr 1fr;gap:12px}"
    "figure{margin:0;border:1px solid #ccc;border-radius:6px;background:#fff;overflow:hidden}"
    "img{width:100%;height:auto;display:block}"
    "figcaption{padding:8px 12px;font-size:14px;border-top:1px solid #eee}"
    ".metrics{color:#555}"
    ".bad{color:#b00;font-weight:bold}"
    ".miss{padding:30px;text-align:center;color:#888;font-size:14px}"
)


def witness_path() -> Path:
    return Path(DATA_DIR) / WITNESS_FILENAME


def current_dir() -> Path:
    return Path(EXPORTS_DIR) / "_compare" / "current"


def etalon_dir() -> Path:
    return Path(EXPORTS_DIR) / "_etalon"


def load_witnesses() -> list[dict]:
    """Le jeu de témoins, créé avec les défauts au premier lancement."""
    path = witness_path()
    if not path.exists():
        payload = json.dumps(list(_DEFAULT_WITNESSES), ensure_ascii=False, indent=2)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join([*_WITNESS_HEADER, payload]) + "\n", encoding="utf-8")
        print(f"🧾 Jeu de témoins créé : {path}")
        return list(_DEFAULT_WITNESSES)
    raw = json.loads(strip_comments(path.read_text(encoding="utf-8")))
    if not isinstance(raw, list):
        raise ValueError(f"{path} : liste JSON attendue")
    return [w for w in raw if isinstance(w, dict) and {"artist", "album", "kind"} <= set(w)]


def slug(witness: dict) -> str:
    safe_artist = _safe_dirname(witness["artist"]).replace(" ", "_")
    safe_album = _safe_dirname(witness["album"]).replace(" ", "_")
    return f"{witness['kind']}_{safe_artist}_{safe_album}"


def generate_all(witnesses: list[dict]) -> list[dict]:
    """Régénère chaque témoin avec le code courant. Une entrée de compte-rendu par témoin."""
    dm = DataManager()
    style = load_style()
    tracks_cache: dict[str, list] = {}
    out_dir = current_dir()
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    for witness in witnesses:
        name = witness["artist"]
        if name not in tracks_cache:
            artist = dm.get_artist_by_name(name)
            tracks_cache[name] = list(artist.tracks or []) if artist else []
        tracks = tracks_cache[name]
        entry = {"witness": witness, "slug": slug(witness), "audit": None, "error": None}
        if not tracks:
            entry["error"] = f"artiste introuvable ou sans morceaux : {name!r}"
            rows.append(entry)
            continue
        generate = generate_bubble_prod if witness["kind"] == "prod" else generate_bubble_feat
        try:
            result = generate(
                tracks,
                witness["album"],
                artist_name=name,
                style=style,
                output_path=out_dir / f"{entry['slug']}.svg",
            )
        except ValueError as exc:  # album absent, aucun crédit du bon type…
            entry["error"] = str(exc)
            rows.append(entry)
            continue
        entry["audit"] = check_spec(result.spec)
        entry["overflow"] = result.spec.overflow
        rows.append(entry)
    return rows


def freeze(rows: list[dict]) -> None:
    """Copie le rendu courant comme étalon (à ne lancer qu'après validation)."""
    target = etalon_dir()
    target.mkdir(parents=True, exist_ok=True)
    frozen = 0
    for entry in rows:
        src = current_dir() / f"{entry['slug']}.svg"
        if entry["error"] or not src.exists():
            continue
        (target / src.name).write_bytes(src.read_bytes())
        frozen += 1
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            cwd=Path(__file__).resolve().parent.parent,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        sha = "inconnu"
    from datetime import date

    meta = {"commit": sha, "date": date.today().isoformat(), "planches": frozen}
    (target / "etalon_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"🧊 Étalon gelé : {frozen} planche(s) dans {target} (commit {sha})")


def _img(path: Path) -> str:
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    return f'<img src="data:image/svg+xml;base64,{b64}">'


def _figure(path: Path, caption: str, missing: str) -> str:
    body = _img(path) if path.exists() else f'<div class="miss">{missing}</div>'
    return f"<figure>{body}<figcaption>{caption}</figcaption></figure>"


def build_html(rows: list[dict]) -> Path:
    """`compare.html` autonome (SVG en data-URI) : étalon | actuel, par témoin."""
    meta_path = etalon_dir() / "etalon_meta.json"
    etalon_note = ""
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            etalon_note = f" (gelé le {meta.get('date', '?')}, commit {meta.get('commit', '?')})"
        except (OSError, json.JSONDecodeError):
            pass

    blocks = []
    for entry in rows:
        witness = entry["witness"]
        title = f"{witness['kind']} · {witness['artist']} — {witness['album']}"
        if entry["error"]:
            blocks.append(
                f"<div class='wit'><h2>{title}</h2>"
                f"<p class='bad'>génération en échec : {entry['error']}</p></div>"
            )
            continue
        audit = entry["audit"]
        metrics = f"vide {audit.void:.0f} px ({audit.void_ratio:.1f}×)"
        if audit.entorses:
            metrics += f' — <span class="bad">{len(audit.entorses)} entorse(s)</span>'
        if audit.compromis:
            metrics += f" — {len(audit.compromis)} compromis"
        if entry.get("overflow"):
            w, h = entry["overflow"]
            metrics += f" — déborde de {w:.0f} × {h:.0f} px"
        left = _figure(
            etalon_dir() / f"{entry['slug']}.svg",
            f"étalon{etalon_note}",
            "aucun étalon gelé — valider une version puis lancer --freeze",
        )
        right = _figure(
            current_dir() / f"{entry['slug']}.svg",
            f"actuel — <span class='metrics'>{metrics}</span>",
            "rendu courant manquant",
        )
        blocks.append(
            f"<div class='wit'><h2>{title}</h2><div class='pair'>{left}{right}</div></div>"
        )

    html = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>Bubble — étalon vs actuel</title><style>{_CSS}</style></head>"
        "<body><h1>Bubble — étalon vs actuel</h1>"
        "<p class='note'>Le verdict se donne à l'œil, planche par planche ; les chiffres "
        "(vide, entorses, compromis) ne sont que consultatifs.</p>"
        f"{''.join(blocks)}</body></html>"
    )
    out = current_dir().parent / "compare.html"
    out.write_text(html, encoding="utf-8")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare les planches témoins à l'étalon gelé.")
    parser.add_argument(
        "--freeze",
        action="store_true",
        help="Gèle le rendu courant comme étalon (après validation)",
    )
    parser.add_argument("--open", action="store_true", help="Ouvre le HTML à la fin")
    args = parser.parse_args()

    witnesses = load_witnesses()
    if not witnesses:
        print("❌ Aucun témoin valide dans le fichier de témoins.")
        return 1

    rows = generate_all(witnesses)
    for entry in rows:
        if entry["error"]:
            print(f"  ❌ {entry['slug']} : {entry['error']}")
            continue
        audit = entry["audit"]
        marker = "❌" if audit.entorses else ("⚠️ " if audit.compromis else "✅")
        print(
            f"  {marker} {entry['slug'][:56]:56} vide {audit.void:4.0f} px "
            f"({audit.void_ratio:.1f}×) · {len(audit.entorses)} entorse(s), "
            f"{len(audit.compromis)} compromis"
        )

    if args.freeze:
        freeze(rows)

    html_path = build_html(rows)
    print(f"\n🖼  {html_path}")
    if args.open:
        import os

        os.startfile(html_path)  # noqa: S606 — HTML local généré
    return 1 if any(e["error"] for e in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
