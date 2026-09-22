"""Gate d'identité Deezer (2026-09-22) : un hit de recherche libre n'est retenu
que s'il EST le morceau — artiste (par id ou par mots), TITRE obligatoire,
durée quand la fiche en a une. Prédicats purs, aucun réseau.
"""

import ast
from pathlib import Path

from src.utils.deezer_identity import (
    artiste_etranger,
    choisir_hit,
    hit_concorde,
    titre_deezer,
    variante_etrangere,
)
from src.utils.version_descriptors import titres_equivalents

SRC = Path(__file__).resolve().parents[1] / "src"
ISHA = 1236609


def _hit(title, artist="ISHA", artist_id=ISHA, duration=200, **extra):
    h = {"id": 1, "title": title, "artist": {"id": artist_id, "name": artist}, "duration": duration}
    h.update(extra)
    return h


class TestArtiste:
    def test_par_id_quand_il_est_connu(self):
        ok, motif = hit_concorde(
            _hit("Durag", artist_id=259696952),
            artist_name="Isha",
            title="Durag",
            artist_deezer_id=ISHA,
        )
        assert not ok and motif.startswith("artiste")
        # L'homonyme porte le MÊME nom : le nom ne rattrape pas un id étranger.
        assert artiste_etranger(
            _hit("Durag", "Isha", 259696952), artist_name="Isha", artist_deezer_id=ISHA
        )

    def test_par_mots_entiers_sans_id(self):
        assert hit_concorde(_hit("Durag", "ISHA"), artist_name="Isha", title="Durag")[0]
        assert not hit_concorde(
            _hit("Durag", "Misha Van Der Werf"), artist_name="Isha", title="Durag"
        )[0]
        assert not hit_concorde(
            _hit("Heartless", "Vitamin String Quartet"), artist_name="Kanye West", title="Heartless"
        )[0]

    def test_un_contributeur_compte(self):
        fiche = _hit("Grünt #33", "Swing", 999, contributors=[{"id": ISHA, "name": "ISHA"}])
        assert hit_concorde(fiche, artist_name="Isha", title="Grünt #33", artist_deezer_id=ISHA)[0]

    def test_sans_artiste_sur_le_hit_on_ne_conclut_pas(self):
        assert not artiste_etranger({"title": "X"}, artist_name="Isha", artist_deezer_id=ISHA)


class TestTitreObligatoire:
    def test_le_premier_hit_de_l_artiste_ne_suffit_pas(self):
        ok, motif = hit_concorde(
            _hit("Tueur de dragon (Vent)"), artist_name="Isha", title="Durag", artist_deezer_id=ISHA
        )
        assert not ok and "Tueur de dragon" in motif

    def test_meme_famille_de_version_acceptee(self):
        assert hit_concorde(
            _hit("Nudes - Acoustic", "A2H"), artist_name="A2H", title="Nudes (Live at AK Studios)"
        )[0]

    def test_descripteur_asymetrique_refuse(self):
        assert not hit_concorde(
            _hit("MW2 (Chopped & $crewed)", "Freeze Corleone"),
            artist_name="Freeze Corleone",
            title="MW2",
        )[0]
        assert variante_etrangere(_hit("Heartless (Remix)"), title="Heartless")

    def test_title_short_et_title_version_font_le_titre(self):
        piste = {
            "title_short": "Argent, drogue et sexe",
            "title_version": "(Live 2006)",
            "artist": {"name": "Diam's"},
        }
        assert titre_deezer(piste) == "Argent, drogue et sexe (Live 2006)"
        assert not hit_concorde(piste, artist_name="Diam's", title="Argent, drogue et sexe")[0]

    def test_titres_equivalents(self):
        assert titres_equivalents("Un pour la plume", "Un Pour La Plume")
        assert titres_equivalents("In Common (Remix)", "In Common - Black Coffee Remix")
        assert not titres_equivalents(
            "1 pour la plume", "Un pour la plume"
        )  # accepté : refuser est un bon résultat
        assert not titres_equivalents("", "X")


class TestDuree:
    def test_ne_juge_que_si_la_fiche_a_une_duree(self):
        assert hit_concorde(_hit("Durag", duration=300), artist_name="Isha", title="Durag")[0]
        assert hit_concorde(
            _hit("Durag", duration=202), artist_name="Isha", title="Durag", previous_duration=200
        )[0]
        ok, motif = hit_concorde(
            _hit("Durag", duration=203), artist_name="Isha", title="Durag", previous_duration=200
        )
        assert not ok and motif.startswith("durée")

    def test_duree_en_chaine_mm_ss(self):
        assert hit_concorde(
            _hit("Durag", duration=200), artist_name="Isha", title="Durag", previous_duration="3:20"
        )[0]


class TestChoisirHit:
    def test_jamais_data_zero(self):
        hits = [_hit("Tueur de dragon (Vent)"), _hit("Durag", duration=180)]
        assert (
            choisir_hit(hits, artist_name="Isha", title="Durag", artist_deezer_id=ISHA)["duration"]
            == 180
        )

    def test_aucun_hit(self):
        assert choisir_hit([], artist_name="Isha", title="Durag") is None
        assert hit_concorde(None, artist_name="Isha", title="Durag") == (False, "aucun hit")


class TestToutProducteurPasseParLeGate:
    """Crible AST : aucun site n'écrit un `deezer_id` de morceau sans gate.

    Exceptions = les sites dont la preuve vit AILLEURS : le mapper (relit la
    colonne), `providers/deezer.py` (consomme `DeezerAPI.enrich_track`, gardé
    par `_choisir_hit`), `ecarts_deezer.py` (la piste vient du CATALOGUE de
    l'artiste, la preuve est celle de `_match_existing`).
    """

    EXCEPTIONS = {"track_mapper.py", "deezer.py", "ecarts_deezer.py"}

    def _sites(self):
        for chemin in SRC.rglob("*.py"):
            texte = chemin.read_text(encoding="utf-8")
            garde = (
                "choisir_hit" in texte
                or "hit_concorde" in texte
                or "fill_track_identities" in texte
            )
            for noeud in ast.walk(ast.parse(texte, filename=str(chemin))):
                if not isinstance(noeud, ast.Assign):
                    continue
                for cible in noeud.targets:
                    est_id = (
                        isinstance(cible, ast.Attribute)
                        and cible.attr == "deezer_id"
                        and isinstance(cible.value, ast.Name)
                        and "track" in cible.value.id.lower()
                    )
                    efface = isinstance(noeud.value, ast.Constant) and noeud.value.value is None
                    if est_id and not efface:
                        yield chemin, noeud.lineno, garde

    def test_aucun_site_non_garde(self):
        fautifs = [
            f"{c.relative_to(SRC)}:{ligne}"
            for c, ligne, garde in self._sites()
            if not garde and c.name not in self.EXCEPTIONS
        ]
        assert fautifs == [], f"Ces sites écrivent un deezer_id sans gate : {fautifs}"

    def test_le_crible_voit_bien_quelque_chose(self):
        assert len(list(self._sites())) >= 3
