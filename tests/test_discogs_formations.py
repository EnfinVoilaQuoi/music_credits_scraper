"""Discogs comme source de CONFIRMATION des formations (lot 3).

Discogs désambiguïse les homonymes par un suffixe numérique — notre rappeur
belge s'appelle « Swing (20) », pas « Swing ». Mesuré le 2026-09-08 contre
l'API réelle, et le piège est vicieux : sans retirer ce suffixe, la recherche
« Swing » ne rend qu'UN homonyme exact, et c'est le mauvais. On obtiendrait une
confirmation confiante et fausse, ce qui est pire que pas de confirmation.

Aucun réseau : le client Discogs est un faux, monté sur les chiffres réels
(7 homonymes exacts pour « Swing », 1 pour « Shurik'N »).
"""

from types import SimpleNamespace

import pytest

from src.api.discogs_api import DiscogsClient, nom_sans_suffixe


def _artiste(nom, groupes=(), membres=(), alias=(), variantes=()):
    return SimpleNamespace(
        name=nom,
        groups=[SimpleNamespace(name=n) for n in groupes],
        members=[SimpleNamespace(name=n) for n in membres],
        aliases=[SimpleNamespace(name=n) for n in alias],
        name_variations=list(variantes),
    )


def test_name_variations_are_info_aliases():
    """Les variantes de graphie Discogs sont des alias « pour info » (detail
    `name_variation`), distincts des alias-identités (pages d'artiste)."""
    liens = DiscogsClient._liens_de(_artiste("Isha", alias=("Psmaker",), variantes=("ISHA",)))
    par_nom = {r.related_name: r for r in liens}
    assert par_nom["Psmaker"].detail is None
    assert par_nom["ISHA"].detail == "name_variation" and par_nom["ISHA"].kind == "alias"


class _FauxClient:
    """Le client discogs_client, réduit à ce que la recherche d'artiste utilise."""

    def __init__(self, resultats):
        self._resultats = resultats
        self.recherches = []

    def search(self, requete, type=None):  # noqa: A002 — signature discogs_client
        self.recherches.append(requete)
        return SimpleNamespace(page=lambda _n: list(self._resultats))


def _client(resultats):
    c = DiscogsClient.__new__(DiscogsClient)
    c.client = _FauxClient(resultats)
    c.rate_limit_remaining = 60
    c.rate_limit_used = 0
    return c


class TestSuffixeHomonyme:
    @pytest.mark.parametrize(
        "brut, attendu",
        [
            ("Swing (20)", "Swing"),
            ("Primero (6)", "Primero"),
            ("Swing", "Swing"),
            ("Shurik'n", "Shurik'n"),
        ],
    )
    def test_retrait(self, brut, attendu):
        assert nom_sans_suffixe(brut) == attendu

    def test_une_parenthese_qui_nest_PAS_un_suffixe_reste(self):
        """Le motif est étroit à dessein — un entier nu en fin de nom — pour ne
        pas amputer un nom légitimement parenthésé."""
        assert nom_sans_suffixe("Sound (Live)") == "Sound (Live)"
        assert nom_sans_suffixe("Groupe (2 Mecs)") == "Groupe (2 Mecs)"

    def test_rien(self):
        assert nom_sans_suffixe("") == ""
        assert nom_sans_suffixe(None) == ""


