"""Spotify par les requêtes internes du lecteur web (`src/api/spotify_pathfinder.py`).

Aucun réseau : la lecture se teste sur des réponses RÉELLES allégées
(`tests/fixtures/spotify_pathfinder/`, lues le 2026-11-20), le transport sur un
faux catalogue qui imite `spotapi.Catalogue().queries.query`.
"""

import asyncio
import json
from pathlib import Path

import pytest
from spotapi.captcha import CaptchaRequired
from spotapi.types.exceptions import BaseClientError, HTTPError

from src.api import spotify_pathfinder as sp
from src.observability import source_usage
from src.observability.issues import IssueKind, SansReponse, classify
from src.persistence import brut

FIXTURES = Path(__file__).parent / "fixtures" / "spotify_pathfinder"


def _fixture(nom: str) -> dict:
    return json.loads((FIXTURES / f"{nom}.json").read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _verdicts_propres():
    source_usage.reset()
    yield
    source_usage.reset()


def _verdicts() -> list[tuple[str, IssueKind]]:
    return [(v.source_key, v.issue) for v in source_usage.flush()]


class _FausseRequete:
    """Imite `Catalogue().queries` : rend (ou lève) ce qu'on lui a dit, par appel."""

    def __init__(self, *reponses):
        self.reponses = list(reponses)
        self.appels: list[tuple[str, dict]] = []

    async def query(self, operation, variables):
        self.appels.append((operation, variables))
        reponse = self.reponses.pop(0)
        if isinstance(reponse, BaseException):
            raise reponse
        if callable(reponse):
            return await reponse()
        return reponse


class _FauxCatalogue:
    def __init__(self, requete: _FausseRequete):
        self.queries = requete
        self.ferme = False

    async def __aexit__(self, *exc_info):
        self.ferme = True


def _client(*reponses, delai_s: float = 5.0):
    requete = _FausseRequete(*reponses)
    client = sp.SpotifyPathfinder(delai_s=delai_s)
    client._catalogue = _FauxCatalogue(requete)
    return client, requete


# ── Lecture du JSON ───────────────────────────────────────────────────────────
class TestLecture:
    def test_piste(self):
        piste = sp.lire_piste(_fixture("piste"))
        assert piste == sp.PisteSpotify(
            id="4uLU6hMCjMI75M1A2tKUQC",
            titre="Never Gonna Give You Up",
            compteur=1204777030,
            duree_s=213.573,
            album_id="6N9PS4QXF1D0OWPk0Sxtb4",
            place=1,
            disque=None,
            artistes=(("Rick Astley", "0gxyHStUsqpMadRV0Di1Qt"),),
        )

    def test_piste_inconnue_est_une_absence(self):
        assert sp.lire_piste(_fixture("piste_inconnue")) is None

    def test_piste_sans_compteur_ne_vaut_pas_zero(self):
        data = _fixture("piste")
        del data["trackUnion"]["playcount"]
        assert sp.lire_piste(data).compteur is None

    def test_pistes_album_dans_l_ordre_avec_leur_compteur(self):
        pistes, total = sp.lire_page_album(_fixture("pistes_album"), "4eEGkGVU2YOsdieBcwKZ9p")
        assert total == 5
        assert [p.place for p in pistes] == [1, 2, 3, 4, 5]
        assert pistes[4].titre == "Jamais assez (Live)"
        assert pistes[4].compteur == 21689
        assert all(p.album_id == "4eEGkGVU2YOsdieBcwKZ9p" and p.disque == 1 for p in pistes)

    def test_artiste_a_la_biographie_vide(self):
        """Le modèle typé de spotapi refuse une biographie au texte `None`
        (Lunatic, Karlit & Kabok) : notre lecture n'en a pas besoin."""
        artiste = sp.lire_artiste(_fixture("artiste"))
        assert artiste.nom == "Weezer"
        assert artiste.auditeurs_mensuels == 20280373
        assert artiste.abonnes == 5790328

    def test_artiste_inconnu_est_une_absence(self):
        assert sp.lire_artiste(_fixture("artiste_inconnu")) is None

    @pytest.mark.parametrize(
        "data",
        [None, {}, {"trackUnion": "x"}, {"trackUnion": {"__typename": "Track"}}],
    )
    def test_structure_inattendue(self, data):
        with pytest.raises(sp.StructureInattendue):
            sp.lire_piste(data)


# ── Transport ─────────────────────────────────────────────────────────────────
class TestTransport:
    def test_piste_lue_gardee_au_brut_et_ok(self):
        client, requete = _client(_fixture("piste"))
        piste = asyncio.run(client.piste("4uLU6hMCjMI75M1A2tKUQC"))
        assert piste.compteur == 1204777030
        assert requete.appels == [("getTrack", {"uri": "spotify:track:4uLU6hMCjMI75M1A2tKUQC"})]
        assert _verdicts() == [("spotify_pathfinder", IssueKind.OK)]
        params = sp._parametres("getTrack", {"uri": "spotify:track:4uLU6hMCjMI75M1A2tKUQC"})
        assert len(brut.lectures(sp.SOURCE, sp.URL_REQUETES, params)) == 1

    def test_not_found_est_une_absence_repondue(self):
        client, _ = _client(_fixture("piste_inconnue"))
        assert asyncio.run(client.piste("0000000000000000000000")) is None
        assert _verdicts() == [("spotify_pathfinder", IssueKind.ABSENT)]

    @pytest.mark.parametrize(
        ("erreur", "nature"),
        [
            (HTTPError("bridé", status_code=429), IssueKind.THROTTLED),
            (HTTPError("panne", status_code=503), IssueKind.UNREACHABLE),
            (HTTPError("refusé", status_code=403), IssueKind.AUTH),
            (
                BaseClientError("Pathfinder query endpoint returned an empty response body"),
                IssueKind.THROTTLED,
            ),
            (BaseClientError("No persisted query hash for 'getTrack'"), IssueKind.PARSE),
            (CaptchaRequired("captcha"), IssueKind.BLOCKED),
        ],
    )
    def test_un_echec_n_est_jamais_une_absence(self, erreur, nature):
        client, _ = _client(erreur)
        with pytest.raises(SansReponse) as e:
            asyncio.run(client.piste("x"))
        assert e.value.kind == nature
        assert _verdicts() == [("spotify_pathfinder", nature)]

    def test_coupure_de_transport_sans_code(self):
        import wreq.exceptions

        enveloppe = HTTPError("Request failed after 3 attempts")
        enveloppe.__cause__ = wreq.exceptions.TimeoutError("lent")
        client, _ = _client(enveloppe, HTTPError("transport"))
        with pytest.raises(SansReponse) as e:
            asyncio.run(client.piste("x"))
        assert e.value.kind == IssueKind.TIMEOUT
        with pytest.raises(SansReponse) as e:
            asyncio.run(client.piste("y"))
        assert e.value.kind == IssueKind.UNREACHABLE

    def test_erreur_wreq_nue(self):
        import wreq.exceptions

        client, _ = _client(wreq.exceptions.ConnectionError("refusée"))
        with pytest.raises(SansReponse) as e:
            asyncio.run(client.piste("x"))
        assert e.value.kind == IssueKind.UNREACHABLE

    def test_le_delai_de_garde_borne_la_pause_de_spotapi(self):
        """Après un 429, spotapi peut attendre jusqu'à une heure : notre délai
        coupe et le dit `timeout`."""

        async def interminable():
            await asyncio.sleep(10)

        client, _ = _client(interminable, delai_s=0.01)
        with pytest.raises(SansReponse) as e:
            asyncio.run(client.piste("x"))
        assert e.value.kind == IssueKind.TIMEOUT

    def test_structure_changee_est_une_casse_de_lecture(self):
        client, _ = _client({"trackUnion": {"__typename": "Track"}})
        with pytest.raises(SansReponse) as e:
            asyncio.run(client.piste("x"))
        assert e.value.kind == IssueKind.PARSE
        assert _verdicts() == [("spotify_pathfinder", IssueKind.PARSE)]

    def test_fermer_ferme_la_session(self):
        client, _ = _client()
        catalogue = client._catalogue
        asyncio.run(client.fermer())
        assert catalogue.ferme and client._catalogue is None


class TestAlbum:
    def test_une_page_suffit(self):
        client, requete = _client(_fixture("pistes_album"))
        pistes = asyncio.run(client.pistes_album("4eEGkGVU2YOsdieBcwKZ9p"))
        assert len(pistes) == 5
        assert len(requete.appels) == 1

    def test_pagination_jusqu_au_total(self, monkeypatch):
        monkeypatch.setattr(sp, "PISTES_PAR_PAGE", 3)
        complet = _fixture("pistes_album")
        items = complet["albumUnion"]["tracksV2"]["items"]

        def page(debut, fin):
            data = json.loads(json.dumps(complet))
            data["albumUnion"]["tracksV2"]["items"] = items[debut:fin]
            return data

        client, requete = _client(page(0, 3), page(3, 6))
        pistes = asyncio.run(client.pistes_album("4eEGkGVU2YOsdieBcwKZ9p"))
        assert [p.place for p in pistes] == [1, 2, 3, 4, 5]
        assert [v["offset"] for _, v in requete.appels] == [0, 3]

    def test_album_inconnu(self):
        client, _ = _client({"albumUnion": {"__typename": "NotFound"}})
        assert asyncio.run(client.pistes_album("x")) is None


class TestRejeu:
    def test_le_rejeu_sert_le_brut_sans_appel(self):
        client, _ = _client(_fixture("piste"))
        asyncio.run(client.piste("4uLU6hMCjMI75M1A2tKUQC"))
        source_usage.reset()

        hors_ligne, requete = _client()  # aucune réponse : un appel lèverait
        with brut.rejeu():
            piste = asyncio.run(hors_ligne.piste("4uLU6hMCjMI75M1A2tKUQC"))
        assert piste.compteur == 1204777030
        assert requete.appels == []

    def test_absent_du_brut_n_est_pas_une_absence(self):
        client, _ = _client()
        with brut.rejeu(), pytest.raises(SansReponse) as e:
            asyncio.run(client.piste("jamais_lue"))
        assert e.value.kind == IssueKind.INDETERMINATE


def test_classement_des_exceptions_spotapi():
    """`issues.classify` reconnaît spotapi par son module, comme httpx ou
    ytmusicapi : le capteur `attempt` d'un autre appelant le classera pareil."""
    assert classify(exc=HTTPError("x", status_code=429)) == IssueKind.THROTTLED
    assert classify(exc=HTTPError("x", status_code=404)) == IssueKind.ABSENT
    assert classify(exc=BaseClientError("x returned an empty response body")) == (
        IssueKind.THROTTLED
    )
    assert classify(exc=BaseClientError("Pathfinder getTrack returned no object data")) == (
        IssueKind.PARSE
    )
