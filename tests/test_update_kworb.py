"""Rapprochement Kworb ↔ base et écriture des streams (`update_kworb`).

Module à 8 % de couverture pour la fonction qui ÉCRIT les streams Spotify en
base. Ses quatre niveaux de rapprochement (ID → titre exact → flou → décision
mémorisée) vivaient en partie dans des closures, donc inatteignables autrement
qu'en bout de chaîne : elles ont été remontées au niveau module le 2026-09-03
(`_resolve_homonym`, `_fuzzy_unique`, `_best_candidate`), sans changement de
comportement.

Le principe qui traverse tout le fichier : en cas d'ambiguïté, on ABSTIENT.
Attribuer des streams au mauvais morceau est pire que ne rien écrire — et
invisible une fois en base.
"""

from datetime import datetime

import pytest

from src.models.track import Track, TrackSpotifyId
from src.utils import update_kworb as uk
from src.utils.update_kworb import (
    _best_candidate,
    _fuzzy_unique,
    _names_match,
    _resolve_homonym,
    sommer_editions,
    update_kworb_streams,
)


def _track(id_, titre, spotify_id=None, album=None, feat=False, primary=None):
    t = Track(title=titre)
    t.id = id_
    t.spotify_id = spotify_id
    t.album = album
    t.is_featuring = feat
    t.primary_artist_name = primary
    return t


def _entry(titre, streams=1000, daily=10, spotify_id=None):
    return {
        "title": titre,
        "streams": streams,
        "daily_streams": daily,
        "spotify_id": spotify_id,
    }


class _Artist:
    def __init__(self, name="Jul", spotify_id="ART1", id_=1):
        self.id = id_
        self.name = name
        self.spotify_id = spotify_id


class _DataManager:
    """Faux DataManager qui ENREGISTRE les écritures au lieu de les faire."""

    def __init__(self, tracks):
        self._tracks = tracks
        self.streams_writes = []
        self.streams_kwargs = []
        self.album_writes = []
        self.totals = None
        self.artist_spotify_id = None
        self.track_spotify_ids = []
        self.variant_writes = []
        self.variant_fiches = []

    def get_artist_tracks(self, artist_id):
        return self._tracks

    def record_spotify_streams(self, track_id, streams, source, updated_at=None, **kw):
        # L'appelant ne DÉCLARE que ce qu'il a vu : la valeur retenue en colonne
        # est arbitrée côté repository (`reconcile_spotify_streams`).
        self.streams_writes.append((track_id, streams, kw.get("daily_streams"), updated_at))
        self.streams_kwargs.append({"source": source, **kw})
        return True

    def update_track_spotify_id(self, track_id, spotify_id):
        self.track_spotify_ids.append((track_id, spotify_id))
        return True

    def update_artist_spotify_id(self, artist_id, spotify_id):
        self.artist_spotify_id = spotify_id
        return True

    def update_artist_kworb_totals(self, artist_id, **kwargs):
        self.totals = kwargs
        return True

    def upsert_album(self, artist_id, titre, streams, daily, **kwargs):
        self.album_writes.append((titre, streams, daily, kwargs))
        return True

    def record_variant_streams(
        self, track_id, spotify_id, streams, daily, seen_at, label=None, variant_track_id=None
    ):
        self.variant_writes.append((track_id, spotify_id, streams, daily, label))
        self.variant_fiches.append((track_id, spotify_id, variant_track_id))
        return True


class _Scraper:
    def __init__(self, songs=None, albums=None):
        self._songs = songs
        self._albums = albums or {"entries": [], "last_updated": None}
        self.songs_calls = []

    def scrape_songs(self, spotify_id):
        self.songs_calls.append(spotify_id)
        return self._songs(spotify_id) if callable(self._songs) else self._songs

    def scrape_albums(self, spotify_id):
        return self._albums


def _page(entries, nom="Jul", maj=None, summary=None):
    return {
        "artist_name": nom,
        "entries": entries,
        "last_updated": maj or datetime(2026, 9, 1),
        "summary": summary,
    }


@pytest.fixture(autouse=True)
def _memoire_isolee(tmp_path, monkeypatch):
    """Neutralise la mémoire des décisions (elle vit dans data/kworb_links)."""

    class _Vide:
        def load(self, artist_name):
            return {"confirmed": {}, "rejected": []}

    monkeypatch.setattr("src.utils.kworb_links_manager.KworbLinksManager", _Vide)
    return _Vide


# ─────────────────────────────────────────────────────── briques de décision


class TestNomsCorrespondent:
    @pytest.mark.parametrize(
        ("page", "artiste"),
        [("Jul", "Jul"), ("JUL", "jul"), ("Jul", "Jul & SCH"), ("Angele", "Angèle")],
    )
    def test_correspondances(self, page, artiste):
        assert _names_match(page, artiste) is True

    @pytest.mark.parametrize(
        ("page", "artiste"),
        [("Limsa d'Aulnay", "Isha"), (None, "Jul"), ("", "Jul"), ("Jul", "")],
    )
    def test_non_correspondances(self, page, artiste):
        assert _names_match(page, artiste) is False

    def test_coquille_toleree(self):
        """Seuil difflib à 0,8 : une lettre de travers ne casse pas l'identité."""
        assert _names_match("Nekfeu", "Nekfeuu") is True

    @pytest.mark.parametrize(
        "page",
        [
            "Coline Schneider",
            "Sébastien Tedeschi",
            "Vicky Schoukroun",
            "Salomé Fleischmann",
            "ScHoolboy Q",
        ],
    )
    def test_un_inconnu_dont_le_nom_contient_le_notre_est_refuse(self, page):
        """CORRIGÉ le 2026-09-04. « sch » est une sous-chaîne de tous ces noms
        sans y être un mot. C'est le garde-fou d'identité du passage Kworb : un
        faux positif fait écrire le catalogue de streams d'un INCONNU sur notre
        artiste. Mesuré sur les 2 515 noms réellement croisés en base : 18 pages
        acceptées à tort avant, 4 après — toutes légitimes."""
        assert _names_match(page, "SCH") is False

    def test_misha_nest_pas_isha(self):
        assert _names_match("Misha Van Der Werf", "Isha") is False

    @pytest.mark.parametrize(("page", "artiste"), [("Isha (7)", "Isha"), ("Sch (5)", "SCH")])
    def test_suffixe_de_desambiguisation_genius_conserve(self, page, artiste):
        """Ce que le correctif devait épargner : « Isha (7) » reste notre Isha."""
        assert _names_match(page, artiste) is True


