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
    # Plus aucune entrée (2026-10-30) : tous les modules délèguent — panneau,
    # politiques, audit, éditions, catalogue Deezer, compteurs partagés, pages
    # Genius, participation, validation et Kworb (routage d'une ligne par sa
    # nature : `porte_une_version`, `est_une_rendition`, `est_remix_nomme`)
    # posent une question du juge.
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


# ── Deux titres normalisés comparés à la main (2026-10-30) ─────────────────────
# `normalize_title(a) == normalize_title(b)` est un verdict de titre rendu hors
# du juge : la question est `meme_titre_normalise` (ou une plus large,
# `titres_equivalents`…). Cliquet, comme `PLAFONDS`.

#: Comparaisons `normalize_title(x) ==/!= normalize_title(y)` qu'un module hors
#: du juge fait ENCORE (liste FERMÉE, chaque entrée commentée).
PLAFONDS_EGALITES = {
    # Dialogue de fusion (IHM CTk, gelée hors correctifs) : titre et album.
    "gui/dialogs/merge_tracks.py": 1,
}


def _est_normalize_title(noeud: ast.AST) -> bool:
    if not isinstance(noeud, ast.Call):
        return False
    f = noeud.func
    return (isinstance(f, ast.Name) and f.id == "normalize_title") or (
        isinstance(f, ast.Attribute) and f.attr == "normalize_title"
    )


def _egalites(arbre: ast.AST) -> int:
    n = 0
    for x in ast.walk(arbre):
        if isinstance(x, ast.Compare):
            termes = [x.left, *x.comparators]
            n += sum(
                1
                for i, op in enumerate(x.ops)
                if isinstance(op, (ast.Eq, ast.NotEq))
                and _est_normalize_title(termes[i])
                and _est_normalize_title(termes[i + 1])
            )
    return n


def _sites_egalites() -> dict[str, int]:
    sites = {}
    for chemin in SRC.rglob("*.py"):
        rel = chemin.relative_to(SRC).as_posix()
        if rel in JUGE:
            continue
        n = _egalites(ast.parse(chemin.read_text(encoding="utf-8")))
        if n:
            sites[rel] = n
    return sites


def test_aucune_egalite_de_titres_normalises_hors_du_juge():
    sites = _sites_egalites()
    depasse = {f: (n, PLAFONDS_EGALITES.get(f, 0)) for f, n in sites.items()}
    depasse = {f: v for f, v in depasse.items() if v[0] > v[1]}
    assert not depasse, (
        "`normalize_title(a) == normalize_title(b)` hors du juge — poser "
        f"`meme_titre_normalise` (ou une question plus juste) : {depasse}"
    )
    a_baisser = {
        f: (sites.get(f, 0), p) for f, p in PLAFONDS_EGALITES.items() if sites.get(f, 0) < p
    }
    assert not a_baisser, f"Baisser PLAFONDS_EGALITES (comparaisons, plafond) : {a_baisser}"


def test_le_crible_voit_les_egalites():
    code = (
        "normalize_title(a) == normalize_title(b)\n"
        "tm.normalize_title(a or '') != tm.normalize_title(\n    b\n)\n"
        "normalize_title(a) == cle\n"
    )
    assert _egalites(ast.parse(code)) == 2


# ── Le disque d'un titre générique : un seul juge (2026-10-30) ─────────────────
# Les 13 verdicts qui ont basculé sur les 105 paires réelles (disque de la
# fiche, parution qui la porte), tous des acceptations justes.


@pytest.mark.parametrize(
    "fiche, servi",
    [
        ("OCHO", "OCHO (Deluxe)"),  # Deezer refusait
        ("Temps mort (Réédition)", "Temps mort"),  # Deezer
        ("M.A.N (Black Roses & Lost Feelings)", "M.A.N (Black Roses & Lost Feelings) (Bonus)"),
        ("Paradisiaque", "Paradisiaque / Mc Solaar"),  # Deezer, YouTube, YTM
        ("Chambre 140 (Part. 1)", "Chambre 140 (Part. 3)"),  # l'édition qui cumule
        ("444Nuits (Version rouge)", "444 Nuits"),  # Spotify, GetSongBPM
    ],
)
def test_meme_disque_reconnait_les_editions(fiche, servi):
    from src.utils.version_descriptors import meme_disque

    assert meme_disque(fiche, servi) and meme_disque(servi, fiche)


@pytest.mark.parametrize(
    "fiche, servi",
    [("0.9", "Autopsie Vol. 3"), ("Memoria", "Private Club"), ("Reste Calme", "444 Nuits")],
)
def test_meme_disque_separe_deux_disques(fiche, servi):
    from src.utils.version_descriptors import meme_disque

    assert not meme_disque(fiche, servi)
    assert not meme_disque(fiche, None) and not meme_disque(None, servi)


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
            # Second passage (2026-10-30) : panneau, éditions, catalogue Deezer…
            "est_un_remix": va.est_remix,
            "est_une_rendition": va.kind == Kind.RENDITION,
            "remixeur_du_titre": va.remixer,
            "socle_du_titre": va.socle,
            "descripteur_du_titre": va.descriptor,
            "rendition_instrumentale": va.kind is Kind.RENDITION
            and "instrumental" in normalize_title(va.descriptor or ""),
            "meme_prise_de_version": vd.meme_prise(va, vb),
            "mention_editoriale": (va.kind == Kind.NONE and bool(va.edition))
            or (
                va.kind == Kind.RENDITION
                and normalize_title(va.descriptor or "")
                in {
                    "version",
                    "original version",
                    "version originale",
                    "album version",
                    "version album",
                }
            ),
            "nature_de_l_edition": "diffusion" if va.kind == Kind.NONE else vd.nature_d_edition(va),
            # Kworb et parutions (2026-10-30) : routage d'une ligne, titre de parution.
            "est_remix_nomme": va.kind == Kind.REMIX_NAMED,
            "nature_de_version": va.kind.value,
            "meme_titre_normalise": normalize_title(a) == normalize_title(b),
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
            "est_un_remix": vd.est_un_remix(a),
            "est_une_rendition": vd.est_une_rendition(a),
            "remixeur_du_titre": vd.remixeur_du_titre(a),
            "socle_du_titre": vd.socle_du_titre(a),
            "descripteur_du_titre": vd.descripteur_du_titre(a),
            "rendition_instrumentale": vd.rendition_instrumentale(a),
            "meme_prise_de_version": vd.meme_prise_de_version(a, b),
            "mention_editoriale": vd.mention_editoriale(a),
            "nature_de_l_edition": vd.nature_de_l_edition(a),
            "est_remix_nomme": vd.est_remix_nomme(a),
            "nature_de_version": vd.nature_de_version(a),
            "meme_titre_normalise": vd.meme_titre_normalise(a, b),
        }
        ecarts += [(q, a, b) for q in attendu if attendu[q] != obtenu[q]]
    assert not ecarts, ecarts[:20]
