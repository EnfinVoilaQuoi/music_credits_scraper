"""Client MusicBrainz — la logique qui décide QUI est qui (lot 3).

Aucun réseau. Les fragments de réponse ci-dessous sont des extraits AUTHENTIQUES
relevés sur l'API le 2026-09-07 (IAM, Akhenaton, la requête « Swing ») : ce sont
eux qui ont dicté les trois décisions du module, ils servent donc à les geler.
"""

import pytest

from src.api.musicbrainz_api import (
    MEMBRE_DE_GROUPE,
    CandidatArtiste,
    candidats_exacts,
    compter_albums_communs,
    departager,
    relations_membre,
)

# ── Extraits réels ───────────────────────────────────────────────────────────

#: Ce que rend la requête `artist:"Swing"` — l'ordre est celui de MusicBrainz.
_CANDIDATS_SWING = [
    {"id": "m0", "name": "Swing Out Sister", "country": "GB", "score": 100},
    {"id": "m1", "name": "Dutch Swing College Band", "country": "NL", "score": 96},
    {"id": "m2", "name": "Swing", "type": "Group", "country": "HK", "score": 90},
    {"id": "m3", "name": "Diablo Swing Orchestra", "country": "SE", "score": 81},
    {
        "id": "m4",
        "name": "Swing",
        "type": "Person",
        "country": "BE",
        "score": 85,
        "disambiguation": "Belgian rapper Siméon Zuyten",
    },
]

#: IAM interrogé en tant que GROUPE : direction backward, la cible est le membre.
_IAM = {
    "name": "IAM",
    "type": "Group",
    "relations": [
        {
            "type": "member of band",
            "type-id": MEMBRE_DE_GROUPE,
            "direction": "backward",
            "begin": "1989-10",
            "end": "2009",
            "ended": True,
            "artist": {"id": "a-freeman", "name": "Freeman", "type": "Person"},
        },
        {
            "type": "collaboration",
            "type-id": "autre-uuid",
            "direction": "backward",
            "artist": {"id": "a-x", "name": "Quelqu'un", "type": "Person"},
        },
    ],
}

#: Akhenaton interrogé en tant que PERSONNE : direction forward, la cible est le groupe.
_AKHENATON = {
    "name": "Akhenaton",
    "type": "Person",
    "relations": [
        {
            "type": "member of band",
            "type-id": MEMBRE_DE_GROUPE,
            "direction": "forward",
            "begin": None,
            "end": None,
            "ended": False,
            "artist": {"id": "a-iam", "name": "IAM", "type": "Group"},
        }
    ],
}


class TestCandidatsExacts:
    """L'égalité EXACTE, et non `names_match_as_words`.

    Le prédicat par mots entiers est le bon outil ailleurs — il sauve « Jul »
    face à « Jul & SCH ». Ici son relâchement est fatal : mesuré sur la vraie
    requête « Swing », il accepte 17 artistes étrangers.
    """

    def test_les_noms_englobants_sont_ecartes(self):
        retenus = [a["name"] for a in candidats_exacts("Swing", _CANDIDATS_SWING)]
        assert retenus == ["Swing", "Swing"]
        assert "Swing Out Sister" not in retenus
        assert "Diablo Swing Orchestra" not in retenus

    def test_lapostrophe_et_la_casse_ne_font_pas_obstacle(self):
        """Notre base écrit « Shurik'N », MusicBrainz « Shurik’n »."""
        candidats = [{"id": "x", "name": "Shurik’n"}]
        assert candidats_exacts("Shurik'N", candidats) == candidats

    def test_les_accents_non_plus(self):
        assert candidats_exacts("Felé Flingue", [{"id": "x", "name": "Félé Flingue"}])

    def test_nom_vide(self):
        assert candidats_exacts("", _CANDIDATS_SWING) == []

    def test_candidat_sans_nom_ignore(self):
        assert candidats_exacts("Swing", [{"id": "x"}]) == []