class TestHomonymes:
    """Deux morceaux au même titre : c'est l'artiste crédité qui départage."""

    def test_un_seul_candidat_compatible(self):
        souffrance = _track(1, "MEILLEUR", feat=True, primary="Souffrance")
        goldee = _track(2, "Meilleur", feat=True, primary="Goldee Money")
        gagnant = _resolve_homonym([souffrance, goldee], "Jul", {"souffrance"})
        assert gagnant is souffrance

    def test_artiste_principal_par_defaut(self):
        """Un morceau NON-feat appartient à l'artiste courant."""
        propre = _track(1, "Titre")
        autre = _track(2, "Titre", feat=True, primary="Quelqu'un")
        assert _resolve_homonym([propre, autre], "Jul", {"jul"}) is propre

    def test_aucun_credit_exploitable(self):
        """Page embed muette → on ne devine pas."""
        assert _resolve_homonym([_track(1, "T"), _track(2, "T")], "Jul", set()) is None

    def test_aucun_candidat_compatible(self):
        cands = [
            _track(1, "T", feat=True, primary="Alpha Wann"),
            _track(2, "T", feat=True, primary="Bekar"),
        ]
        assert _resolve_homonym(cands, "Jul", {"dinos"}) is None

    def test_deux_candidats_compatibles_abstention(self):
        """Ambiguïté non levée : ne rien écrire plutôt qu'écrire au hasard."""
        cands = [_track(1, "T"), _track(2, "T")]
        assert _resolve_homonym(cands, "Jul", {"jul"}) is None

    def test_correspondance_par_inclusion(self):
        """« Jul » doit matcher un crédit « Jul & SCH »."""
        cible = _track(1, "T", feat=True, primary="Jul")
        autre = _track(2, "T", feat=True, primary="Ninho")
        assert _resolve_homonym([cible, autre], "X", {"jul and sch"}) is cible

    def test_sous_chaine_nue_ne_departage_plus(self):
        """CORRIGÉ le 2026-09-04 : l'inclusion ne compte qu'en MOTS ENTIERS.
        « IAM » est contenu dans « WILLIAMS » mais n'y est pas un mot — le crédit
        d'un homonyme faisait donc élire le mauvais morceau, et lui attribuait
        les streams. Abstention désormais, ce qui est le comportement voulu :
        ne rien écrire vaut mieux qu'écrire au hasard."""
        iam = _track(1, "T", feat=True, primary="IAM")
        autre = _track(2, "T", feat=True, primary="Ninho")
        assert _resolve_homonym([iam, autre], "X", {"williams"}) is None

    def test_le_vrai_iam_est_toujours_reconnu(self):
        iam = _track(1, "T", feat=True, primary="IAM")
        autre = _track(2, "T", feat=True, primary="Ninho")
        assert _resolve_homonym([iam, autre], "X", {"iam akhenaton"}) is iam


class TestRapprochementFlou:
    def test_coquille_rapprochee(self):
        tracks = [_track(1, "Rythm"), _track(2, "Complètement autre chose")]
        trouve, score = _fuzzy_unique("Rhythm", tracks)
        assert trouve.id == 1
        assert score >= 0.87

    def test_aucun_candidat(self):
        assert _fuzzy_unique("Inconnu", [_track(1, "Rien à voir")]) == (None, 0.0)

    def test_deux_candidats_abstention(self):
        """Garde-fou anti-fusion : deux variantes proches → on ne choisit pas."""
        tracks = [_track(1, "My Love"), _track(2, "My Lovee")]
        assert _fuzzy_unique("My Lovee", tracks) == (None, 0.0)

    def test_descripteurs_de_version_non_strippes(self):
        """Le studio et l'acoustique sont deux morceaux : les fusionner
        additionnerait leurs streams sur un seul."""
        tracks = [_track(1, "Titre"), _track(2, "Titre - Acoustic")]
        trouve, _ = _fuzzy_unique("Titre", tracks)
        assert trouve is None or trouve.id == 1

    def test_liste_vide(self):
        assert _fuzzy_unique("Titre", []) == (None, 0.0)

    def test_seuil_ajustable(self):
        tracks = [_track(1, "Quelque chose de different")]
        assert _fuzzy_unique("Quelque chose", tracks) == (None, 0.0)
        trouve, _ = _fuzzy_unique("Quelque chose", tracks, threshold=0.5)
        assert trouve.id == 1


class TestSuggestions:
    def test_candidat_dans_la_bande_incertaine(self):
        tracks = [_track(1, "Matrix (Intro)"), _track(2, "Rien de comparable ici")]
        cand, score = _best_candidate("Matrix", tracks)
        assert cand.id == 1
        assert 0.55 <= score < 0.87

    def test_trop_ressemblant_laisse_au_flou(self):
        """≥ 0,87 : c'est le niveau 3 (écriture directe) qui s'en occupe, pas
        une suggestion — sinon on demanderait confirmation pour rien."""
        assert _best_candidate("Matrix", [_track(1, "Matrix")]) == (None, 0.0)

    def test_trop_different_ignore(self):
        assert _best_candidate("Matrix", [_track(1, "Absolument rien à voir")]) == (None, 0.0)

    def test_deux_candidats_serres_abstention(self):
        """Écart < 0,08 avec le deuxième : proposer l'un plutôt que l'autre
        serait arbitraire."""
        tracks = [_track(1, "Matrix Intro"), _track(2, "Matrix Outro")]
        assert _best_candidate("Matrix", tracks) == (None, 0.0)

    def test_liste_vide(self):
        assert _best_candidate("Matrix", []) == (None, 0.0)


# ─────────────────────────────────────────────────────── chaîne complète