class TestUnSeulCandidat:
    """« Shurik'N » : Discogs n'a qu'un homonyme exact, il peut proposer."""

    def test_les_liens_deviennent_des_propositions(self):
        c = _client([_artiste("Shurik'n", groupes=["IAM"], alias=["Chien de la casse"])])
        r = c.get_artist_groups("Shurik'N")

        assert r["candidats"] == 1
        assert {(x.kind, x.related_name) for x in r["proposees"]} == {
            ("member_of", "IAM"),
            ("alias", "Chien de la casse"),
        }

    def test_la_provenance_est_marquee(self):
        c = _client([_artiste("Shurik'n", groupes=["IAM"])])
        assert {x.source for x in c.get_artist_groups("Shurik'N")["proposees"]} == {"discogs"}

    def test_les_membres_dun_groupe_aussi(self):
        c = _client([_artiste("L'Or Du Commun", membres=["Swing (20)", "Primero (6)"])])
        r = c.get_artist_groups("L'Or du Commun")
        assert {(x.kind, x.related_name) for x in r["proposees"]} == {
            ("has_member", "Swing"),
            ("has_member", "Primero"),
        }

    def test_le_suffixe_est_retire_A_LENTREE(self):
        """2026-09-23 : la version précédente gardait « Swing (20) » en
        promettant de retirer le suffixe « à la comparaison » — ce qui n'arrivait
        jamais (`normalize_name` garde « (20) »). En base : `667 (4)` en double
        de `667`, `CFR (2)`, `Moon Man (9)`, jamais résolus vers un artiste."""
        c = _client([_artiste("L'Or Du Commun", membres=["Swing (20)"])])
        assert c.get_artist_groups("L'Or du Commun")["proposees"][0].related_name == "Swing"

    def test_deux_liens_qui_ne_different_que_par_le_suffixe_nen_font_quun(self):
        liens = DiscogsClient._liens_de(
            _artiste("SCH", groupes=["667", "667 (4)"], variantes=["Sch (2)", "SCH"])
        )
        assert [(r.kind, r.related_name, r.detail) for r in liens] == [
            ("member_of", "667", None),
            ("alias", "Sch", "name_variation"),
        ]


class TestPlusieursCandidats:
    """« Swing » : 7 homonymes exacts. Discogs ne peut plus proposer — mais il
    peut encore confirmer, et l'ambiguïté est levée par la chose même qu'on
    cherchait à vérifier."""

    def _sept_swings(self):
        return [
            _artiste("Swing"),
            _artiste("Swing (20)", groupes=["L'Or Du Commun"]),
            *[_artiste(f"Swing ({n})") for n in (4, 5, 6, 15, 19)],
        ]

    def test_la_confirmation_leve_lambiguite(self):
        c = _client(self._sept_swings())
        r = c.get_artist_groups("Swing", attendues={"L'Or du Commun"})

        assert r["candidats"] == 7
        assert r["confirmees"] == {"l or du commun"}
        assert [x.related_name for x in r["proposees"]] == ["L'Or Du Commun"]

    def test_sans_attente_rien_nest_propose(self):
        """Choisir parmi sept homonymes sans oracle reviendrait à jouer l'identité
        de quelqu'un à pile ou face."""
        c = _client(self._sept_swings())
        r = c.get_artist_groups("Swing")
        assert r["proposees"] == [] and r["confirmees"] == set()

    def test_une_attente_que_personne_ne_declare_ne_confirme_rien(self):
        c = _client(self._sept_swings())
        r = c.get_artist_groups("Swing", attendues={"Un Autre Groupe"})
        assert r["confirmees"] == set() and r["proposees"] == []

    def test_sans_retirer_le_suffixe_on_choisirait_le_MAUVAIS(self):
        """Le cœur du piège, gelé : « Swing » tout court existe chez Discogs et
        n'est PAS le nôtre. Une comparaison sur le nom brut ne retiendrait que
        lui — et il ne déclare aucun groupe."""
        c = _client(self._sept_swings())
        exacts = c._candidats_formation("Swing")
        assert len(exacts) == 7
        assert "Swing (20)" in {a.name for a in exacts}


class TestAucunCandidat:
    def test_artiste_inconnu(self):
        c = _client([_artiste("Quelqu'un d'autre")])
        r = c.get_artist_groups("Introuvable")
        assert r == {"proposees": [], "confirmees": set(), "candidats": 0, "diagnostic": None}

    def test_nom_vide(self):
        c = _client([])
        assert c.get_artist_groups("")["candidats"] == 0
        assert c.client.recherches == []  # pas même une requête


# ── Identité connue, garde de contradiction, ambiguïté (2026-09-23) ──────────


class _FauxClientParId(_FauxClient):
    """Ajoute la lecture d'une fiche par id — ce que fait `client.artist(id)`."""

    def __init__(self, resultats, fiches=None):
        super().__init__(resultats)
        self.fiches = fiches or {}
        self.lus = []

    def artist(self, artist_id):
        self.lus.append(artist_id)
        return self.fiches[artist_id]


