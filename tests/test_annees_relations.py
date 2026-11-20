"""Année des morceaux visés par une relation Genius (`src.utils.annees_relations`)."""

from src.utils.annees_relations import annee_de, dater_relations


class TestAnneeDe:
    def test_annee_servie(self):
        assert annee_de({"release_date_components": {"year": 1981, "month": None}}) == 1981

    def test_sans_date(self):
        assert annee_de({}) is None
        assert annee_de({"release_date_components": None}) is None
        assert annee_de({"release_date_components": {"year": None}}) is None


class TestDaterRelations:
    def test_ajoute_l_annee_par_l_adresse(self):
        posees = [{"type": "samples", "title": "A", "url": "u1"}]
        lues = [{"type": "samples", "title": "A (autre graphie)", "url": "u1", "year": 2005}]
        assert dater_relations(posees, lues) == [
            {"type": "samples", "title": "A", "url": "u1", "year": 2005}
        ]

    def test_une_annee_posee_n_est_jamais_remplacee(self):
        posees = [{"type": "samples", "url": "u1", "year": 2004}]
        assert dater_relations(posees, [{"url": "u1", "year": 2005}]) is None

    def test_relation_manuelle_sans_adresse_intacte(self):
        posees = [{"type": "version_of", "title": "B", "origine": "manuel"}]
        assert dater_relations(posees, [{"url": "u1", "year": 2005}]) is None

    def test_rien_a_ajouter(self):
        assert dater_relations([{"url": "u1"}], [{"url": "u2", "year": 1999}]) is None
        assert dater_relations([], [{"url": "u1", "year": 1999}]) is None
        assert dater_relations(None, []) is None

    def test_les_autres_relations_restent_telles_quelles(self):
        posees = [{"url": "u1"}, {"url": "u2", "origine": "manuel"}]
        datees = dater_relations(posees, [{"url": "u1", "year": 1976}])
        assert datees == [{"url": "u1", "year": 1976}, {"url": "u2", "origine": "manuel"}]
        assert posees == [{"url": "u1"}, {"url": "u2", "origine": "manuel"}]  # non muté