class TestNiveauxDeRapprochement:
    def _run(self, tracks, entries, **kw):
        dm = _DataManager(tracks)
        scraper = _Scraper(songs=_page(entries))
        res = update_kworb_streams(_Artist(), dm, scraper=scraper, **kw)
        return res, dm

    def test_niveau_1_par_spotify_id(self):
        """L'ID prime sur le titre : c'est la seule clé sans ambiguïté."""
        t = _track(1, "Titre en base", spotify_id="SP1")
        res, dm = self._run([t], [_entry("Titre Kworb Différent", 5000, 50, "SP1")])
        assert res["matched_by_id"] == 1
        assert dm.streams_writes == [(1, 5000, 50, datetime(2026, 9, 1))]

    def test_niveau_2_par_titre_unique(self):
        t = _track(1, "Bande organisée")
        res, dm = self._run([t], [_entry("bande organisee", 3000)])
        assert res["matched_by_title"] == 1
        assert dm.streams_writes[0][1] == 3000

    def test_niveau_2_titre_ambigu_sans_id_passe(self):
        """Homonymes sans spotify_id sur la ligne Kworb : rien à départager."""
        tracks = [_track(1, "Meilleur"), _track(2, "MEILLEUR")]
        res, dm = self._run(tracks, [_entry("Meilleur")])
        assert res["unmatched"] == 1
        assert dm.streams_writes == []

    def test_niveau_3_flou(self):
        t = _track(1, "Rythm")
        res, _ = self._run([t], [_entry("Rhythm")])
        assert res["matched_by_fuzzy"] == 1
        assert res["fuzzy_matched"][0][0] == "Rhythm"

    def test_niveau_4_suggestion_non_ecrite(self):
        """Une suggestion attend l'utilisateur : ni écrite, ni comptée
        « non matchée » (elle n'est pas un échec, juste en attente)."""
        t = _track(1, "Matrix (Intro)")
        res, dm = self._run([t], [_entry("Matrix", 900, 9)])
        assert res["unmatched"] == 0
        assert dm.streams_writes == []
        assert res["suggestions"] == [
            {
                "kworb_title": "Matrix",
                "streams": 900,
                "daily": 9,
                "track_id": 1,
                "db_title": "Matrix (Intro)",
                "score": pytest.approx(res["suggestions"][0]["score"]),
            }
        ]

    def test_aucun_rapprochement(self):
        res, dm = self._run([_track(1, "Un titre")], [_entry("Absolument rien à voir")])
        assert res["unmatched"] == 1
        assert res["unmatched_titles"] == ["Absolument rien à voir"]
        assert dm.streams_writes == []

    def test_non_matches_tries_par_streams(self):
        """La GUI affiche les plus gros écarts d'abord : c'est là qu'est l'enjeu."""
        res, _ = self._run(
            [_track(1, "Un titre")],
            [_entry("Zzz inconnu un", 100), _entry("Zzz inconnu deux", 9000)],
        )
        assert [s for _, s in res["unmatched_details"]] == [9000, 100]


class TestDecisionsMemorisees:
    def _run_avec_memoire(self, monkeypatch, memoire, tracks, entries):
        class _Memoire:
            def load(self, artist_name):
                return memoire

        monkeypatch.setattr("src.utils.kworb_links_manager.KworbLinksManager", _Memoire)
        dm = _DataManager(tracks)
        res = update_kworb_streams(_Artist(), dm, scraper=_Scraper(songs=_page(entries)))
        return res, dm

    def test_confirmation_appliquee_sans_redemander(self, monkeypatch):
        memoire = {"confirmed": {"matrix": 1}, "rejected": []}
        res, dm = self._run_avec_memoire(
            monkeypatch, memoire, [_track(1, "Matrix (Intro)")], [_entry("Matrix", 900)]
        )
        assert res["suggestions"] == []
        assert dm.streams_writes == [(1, 900, 10, datetime(2026, 9, 1))]

    def test_rejet_reste_silencieux(self, monkeypatch):
        """Rejeté une fois = plus jamais proposé, et pas signalé non plus."""
        memoire = {"confirmed": {}, "rejected": ["matrix"]}
        res, dm = self._run_avec_memoire(
            monkeypatch, memoire, [_track(1, "Matrix (Intro)")], [_entry("Matrix")]
        )
        assert res["suggestions"] == []
        assert res["unmatched"] == 1
        assert dm.streams_writes == []

    def test_confirmation_sur_un_morceau_disparu(self, monkeypatch):
        """Le morceau confirmé a été supprimé depuis : on ne plante pas."""
        memoire = {"confirmed": {"matrix": 999}, "rejected": []}
        res, dm = self._run_avec_memoire(
            monkeypatch, memoire, [_track(1, "Autre chose entièrement")], [_entry("Matrix")]
        )
        assert dm.streams_writes == []
        assert res["unmatched"] == 1

    def test_memoire_illisible(self, monkeypatch):
        class _Casse:
            def load(self, artist_name):
                raise OSError("fichier illisible")

        monkeypatch.setattr("src.utils.kworb_links_manager.KworbLinksManager", _Casse)
        dm = _DataManager([_track(1, "Bande organisée")])
        res = update_kworb_streams(
            _Artist(), dm, scraper=_Scraper(songs=_page([_entry("bande organisee")]))
        )
        assert res["matched"] == 1  # le rapprochement direct fonctionne quand même


class TestAgregationEtBackfill:
    def test_plusieurs_lignes_kworb_sommees(self):
        """Un morceau sorti en single puis en album a deux lignes Kworb : leurs
        streams s'ADDITIONNENT, ils ne s'écrasent pas."""
        dm = _DataManager([_track(1, "Titre")])
        res = update_kworb_streams(
            _Artist(),
            dm,
            scraper=_Scraper(songs=_page([_entry("Titre", 1000, 10), _entry("Titre", 500, 5)])),
        )
        assert dm.streams_writes == [(1, 1500, 15, datetime(2026, 9, 1))]
        assert res["matched"] == 2  # deux lignes matchées, une seule écriture

    def test_backfill_de_l_id_spotify(self):
        """Kworb donne l'URL du track : on en profite pour compléter la base."""
        dm = _DataManager([_track(1, "Titre")])
        res = update_kworb_streams(
            _Artist(), dm, scraper=_Scraper(songs=_page([_entry("Titre", spotify_id="SP9")]))
        )
        assert dm.track_spotify_ids == [(1, "SP9")]
        assert res["spotify_ids_backfilled"] == 1

    def test_id_existant_jamais_ecrase(self):
        dm = _DataManager([_track(1, "Titre", spotify_id="DEJA")])
        update_kworb_streams(
            _Artist(), dm, scraper=_Scraper(songs=_page([_entry("Titre", spotify_id="SP9")]))
        )
        assert dm.track_spotify_ids == []

    def test_totaux_artiste(self):
        dm = _DataManager([])
        summary = {
            "streams": {"total": 1_000_000, "as_lead": 800_000, "as_feature": 200_000},
            "daily": {"total": 5000},
        }
        update_kworb_streams(
            _Artist(),
            dm,
            scraper=_Scraper(songs=_page([_entry("Peu importe")], summary=summary)),
        )
        assert dm.totals["total"] == 1_000_000
        assert dm.totals["lead"] == 800_000
        assert dm.totals["feat"] == 200_000

    def test_pas_de_totaux_sans_recap(self):
        dm = _DataManager([])
        update_kworb_streams(_Artist(), dm, scraper=_Scraper(songs=_page([_entry("Peu importe")])))
        assert dm.totals is None

    def test_fraicheur_de_la_page_pas_maintenant(self):
        """La date vient du « Last updated » de Kworb : dater de now() ferait
        croire à une donnée fraîche alors que la page ne l'est peut-être pas."""
        dm = _DataManager([_track(1, "Titre")])
        page = _page([_entry("Titre")], maj=datetime(2020, 1, 15))
        update_kworb_streams(_Artist(), dm, scraper=_Scraper(songs=page))
        assert dm.streams_writes[0][3] == datetime(2020, 1, 15)


