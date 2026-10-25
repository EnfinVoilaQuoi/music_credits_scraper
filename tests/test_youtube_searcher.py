"""Recherche YouTube avec cache et repli (`youtube_searcher`).

À 24 % de couverture. Le cache SQLite évite de refaire une recherche réseau à
chaque passage sur un morceau ; le score de pertinence décide quel résultat est
proposé en premier — donc quel lien finit associé au morceau.

`DATA_DIR` est redirigé vers `tmp_path` : le cache réel (`data/youtube_cache.db`)
n'est jamais touché, et `ytmusicapi` est remplacé par un faux client.
"""

import sqlite3
from datetime import datetime, timedelta

import pytest
import requests

from src.youtube import youtube_searcher as ys
from src.youtube.youtube_searcher import YouTubeSearcher


class _FauxYT:
    def __init__(self, resultats=None, leve=None):
        self._resultats = resultats if resultats is not None else []
        self._leve = leve
        self.requetes = []

    def search(self, query, filter=None, limit=None):  # noqa: A002 — signature ytmusicapi
        self.requetes.append(query)
        if self._leve is not None:
            raise self._leve
        return self._resultats


@pytest.fixture
def searcher(tmp_path, monkeypatch):
    """Chercheur au cache isolé, sans client YTMusic (repli par défaut)."""
    monkeypatch.setattr(ys, "DATA_DIR", tmp_path)
    s = YouTubeSearcher.__new__(YouTubeSearcher)
    s.cache_db = tmp_path / "youtube_cache.db"
    s._init_cache()
    s.ytmusic = None
    s.ytmusic_available = False
    return s


def _resultat_ytm(titre="Bande organisée", artiste="Jul", vid="V1", duree="3:34"):
    return {
        "videoId": vid,
        "title": titre,
        "artists": [{"name": artiste}],
        "duration": duree,
        "thumbnails": [
            {"url": "https://img/petite.jpg", "width": 60, "height": 60},
            {"url": "https://img/grande.jpg", "width": 544, "height": 544},
        ],
    }


class TestCache:
    def test_table_creee(self, searcher):
        conn = sqlite3.connect(searcher.cache_db)
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        conn.close()
        assert "youtube_search_cache" in tables

    def test_aller_retour(self, searcher):
        resultats = [{"video_id": "V1", "title": "Titre"}]
        searcher._cache_result("cle", resultats)
        assert searcher._get_cached_result("cle") == resultats

    def test_cle_absente(self, searcher):
        assert searcher._get_cached_result("jamais vue") is None

    def test_entree_expiree_ignoree(self, searcher):
        """Une recherche vieille de plusieurs jours ne reflète plus la
        plateforme : passée l'échéance, on refait la recherche."""
        conn = sqlite3.connect(searcher.cache_db)
        import pickle

        conn.execute(
            "INSERT INTO youtube_search_cache VALUES (?, ?, ?, ?)",
            (
                "perimee",
                pickle.dumps([{"video_id": "V1"}]),
                datetime.now() - timedelta(days=10),
                datetime.now() - timedelta(days=1),
            ),
        )
        conn.commit()
        conn.close()
        assert searcher._get_cached_result("perimee") is None

    def test_ecrasement_de_la_meme_cle(self, searcher):
        searcher._cache_result("cle", [{"video_id": "ANCIEN"}])
        searcher._cache_result("cle", [{"video_id": "NOUVEAU"}])
        assert searcher._get_cached_result("cle")[0]["video_id"] == "NOUVEAU"

    def test_base_illisible_ne_fait_pas_crasher(self, searcher):
        """Un cache corrompu doit dégrader vers « pas de cache », pas interrompre
        l'enrichissement."""
        searcher.cache_db = "/chemin/impossible/cache.db"
        assert searcher._get_cached_result("cle") is None
        searcher._cache_result("cle", [{"a": 1}])  # ne lève pas


