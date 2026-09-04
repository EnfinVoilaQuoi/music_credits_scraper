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

from src.models.track import Track
from src.utils import update_kworb as uk
from src.utils.update_kworb import (
    _best_candidate,
    _fuzzy_unique,
    _names_match,
    _resolve_homonym,
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
        self.album_writes = []
        self.totals = None
        self.artist_spotify_id = None
        self.track_spotify_ids = []

    def get_artist_tracks(self, artist_id):
        return self._tracks

    def update_track_spotify_streams(self, track_id, streams, daily, updated_at=None):
        self.streams_writes.append((track_id, streams, daily, updated_at))
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
    def _run(self, tracks, album_entries):
        dm = _DataManager(tracks)
        scraper = _Scraper(
            # Au moins une entrée songs : une page vide est refusée par la
            # validation d'identité (cf. TestValidationDIdentite).
            songs=_page([_entry("Morceau quelconque")]),
            albums={"entries": album_entries, "last_updated": datetime(2026, 9, 1)},
        )
        return update_kworb_streams(_Artist(), dm, scraper=scraper), dm

    def test_album_propre_ecrit(self):
        tracks = [_track(1, "A", album="Mon Album"), _track(2, "B", album="Mon Album")]
        res, dm = self._run(tracks, [_entry("Mon Album", 50000, 500)])
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
        res, _ = self._run(tracks, [_entry("Bitume Caviar", 10)])
        assert res["albums_updated"] == 1

    def test_editions_multiples_agregees(self):
        """Deluxe et édition standard portent le même titre normalisé : leurs
        streams sont sommés et les deux IDs conservés."""
        tracks = [_track(1, "A", album="Mon Album"), _track(2, "B", album="Mon Album")]
        res, dm = self._run(
            tracks,
            [
                _entry("Mon Album", 30000, 300, "AL1"),
                _entry("mon album", 20000, 200, "AL2"),
            ],
        )
        assert res["albums_updated"] == 1
        titre, streams, daily, kwargs = dm.album_writes[0]
        assert (streams, daily) == (50000, 500)
        assert kwargs["spotify_album_ids"] == "AL1,AL2"

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
    """Deux morceaux au même titre, départagés par la page embed du track."""

    def _run(self, embed, tracks, entries):
        dm = _DataManager(tracks)
        res = update_kworb_streams(_Artist(), dm, scraper=_Scraper(songs=_page(entries)))
        return res, dm

    def test_homonyme_resolu(self, embed):
        embed["track_artists"] = {"SPX": [{"name": "Souffrance"}]}
        tracks = [
            _track(1, "Meilleur", feat=True, primary="Souffrance"),
            _track(2, "Meilleur", feat=True, primary="Goldee Money"),
        ]
        res, dm = self._run(embed, tracks, [_entry("Meilleur", 4000, 40, "SPX")])
        assert res["matched_by_title"] == 1
        assert dm.streams_writes == [(1, 4000, 40, datetime(2026, 9, 1))]

    def test_embed_muet_abstention(self, embed):
        embed["track_artists"] = {"SPX": []}
        tracks = [_track(1, "Meilleur"), _track(2, "MEILLEUR")]
        res, dm = self._run(embed, tracks, [_entry("Meilleur", spotify_id="SPX")])
        assert res["unmatched"] == 1
        assert dm.streams_writes == []

    def test_scraper_embed_ferme(self, embed):
        """Un navigateur laissé ouvert bloque la fermeture de l'application."""
        embed["track_artists"] = {"SPX": [{"name": "Jul"}]}
        tracks = [_track(1, "Meilleur"), _track(2, "MEILLEUR", feat=True, primary="Autre")]
        self._run(embed, tracks, [_entry("Meilleur", spotify_id="SPX")])
        assert _EmbedScraper.instances[0].closed is True

    def test_erreur_embed_traitee_comme_muette(self, embed, monkeypatch):
        from playwright.sync_api import Error as PlaywrightError

        class _Casse(_EmbedScraper):
            def get_track_artists(self, spotify_id):
                raise PlaywrightError("page morte")

        monkeypatch.setattr(
            "src.scrapers.spotify_id_scraper_v2.SpotifyIDScraper",
            lambda headless=True: _Casse(headless),
        )
        tracks = [_track(1, "Meilleur"), _track(2, "MEILLEUR")]
        res, dm = self._run(embed, tracks, [_entry("Meilleur", spotify_id="SPX")])
        assert res["unmatched"] == 1
        assert dm.streams_writes == []