class TestAlbums:
    def _run(self, tracks, album_entries, song_entries=None):
        dm = _DataManager(tracks)
        scraper = _Scraper(
            # Au moins une entrée songs : une page vide est refusée par la
            # validation d'identité (cf. TestValidationDIdentite).
            songs=_page(song_entries or [_entry("Morceau quelconque")]),
            albums={"entries": album_entries, "last_updated": datetime(2026, 9, 1)},
        )
        return update_kworb_streams(_Artist(), dm, scraper=scraper), dm

    def test_album_propre_ecrit(self):
        tracks = [_track(1, "A", album="Mon Album"), _track(2, "B", album="Mon Album")]
        res, dm = self._run(
            tracks,
            [_entry("Mon Album", 50000, 500)],
            song_entries=[_entry("A", 30000, 300), _entry("B", 20000, 200)],
        )
        assert res["albums_updated"] == 1
        assert dm.album_writes[0][:3] == ("Mon Album", 50000, 500)

    def test_simple_apparition_ecartee(self):
        """Un seul morceau à nous sur l'album : ses streams sont déjà comptés au
        niveau du morceau, les compter en album ferait un doublon."""
        tracks = [_track(1, "A", album="Album d'un autre")]
        res, dm = self._run(tracks, [_entry("Album d'un autre", 50000)])
        assert res["albums_updated"] == 0
        assert res["albums_excluded"] == ["Album d'un autre"]
        assert dm.album_writes == []

    def test_projet_commun_conserve(self):
        """≥ 2 morceaux en base : c'est un vrai projet (type Bitume Caviar)."""
        tracks = [_track(1, "A", album="Bitume Caviar"), _track(2, "B", album="Bitume Caviar")]
        res, _ = self._run(
            tracks,
            [_entry("Bitume Caviar", 10)],
            song_entries=[_entry("A", 7, 1), _entry("B", 3, 1)],
        )
        assert res["albums_updated"] == 1

    def test_le_total_vient_des_MORCEAUX_pas_des_lignes_d_album(self):
        """CORRIGÉ le 2026-09-05. Kworb liste une ligne par édition, mais ses
        compteurs sont ceux de Spotify, **cumulés par ENREGISTREMENT** : une
        réédition ne repart pas de zéro, donc les deux lignes d'un album réédité
        portent les mêmes titres. Les sommer comptait deux fois les titres
        partagés — 99 206 484 sur « Bitume Caviar (vol.1) » au lieu de 50 342 979,
        la ligne de l'édition originale étant INTÉGRALEMENT constituée des titres
        que la réédition reprend.

        Le total se calcule donc sur les MORCEAUX, où chaque enregistrement
        n'apparaît qu'une fois. Les lignes d'album (ici 30 000 + 30 000 = 60 000)
        ne servent plus qu'à nommer le disque et collecter ses IDs d'édition."""
        tracks = [_track(1, "A", album="Mon Album"), _track(2, "B", album="Mon Album")]
        res, dm = self._run(
            tracks,
            [_entry("Mon Album", 30000, 300, "AL1"), _entry("mon album", 30000, 300, "AL2")],
            song_entries=[_entry("A", 30000, 300), _entry("B", 20000, 200)],
        )
        assert res["albums_updated"] == 1
        titre, streams, daily, kwargs = dm.album_writes[0]
        assert (streams, daily) == (50000, 500), "somme des MORCEAUX, pas des lignes d'album"
        assert kwargs["spotify_album_ids"] == "AL1,AL2", "les deux éditions restent tracées"

    def test_une_edition_ecartee_legue_son_identifiant(self):
        """« … (Bonus) » n'a aucun morceau propre en base : Kworb l'écartait, et
        son IDENTIFIANT d'édition se perdait avec elle. Le scrape Spotify ne
        pouvait alors plus atteindre cette édition pour totaliser le disque —
        constaté sur « DOM PERIGNON CRYING (Bonus) », dont l'album ressortait
        ~10 % sous son vrai total, sans que rien ne le signale."""
        tracks = [_track(1, "A", album="Mon Album"), _track(2, "B", album="Mon Album")]
        res, dm = self._run(
            tracks,
            [_entry("Mon Album", 100, 1, "AL1"), _entry("Mon Album (Bonus)", 50, 1, "AL2")],
            song_entries=[_entry("A", 30000, 300), _entry("B", 20000, 200)],
        )
        assert res["albums_updated"] == 1
        assert res["albums_excluded"] == [], "l'édition n'est plus un album à part"
        titre, streams, _, kwargs = dm.album_writes[0]
        assert titre == "Mon Album", "le titre de BASE, pas celui de l'édition"
        assert kwargs["spotify_album_ids"] == "AL1,AL2"
        assert streams == 50000, "toujours la somme des MORCEAUX, pas des lignes"

    def test_une_SUITE_reste_un_album_distinct(self):
        """Le pendant : « Mon Album 2 » n'est pas une édition de « Mon Album »."""
        tracks = [_track(1, "A", album="Mon Album"), _track(2, "B", album="Mon Album")]
        res, dm = self._run(
            tracks,
            [_entry("Mon Album", 100, 1, "AL1"), _entry("Mon Album 2", 50, 1, "AL2")],
            song_entries=[_entry("A", 30000, 300), _entry("B", 20000, 200)],
        )
        assert res["albums_updated"] == 1
        assert dm.album_writes[0][3]["spotify_album_ids"] == "AL1"

    def test_album_sans_morceau_chiffre_n_est_pas_remis_a_zero(self):
        """Aucun morceau de l'album n'a de compteur ce run : écrire 0 effacerait
        un total valide par une valeur qui n'en est pas une."""
        tracks = [_track(1, "A", album="Mon Album"), _track(2, "B", album="Mon Album")]
        res, dm = self._run(tracks, [_entry("Mon Album", 50000, 500)])
        assert res["albums_updated"] == 0
        assert dm.album_writes == []

    def test_aucun_album(self):
        res, dm = self._run([_track(1, "A")], [])
        assert res["albums_updated"] == 0
        assert dm.album_writes == []