class TestScoreDePertinence:
    def test_correspondance_parfaite(self, searcher):
        score = searcher._calculate_relevance_score(_resultat_ytm(), "Jul", "Bande organisée")
        assert score == pytest.approx(1.0)

    def test_titre_pese_plus_que_l_artiste(self, searcher):
        """Pondération 60/40 : un bon titre chez le mauvais artiste marque plus
        qu'un bon artiste sur le mauvais titre."""
        bon_titre = searcher._calculate_relevance_score(
            _resultat_ytm(artiste="Quelqu'un d'autre"), "Jul", "Bande organisée"
        )
        bon_artiste = searcher._calculate_relevance_score(
            _resultat_ytm(titre="Rien à voir du tout"), "Jul", "Bande organisée"
        )
        assert bon_titre > bon_artiste

    def test_aucune_correspondance(self, searcher):
        score = searcher._calculate_relevance_score(
            _resultat_ytm(titre="zzzz", artiste="wwww"), "Jul", "Bande organisée"
        )
        assert score < 0.3

    def test_artiste_en_chaine_brute(self, searcher):
        """ytmusicapi rend tantôt des dicts, tantôt des chaînes."""
        resultat = {"title": "Bande organisée", "artists": ["Jul"]}
        assert searcher._calculate_relevance_score(resultat, "Jul", "Bande organisée") == 1.0

    def test_resultat_sans_artiste(self, searcher):
        resultat = {"title": "Bande organisée", "artists": []}
        score = searcher._calculate_relevance_score(resultat, "Jul", "Bande organisée")
        assert score == pytest.approx(0.6)  # titre seul

    def test_plusieurs_artistes_le_meilleur_compte(self, searcher):
        resultat = {
            "title": "Bande organisée",
            "artists": [{"name": "Inconnu"}, {"name": "Jul"}],
        }
        assert searcher._calculate_relevance_score(resultat, "Jul", "Bande organisée") == 1.0


class TestVignette:
    def test_meilleure_resolution_choisie(self, searcher):
        vignettes = [
            {"url": "petite", "width": 60, "height": 60},
            {"url": "grande", "width": 544, "height": 544},
            {"url": "moyenne", "width": 226, "height": 226},
        ]
        assert searcher._get_best_thumbnail(vignettes) == "grande"

    def test_aucune_vignette(self, searcher):
        assert searcher._get_best_thumbnail([]) is None

    def test_dimensions_absentes(self, searcher):
        assert searcher._get_best_thumbnail([{"url": "sans_taille"}]) == "sans_taille"


class TestRechercheYtMusic:
    def _searcher(self, searcher, resultats=None, leve=None):
        searcher.ytmusic = _FauxYT(resultats=resultats, leve=leve)
        searcher.ytmusic_available = True
        return searcher

    def test_resultat_formate(self, searcher):
        s = self._searcher(searcher, [_resultat_ytm()])
        res = s.search_track("Jul", "Bande organisée")
        assert res[0]["video_id"] == "V1"
        assert res[0]["channel_title"] == "Jul"
        assert res[0]["source"] == "ytmusicapi"
        assert res[0]["url"] == "https://youtube.com/watch?v=V1"
        assert res[0]["thumbnail_url"] == "https://img/grande.jpg"

    def test_doublons_ecartes(self, searcher):
        """Trois requêtes sont lancées : le même videoId ne doit apparaître
        qu'une fois."""
        s = self._searcher(searcher, [_resultat_ytm(), _resultat_ytm()])
        assert len(s.search_track("Jul", "Bande organisée")) == 1

    def test_plusieurs_variantes_de_requete(self, searcher):
        """Trois formulations sont tentées : la recherche exacte, la citée et
        celle avec « official »."""
        s = self._searcher(searcher, [_resultat_ytm()])
        s.search_track("Jul", "Bande organisée")
        assert len(s.ytmusic.requetes) >= 1
        assert "Jul Bande organisée" in s.ytmusic.requetes[0]

    def test_tri_par_pertinence(self, searcher):
        s = self._searcher(
            searcher,
            [
                _resultat_ytm(titre="Rien à voir", vid="MAUVAIS"),
                _resultat_ytm(vid="BON"),
            ],
        )
        res = s.search_track("Jul", "Bande organisée")
        assert res[0]["video_id"] == "BON"

    def test_resultats_sans_identifiant_ignores(self, searcher):
        s = self._searcher(searcher, [{"title": "Sans videoId"}, _resultat_ytm()])
        assert len(s.search_track("Jul", "Bande organisée")) == 1

    def test_plafond_de_resultats(self, searcher):
        s = self._searcher(searcher, [_resultat_ytm(vid=f"V{i}") for i in range(20)])
        assert len(s.search_track("Jul", "Titre", max_results=3)) == 3

    def test_erreur_bascule_sur_le_repli(self, searcher):
        """ytmusicapi en panne → le repli prend la main plutôt que de rendre
        une liste vide."""
        s = self._searcher(searcher, leve=requests.RequestException("timeout"))
        res = s.search_track("Jul", "Bande organisée")
        assert res
        assert all(r["source"] != "ytmusicapi" for r in res)


