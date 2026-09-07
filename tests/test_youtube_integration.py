"""Choix du lien YouTube d'un morceau (`YouTubeIntegration`) + classification.

Couverture DIFFÉRÉE au « tour global YouTube » par le WIP : la tester plus tôt
aurait figé des décisions qui allaient changer. Le lot 2 est passé, et il a
laissé ces décisions-là intactes — seuils, stratégies de requête et priorité du
lien en base sont donc gelés ici, pour qu'un prochain passage ait à les assumer.

Aucun réseau : le chercheur est remplacé, et le singleton du module construit le
sien PARESSEUSEMENT (importer le module n'ouvre plus ni cache ni client YTMusic).
"""

import pytest

import src.utils.youtube_integration as yi
from src.utils.youtube_integration import YouTubeIntegration
from src.youtube.track_classifier import TrackClassifier, TrackType


class _FauxChercheur:
    def __init__(self, resultats=None):
        self._resultats = resultats or []
        self.appels = []

    def search_track(self, artist, title, max_results=25):
        self.appels.append((artist, title))
        return self._resultats


def _integration(resultats=None, chercheur=None):
    integration = YouTubeIntegration()
    integration._searcher = chercheur or _FauxChercheur(resultats)
    return integration


def _resultat(score=0.95, **kw):
    base = {
        "url": "https://youtube.com/watch?v=dQw4w9WgXcQ",
        "video_id": "dQw4w9WgXcQ",
        "title": "Magot",
        "channel_title": "Isha",
        "relevance_score": score,
    }
    base.update(kw)
    return base


class TestLienDejaEnBase:
    """Un lien connu court-circuite TOUTE recherche : c'est ce qui rend
    l'ouverture d'une fiche instantanée, et non un appel réseau par affichage."""

    def test_aucune_recherche_lancee(self):
        chercheur = _FauxChercheur([_resultat()])
        res = _integration(chercheur=chercheur).get_youtube_link_for_track(
            "Isha", "Magot", known_url="https://youtu.be/aaaaaaaaaaa"
        )
        assert chercheur.appels == []
        assert res["url"] == "https://youtu.be/aaaaaaaaaaa"
        assert (res["type"], res["method"], res["confidence"]) == ("direct", "stored", 1.0)

    def test_la_provenance_par_defaut_est_genius(self):
        res = _integration().get_youtube_link_for_track(
            "Isha", "Magot", known_url="https://youtu.be/aaaaaaaaaaa"
        )
        assert res["source"] == "genius_media"
        assert "Genius" in res["channel"]

    def test_une_provenance_connue_est_respectee(self):
        res = _integration().get_youtube_link_for_track(
            "Isha", "Magot", known_url="https://youtu.be/aaaaaaaaaaa", known_source="manual"
        )
        assert res["source"] == "manual"


class TestSelectionAutomatique:
    def test_au_dessus_du_seuil_le_resultat_est_retenu(self, monkeypatch):
        monkeypatch.setattr(yi, "YOUTUBE_AUTO_SELECT_ALBUM_TRACKS", True)
        res = _integration([_resultat(score=0.95)]).get_youtube_link_for_track(
            "Isha", "Magot", album="Alb"
        )
        assert (res["type"], res["method"], res["source"]) == (
            "direct",
            "auto_selected",
            "search_auto",
        )
        assert res["confidence"] == 0.95

    def test_sous_le_seuil_on_retombe_sur_une_recherche(self, monkeypatch):
        """Le seuil ALBUM est 0,85 : en dessous, aucun lien n'est proposé — et
        c'est une URL de recherche, explicitement, pas une vidéo au hasard."""
        monkeypatch.setattr(yi, "YOUTUBE_AUTO_SELECT_ALBUM_TRACKS", True)
        res = _integration([_resultat(score=0.5)]).get_youtube_link_for_track(
            "Isha", "Magot", album="Alb"
        )
        assert res["type"] == "search"
        assert "results?search_query=" in res["url"]

    def test_un_lien_de_recherche_nest_jamais_pris_pour_une_video(self, monkeypatch):
        """Le repli sans ytmusicapi rend des `is_search_url` à score élevé :
        les accepter poserait en base un lien dont aucune vue n'est comptable."""
        monkeypatch.setattr(yi, "YOUTUBE_AUTO_SELECT_ALBUM_TRACKS", True)
        res = _integration([_resultat(score=0.99, is_search_url=True)]).get_youtube_link_for_track(
            "Isha", "Magot", album="Alb"
        )
        assert res["type"] == "search"

    def test_aucun_resultat(self, monkeypatch):
        monkeypatch.setattr(yi, "YOUTUBE_AUTO_SELECT_ALBUM_TRACKS", True)
        res = _integration([]).get_youtube_link_for_track("Isha", "Magot", album="Alb")
        assert res["type"] == "search"

    def test_le_reglage_global_coupe_la_selection_auto(self, monkeypatch):
        monkeypatch.setattr(yi, "YOUTUBE_AUTO_SELECT_ALBUM_TRACKS", False)
        chercheur = _FauxChercheur([_resultat(score=0.99)])
        res = _integration(chercheur=chercheur).get_youtube_link_for_track(
            "Isha", "Magot", album="Alb"
        )
        assert chercheur.appels == []  # pas même de recherche
        assert res["type"] == "search"

    def test_un_type_non_auto_selectionnable_ne_cherche_pas(self, monkeypatch):
        """LIVE et EXOTIC sont laissés à l'humain : trop de variantes."""
        monkeypatch.setattr(yi, "YOUTUBE_AUTO_SELECT_ALBUM_TRACKS", True)
        chercheur = _FauxChercheur([_resultat(score=0.99)])
        res = _integration(chercheur=chercheur).get_youtube_link_for_track(
            "Isha", "Magot (Live)", album="Alb"
        )
        assert chercheur.appels == []
        assert res["track_type"] == "live"