class TestValidationDIdentite:
    def test_page_d_un_homonyme_refusee(self, monkeypatch):
        """Bug historique : l'ID d'Isha pointait vers Limsa d'Aulnay. Une page
        au mauvais nom ne doit RIEN écrire."""
        monkeypatch.setattr(uk, "_vote_artist_spotify_id", lambda *a, **k: None)
        dm = _DataManager([_track(1, "Titre")])
        scraper = _Scraper(songs=_page([_entry("Titre")], nom="Limsa d'Aulnay"))
        res = update_kworb_streams(_Artist(name="Isha"), dm, scraper=scraper)
        assert res["matched"] == 0
        assert dm.streams_writes == []
        assert res["artist_name"] is None

    def test_revote_corrige_l_identite(self, monkeypatch):
        monkeypatch.setattr(uk, "_vote_artist_spotify_id", lambda *a, **k: "BONID")
        pages = {
            "ART1": _page([_entry("Titre")], nom="Limsa d'Aulnay"),
            "BONID": _page([_entry("Titre", 700)], nom="Isha"),
        }
        dm = _DataManager([_track(1, "Titre")])
        scraper = _Scraper(songs=lambda sid: pages.get(sid))
        res = update_kworb_streams(_Artist(name="Isha"), dm, scraper=scraper)
        assert res["matched"] == 1
        assert dm.artist_spotify_id == "BONID"
        assert dm.streams_writes[0][1] == 700

    def test_revote_infructueux_abandonne(self, monkeypatch):
        """Deuxième page toujours au mauvais nom : abandon, aucune écriture."""
        monkeypatch.setattr(uk, "_vote_artist_spotify_id", lambda *a, **k: "AUTRE")
        pages = {
            "ART1": _page([_entry("T")], nom="Limsa d'Aulnay"),
            "AUTRE": _page([_entry("T")], nom="Encore quelqu'un d'autre"),
        }
        dm = _DataManager([_track(1, "T")])
        res = update_kworb_streams(
            _Artist(name="Isha"), dm, scraper=_Scraper(songs=lambda sid: pages.get(sid))
        )
        assert res["matched"] == 0
        assert dm.streams_writes == []

    def test_page_sans_aucun_morceau_refusee(self, monkeypatch):
        """Une page qui ne liste RIEN est traitée comme non validée, au même
        titre qu'une page au mauvais nom : Kworb sert parfois une page vide pour
        un ID erroné, et l'accepter écrirait des totaux sans les morceaux."""
        monkeypatch.setattr(uk, "_vote_artist_spotify_id", lambda *a, **k: None)
        dm = _DataManager([_track(1, "T")])
        res = update_kworb_streams(_Artist(), dm, scraper=_Scraper(songs=_page([])))
        assert res["artist_name"] is None
        assert dm.totals is None

    def test_page_absente(self, monkeypatch):
        monkeypatch.setattr(uk, "_vote_artist_spotify_id", lambda *a, **k: None)
        dm = _DataManager([_track(1, "T")])
        res = update_kworb_streams(_Artist(), dm, scraper=_Scraper(songs=None))
        assert res["matched"] == 0

    def test_sans_id_artiste_le_vote_est_tente(self, monkeypatch):
        monkeypatch.setattr(uk, "_vote_artist_spotify_id", lambda *a, **k: "VOTE1")
        dm = _DataManager([_track(1, "Titre")])
        scraper = _Scraper(songs=_page([_entry("Titre")]))
        res = update_kworb_streams(_Artist(spotify_id=None), dm, scraper=scraper)
        assert dm.artist_spotify_id == "VOTE1"
        assert scraper.songs_calls == ["VOTE1"]
        assert res["matched"] == 1

    def test_sans_id_artiste_et_vote_infructueux(self, monkeypatch):
        """Aucune identité fiable : on s'arrête avant même de scraper."""
        monkeypatch.setattr(uk, "_vote_artist_spotify_id", lambda *a, **k: None)
        dm = _DataManager([_track(1, "T")])
        scraper = _Scraper(songs=_page([_entry("T")]))
        res = update_kworb_streams(_Artist(spotify_id=None), dm, scraper=scraper)
        assert res["matched"] == 0
        assert scraper.songs_calls == []


class _EmbedScraper:
    """Faux scraper de pages embed Spotify (contexte + méthodes utilisées)."""

    instances = []

    def __init__(self, headless=True):
        self.closed = False
        self.votes = {}
        self.track_artists = {}
        self.by_name = None
        _EmbedScraper.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.closed = True
        return False

    def close(self):
        self.closed = True

    def get_artist_id_from_track(self, sid, expected_name=None):
        return self.votes.get(sid)

    def get_artist_spotify_id(self, name):
        return self.by_name

    def get_track_artists(self, spotify_id):
        return self.track_artists.get(spotify_id, [])


@pytest.fixture
def embed(monkeypatch):
    """Installe le faux scraper embed et rend une fabrique de configuration."""
    _EmbedScraper.instances = []
    config = {}

    def _fabrique(headless=True):
        s = _EmbedScraper(headless)
        s.votes = config.get("votes", {})
        s.by_name = config.get("by_name")
        s.track_artists = config.get("track_artists", {})
        return s

    monkeypatch.setattr("src.scrapers.spotify_id_scraper_v2.SpotifyIDScraper", _fabrique)
    return config