class TestCacheDansLaRecherche:
    def test_second_appel_servi_par_le_cache(self, searcher):
        searcher.ytmusic = _FauxYT([_resultat_ytm()])
        searcher.ytmusic_available = True
        premier = searcher.search_track("Jul", "Bande organisée")
        nb_requetes = len(searcher.ytmusic.requetes)
        second = searcher.search_track("Jul", "Bande organisée")
        assert second == premier
        assert len(searcher.ytmusic.requetes) == nb_requetes  # aucun appel de plus

    def test_cle_insensible_a_la_casse(self, searcher):
        searcher._cache_result("jul::bande_organisée", [{"video_id": "CACHE"}])
        res = searcher.search_track("JUL", "Bande Organisée")
        assert res[0]["video_id"] == "CACHE"


class TestRepliSansYtMusic:
    def test_liens_de_recherche_generes(self, searcher):
        """Sans ytmusicapi, le repli ne rend pas de vidéo mais des liens de
        recherche : c'est explicite, ça ne se fait pas passer pour un résultat."""
        res = searcher.search_track("Jul", "Bande organisée")
        assert res
        assert all("youtube.com" in r["url"] for r in res)


class TestScoreNormalise:
    """Le score compare des formes NORMALISÉES (lot 2 — la bande 0,85-0,90).

    En brut, une paire PARFAITE écrite autrement plafonnait à 0,7-0,8 : sous le
    seuil de persistance (0,90), donc jamais enregistrée, donc des vues jamais
    comptées. Mesuré sur les 401 recherches du cache réel (2026-09-07) :
    77 meilleurs résultats remontent, dont 72 de « < 0,85 » à « ≥ 0,90 ».
    """

    @pytest.mark.parametrize(
        "titre_resultat, titre_cible",
        [
            ("SOAB", "S.O.A.B"),  # acronyme pointé
            ("Mauvaise Humeur (feat. Leto)", "Mauvaise Humeur"),  # suffixe featuring
            ("La vie qu'on mène", "La vie qu’on mène"),  # apostrophe typographique
            ("RAZ DE MARÉE", "Raz de marée"),  # casse
        ],
    )
    def test_une_paire_juste_ecrite_autrement_atteint_1(self, titre_resultat, titre_cible):
        assert ys.relevance_score(titre_resultat, ["ISHA"], "Isha", titre_cible) == pytest.approx(
            1.0
        )

    def test_un_mauvais_appariement_reste_bas(self):
        """La normalisation ne doit pas rapprocher n'importe quoi."""
        assert ys.relevance_score("Chicago Freestyle", ["Drake"], "Josman", "Ecstasy") < 0.5

    def test_sans_artiste_le_titre_seul_plafonne_a_0_6(self):
        assert ys.relevance_score("Magot", [], "Isha", "Magot") == pytest.approx(0.6)


class TestPurgeDuCache:
    def test_forget_retire_lentree(self, searcher):
        """Sans purge, la fiche reproposerait le même mauvais résultat jusqu'à
        expiration du cache — le rejet semblerait sans effet."""
        searcher._cache_result(ys.cache_key("Isha", "Magot"), [{"video_id": "X"}])
        assert searcher.search_track("Isha", "Magot")[0]["video_id"] == "X"

        assert searcher.forget("Isha", "Magot") is True
        assert searcher._get_cached_result(ys.cache_key("Isha", "Magot")) is None

    def test_forget_sur_une_entree_absente(self, searcher):
        assert searcher.forget("Isha", "Jamais cherché") is False

    def test_la_cle_est_la_meme_a_la_lecture_et_a_la_purge(self, searcher):
        """Casse et espaces : la purge doit viser l'entrée que la recherche a
        écrite, pas une clé voisine."""
        searcher.search_track("JUL", "Bande Organisée")
        assert searcher.forget("jul", "bande organisée") is True