class TestRelationsMembre:
    """La direction est RELATIVE à l'entité interrogée — le piège de l'API."""

    def test_depuis_le_groupe_la_cible_est_un_membre(self):
        (rel,) = relations_membre(_IAM)
        assert rel.kind == "has_member"
        assert (rel.nom, rel.mbid) == ("Freeman", "a-freeman")
        assert (rel.begin, rel.end, rel.ended) == ("1989-10", "2009", True)

    def test_depuis_la_personne_la_cible_est_le_groupe(self):
        (rel,) = relations_membre(_AKHENATON)
        assert rel.kind == "member_of"
        assert rel.nom == "IAM"
        assert rel.ended is False

    def test_les_autres_types_de_relation_sont_ignores(self):
        """`collaboration` et `founder` cohabitent dans la même liste."""
        assert len(relations_membre(_IAM)) == 1

    def test_le_filtrage_porte_sur_luuid_pas_sur_le_libelle(self):
        """Le libellé est du texte d'interface, l'UUID est le contrat."""
        renomme = {
            "relations": [
                {
                    "type": "membre du groupe",  # libellé traduit
                    "type-id": MEMBRE_DE_GROUPE,
                    "direction": "forward",
                    "artist": {"id": "a", "name": "Groupe"},
                }
            ]
        }
        assert len(relations_membre(renomme)) == 1

    def test_une_relation_sans_cible_exploitable_est_ecartee(self):
        bancale = {
            "relations": [
                {"type-id": MEMBRE_DE_GROUPE, "direction": "forward", "artist": {"name": "X"}}
            ]
        }
        assert relations_membre(bancale) == []

    def test_aucune_relation(self):
        assert relations_membre({}) == []
        assert relations_membre({"relations": None}) == []


class TestOracleAlbums:
    def test_recouvrement_normalise(self):
        detail = {"release-groups": [{"title": "ALT F4"}, {"title": "Au Revoir Siméon"}]}
        assert compter_albums_communs(detail, {"alt f4", "au revoir simeon"}) == 2

    def test_aucun_album_de_notre_cote(self):
        """Sans base de comparaison, l'oracle ne dit RIEN — il ne rend pas 0
        comme un verdict, il est simplement muet."""
        assert compter_albums_communs({"release-groups": [{"title": "X"}]}, set()) == 0

    def test_candidat_sans_discographie(self):
        assert compter_albums_communs({}, {"alt f4"}) == 0


class TestDepartager:
    """Même règle que le départage des canaux YTM : pluralité NETTE ou rien."""

    def _c(self, nom, communs):
        return CandidatArtiste(mbid=nom, nom=nom, albums_communs=communs)

    def test_le_recouvrement_designe_le_bon_homonyme(self):
        """Le cas mesuré : 8 homonymes exacts pour « Swing », seul le rappeur
        belge a des albums communs — alors que le score classait premier un duo
        de Hong Kong."""
        retenu = departager([self._c("hk", 0), self._c("be", 3), self._c("us", 0)])
        assert retenu.nom == "be"

    def test_un_seul_candidat_passe_sans_recouvrement(self):
        """Personne avec qui le confondre. La validation humaine reste le
        dernier mot."""
        assert departager([self._c("seul", 0)]).nom == "seul"

    def test_egalite_en_tete_non_tranchee(self):
        """L'ordre y serait arbitraire, et se tromper produit des relations
        crédibles vers quelqu'un d'autre."""
        assert departager([self._c("a", 2), self._c("b", 2)]) is None

    def test_aucun_recouvrement_du_tout(self):
        assert departager([self._c("a", 0), self._c("b", 0)]) is None

    def test_aucun_candidat(self):
        assert departager([]) is None


class TestContratDuModule:
    def test_luuid_de_member_of_band_est_gele(self):
        """Relevé sur l'API réelle. S'il change, tout le lot 3 devient muet —
        mieux vaut un test rouge qu'un silence."""
        assert MEMBRE_DE_GROUPE == "5be4c609-9afa-4ea0-910b-12ffb71e3821"

    @pytest.mark.parametrize("champ", ["User-Agent"])
    def test_len_tete_identifiante_est_posee(self, champ):
        """Sans elle, l'API rend 503 — mesuré. C'est le contrat, pas une politesse."""
        from src.api.musicbrainz_api import MusicBrainzAPI

        api = MusicBrainzAPI()
        try:
            ua = api.session.headers[champ]
        finally:
            api.close()
        assert "MusicCreditsScraper" in ua
        assert "(" in ua and ")" in ua  # contact exigé par MusicBrainz