def _client_par_id(resultats=(), fiches=None):
    c = _client([])
    c.client = _FauxClientParId(list(resultats), fiches)
    return c


class TestLectureParId:
    """Identité tranchée par l'oracle (disques rattachés) : la fiche est lue
    DIRECTEMENT, sans recherche — c'est ce qui débloque les 14 artistes sur 25
    à homonymes (Isha, Swing, SCH…)."""

    def test_une_requete_sans_recherche(self):
        c = _client_par_id(
            fiches={6244752: _artiste("Isha (7)", groupes=["Bavoog Avers (2)"], alias=["Psmaker"])}
        )
        r = c.get_artist_groups("Isha", attendues={"Bavoog Avers"}, artist_id=6244752)
        assert c.client.recherches == [] and c.client.lus == [6244752]
        assert {(x.kind, x.related_name) for x in r["proposees"]} == {
            ("member_of", "Bavoog Avers"),
            ("alias", "Psmaker"),
        }
        assert r["confirmees"] == {"bavoog avers"} and r["candidats"] == 1
        assert r["diagnostic"] is None

    def test_sans_attente_les_liens_restent_des_propositions(self):
        c = _client_par_id(fiches={1: _artiste("Swing (20)", groupes=["L'Or Du Commun"])})
        r = c.get_artist_groups("Swing", artist_id=1)
        assert [x.related_name for x in r["proposees"]] == ["L'Or Du Commun"]
        assert r["confirmees"] == set()


class TestGardes:
    def test_contradiction_candidat_unique_qui_ne_recoupe_rien(self):
        """MusicBrainz nomme IAM ; l'unique « Shurik'n » de Discogs ne déclare
        que « Autre Groupe » : ce n'est probablement pas le nôtre."""
        c = _client([_artiste("Shurik'n", groupes=["Autre Groupe"])])
        r = c.get_artist_groups("Shurik'N", attendues={"IAM"})
        assert r["proposees"] == [] and r["confirmees"] == set()
        assert "aucune des formations" in r["diagnostic"]

    def test_sans_attente_pas_de_contradiction(self):
        c = _client([_artiste("Shurik'n", groupes=["IAM"])])
        r = c.get_artist_groups("Shurik'N", attendues=set())
        assert [x.related_name for x in r["proposees"]] == ["IAM"]
        assert r["diagnostic"] is None

    def test_deux_homonymes_qui_confirment_cest_rien(self):
        """L'ancienne boucle ÉCRASAIT `proposees` à chaque confirmant : le
        dernier gagnait, en silence."""
        c = _client(
            [
                _artiste("Swing (20)", groupes=["L'Or Du Commun"], alias=["A"]),
                _artiste("Swing (4)", groupes=["L'Or du Commun"], alias=["B"]),
            ]
        )
        r = c.get_artist_groups("Swing", attendues={"L'Or du Commun"})
        assert r["proposees"] == [] and r["confirmees"] == set()
        assert "2 homonymes" in r["diagnostic"]


class _Disque:
    def __init__(self, artistes, pistes=(), titre="Disque"):
        self.title = titre
        self.artists = [SimpleNamespace(id=i, name=n) for i, n in artistes]
        self.tracklist = [
            SimpleNamespace(title=t, artists=[SimpleNamespace(id=i, name=n) for i, n in a])
            for t, a in pistes
        ]


class TestLectureDeDisque:
    def test_artistes_et_pistes(self):
        c = _client([])
        c.client.release = lambda rid: _Disque(
            [(6244752, "Isha (7)"), (99, "Limsa D'Aulnay")],
            pistes=[("Durag", []), ("Feat", [(5, "Invité")])],
        )
        assert c.artistes_du_disque(1) == [(6244752, "Isha (7)"), (99, "Limsa D'Aulnay")]
        disque = c.lire_disque(1)
        assert disque["pistes"] == [("Durag", []), ("Feat", [(5, "Invité")])]

    def test_disque_illisible_rend_none(self):
        from discogs_client.exceptions import DiscogsAPIError

        c = _client([])

        def _boum(rid):
            raise DiscogsAPIError("down")

        c.client.release = _boum
        assert c.artistes_du_disque(1) is None