class TestAutreVersion:
    """2026-10-10 : « Black Skinhead » (42 M) passait au-dessus du seuil pour la
    fiche « Black Skinhead (Remix) », « All Of The Lights » (64 M) pour son
    remix — mesuré : 32 des 1 687 vidéos posées par la recherche."""

    def test_l_original_n_est_pas_le_remix(self):
        from src.youtube.youtube_searcher import relevance_score

        assert (
            relevance_score(
                "Black Skinhead", ["Kanye West"], "Kanye West", "Black Skinhead (Remix)"
            )
            == 0.0
        )
        assert relevance_score("Interlude", ["Django"], "Django", "Nuit (Interlude)") == 0.0

    def test_la_meme_version_reste_notee(self):
        from src.youtube.youtube_searcher import relevance_score

        assert (
            relevance_score(
                "Black Skinhead (Remix)", ["Kanye West"], "Kanye West", "Black Skinhead (Remix)"
            )
            > 0.9
        )

    def test_bonus_vaut_le_titre_nu(self):
        from src.utils.version_descriptors import meme_version_publiee

        assert meme_version_publiee("LE NOM DU PÈRE (Bonus)", "LE NOM DU PÈRE")
        assert meme_version_publiee("Champions du monde", "Champions du monde (Bonus Track)")
        assert not meme_version_publiee("Ring Ring [Extended Version]", "Ring Ring")

    def test_une_performance_d_un_seul_morceau_vaut_le_morceau(self):
        """Décision utilisateur 2026-10-10 (option 2) : COLORS, live, session
        acoustique comptent pour l'original ; remix, démo, instrumental non."""
        from src.utils.version_descriptors import meme_version_publiee

        assert meme_version_publiee("Loup noir", "Loup noir - A COLORS SHOW")
        assert meme_version_publiee("Double discours", "Double Discours - Live")
        assert meme_version_publiee("Nudes", "Nudes (Acoustic)")
        assert not meme_version_publiee("Apprentissage (Remix)", "Apprentissage")
        assert not meme_version_publiee("Space Cadet", "Space Cadet (Demo)")
        assert not meme_version_publiee("Harley Davidson", "Harley Davidson (Instrumental)")
        # Jamais l'inverse : la fiche du live ne reçoit pas le clip studio.
        assert not meme_version_publiee("Double Discours - Live", "Double discours")