# ── Transport : le 503 est un ALÉA, pas un verdict ───────────────────────────


class _Reponse:
    def __init__(self, status, payload=None, retry_after=None):
        self.status_code = status
        self._payload = payload
        self.headers = {} if retry_after is None else {"Retry-After": retry_after}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def _client(monkeypatch, reponses):
    """Client dont la session sert `reponses` dans l'ordre, sans réseau ni attente."""
    import src.api.musicbrainz_api as mb

    monkeypatch.setattr(mb.time, "sleep", lambda s: None)
    api = mb.MusicBrainzAPI()
    file = list(reponses)
    api.session.get = lambda *a, **k: file.pop(0)
    return api, file


class TestReessaiSur503:
    """Mesuré le 2026-09-15 : à cadence respectée, 3 lookups sur 6 rendaient
    « server is currently busy » avec `Retry-After: 0`. Un lookup en 503 sauté
    (`continue`) faisait disparaître le SEUL homonyme exact de Kid Cudi, et la
    GUI concluait « ambiguïté » sur un aléa de transport."""

    def test_un_503_puis_200_rend_le_200(self, monkeypatch):
        api, file = _client(
            monkeypatch, [_Reponse(503, retry_after="0"), _Reponse(200, {"name": "Kid Cudi"})]
        )
        assert api.details_artiste("mbid")["name"] == "Kid Cudi"
        assert file == []

    def test_503_persistant_leve_et_ne_rend_jamais_none(self, monkeypatch):
        from src.api.musicbrainz_api import _TENTATIVES, MusicBrainzSature

        api, file = _client(monkeypatch, [_Reponse(503)] * (_TENTATIVES + 2))
        with pytest.raises(MusicBrainzSature):
            api.details_artiste("mbid")
        assert len(file) == 2  # exactement _TENTATIVES essais, pas un de plus

    def test_recherche_saturee_leve_plutot_que_liste_vide(self, monkeypatch):
        """`[]` se lirait « aucun artiste de ce nom » : un verdict, sur une panne."""
        from src.api.musicbrainz_api import _TENTATIVES, MusicBrainzSature

        api, _ = _client(monkeypatch, [_Reponse(503)] * _TENTATIVES)
        with pytest.raises(MusicBrainzSature):
            api.rechercher_artiste("Kid Cudi")

    def test_resoudre_propage_la_saturation_au_lieu_de_conclure_ambigu(self, monkeypatch):
        from src.api.musicbrainz_api import _TENTATIVES, MusicBrainzSature

        recherche = _Reponse(
            200, {"artists": [{"id": "e0e1", "name": "Kid Cudi", "type": "Person"}]}
        )
        api, _ = _client(monkeypatch, [recherche] + [_Reponse(503)] * _TENTATIVES)
        with pytest.raises(MusicBrainzSature):
            api.resoudre_artiste("Kid Cudi", set())

    def test_un_seul_homonyme_dont_le_lookup_passe_au_2e_essai_est_retenu(self, monkeypatch):
        recherche = _Reponse(
            200, {"artists": [{"id": "e0e1", "name": "Kid Cudi", "type": "Person"}]}
        )
        lookup = _Reponse(200, {"name": "Kid Cudi", "relations": [], "release-groups": []})
        api, _ = _client(monkeypatch, [recherche, _Reponse(503), lookup])
        assert api.resoudre_artiste("Kid Cudi", set()).mbid == "e0e1"

    def test_autre_erreur_http_nest_pas_reessayee(self, monkeypatch):
        api, file = _client(monkeypatch, [_Reponse(500), _Reponse(200, {})])
        with pytest.raises(RuntimeError, match="HTTP 500"):
            api.details_artiste("mbid")
        assert len(file) == 1


class TestRetryAfter:
    def test_jamais_sous_la_cadence(self):
        from src.api.musicbrainz_api import _INTERVALLE_MIN_S, _retry_after

        assert _retry_after("0") == _INTERVALLE_MIN_S
        assert _retry_after(None) == _INTERVALLE_MIN_S
        assert _retry_after("n'importe quoi") == _INTERVALLE_MIN_S

    def test_honore_un_delai_plus_long(self):
        from src.api.musicbrainz_api import _retry_after

        assert _retry_after("5") == 5.0