class TestVoteIdArtiste:
    """Garde-fou anti-Limsa : l'ID artiste est élu sur les NON-FEATS.

    Bug historique (JOURNAL 2026-07-02) : l'ID d'Isha pointait vers Limsa
    d'Aulnay parce que le vote était dominé par les pages de ses featurings.
    """

    def test_majorite_l_emporte(self, embed):
        embed["votes"] = {"S1": "BON", "S2": "BON", "S3": "MAUVAIS"}
        tracks = [_track(i, f"T{i}", spotify_id=f"S{i}") for i in (1, 2, 3)]
        assert uk._vote_artist_spotify_id(_Artist(), _DataManager(tracks)) == "BON"

    def test_une_seule_voix_suffit(self, embed):
        """Un seul morceau exploitable : sa page fait foi, faute de mieux."""
        embed["votes"] = {"S1": "SEUL"}
        tracks = [_track(1, "T1", spotify_id="S1")]
        assert uk._vote_artist_spotify_id(_Artist(), _DataManager(tracks)) == "SEUL"

    def test_vote_non_concluant_repli_sur_le_nom(self, embed):
        """Deux voix qui se valent : aucune majorité, on retombe sur la
        recherche par nom (ambiguë mais explicite)."""
        embed["votes"] = {"S1": "A", "S2": "B"}
        embed["by_name"] = "PAR_NOM"
        tracks = [_track(i, f"T{i}", spotify_id=f"S{i}") for i in (1, 2)]
        assert uk._vote_artist_spotify_id(_Artist(), _DataManager(tracks)) == "PAR_NOM"

    def test_featurings_exclus_du_vote(self, embed):
        """LE garde-fou : un feat vote pour l'artiste principal du morceau."""
        embed["votes"] = {"S1": "LIMSA", "S2": "ISHA"}
        embed["by_name"] = None
        tracks = [
            _track(1, "Feat", spotify_id="S1", feat=True, primary="Limsa d'Aulnay"),
            _track(2, "Propre", spotify_id="S2"),
        ]
        assert uk._vote_artist_spotify_id(_Artist(name="Isha"), _DataManager(tracks)) == "ISHA"

    def test_morceaux_sans_id_ignores(self, embed):
        embed["votes"] = {"S2": "BON"}
        tracks = [_track(1, "Sans id"), _track(2, "Avec id", spotify_id="S2")]
        assert uk._vote_artist_spotify_id(_Artist(), _DataManager(tracks)) == "BON"

    def test_nombre_de_pages_plafonne(self, embed):
        """Chaque page est un aller-retour réseau : on n'en ouvre pas 300."""
        embed["votes"] = {f"S{i}": "BON" for i in range(10)}
        embed["by_name"] = None
        tracks = [_track(i, f"T{i}", spotify_id=f"S{i}") for i in range(10)]
        uk._vote_artist_spotify_id(_Artist(), _DataManager(tracks), max_pages=2)
        # 2 pages consultées → 2 voix → majorité atteinte sans ouvrir les 8 autres
        assert _EmbedScraper.instances[0].closed is True

    def test_aucune_voix_repli_sur_le_nom(self, embed):
        embed["votes"] = {}
        embed["by_name"] = "PAR_NOM"
        tracks = [_track(1, "T", spotify_id="S1")]
        assert uk._vote_artist_spotify_id(_Artist(), _DataManager(tracks)) == "PAR_NOM"

    def test_base_indisponible(self, embed):
        """Une erreur SQL ne doit pas remonter : on vote sur zéro morceau."""
        from sqlalchemy.exc import SQLAlchemyError

        class _DMCasse(_DataManager):
            def get_artist_tracks(self, artist_id):
                raise SQLAlchemyError("base verrouillée")

        embed["by_name"] = "PAR_NOM"
        assert uk._vote_artist_spotify_id(_Artist(), _DMCasse([])) == "PAR_NOM"

    def test_scraper_en_erreur(self, monkeypatch):
        from playwright.sync_api import Error as PlaywrightError

        def _boom(headless=True):
            raise PlaywrightError("navigateur mort")

        monkeypatch.setattr("src.scrapers.spotify_id_scraper_v2.SpotifyIDScraper", _boom)
        assert uk._vote_artist_spotify_id(_Artist(), _DataManager([])) is None


class TestDesambiguisationParEmbed:
    """Deux morceaux au même titre, départagés par les artistes crédités de
    l'embed — lus par le lecteur d'identité INJECTÉ (`lire_identite`), le même
    que le gate et l'audit : plus de navigateur ouvert dans ce module."""

    def _run(self, identites, tracks, entries):
        dm = _DataManager(tracks)
        res = update_kworb_streams(
            _Artist(),
            dm,
            scraper=_Scraper(songs=_page(entries)),
            lire_identite=lambda sid: identites.get(sid),
        )
        return res, dm

    def test_homonyme_resolu(self):
        identites = {"SPX": {"name": "Meilleur", "artists": ["Souffrance"], "duration": 200}}
        tracks = [
            _track(1, "Meilleur", feat=True, primary="Souffrance"),
            _track(2, "Meilleur", feat=True, primary="Goldee Money"),
        ]
        res, dm = self._run(identites, tracks, [_entry("Meilleur", 4000, 40, "SPX")])
        assert res["matched_by_title"] == 1
        assert dm.streams_writes == [(1, 4000, 40, datetime(2026, 9, 1))]

    def test_embed_muet_abstention(self):
        identites = {"SPX": {"name": "Meilleur", "artists": [], "duration": None}}
        tracks = [_track(1, "Meilleur"), _track(2, "MEILLEUR")]
        res, dm = self._run(identites, tracks, [_entry("Meilleur", spotify_id="SPX")])
        assert res["unmatched"] == 1
        assert dm.streams_writes == []

    def test_embed_illisible_abstention(self):
        """Un lecteur qui rend None (page illisible) ne tranche pas."""
        tracks = [_track(1, "Meilleur"), _track(2, "MEILLEUR")]
        res, dm = self._run({}, tracks, [_entry("Meilleur", spotify_id="SPX")])
        assert res["unmatched"] == 1
        assert dm.streams_writes == []


class TestKworbDeclareSansArbitrer:
    """Kworb ne decide pas de ce qui atterrit en colonne.

    Il nomme sa source et livre ce qu'il a vu ; l'arbitrage entre sources vit
    dans `reconcile_spotify_streams`. C'est ce qui rend l'ordre des passes sans
    effet sur la donnee.
    """

    def test_kworb_nomme_sa_source(self):
        dm = _DataManager([_track(1, "Titre")])
        update_kworb_streams(_Artist(), dm, scraper=_Scraper(songs=_page([_entry("Titre")])))
        assert dm.streams_kwargs[0]["source"] == "kworb"

    def test_kworb_transmet_le_quotidien_dont_il_est_seule_source(self):
        """Spotify n'en publie aucun : c'est la seule donnee que Kworb garde
        pour lui, et elle ne doit pas se perdre dans le passage a l'arbitrage."""
        dm = _DataManager([_track(1, "Titre")])
        update_kworb_streams(
            _Artist(), dm, scraper=_Scraper(songs=_page([_entry("Titre", daily=42)]))
        )
        assert dm.streams_kwargs[0]["daily_streams"] == 42


# ─────────────────────────────────────── 2026-09-21 : variantes, remix, sommation


def _memoire(monkeypatch, **data):
    data.setdefault("confirmed", {})
    data.setdefault("rejected", [])
    data.setdefault("decisions", {})

    class _Memoire:
        def load(self, artist_name):
            return data

    monkeypatch.setattr("src.utils.kworb_links_manager.KworbLinksManager", _Memoire)


def _run(tracks, entries, identites=None, **kw):
    dm = _DataManager(tracks)
    res = update_kworb_streams(
        _Artist(),
        dm,
        scraper=_Scraper(songs=_page(entries)),
        lire_identite=lambda sid: (identites or {}).get(sid),
        **kw,
    )
    return res, dm


def _identite(nom, artistes, duree):
    return {"name": nom, "artists": artistes, "duration": duree}


class TestSommerEditions:
    def test_uploads_distincts_sommes(self):
        """Runaway : 1,26 Md + 35 M sont deux uploads, toutes leurs écoutes comptent."""
        r = sommer_editions(
            [{"streams": 1_262_752_224, "daily": 100}, {"streams": 35_465_477, "daily": 5}]
        )
        assert (r["streams"], r["daily"]) == (1_298_217_701, 105)
        assert r["ecartees"] == []

    def test_doublon_pur_compte_une_fois(self):
        """« La zone » : 19 422 364 / 19 411 834, le même compteur relevé deux
        jours différents — additionner doublait le morceau (38,8 M chez Booba)."""
        r = sommer_editions(
            [{"streams": 19_422_364, "daily": 10}, {"streams": 19_411_834, "daily": 10}]
        )
        assert (r["streams"], r["daily"]) == (19_422_364, 10)
        assert len(r["ecartees"]) == 1

    def test_vide(self):
        assert sommer_editions([])["streams"] == 0