class TestRefusATortDuScore:
    """2026-10-20 : en rejouant les 2 260 vidéos posées par la recherche, des
    liens JUSTES tombaient sous le seuil — défauts du score, pas de la vidéo."""

    @pytest.mark.parametrize(
        "video, artistes, artiste, fiche",
        [
            # 2026-10-25 : 0,76 / 0,84 / 0,72 sous le seuil — le SOUS-TITRE de
            # la fiche manque à la vidéo (rejeu : 4 liens, 1,22 M de vues).
            (
                "Make Her Say (feat. Kanye West & Common)",
                ["Kid Cudi"],
                "Kid Cudi",
                "Make Her Say (I Poke Her Face)",
            ),
            ("Man On The Moon", ["Kid Cudi"], "Kid Cudi", "Man on the Moon (The Anthem)"),
            ("La tess", ["Medine, samb"], "Médine", "La Tess (Sambterlude)"),
        ],
    )
    def test_un_sous_titre_absent_de_la_video_ne_la_refuse_pas(
        self, video, artistes, artiste, fiche
    ):
        from src.config import YOUTUBE_CONFIDENCE_THRESHOLD
        from src.youtube.youtube_searcher import relevance_score

        assert relevance_score(video, artistes, artiste, fiche) >= YOUTUBE_CONFIDENCE_THRESHOLD

    def test_un_descripteur_n_est_pas_un_sous_titre(self):
        """La version reste jugée sur le titre COMPLET : l'original n'est
        jamais la vidéo d'un live, d'un remix ; un titre générique garde la
        parenthèse qui seule le distingue."""
        from src.youtube.youtube_searcher import relevance_score

        assert relevance_score("Blues", ["Kid Cudi"], "Kid Cudi", "Blues (Live at AK Studios)") == 0
        assert relevance_score("Heartless", ["Kanye West"], "Kanye West", "Heartless (Remix)") == 0
        assert relevance_score("Intro", ["Booba"], "Booba", "Intro (A2)") == 0

    def test_le_rappel_final_de_youtube_music_ne_compte_pas(self):
        """« (Pt. 1; Live 2006) » répète les groupes précédents : la Boulette
        de Diam's tombait à 0 (version jugée autre)."""
        from src.youtube.youtube_searcher import formes_publiees, relevance_score

        video = "La Boulette (Pt. 1) [Live 2006] (Pt. 1; Live 2006)"
        assert formes_publiees(video) == [
            "La Boulette (Pt. 1) [Live 2006]",
            "La Boulette (Pt. 1; Live 2006)",
        ]
        assert relevance_score(video, ["Diam's"], "Diam's", "La Boulette - Pt. 1- Live 2006") > 0.95
        # La fiche peut garder l'écriture du rappel lui-même.
        assert (
            relevance_score(
                "Ma France à moi (Version Yann Tiersen) [Live 2006] (Version Yann Tiersen; Live 2006)",
                ["Yann Tiersen", "Diam's"],
                "Diam’s",
                "Ma France à moi (Version Yann Tiersen; Live 2006)",
            )
            > 0.95
        )
        # Un dernier groupe qui dit autre chose reste là.
        assert formes_publiees("Tony (Remix) (Live)") == ["Tony (Remix) (Live)"]
        # Et la partie reste jugée : la Pt. 2 n'est pas la Pt. 1.
        assert (
            relevance_score(
                "La Boulette (Pt. 2) [Live 2006] (Pt. 2; Live 2006)",
                ["Diam's"],
                "Diam's",
                "La Boulette - Pt. 1- Live 2006",
            )
            == 0
        )

    def test_un_artiste_nomme_en_invite_dans_le_titre(self):
        """YouTube Music ne liste que Bruler pour « Moscou (feat. Sadek &
        Rabah) » : Sadek, nommé dans le titre, est bien un artiste du résultat."""
        from src.youtube.youtube_searcher import invites_nommes, relevance_score

        assert relevance_score("Moscou (feat. Sadek & Rabah)", ["Bruler"], "Sadek", "Moscou") == 1.0
        assert relevance_score("Moscou", ["Bruler"], "Sadek", "Moscou") < 0.85
        # Un « (with … Remix) » n'est pas une liste d'invités.
        assert invites_nommes("Hold On (With You Remix)") == []

    def test_une_date_abimee_par_la_plateforme(self):
        """Médine « 17 Octobre » est publié « 17-Oct », « 11 Septembre » « 11-Sep »."""
        from src.youtube.youtube_searcher import relevance_score

        assert relevance_score("17-Oct", ["Medine"], "Médine", "17 Octobre") == 1.0
        assert relevance_score("11-Sep", ["Medine"], "Médine", "11 Septembre") == 1.0
        # Un autre jour n'est pas la même date.
        assert relevance_score("18-Oct", ["Medine"], "Médine", "17 Octobre") < 0.85

    def test_bonus_seul_ne_pese_pas_dans_la_ressemblance(self):
        """Le « Bonus Track » de Flynt EST le morceau (décision 2026-10-15) :
        il plafonnait à 0,85, sous le seuil de 0,90 d'un lien de fiche."""
        from src.youtube.youtube_searcher import relevance_score

        assert (
            relevance_score(
                "Champions du monde", ["Flynt"], "Flynt", "Champions du monde (Bonus Track)"
            )
            == 1.0
        )
        assert relevance_score("DKR (Bonus Track)", ["Booba"], "Booba", "DKR") == 1.0
        # Les invités après la mention ne la masquent pas.
        assert (
            relevance_score(
                "PITLANE [Bonus] (feat. ElGrandeToto, Zed & Zamdane)",
                ["GP Explorer"],
                "GP Explorer",
                "PITLANE (Bonus)",
            )
            == 1.0
        )

    def test_une_edition_de_support_vaut_le_morceau(self):
        """Kanye « Love Lockdown » ← « (Album Version) » : même enregistrement
        (taxonomie : `_MOTS_DU_SUPPORT`), il tombait à 0,79."""
        from src.youtube.youtube_searcher import relevance_score

        assert (
            relevance_score(
                "Love Lockdown (Album Version)", ["Kanye West"], "Kanye West", "Love Lockdown"
            )
            == 1.0
        )
        assert (
            relevance_score("Eaters (Clean)", ["Young Stoner Life"], "Young Stoner Life", "Eaters")
            == 1.0
        )

    def test_un_autre_montage_reste_sous_le_seuil(self):
        """Un MONTAGE n'est pas une édition de support : « Radio Edit »,
        « Extended » restent dans la ressemblance."""
        from src.youtube.youtube_searcher import relevance_score

        assert relevance_score("Evasion (Radio Edit)", ["Diam's"], "Diam's", "Evasion") < 0.85

    def test_une_partie_1_publiee_sans_son_numero(self):
        from src.youtube.youtube_searcher import relevance_score

        assert (
            relevance_score("Arabian panthers", ["Medine"], "Médine", "Arabian Panthers 1") > 0.95
        )
        # Jamais une autre partie, ni un « #1 » qui numérote une version.
        assert relevance_score("Leer", ["Shurik'n"], "Shurik'n", "Leer 2") == 0
        assert (
            relevance_score(
                "If I Ain't Got You (Kanye West Radio Mix #1)",
                ["Alicia Keys"],
                "Alicia Keys",
                "If I Ain’t Got You (Kanye West Remix)",
            )
            == 0
        )

    def test_un_titre_generique_se_juge_sur_son_disque(self):
        """« Intro (Table d'écoute) » est l'Intro de *Table d'écoute* — mais
        une « Intro » ne désigne une fiche que sur le MÊME disque : la même
        vidéo à 5 M de vues était posée sur trois Intro de Freeze Corleone."""
        from src.youtube.youtube_searcher import relevance_score

        assert (
            relevance_score(
                "Intro",
                ["Medine"],
                "Médine",
                "Intro (Table d’écoute)",
                album="Table d’écoute",
                result_album="Table d'écoute",
            )
            == 1.0
        )
        assert (
            relevance_score(
                "Intro",
                ["Freeze Corleone"],
                "Freeze Corleone",
                "Intro",
                album="Projet Blue Beam",
                result_album="RIYAD SADIO",
            )
            == 0
        )
        # Une édition du disque reste le disque.
        assert (
            relevance_score(
                "Interlude",
                ["Booba"],
                "Booba",
                "Interlude",
                album="Temps mort (Réédition)",
                result_album="Temps mort",
            )
            == 1.0
        )
        assert (
            relevance_score(
                "Outro",
                ["Népal"],
                "Népal",
                "Outro",
                album="444Nuits (Version rouge)",
                result_album="444 Nuits",
            )
            == 1.0
        )
        # Disque inconnu d'un côté : rien ne prouve que c'est la bonne Intro.
        assert relevance_score("Intro", ["Booba"], "Booba", "Intro") == 0

    def test_la_recherche_garde_artistes_et_disque(self, searcher):
        """Pour REJUGER un lien quand le score change : la chaîne ne nomme que
        le premier artiste (« Simorgh » pour un disque avec Lucio Bukowski)."""
        resultat = _resultat_ytm(titre="Intro", artiste="Freeze Corleone")
        resultat["artists"].append({"name": "Osirus Jack"})
        resultat["album"] = {"name": "Projet Blue Beam", "id": "MPRE"}
        searcher.ytmusic = _FauxYT(resultats=[resultat])
        searcher.ytmusic_available = True

        res = searcher.search_track("Freeze Corleone", "Intro", album="Projet Blue Beam")
        assert res[0]["artists"] == ["Freeze Corleone", "Osirus Jack"]
        assert res[0]["album"] == "Projet Blue Beam"
        assert res[0]["relevance_score"] == 1.0
        assert searcher.search_track("Freeze Corleone", "Intro")[0]["album"] == "Projet Blue Beam"
