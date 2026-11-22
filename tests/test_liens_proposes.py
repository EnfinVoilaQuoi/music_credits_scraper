"""Liens proposés que les noms SÛRS tranchent (`utils.liens_proposes`,
`revue:liens_proposes`, 2026-11-22) : nom civil de l'artiste, autre nom d'un
membre confirmé, nom d'un membre proposé comme alias d'une formation, membre
proposé à une personne."""

from types import SimpleNamespace

import pytest

from src.models import Artist, ArtistRelation
from src.services import revue_auto
from src.utils import liens_proposes as lp


def _alias(nom, status="confirmed", detail=None):
    return SimpleNamespace(kind="alias", related_name=nom, status=status, detail=detail)


def _temoins(artiste, membres=(), par_leur_lien=(), a_des_membres=None):
    membres = list(membres)
    return lp.TemoinsLiens(
        artiste,
        membres,
        {lp.cle(n) for n in par_leur_lien},
        bool(membres) if a_des_membres is None else a_des_membres,
    )


class TestNomCivil:
    def test_le_realname_discogs_se_tait(self):
        dosseh = lp.personne("Dosseh", (), {"realname": "Dosseh Dorian N'Goumou"})
        v = lp.juger("alias", "Dosseh Dorian N'Goumou", _temoins(dosseh))
        assert v.action == "taire_lien" and "Discogs" in v.motif

    def test_sans_un_prenom_du_milieu(self):
        swift = lp.personne("Swift Guad", (), {"realname": "François Yvan Marletta"})
        assert lp.juger("alias", "François Marletta", _temoins(swift)).action == "taire_lien"

    def test_le_legal_name_musicbrainz_garde_pour_info_compte(self):
        nepal = lp.personne(
            "Népal", [_alias("Clément Enzo Florent Di Fiore", "info", "Legal name")]
        )
        assert lp.juger("alias", "Clément Di Fiore", _temoins(nepal)).action == "taire_lien"

    @pytest.mark.parametrize("nom", ["Marletta", "François Dupont", "Yvan Marletta"])
    def test_pas_un_autre_nom(self, nom):
        """Un seul mot, un autre nom de famille, un autre premier mot : l'humain."""
        swift = lp.personne("Swift Guad", (), {"realname": "François Yvan Marletta"})
        assert lp.juger("alias", nom, _temoins(swift)) is None

    def test_un_nom_de_scene_reste_a_l_humain(self):
        cudi = lp.personne("Kid Cudi", (), {"realname": "Scott Ramon Seguro Mescudi"})
        assert lp.juger("alias", "Moon Man", _temoins(cudi)) is None


class TestMembreConnu:
    def _scotts(self):
        cudi = lp.personne(
            "Kid Cudi", [_alias("Scott Mescudi")], {"aliases": [{"name": "Moon Man (9)"}]}
        )
        travis = lp.personne("Travis Scott", [_alias("Jacques Webster", "info", "Search hint")])
        return _temoins(lp.personne("THE SCOTTS"), [cudi, travis], ["Kid Cudi", "Travis Scott"])

    def test_l_autre_nom_d_un_membre_confirme_se_tait(self):
        t = self._scotts()
        v = lp.juger("has_member", "Scott Mescudi", t)
        assert v.action == "taire_lien" and "Kid Cudi" in v.motif
        assert lp.juger("has_member", "Jacques Webster", t).action == "taire_lien"

    def test_le_membre_qui_se_dit_membre_se_confirme(self):
        v = lp.juger("has_member", "Travis Scott", self._scotts())
        assert v.action == "confirmer_formation"

    def test_un_alias_seulement_propose_ne_prouve_rien(self):
        nepal = lp.personne("Népal", [_alias("Clément Di Fiore", "proposed")])
        t = _temoins(lp.personne("2Fingz"), [nepal])
        assert lp.juger("has_member", "Clément Di Fiore", t) is None

    def test_la_page_d_alias_discogs_du_membre_compte(self):
        nepal = lp.personne("Népal", (), {"aliases": [{"name": "Clément Di Fiore", "id": 1}]})
        t = _temoins(lp.personne("2Fingz"), [nepal])
        assert lp.juger("has_member", "Clément Di Fiore", t).action == "taire_lien"

    def test_un_inconnu_reste_a_l_humain(self):
        assert lp.juger("has_member", "Khadimou Fall", self._scotts()) is None


def test_sans_temoins_rien_ne_change():
    assert lp.juger("alias", "Qui", None) is None


def test_nom_civil_garde_pour_info_par_regle_puis_retabli(data_manager, monkeypatch):
    monkeypatch.setattr(revue_auto, "REGLES_ETENDUES_BRANCHEES", True)
    a = Artist(name="Swift Guad")
    a.id = data_manager.save_artist(a)
    data_manager.propose_artist_relations(
        a.id,
        [
            ArtistRelation(
                related_name="François Yvan Marletta",
                kind="alias",
                source="musicbrainz",
                detail="Legal name",
            )
        ],
        status="info",
    )
    data_manager.propose_artist_relations(
        a.id, [ArtistRelation(related_name="François Marletta", kind="alias", source="discogs")]
    )
    a.tracks = []
    revue_auto.corriger(data_manager, a)
    statuts = {r.related_name: r.status for r in data_manager.get_artist_relations(a.id, None)}
    assert statuts["François Marletta"] == "info"
    (correction,) = [
        c for c in data_manager.corrections_revue(a.id) if c["detecteur"] == "liens_proposes"
    ]
    revue_auto.retablir(data_manager, a, correction)
    statuts = {r.related_name: r.status for r in data_manager.get_artist_relations(a.id, None)}
    assert statuts["François Marletta"] == "proposed"


class TestMemePersonne:
    """Mac Miller proposé deux fois chez Larry Lovestein & The Velvet Revival."""

    def _t(self, proposes):
        mac = lp.personne(
            "Mac Miller",
            [
                _alias("Larry Lovestein"),
                _alias("Malcolm McCormick"),
                _alias("Malcolm James McCormick", "info", "Legal name"),
            ],
        )
        t = _temoins(lp.personne("Larry Lovestein & The Velvet Revival"))
        t.membres_proposes = list(proposes)
        t.personnes_de_la_base = [mac]
        return t

    def test_le_nom_civil_se_tait_le_nom_de_scene_reste(self):
        t = self._t(["Larry Lovestein", "Malcolm McCormick", "Choo Jackson"])
        v = lp.juger("has_member", "Malcolm McCormick", t)
        assert v.action == "taire_lien" and "Larry Lovestein" in v.motif
        assert lp.juger("has_member", "Larry Lovestein", t) is None
        assert lp.juger("has_member", "Choo Jackson", t) is None

    def test_le_nom_meme_de_l_artiste_est_garde(self):
        t = self._t(["Larry Lovestein", "Mac Miller"])
        assert lp.juger("has_member", "Larry Lovestein", t).action == "taire_lien"
        assert lp.juger("has_member", "Mac Miller", t) is None

    def test_seul_il_reste_a_l_humain(self):
        assert lp.juger("has_member", "Malcolm McCormick", self._t(["Malcolm McCormick"])) is None
