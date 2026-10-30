"""Un verdict de TITRE, un seul endroit : le juge (chantier D, 2026-10-29).

Règle « Un verdict, un seul endroit » de PROJET.md, mécanisée pour les
rapprochements. Les primitives de la taxonomie des versions — `parse_variant`,
`Kind`, `meme_famille`, `meme_prise`, `parties_du_morceau`… — ne se lisent que
dans les modules du JUGE. Ailleurs (gates Spotify, Deezer, Apple, Kworb,
SongBPM, YouTube, LRC, certifs, panneau, audit), on POSE une question du juge
(`titres_equivalents`, `meme_version_mesuree`, `meme_famille_de_version`,
`porte_une_version`…) : deux gates qui combinent les primitives chacun à sa
façon finissent par dire deux choses différentes du même couple de titres.

`PLAFONDS` est un CLIQUET : le nombre de lectures de primitives qu'un module
hors du juge fait encore. Il ne monte jamais (un module nouveau en a zéro) et
DOIT descendre quand un module délègue — le test rougit sinon, pour que la
liste dise toujours où en est la délégation.
"""

import ast
import json
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
FIXTURE = Path(__file__).parent / "fixtures" / "paires_titres.json"

#: Les modules du juge : la clé, ses primitives et les questions sur deux titres.
JUGE = {
    "utils/version_descriptors.py",
    "utils/title_matching.py",
    # Juges spécialisés, bâtis sur la clé : certif ↔ fiche, titre + année nue,
    # héritage de version et remixeur étranger.
    "utils/cert_titres.py",
    "utils/titre_annee.py",
    "utils/version_heritage.py",
}

#: Les primitives que seul le juge lit.
PRIMITIVES = frozenset(
    {
        "parse_variant",
        "Kind",
        "Variant",
        "meme_famille",
        "meme_prise",
        "parties_du_morceau",
        "memes_parties",
        "qualificatifs_incompatibles",
    }
)

#: Lectures de primitives qu'un module hors du juge fait ENCORE (cliquet).
PLAFONDS = {
    # Kworb : le ROUTAGE d'une ligne par sa nature (sans version, rendition,
    # remix nommé) et le remixeur affiché au dialogue — une lecture, pas un
    # jugement : les rapprochements passent par le juge.
    "utils/update_kworb.py": 4,
    # Catalogue Deezer : appariement des écarts par socle et nature de version.
    "services/ecarts_deezer.py": 18,
    # Panneau « À trancher », ses politiques et l'audit.
    "services/revue.py": 11,
    "services/politiques_revue.py": 10,
    "services/audit.py": 8,
    # Éditions et doublons.
    "services/editions.py": 8,
    # Compteurs partagés (règle 2a) : version proche montrée.
    "utils/compteurs_partages.py": 5,
    # Pages Genius, participation, validation.
    "utils/pages_genius.py": 3,
    "utils/participation.py": 2,
    "utils/track_validation.py": 1,
}


def _lectures(arbre: ast.AST) -> int:
    return sum(
        1
        for x in ast.walk(arbre)
        if (isinstance(x, ast.Name) and x.id in PRIMITIVES)
        or (isinstance(x, ast.Attribute) and x.attr in PRIMITIVES)
    )


def _sites() -> dict[str, int]:
    sites = {}
    for chemin in SRC.rglob("*.py"):
        rel = chemin.relative_to(SRC).as_posix()
        if rel in JUGE:
            continue
        n = _lectures(ast.parse(chemin.read_text(encoding="utf-8")))
        if n:
            sites[rel] = n
    return sites


def test_aucun_module_hors_du_juge_ne_lit_plus_de_primitives_que_son_plafond():
    depasse = {f: n for f, n in _sites().items() if n > PLAFONDS.get(f, 0)}
    assert not depasse, (
        "Primitive de version lue hors du juge — poser une question de "
        "`version_descriptors` (ou en ajouter une) : "
        f"{ {f: (n, PLAFONDS.get(f, 0)) for f, n in depasse.items()} }"
    )


def test_le_cliquet_descend():
    """Un module qui a délégué voit son plafond baisser (ou sortir de la liste)."""
    sites = _sites()
    a_baisser = {f: (sites.get(f, 0), p) for f, p in PLAFONDS.items() if sites.get(f, 0) < p}
    assert not a_baisser, f"Baisser PLAFONDS (lectures, plafond) : {a_baisser}"


def test_le_crible_voit_les_deux_formes():
    code = (
        "from src.utils import version_descriptors as vd\n"
        "vd.parse_variant(t).kind == vd.Kind.NONE\n"
        "meme_famille(a, b)\n"
    )
    assert _lectures(ast.parse(code)) == 3


# ── Les questions des gates répondent comme les primitives qu'elles remplacent ──
# Preuve de la délégation SANS EFFET (2026-10-29) : sur les ~7 600 paires
# réelles du corpus, chaque question rend exactement la combinaison de
# primitives que le gate faisait lui-même.


@pytest.fixture(scope="module")
def paires():
    return [(p["a"], p["b"]) for p in json.loads(FIXTURE.read_text(encoding="utf-8"))]


def test_les_questions_des_gates_valent_les_primitives_sur_le_corpus(paires):
    from src.utils import version_descriptors as vd
    from src.utils.title_matching import normalize_title

    Kind = vd.Kind
    ecarts = []
    for a, b in paires:
        va, vb = vd.parse_variant(a), vd.parse_variant(b)
        attendu = {
            "est_remix_nu": va.kind == Kind.REMIX_BARE,
            "porte_une_version": va.kind != Kind.NONE,
            "edition_de_diffusion": va.edition,
            "meme_famille_de_version": vd.meme_famille(va, vb),
            "version_au_qualificatif_pres": not vd.meme_famille(va, vb)
            and vd.meme_famille(va, vb, compter_qualificatifs=False),
            "meme_version_stricte": (
                True
                if va.kind == Kind.NONE and vb.kind == Kind.NONE
                else (
                    vd.meme_famille(va, vb)
                    if va.est_remix and vb.est_remix
                    else vd.meme_prise(va, vb)
                )
            ),
            "meme_version_certifiee": va.kind != Kind.NONE
            and va.kind == vb.kind
            and (vb.est_remix or vd.meme_prise(va, vb)),
            "meme_prise_du_morceau": normalize_title(va.socle) == normalize_title(vb.socle)
            and vd.meme_prise(va, vb),
            "remix_du_meme_morceau": va.est_remix
            and normalize_title(va.socle) == normalize_title(vb.socle),
        }
        obtenu = {
            "est_remix_nu": vd.est_remix_nu(a),
            "porte_une_version": vd.porte_une_version(a),
            "edition_de_diffusion": vd.edition_de_diffusion(a),
            "meme_famille_de_version": vd.meme_famille_de_version(a, b),
            "version_au_qualificatif_pres": vd.version_au_qualificatif_pres(a, b),
            "meme_version_stricte": vd.meme_version_stricte(a, b),
            "meme_version_certifiee": vd.meme_version_certifiee(a, b),
            "meme_prise_du_morceau": vd.meme_prise_du_morceau(a, b),
            "remix_du_meme_morceau": vd.remix_du_meme_morceau(a, b),
        }
        ecarts += [(q, a, b) for q in attendu if attendu[q] != obtenu[q]]
    assert not ecarts, ecarts[:20]
