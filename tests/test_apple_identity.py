"""Lot B6 : l'`apple_music_id` que porte Genius se VÉRIFIE (iTunes lookup).

Cas réels mesurés le 2026-09-29 sur 40 fiches tirées au hasard. Aucun réseau.
"""

from src.api import itunes_api
from src.models import Artist, Track
from src.utils.apple_identity import fiche_concorde


def _t(titre, artiste, **kw):
    t = Track(title=titre, artist=Artist(name=artiste))
    for k, v in kw.items():
        setattr(t, k, v)
    return t


def test_genius_porte_des_faux_refuses_par_l_artiste():
    ok, motif = fiche_concorde(
        _t("Gold Digger", "Kanye West"), {"artistName": "Beau Monga", "trackName": "Gold Digger"}
    )
    assert not ok and "Beau Monga" in motif
    t = _t("Heartless", "Kid Cudi", primary_artist_name="Kanye West")
    assert not fiche_concorde(t, {"artistName": "Kris Allen", "trackName": "Heartless"})[0]


def test_un_invite_credite_dans_le_titre():
    """« Première Catégorie (Featuring Calbo Et Booba) » est crédité à Lino."""
    t = _t("Première catégorie", "Booba", primary_artist_name="Lino")
    fiche = {"artistName": "Lino", "trackName": "Première Catégorie (Featuring Calbo Et Booba)"}
    assert fiche_concorde(t, fiche)[0]
    t = _t("3.5.7", "Booba")
    assert fiche_concorde(t, {"artistName": "USKY & Booba", "trackName": "3.5.7"})[0]


def test_titre_et_duree_contredisent():
    t = _t("Stronger", "Kanye West", duration=311)
    assert not fiche_concorde(t, {"artistName": "Kanye West", "trackName": "Stronger (Live)"})[0]
    fiche = {"artistName": "Kanye West", "trackName": "Stronger", "trackTimeMillis": 250000}
    assert "durée" in fiche_concorde(t, fiche)[1]
    assert not fiche_concorde(t, None)[0]


def test_lookup_par_lots_et_par_boutique(monkeypatch):
    """Un identifiant absent de la boutique FR est redemandé aux US."""
    demandes = []

    class _Resp:
        status_code = 200
        headers: dict = {}

        def __init__(self, results):
            self._r = results

        def raise_for_status(self):
            pass

        def json(self):
            return {"results": self._r}

    catalogue = {"fr": {"1": "A"}, "us": {"2": "B"}}

    def get(url, params, timeout):
        demandes.append((params["country"], params["id"]))
        ids = params["id"].split(",")
        return _Resp(
            [
                {"wrapperType": "track", "trackId": int(i), "trackName": n}
                for i, n in catalogue[params["country"]].items()
                if i in ids
            ]
        )

    # Sous le capteur (`requests_get` doit voir la réponse, sinon le verdict
    # serait `indeterminate` — une panne, et le lookup lèverait).
    monkeypatch.setattr("requests.get", get)
    monkeypatch.setattr(itunes_api, "_INTERVALLE_S", 0)
    trouves = itunes_api.ITunesAPI().lookup(["1", "2", "3"])
    assert set(trouves) == {"1", "2"}
    assert demandes == [("fr", "1,2,3"), ("us", "2,3")]