class TestIdsPartages:
    def test_un_id_sur_deux_lignes_ne_recoit_rien(self):
        """« OUTSIDE » (Jackboys 2) et « outside » (Birds in the Trap) portent le
        même ID : le dict d'avant en désignait UNE au hasard."""
        tracks = [_track(1, "OUTSIDE", spotify_id="SPX"), _track(2, "outside", spotify_id="SPX")]
        res, dm = _run(tracks, [_entry("outside", 108_000_000, 9, "SPX")])
        assert dm.streams_writes == []
        assert res["ids_partages"] == [("SPX", ["OUTSIDE", "outside"])]
        assert res["unmatched"] == 0  # signalé à part, pas « non matché »

    def test_les_ids_de_la_table_comptent_aussi(self):
        t1 = _track(1, "A", spotify_id="SP1")
        t1.spotify_id_entries = [TrackSpotifyId(spotify_id="SP2", source="kworb")]
        t2 = _track(2, "B", spotify_id="SP2")
        res, dm = _run([t1, t2], [_entry("B", 500, 5, "SP2")])
        assert dm.streams_writes == []
        assert [sid for sid, _ in res["ids_partages"]] == ["SP2"]


class TestRenditions:
    def test_bonus_track_rattache_au_socle(self):
        """« DKR - Bonus Track » (108 M) : rendition de « DKR », rattachée
        automatiquement — hors colonne, hors total."""
        res, dm = _run(
            [_track(1, "DKR", spotify_id="SP1")], [_entry("DKR - Bonus Track", 108, 1, "SPB")]
        )
        assert dm.streams_writes == []
        assert dm.variant_writes == [(1, "SPB", 108, 1, "DKR - Bonus Track")]
        assert res["renditions_rattachees"] == [("DKR - Bonus Track", "DKR", 108)]
        assert res["unmatched"] == 0

    def test_un_id_connu_comme_rendition_va_sur_la_variante(self):
        t = _track(1, "DKR", spotify_id="SP1")
        t.spotify_id_entries = [
            TrackSpotifyId(spotify_id="SP1", source="genius_media"),
            TrackSpotifyId(spotify_id="SPB", source="kworb", kind="rendition"),
        ]
        res, dm = _run(
            [t], [_entry("DKR", 1000, 10, "SP1"), _entry("DKR - Bonus Track", 108, 1, "SPB")]
        )
        assert dm.streams_writes == [(1, 1000, 10, datetime(2026, 9, 1))]
        assert dm.variant_writes == [(1, "SPB", 108, 1, "DKR - Bonus Track")]

    def test_une_variante_devenue_morceau_reprend_son_id(self):
        """La rendition a depuis sa propre ligne en base : l'ID est à elle."""
        parent = _track(1, "DKR", spotify_id="SP1")
        parent.spotify_id_entries = [
            TrackSpotifyId(spotify_id="SPB", source="kworb", kind="rendition")
        ]
        propre = _track(2, "DKR (Bonus Track)")
        res, dm = _run([parent, propre], [_entry("DKR - Bonus Track", 108, 1, "SPB")])
        assert dm.variant_writes == []
        assert dm.streams_writes == [(2, 108, 1, datetime(2026, 9, 1))]

    def test_une_fiche_de_la_version_prend_la_ligne(self):
        """« Nudes (Live at AK Studios) » a sa page Genius : « Nudes - Acoustic »
        (même famille « performance ») est CE morceau, pas une variante de « Nudes »."""
        tracks = [_track(1, "Nudes", spotify_id="SP1"), _track(2, "Nudes (Live at AK Studios)")]
        res, dm = _run(tracks, [_entry("Nudes - Acoustic", 14_000_000, 100, "SPA")])
        assert dm.streams_writes == [(2, 14_000_000, 100, datetime(2026, 9, 1))]
        # Le souche garde l'indication, avec le pointeur vers la fiche.
        assert dm.variant_fiches == [(1, "SPA", 2)]
        assert dm.track_spotify_ids == [(2, "SPA")]  # la fiche reçoit l'ID de Kworb

    def test_une_variante_deja_rattachee_rejoint_sa_fiche(self):
        parent = _track(1, "Blues", spotify_id="SP1")
        parent.spotify_id_entries = [
            TrackSpotifyId(spotify_id="SPA", source="kworb", kind="rendition")
        ]
        fiche = _track(2, "Blues (Live at AK Studios)")
        res, dm = _run([parent, fiche], [_entry("Blues - Acoustic", 342, 3, "SPA")])
        assert dm.streams_writes == [(2, 342, 3, datetime(2026, 9, 1))]
        # L'indication reste sur « Blues », désormais pointée vers sa fiche.
        assert dm.variant_fiches == [(1, "SPA", 2)]

    def test_socle_ambigu_devient_proposition(self):
        tracks = [_track(1, "Meilleur"), _track(2, "MEILLEUR")]
        res, dm = _run(tracks, [_entry("Meilleur - Live", 100, 1, "SPL")])
        assert dm.variant_writes == []
        (s,) = res["suggestions"]
        assert s["kind"] == "rendition" and s["proposition"] == "ignore"

    def test_sans_socle_en_base_rien(self):
        res, dm = _run([_track(1, "Autre")], [_entry("Inconnu - Live", 100, 1, "SPL")])
        assert res["unmatched"] == 1 and dm.variant_writes == []


