"""Tests du StreamsProvider (src/enrichment/providers/streams) — sans réseau.

Vérifie la délégation aux updaters, le PARTAGE du client YTM entre streams et
vues vidéo (créé lazy une seule fois), et le close() défensif.
"""

import src.api.ytmusic_api as ytmapi_mod
import src.utils.update_kworb as kworb_mod
import src.utils.update_video_views as vv_mod
import src.utils.update_ytmusic as ytm_mod
from src.enrichment.providers.streams import StreamsProvider


def test_fetch_spotify_delegue_avec_le_client_kworb(monkeypatch):
    seen = {}

    def fake(artist, dm, scraper=None):
        seen["scraper"] = scraper
        return {"matched": 1}

    monkeypatch.setattr(kworb_mod, "update_kworb_streams", fake)
    kworb = object()
    provider = StreamsProvider(kworb=kworb)
    assert provider.fetch_spotify("A", "DM") == {"matched": 1}
    assert seen["scraper"] is kworb  # client injecté transmis, pas de création


def test_fetch_ytm_delegue_avec_le_client_ytm(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        ytm_mod,
        "update_ytmusic_streams",
        lambda a, d, api=None: seen.update(api=api) or {"matched": 2},
    )
    ytm = object()
    provider = StreamsProvider(ytm=ytm)
    assert provider.fetch_ytm("A", "DM") == {"matched": 2}
    assert seen["api"] is ytm


def test_client_ytm_cree_lazy_une_seule_fois_et_partage(monkeypatch):
    """Streams YTM et vues vidéo partagent LE MÊME YTMusicAPI (créé une fois)."""
    count = {"n": 0}

    class FakeYTM:
        def __init__(self):
            count["n"] += 1

    monkeypatch.setattr(ytmapi_mod, "YTMusicAPI", FakeYTM)
    captured = []
    monkeypatch.setattr(
        ytm_mod, "update_ytmusic_streams", lambda a, d, api=None: captured.append(api) or {}
    )
    monkeypatch.setattr(
        vv_mod, "update_video_views", lambda a, t, d, api=None: captured.append(api) or {}
    )

    provider = StreamsProvider()  # ytm non injecté → lazy
    provider.fetch_ytm("A", "DM")
    provider.fetch_video_views("A", [], "DM")

    assert count["n"] == 1  # une seule instanciation YTMusicAPI
    assert captured[0] is captured[1]  # même instance partagée
    assert isinstance(captured[0], FakeYTM)


def test_close_ferme_les_clients_qui_l_exposent():
    closed = {"k": False}

    class _Closable:
        def close(self):
            closed["k"] = True

    provider = StreamsProvider(kworb=_Closable(), ytm=object())  # ytm sans close()
    provider.close()  # ne lève pas malgré le client sans close()
    assert closed["k"] is True