class TestUrlDeRecherche:
    def test_la_requete_suit_la_strategie_du_type(self):
        res = _integration().get_youtube_link_for_track("Isha", "Magot", album="Alb")
        assert res["query"] == '"Isha" "Magot"'  # stratégie ALBUM
        assert res["method"] == "optimized_search"

    def test_un_single_cherche_official(self):
        res = _integration().get_youtube_link_for_track("Isha", "Magot")
        assert res["query"] == "Isha Magot official"

    def test_repli_quand_la_classification_casse(self, monkeypatch):
        """Dernier ressort : une recherche basique plutôt qu'aucun lien."""
        integration = _integration()
        monkeypatch.setattr(
            integration.classifier,
            "classify_track",
            lambda *a, **k: (_ for _ in ()).throw(TypeError("boom")),
        )
        res = integration.get_youtube_link_for_track("Isha", "Magot")
        assert res["method"] == "fallback_search"
        assert res["track_type"] == "unknown"


class TestOuverture:
    def test_ouvre_lurl(self, monkeypatch):
        ouvertes = []
        monkeypatch.setattr(yi.webbrowser, "open", ouvertes.append)
        assert _integration().open_youtube_link({"url": "https://y/x", "type": "direct"}) is True
        assert ouvertes == ["https://y/x"]

    def test_sans_url_ne_fait_rien(self, monkeypatch):
        monkeypatch.setattr(yi.webbrowser, "open", lambda u: pytest.fail("rien à ouvrir"))
        assert _integration().open_youtube_link({}) is False

    def test_un_navigateur_indisponible_ne_fait_pas_crasher(self, monkeypatch):
        def _boom(url):
            raise OSError("pas de navigateur")

        monkeypatch.setattr(yi.webbrowser, "open", _boom)
        assert _integration().open_youtube_link({"url": "https://y/x"}) is False


class TestClassifieurDecisions:
    """Les trois tables qui pilotent la recherche. Elles ne sont atteintes QUE
    par `YouTubeIntegration` : les deux modules tombent ensemble."""

    @pytest.fixture
    def classifier(self):
        return TrackClassifier()

    @pytest.mark.parametrize(
        "type_, auto",
        [
            (TrackType.ALBUM, True),
            (TrackType.SINGLE, True),
            (TrackType.REMIX, True),
            (TrackType.ACOUSTIC, True),
            (TrackType.LIVE, False),
            (TrackType.EXOTIC, False),
        ],
    )
    def test_qui_peut_etre_choisi_automatiquement(self, classifier, type_, auto):
        assert classifier.should_auto_select(type_) is auto

    def test_les_seuils_vont_du_plus_strict_au_plus_permissif(self, classifier):
        """Un morceau d'album a une vidéo canonique, un inédit non : le seuil
        suit la certitude qu'on peut avoir du résultat."""
        assert classifier.get_confidence_threshold(TrackType.ALBUM) == 0.85
        assert classifier.get_confidence_threshold(TrackType.EXOTIC) == 0.60
        assert classifier.get_confidence_threshold(
            TrackType.ALBUM
        ) > classifier.get_confidence_threshold(TrackType.LIVE)

    def test_un_type_inconnu_prend_un_seuil_median(self, classifier):
        assert classifier.get_confidence_threshold("pas un type") == 0.75

    def test_chaque_type_a_une_strategie_complete(self, classifier):
        for type_ in TrackType:
            strategie = classifier.get_search_strategy(type_)
            assert {"primary_query", "fallback_query", "priority"} <= set(strategie)
            assert "{title}" in strategie["primary_query"]

    def test_un_type_inconnu_retombe_sur_la_strategie_album(self, classifier):
        assert classifier.get_search_strategy("inconnu") == classifier.get_search_strategy(
            TrackType.ALBUM
        )

    @pytest.mark.parametrize(
        "titre, album, attendu",
        [
            ("Magot", "Alb", TrackType.ALBUM),
            ("Magot", None, TrackType.SINGLE),
            ("Magot (Remix)", "Alb", TrackType.REMIX),
            ("Magot - Live", "Alb", TrackType.LIVE),
            ("Magot (Acoustic)", "Alb", TrackType.ACOUSTIC),
            ("Magot (Instrumental)", "Alb", TrackType.EXOTIC),
            ("Freestyle Planète Rap", "Alb", TrackType.EXOTIC),
        ],
    )
    def test_classification(self, classifier, titre, album, attendu):
        assert classifier.classify_track(titre, album=album) == attendu

    def test_un_titre_ancien_est_exotique(self, classifier):
        assert (
            classifier.classify_track("Magot", album="Alb", release_year=1985) is TrackType.EXOTIC
        )