class TestRemix:
    def test_remix_nomme_propose_tiers_sans_ecrire(self):
        identites = {
            "SPR": _identite("Dolce Camara - Snight B Remix", ["Booba", "Snight B", "SDM"], 144)
        }
        res, dm = _run(
            [_track(1, "Dolce Camara", spotify_id="SP1")],
            [_entry("Dolce Camara - Snight B Remix", 25_000_000, 100, "SPR")],
            identites,
        )
        assert dm.streams_writes == [] and dm.variant_writes == []
        (s,) = res["suggestions"]
        assert (s["kind"], s["remixer"], s["proposition"], s["parent_track_id"]) == (
            "remix_named",
            "Snight B",
            "tiers",
            1,
        )
        assert s["credited"] == ["Booba", "Snight B", "SDM"]
        assert any("Snight B" in m for m in s["motifs"])
        assert res["unmatched"] == 0

    def test_remix_nu_propose_collab(self):
        res, _ = _run([_track(1, "5G", spotify_id="SP1")], [_entry("5G Remix", 500, 5, "SPR")])
        (s,) = res["suggestions"]
        assert (s["kind"], s["proposition"]) == ("remix_bare", "collab")

    def test_etoile_kworb_penche_vers_tiers(self):
        e = _entry("5G Remix", 500, 5, "SPR")
        e["is_feature"] = True
        res, _ = _run([_track(1, "5G", spotify_id="SP1")], [e])
        assert res["suggestions"][0]["proposition"] == "tiers"

    def test_remix_deja_en_base_par_relation(self):
        """« DCR (Dolce Camara Remix) » existe (Genius, `remix_of → Dolce Camara`) :
        la ligne Kworb lui est proposée, pas de création."""
        dcr = _track(2, "DCR (Dolce Camara Remix)")
        dcr.relationships = [{"type": "remix_of", "title": "Dolce Camara", "artist": "Booba"}]
        res, dm = _run(
            [_track(1, "Dolce Camara", spotify_id="SP1"), dcr],
            [_entry("Dolce Camara - Snight B Remix", 25_000_000, 100, "SPR")],
        )
        (s,) = res["suggestions"]
        assert s["proposition"] == "existant"
        assert s["existants"] == [(2, "DCR (Dolce Camara Remix)")]
        assert s["track_id"] == 2

    def test_rejet_ancien_repropose_une_fois(self, monkeypatch):
        _memoire(monkeypatch, rejected=["dolce camara snight b remix"])
        res, _ = _run(
            [_track(1, "Dolce Camara", spotify_id="SP1")],
            [_entry("Dolce Camara - Snight B Remix", 25, 1, "SPR")],
        )
        (s,) = res["suggestions"]
        assert any("précédemment rejeté" in m for m in s["motifs"])

    def test_decision_ignore_reste_silencieuse(self, monkeypatch):
        _memoire(
            monkeypatch,
            rejected=["dolce camara snight b remix"],
            decisions={"dolce camara snight b remix": {"kind": "ignore", "track_id": None}},
        )
        res, dm = _run(
            [_track(1, "Dolce Camara", spotify_id="SP1")],
            [_entry("Dolce Camara - Snight B Remix", 25, 1, "SPR")],
        )
        assert res["suggestions"] == [] and dm.streams_writes == []

    def test_decision_rendition_memorisee(self, monkeypatch):
        _memoire(monkeypatch, decisions={"x club remix": {"kind": "rendition", "track_id": 1}})
        res, dm = _run([_track(1, "X", spotify_id="SP1")], [_entry("X - Club Remix", 25, 1, "SPR")])
        assert dm.variant_writes == [(1, "SPR", 25, 1, "X - Club Remix")]

    def test_decision_morceau_memorisee(self, monkeypatch):
        _memoire(monkeypatch, decisions={"x club remix": {"kind": "existant", "track_id": 2}})
        res, dm = _run(
            [_track(1, "X", spotify_id="SP1"), _track(2, "X (Remix)")],
            [_entry("X - Club Remix", 25, 1, "SPR")],
        )
        assert dm.streams_writes == [(2, 25, 1, datetime(2026, 9, 1))]


class TestHomonymesATitreNu:
    def test_deux_morceaux_differents_au_meme_titre(self):
        """« Forever » (Drake, 809 M) et « FOREVER » (Vultures 2, 7 M, crédité ¥$)
        sur une base qui n'a qu'une ligne « Forever » : l'ARTISTE sépare."""
        identites = {
            "SPD": _identite("Forever", ["Drake", "Kanye West"], 357),
            "SPV": _identite("FOREVER", ["¥$"], 180),
        }
        t = _track(1, "Forever", feat=True, primary="Drake")
        t.artist, t.duration = _Artist(), 357
        res, dm = _run(
            [t], [_entry("Forever", 809, 8, "SPD"), _entry("Forever", 7, 1, "SPV")], identites
        )
        assert dm.streams_writes == [(1, 809, 8, datetime(2026, 9, 1))]
        assert res["lignes_ecartees"] == [("Forever", 7, "Forever")]
        assert res["unmatched"] == 1

    def test_deux_uploads_a_durees_differentes_sommes(self):
        """Le catalogue MC Solaar re-sorti en 2021 : deux uploads de « Caroline »,
        même artiste, durées différentes — toutes les écoutes comptent."""
        identites = {
            "SP1": _identite("Caroline", ["Jul"], 258),
            "SP2": _identite("Caroline", ["Jul"], 245),
        }
        t = _track(1, "Caroline")
        t.artist, t.duration = _Artist(), 258
        res, dm = _run(
            [t], [_entry("Caroline", 14, 1, "SP1"), _entry("Caroline", 13, 1, "SP2")], identites
        )
        assert dm.streams_writes == [(1, 27, 2, datetime(2026, 9, 1))]
        assert res["lignes_ecartees"] == []

    def test_un_titre_generique_se_departage_par_la_duree(self):
        """Trois « Interlude » de Jazzy Bazz sont trois morceaux : seule la ligne
        dont la durée concorde avec la base est la sienne."""
        identites = {
            "SP1": _identite("Interlude", ["Jul"], 61),
            "SP2": _identite("Interlude", ["Jul"], 95),
        }
        t = _track(1, "Interlude")
        t.artist, t.duration = _Artist(), 61
        res, dm = _run(
            [t],
            [_entry("Interlude", 1342, 1, "SP1"), _entry("Interlude", 343, 1, "SP2")],
            identites,
        )
        assert dm.streams_writes == [(1, 1342, 1, datetime(2026, 9, 1))]
        assert res["lignes_ecartees"] == [("Interlude", 343, "Interlude")]

    def test_deux_uploads_du_meme_enregistrement_sommes(self):
        identites = {
            "SP1": _identite("Runaway", ["Kanye West"], 548),
            "SP2": _identite("Runaway", ["Kanye West"], 549),
        }
        res, dm = _run(
            [_track(1, "Runaway")],
            [_entry("Runaway", 1_262, 10, "SP1"), _entry("Runaway", 35, 1, "SP2")],
            identites,
        )
        assert dm.streams_writes == [(1, 1_297, 11, datetime(2026, 9, 1))]
        assert res["multi_lignes"] == [("Runaway", 2, 2, 1_297)]


class TestVariantesSuspectes:
    def test_id_en_base_dont_le_titre_spotify_est_une_autre_version(self):
        """« Heartless (Remix) » porte l'ID de « Heartless » : on écrit ce que
        l'ID affirme, mais on le DIT — la vérification des identifiants répare."""
        res, dm = _run(
            [_track(1, "Heartless (Remix)", spotify_id="SPH")],
            [_entry("Heartless", 2_109, 9, "SPH")],
        )
        assert dm.streams_writes == [(1, 2_109, 9, datetime(2026, 9, 1))]
        assert res["variantes_suspectes"] == [("Heartless (Remix)", "Heartless", "SPH")]
