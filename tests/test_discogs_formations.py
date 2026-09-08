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


def _artiste(nom, groupes=(), membres=(), alias=()):
    return SimpleNamespace(
        name=nom,
        groups=[SimpleNamespace(name=n) for n in groupes],
        members=[SimpleNamespace(name=n) for n in membres],
        aliases=[SimpleNamespace(name=n) for n in alias],
    )


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
            ("has_member", "Swing (20)"),
            ("has_member", "Primero (6)"),
        }

    def test_le_nom_est_conserve_VERBATIM(self):
        """Le suffixe distingue « Swing (20) » de « Swing (6) » : le retirer ici
        perdrait l'information. Il n'est retiré qu'à la COMPARAISON."""
        c = _client([_artiste("L'Or Du Commun", membres=["Swing (20)"])])
        assert c.get_artist_groups("L'Or du Commun")["proposees"][0].related_name == "Swing (20)"


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
        assert r == {"proposees": [], "confirmees": set(), "candidats": 0}

    def test_nom_vide(self):
        c = _client([])
        assert c.get_artist_groups("")["candidats"] == 0
        assert c.client.recherches == []  # pas même une requête
