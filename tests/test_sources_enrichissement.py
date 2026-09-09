"""La fenêtre des sources ne propose plus de décider du scraper Spotify ID.

Ce n'est pas à l'utilisateur de dire si ce scraper sert : la fenêtre l'avouait
elle-même — « laisser coché suffit » — et une case dont la notice dit de ne pas y
toucher n'est pas un réglage. Le source est AUTO-RÉGULÉ : `SpotifyIdProvider.gate()`
saute quand un identifiant valide existe déjà ou quand la voie ISRC a satisfait
ReccoBeats.

⚠️ Mais la CLÉ doit rester dans la liste transmise, et c'est contre-intuitif :
`data_enricher` calcule `allow_spotify_scrape=("spotify_id" not in sources)`, si
bien que la présence de la clé dit à ReccoBeats de **ne pas** scraper de son
côté. L'omettre déclencherait un SECOND scrape Playwright par morceau.

Ces tests portent sur le SOURCE et non sur les widgets : le dialogue ne se monte
pas sans Tk, mais ce qui compte ici est un invariant de câblage, et il se lit.
"""

import ast
from pathlib import Path

FICHIER = Path(__file__).resolve().parents[1] / "src" / "gui" / "workers" / "enrichment.py"
SOURCE = FICHIER.read_text(encoding="utf-8")


def _dict_des_sources() -> list[str]:
    """Les clés de `sources_info`, telles que le dialogue les propose."""
    arbre = ast.parse(SOURCE)
    for noeud in ast.walk(arbre):
        if (
            isinstance(noeud, ast.Assign)
            and any(isinstance(c, ast.Name) and c.id == "sources_info" for c in noeud.targets)
            and isinstance(noeud.value, ast.Dict)
        ):
            return [k.value for k in noeud.value.keys if isinstance(k, ast.Constant)]
    raise AssertionError("`sources_info` introuvable — la fenêtre a changé de forme")


class TestLaCaseADisparu:
    def test_spotify_id_n_est_plus_proposé(self):
        assert "spotify_id" not in _dict_des_sources()

    def test_les_autres_sources_restent_au_choix(self):
        """Le retrait vise CE source, pas la fenêtre : les six autres sont de
        vrais choix (une clé d'API manquante, un compte, un coût)."""
        proposees = _dict_des_sources()
        assert set(proposees) == {
            "reccobeats",
            "getsongbpm",
            "songbpm",
            "bpmfinder",
            "deezer",
            "discogs",
        }

    def test_l_info_devenue_sans_objet_est_partie(self):
        """Cherché dans les CHAÎNES du module, pas dans son texte.

        Une assertion textuelle attraperait le commentaire qui explique le
        retrait — il cite la phrase. C'est la deuxième fois de la session qu'un
        garde-fou s'accroche à sa propre explication ; l'AST ne voit pas les
        commentaires, ce qui règle la question par construction.
        """
        arbre = ast.parse(SOURCE)
        chaines = [
            n.value
            for n in ast.walk(arbre)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
        ]
        assert not any("laisser coché suffit" in c for c in chaines)


class TestLaCleEstQuandMemeTransmise:
    """L'invariant qui se ferait retirer par mégarde — « la case n'existe plus,
    donc la ligne ne sert plus » — et qui doublerait le coût du run."""

    def test_spotify_id_est_injecte_dans_la_liste(self):
        assert 'selected_sources.append("spotify_id")' in SOURCE

    def test_la_RAISON_est_ecrite_sur_place(self):
        """Un invariant contre-intuitif sans sa raison se fait supprimer au
        premier nettoyage. Le commentaire doit nommer le mécanisme."""
        assert "allow_spotify_scrape" in SOURCE

    def test_le_mecanisme_est_toujours_celui_decrit(self):
        """Si `data_enricher` cessait de dériver le drapeau de la liste, ce test
        deviendrait le seul endroit à le dire — donc il vérifie l'autre bout."""
        enricheur = (FICHIER.parents[2] / "utils" / "data_enricher.py").read_text(encoding="utf-8")
        assert 'allow_spotify_scrape=("spotify_id" not in sources)' in enricheur
