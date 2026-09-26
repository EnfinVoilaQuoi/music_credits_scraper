"""Crédits de l'API Genius — PROVISOIRES jusqu'au scrape (2026-09-26).

La fiche `GET /songs/{id}` est déjà lue à l'import : ses crédits sont gratuits
et en rendent 96 % (mesuré sur 20 morceaux). Ils sont enregistrés sous une
source DISTINCTE, `genius_api`, qui renseigne la fiche sans dispenser du
scrape : le scrape les compare (signal de santé du parseur) puis les remplace.

Une règle de coexistence PAR (personne, rôle), vérifiée à chaque endroit où
des crédits se rencontrent : un `genius_api` que la page confirme est remplacé
par le crédit scrapé, ce que seule l'API donne reste (🎫).
"""

import pytest
from sqlalchemy import text

from src.api.genius_api import GeniusAPI
from src.models import Artist, Track
from src.models.track import Credit, CreditRole
from src.scrapers.genius_scraper_v3 import GeniusScraperV3
from src.services import discographie as disco
from src.services.credits import _manque_credits
from src.services.runtime import Manque, est_manquant
from src.utils import credits_genius_api as cga
from src.utils.track_validation import credits_confirmes

MOI, AUTRE = 100, 200

#: Fiche détail réduite — la forme réelle de `GET /songs/{id}`.
DETAIL = {
    "producer_artists": [{"id": 1, "name": "Kanye West"}],
    "writer_artists": [{"id": 2, "name": "Kid Cudi"}, {"id": 1, "name": "Kanye West"}],
    "featured_artists": [{"id": 3, "name": "Pusha T"}],
    "custom_performances": [
        {"label": "Assistant Mixing Engineer", "artists": [{"id": 4, "name": "Noah Goldstein"}]},
        {"label": "Phonographic Copyright ℗", "artists": [{"id": 5, "name": "Def Jam"}]},
        {"label": "Genre", "artists": [{"id": 6, "name": "Hip-Hop"}]},
        {"label": "Programmer", "artists": [{"id": 2, "name": "Kid Cudi"}]},
    ],
}


def _c(nom, role, source="genius", detail=None):
    return Credit(name=nom, role=role, role_detail=detail, source=source)


def _api(nom, role, detail=None):
    return _c(nom, role, cga.SOURCE_API, detail)


# ──────────────────────────────────────────────────────── lecture de la fiche


class TestCreditsDuDetail:
    def test_champs_et_performances_traduits_par_la_table_du_scrape(self):
        credits = cga.credits_du_detail(DETAIL)
        vus = {(c.name, c.role) for c in credits}
        assert vus == {
            ("Kanye West", CreditRole.PRODUCER),
            ("Kanye West", CreditRole.WRITER),
            ("Kid Cudi", CreditRole.WRITER),
            ("Kid Cudi", CreditRole.PROGRAMMER),
            ("Pusha T", CreditRole.FEATURED),
            ("Noah Goldstein", CreditRole.ASSISTANT_MIXING_ENGINEER),
            ("Def Jam", CreditRole.OTHER),
        }
        assert all(c.source == "genius_api" for c in credits)

    def test_libelle_brut_garde_pour_un_other_seulement(self):
        """Même convention que le scrape : sans le libellé, un `Other` ne
        pourrait jamais être reclassé (cas des assistants du 2026-09-26)."""
        par_nom = {c.name: c for c in cga.credits_du_detail(DETAIL)}
        assert par_nom["Def Jam"].role_detail == "Phonographic Copyright ℗"
        assert par_nom["Pusha T"].role_detail is None

    def test_metadonnees_ecartees_comme_au_scrape(self):
        assert "Hip-Hop" not in {c.name for c in cga.credits_du_detail(DETAIL)}

    def test_entrees_malformees(self):
        detail = {
            "producer_artists": [None, {"name": ""}, {"name": "X"}, "chaine"],
            "custom_performances": [None, {"label": "", "artists": [{"name": "Y Y"}]}],
        }
        assert cga.credits_du_detail(detail) == []


class TestStatut:
    def test_provisoire_tant_que_le_scrape_n_est_pas_passe(self):
        assert cga.sont_provisoires([_api("A", CreditRole.PRODUCER)])
        assert not cga.sont_provisoires(
            [_api("A", CreditRole.PRODUCER), _c("B", CreditRole.WRITER)]
        )
        assert not cga.sont_provisoires([_c("B", CreditRole.WRITER, "discogs")])
        assert not cga.sont_provisoires([])

    def test_couverts_par_personne_ET_role(self):
        """Seul un crédit que la page confirme (même personne, même rôle) part.
        Un rôle de plus pour une personne que la page cite RESTE : c'est le cas
        réel de Kid Cudi « Programmer » (décision utilisateur 2026-09-26)."""
        credits = [
            _api("Kid Cudi", CreditRole.PRODUCER),
            _api("Kid Cudi", CreditRole.PROGRAMMER),
            _api("Absent", CreditRole.WRITER),
            _c("Kid Cudi", CreditRole.PRODUCER),
            _c("C", CreditRole.PRODUCER, "discogs"),
        ]
        restent = [(c.name, c.role, c.source) for c in cga.sans_provisoires_couverts(credits)]
        assert restent == [
            ("Kid Cudi", CreditRole.PROGRAMMER, "genius_api"),
            ("Absent", CreditRole.WRITER, "genius_api"),
            ("Kid Cudi", CreditRole.PRODUCER, "genius"),
            ("C", CreditRole.PRODUCER, "discogs"),
        ]
        # Une autre SOURCE ne confirme rien : seul le scrape de la page.
        seuls = [_api("A", CreditRole.PRODUCER), _c("A", CreditRole.PRODUCER, "discogs")]
        assert cga.sans_provisoires_couverts(seuls) == seuls

    def test_la_cle_passe_par_le_nom_normalise(self):
        credits = [_api("Kid  Cudi", CreditRole.WRITER), _c("KID CUDI", CreditRole.WRITER)]
        assert [c.source for c in cga.sans_provisoires_couverts(credits)] == ["genius"]

    def test_le_scrape_reste_a_faire(self):
        """`--manquants` ne regarde que la source `genius` : des crédits API
        seuls laissent le morceau À SCRAPER — c'est tout le statut."""
        t = Track(title="T")
        t.credits = [_api("A", CreditRole.PRODUCER), _api("B", CreditRole.WRITER)]
        assert est_manquant(t, Manque.CREDITS_GENIUS)

    def test_ne_dispense_pas_de_la_phase_youtube(self):
        t = Track(title="T")
        t.credits = [_api("A", CreditRole.PRODUCER), _api("B", CreditRole.WRITER)]
        assert _manque_credits(t)
        t.credits.append(_c("C", CreditRole.PRODUCER, "discogs"))
        assert not _manque_credits(t)

    def test_ne_confirment_rien(self):
        t = Track(title="T")
        t.credits = [_api("A", CreditRole.PRODUCER), _c("A", CreditRole.PRODUCER, "discogs")]
        assert not credits_confirmes(t)
        t.credits.append(_c("A", CreditRole.PRODUCER))
        assert credits_confirmes(t)


class TestPoser:
    def test_pose_sur_une_fiche_sans_scrape(self):
        t = Track(title="T")
        t.credits = [_c("Z", CreditRole.PRODUCER, "discogs")]
        assert cga.poser(t, DETAIL) is True
        sources = {c.source for c in t.credits}
        assert sources == {"discogs", "genius_api"}

    def test_jamais_sur_une_fiche_scrapee(self):
        t = Track(title="T")
        t.credits = [_c("A", CreditRole.PRODUCER)]
        assert cga.poser(t, DETAIL) is False
        assert [c.name for c in t.credits] == ["A"]

    def test_les_frais_remplacent_les_anciens_provisoires(self):
        t = Track(title="T")
        t.credits = [_api("Ancien", CreditRole.PRODUCER)]
        assert cga.poser(t, {"producer_artists": [{"name": "Nouveau"}]}) is True
        assert [c.name for c in t.credits] == ["Nouveau"]

    def test_rien_ne_change_rien_ne_signale(self):
        t = Track(title="T")
        cga.poser(t, DETAIL)
        assert cga.poser(t, DETAIL) is False
        assert cga.poser(Track(title="U"), {}) is False


class TestUnir:
    def test_les_credits_scrapes_en_base_survivent_a_une_fiche_neuve(self):
        """Le piège : `save_track` réécrit la table depuis l'objet. Une fiche
        neuve venue de l'API, préférée à la fiche en base, EFFAÇAIT le scrape."""
        existants = [_c("A", CreditRole.PRODUCER), _c("D", CreditRole.WRITER, "discogs")]
        entrants = [_api("A", CreditRole.PRODUCER), _api("B", CreditRole.WRITER)]
        out = cga.unir(existants, entrants)
        # A : confirmé par le scrape en base ; B : que l'API donne, gardé.
        assert [(c.name, c.source) for c in out] == [
            ("A", "genius"),
            ("D", "discogs"),
            ("B", "genius_api"),
        ]

    def test_sans_scrape_les_frais_remplacent_les_provisoires(self):
        out = cga.unir(
            [_api("Ancien", CreditRole.PRODUCER), _c("D", CreditRole.WRITER, "discogs")],
            [_api("Nouveau", CreditRole.PRODUCER)],
        )
        assert [c.name for c in out] == ["D", "Nouveau"]

    def test_fiche_neuve_sans_credit(self):
        existants = [_c("A", CreditRole.PRODUCER)]
        assert cga.unir(existants, []) == existants


class TestComparer:
    def test_trois_cas(self):
        api = [
            _api("Kanye West", CreditRole.PRODUCER),
            _api("Noah Goldstein", CreditRole.ASSISTANT_MIXING_ENGINEER),
            _api("Kid Cudi", CreditRole.PROGRAMMER),
        ]
        scrape = [
            _c("Kanye West", CreditRole.PRODUCER),
            _c("Noah Goldstein", CreditRole.MIXING_ENGINEER),
            _c("Glenwood Studios", CreditRole.OTHER, detail="Recorded At"),
        ]
        e = cga.comparer(api, scrape)
        assert e.signale
        assert e.api_seuls == ["kid cudi (Programmer)"]
        assert len(e.roles_divergents) == 1 and "noah goldstein" in e.roles_divergents[0]
        # Un lieu, un ingénieur sans page : NORMAL, compté sans alerte.
        assert e.scrape_seuls == 2

    def test_accord_complet_ne_signale_rien(self):
        e = cga.comparer([_api("A", CreditRole.PRODUCER)], [_c("A", CreditRole.PRODUCER)])
        assert not e.signale and e.scrape_seuls == 0


# ─────────────────────────────────────────────────────────────── voie API


class _Genius:
    def __init__(self, details):
        self.details = details

    def song(self, song_id):
        return {"song": self.details.get(song_id, {})}

    def artist_songs(self, artist_id, sort=None, per_page=None, page=1):
        songs = [
            {
                "id": 7,
                "title": "Prod",
                "url": "https://genius.com/7",
                "primary_artist": {"id": AUTRE, "name": "Autre"},
            }
        ]
        return {"songs": songs if page == 1 else [], "next_page": 2 if page == 1 else None}


@pytest.fixture
def api(monkeypatch):
    inst = GeniusAPI.__new__(GeniusAPI)
    monkeypatch.setattr("src.api.genius_api.time.sleep", lambda *_: None)
    return inst


class TestVoieApi:
    def test_la_fiche_detail_pose_ses_credits(self, api):
        api.genius = _Genius({123: DETAIL})
        t = Track(title="T")
        t.genius_id = 123
        assert api.apply_song_metadata(t) is True
        assert cga.sont_provisoires(t.credits)

    def test_verification_d_un_role_secondaire_sans_second_appel(self, api):
        """Le détail lu pour VÉRIFIER le rôle porte les crédits : posés sur le
        morceau créé, sans requête de plus (quota Genius 10 000 / 24 h)."""
        detail = {"producer_artists": [{"id": MOI, "name": "Isha"}], **DETAIL}
        detail["producer_artists"] = [{"id": MOI, "name": "Isha"}]
        api.genius = _Genius({7: detail})
        (t,) = api._get_artist_songs_manual(
            Artist(name="Isha", genius_id=MOI), None, include_features=True, include_prods=True
        )
        assert t.secondary_role == "Producer"
        assert ("Isha", CreditRole.PRODUCER, "genius_api") in {
            (c.name, c.role, c.source) for c in t.credits
        }

    def test_verification_rend_le_detail_lu(self, api):
        api.genius = _Genius({7: {"producer_artists": [{"id": MOI, "name": "Isha"}]}})
        out = {}
        assert api._verify_artist_credit(7, MOI, detail_out=out) == ("secondary", "Producer")
        assert out["song"]["producer_artists"][0]["name"] == "Isha"


# ──────────────────────────────────────────────────────────────── scrape

_PAGE = """<html><body>
<div class="Credit__Container-x"><div class="Credit__Label-x">Producer</div>
<div class="Credit__Contributor-x"><div><a href="#">Kanye West</a></div></div></div>
<div class="Credit__Container-x"><div class="Credit__Label-x">Recorded At</div>
<div class="Credit__Contributor-x">Cabo, Mexico; Pio Pico, Los Angeles, CA</div></div>
<div class="Credit__Container-x"><div class="Credit__Label-x">Released on</div>
<div class="Credit__Contributor-x">December 16, 2016</div></div>
</body></html>"""


@pytest.fixture
def scraper(monkeypatch):
    s = GeniusScraperV3.__new__(GeniusScraperV3)
    s.derniers_ecarts_api = None
    s._crawl_page = lambda **kw: ("", _PAGE)
    monkeypatch.setattr("src.scrapers.genius_scraper_v3.time.sleep", lambda *_: None)
    return s


class TestScrape:
    def _track(self, *credits):
        t = Track(title="Delresto", album="Utopia")
        t.genius_url = "https://genius.com/x"
        t.credits = list(credits)
        return t

    def test_le_scrape_remplace_ce_qu_il_confirme_et_garde_le_reste(self, scraper):
        t = self._track(
            _api("Kanye West", CreditRole.PRODUCER),
            _api("Kanye West", CreditRole.PROGRAMMER),
            _api("Kid Cudi", CreditRole.WRITER),
            _c("Z", CreditRole.WRITER, "discogs"),
        )
        scraper.scrape_track_credits(t, include_lyrics=False)
        vus = {(c.name, c.role, c.source) for c in t.credits}
        # Confirmé par la page : devient un crédit scrapé, une seule fois.
        assert ("Kanye West", CreditRole.PRODUCER, "genius") in vus
        assert ("Kanye West", CreditRole.PRODUCER, "genius_api") not in vus
        # Que l'API donne : gardé en 🎫 (personne absente, ou rôle de plus).
        assert ("Kid Cudi", CreditRole.WRITER, "genius_api") in vus
        assert ("Kanye West", CreditRole.PROGRAMMER, "genius_api") in vus
        assert ("Z", CreditRole.WRITER, "discogs") in vus
        assert not cga.sont_provisoires(t.credits)
        e = scraper.derniers_ecarts_api
        assert e.api_seuls == ["kid cudi (Writer)"]
        assert len(e.roles_divergents) == 1 and "Programmer" in e.roles_divergents[0]

    def test_un_second_scrape_garde_les_complements(self, scraper):
        """Le scrape ne purge que SES crédits : un complément 🎫 retenu au
        premier passage survit aux suivants."""
        t = self._track(_api("Kid Cudi", CreditRole.WRITER))
        scraper.scrape_track_credits(t, include_lyrics=False)
        scraper.scrape_track_credits(t, include_lyrics=False)
        assert ("Kid Cudi", "genius_api") in {(c.name, c.source) for c in t.credits}

    def test_un_scrape_vide_laisse_les_provisoires(self, scraper):
        scraper._crawl_page = lambda **kw: ("", "<html></html>")
        t = self._track(_api("Kanye West", CreditRole.PRODUCER))
        scraper.scrape_track_credits(t, include_lyrics=False)
        assert cga.sont_provisoires(t.credits)

    def test_bilan_du_lot(self, scraper):
        """Des crédits API seuls ne font pas un scrape réussi, et les écarts
        remontent au bilan."""
        ok = self._track(_api("Kid Cudi", CreditRole.WRITER))
        rate = self._track(_api("Kanye West", CreditRole.PRODUCER))
        pages = iter([_PAGE, "<html></html>"])
        scraper._crawl_page = lambda **kw: ("", next(pages))
        scraper._apply_lyrics_from_html = lambda html, track: None
        res = scraper.scrape_multiple_tracks([ok, rate])
        assert (res["success"], res["failed"]) == (1, 1)
        assert res["api_remplaces"] == 1
        assert list(res["api_ecarts"]) == ["Delresto"]


class TestTexteLibre:
    """« Recorded At » est un texte libre, pas une liste d'artistes : le
    découper à la virgule donnait « Burbank » et « CA. » en crédits (1 836
    lignes en base, 2026-09-26)."""

    def test_lieux_separes_par_point_virgule_seulement(self, scraper):
        credits = scraper._extract_fallback_bs4(_PAGE)
        lieux = [c.name for c in credits if c.role_detail == "Recorded At"]
        assert lieux == ["Cabo, Mexico", "Pio Pico, Los Angeles, CA"]

    def test_un_lieu_avec_parentheses_reste_entier(self, scraper):
        page = _PAGE.replace(
            "Cabo, Mexico; Pio Pico, Los Angeles, CA", "Powerhouse Studios (Yonkers, NY)"
        )
        credits = scraper._extract_fallback_bs4(page)
        assert [c.name for c in credits if c.role_detail == "Recorded At"] == [
            "Powerhouse Studios (Yonkers, NY)"
        ]

    def test_les_artistes_lies_ne_changent_pas(self, scraper):
        page = _PAGE.replace(
            '<a href="#">Kanye West</a>', '<a href="#">Kanye West</a> &amp; <a href="#">No I.D.</a>'
        )
        prods = [
            c.name for c in scraper._extract_fallback_bs4(page) if c.role == CreditRole.PRODUCER
        ]
        assert prods == ["Kanye West", "No I.D."]

    def test_la_date_reste_hors_credits(self, scraper):
        assert "December 16, 2016" not in {c.name for c in scraper._extract_fallback_bs4(_PAGE)}


# ────────────────────────────────────────────────── import et persistance


class TestFusionImport:
    def test_une_fiche_neuve_ne_perd_pas_le_scrape_en_base(self):
        exist = Track(title="T")
        exist.id, exist.genius_id = 5, 9
        exist.credits = [_c("A", CreditRole.PRODUCER)]
        nouveau = Track(title="T")
        nouveau.genius_id = 9
        nouveau.credits = [_api("A", CreditRole.PRODUCER), _api("B", CreditRole.WRITER)]
        disco.fusionner([nouveau], [exist])
        assert [(c.name, c.source) for c in nouveau.credits] == [
            ("A", "genius"),
            ("B", "genius_api"),
        ]


def _credits_en_base(data_manager, track_id):
    with data_manager.engine.connect() as conn:
        return sorted(
            conn.execute(
                text("SELECT name, source FROM credits WHERE track_id = :t"), {"t": track_id}
            ).all()
        )


@pytest.fixture
def artiste(data_manager):
    a = Artist(name="Isha")
    a.id = data_manager.save_artist(a)
    return a


class TestPersistance:
    def test_save_track_efface_les_provisoires_couverts(self, data_manager, artiste):
        t = Track(title="T", artist=artiste)
        t.credits = [
            _api("A", CreditRole.PRODUCER),
            _api("C", CreditRole.WRITER),
            _c("A", CreditRole.PRODUCER),
        ]
        tid = data_manager.save_track(t)
        assert _credits_en_base(data_manager, tid) == [("A", "genius"), ("C", "genius_api")]

    def test_save_track_garde_les_provisoires_seuls(self, data_manager, artiste):
        t = Track(title="T", artist=artiste)
        t.credits = [_api("A", CreditRole.PRODUCER)]
        tid = data_manager.save_track(t)
        assert _credits_en_base(data_manager, tid) == [("A", "genius_api")]

    def test_le_scrape_d_une_soeur_vaut_pour_toutes(self, data_manager, artiste):
        """La propagation unit sans regarder la source : sans la purge, le
        provisoire de la sœur bloquait la copie du crédit scrapé identique et
        restait à côté du scrape."""
        swing = Artist(name="Swing")
        swing.id = data_manager.save_artist(swing)
        chez_isha = Track(title="Grünt #33", artist=artiste)
        chez_isha.genius_id = 3365192
        chez_isha.credits = [_api("Swing", CreditRole.PRODUCER), _api("Vieux", CreditRole.WRITER)]
        data_manager.save_track(chez_isha)

        chez_swing = Track(title="Grünt #33", artist=swing)
        chez_swing.genius_id = 3365192
        chez_swing.credits = [_c("Swing", CreditRole.PRODUCER), _c("Isha", CreditRole.WRITER)]
        data_manager.save_track(chez_swing)

        # Swing Producer : confirmé chez la sœur, remplacé ; « Vieux » : que
        # l'API donne, gardé ; Isha Writer : arrivé par l'union.
        assert _credits_en_base(data_manager, chez_isha.id) == [
            ("Isha", "genius"),
            ("Swing", "genius"),
            ("Vieux", "genius_api"),
        ]

    def test_fusion_de_fiches(self, data_manager, artiste):
        keep = Track(title="Garde", artist=artiste)
        keep.credits = [_api("A", CreditRole.PRODUCER)]
        keep_id = data_manager.save_track(keep)
        drop = Track(title="Doublon", artist=artiste)
        drop.credits = [_c("A", CreditRole.PRODUCER), _c("B", CreditRole.WRITER)]
        drop_id = data_manager.save_track(drop)

        assert data_manager.merge_tracks(keep_id, drop_id) is True
        assert _credits_en_base(data_manager, keep_id) == [("A", "genius"), ("B", "genius")]


class TestCelluleGui:
    def test_compte_et_sablier(self):
        from src.gui import helpers

        t = Track(title="T")
        assert helpers.format_credits_cell(t) == "0"
        t.credits = [_api("A", CreditRole.PRODUCER), _c("B", CreditRole.WRITER, "discogs")]
        assert helpers.format_credits_cell(t) == "2 ⏳"
        t.credits.append(_c("A", CreditRole.PRODUCER))
        assert helpers.format_credits_cell(t) == "3"
        assert helpers.EMOJI_SOURCE["genius_api"] == "🎫"


class TestSoeursAuScrape:
    """Un scrape relit la page de TOUTES les lignes sœurs (même genius_id) :
    l'union seule réinjectait leurs anciens crédits `genius` — les « Recorded
    At » découpés sont revenus sur « The Morning » (Kanye, Cudi, Travis) après
    le re-scrape du 2026-09-26."""

    def _famille(self, data_manager, artiste):
        kanye = Artist(name="Kanye West")
        kanye.id = data_manager.save_artist(kanye)
        lignes = []
        for art in (artiste, kanye):
            t = Track(title="The Morning", artist=art)
            t.genius_id = 87430
            t.credits = [
                _c("NYC", CreditRole.OTHER, detail="Recorded At"),
                _c("Jungle City Studios", CreditRole.OTHER, detail="Recorded At"),
                _c("Kanye West", CreditRole.PRODUCER),
            ]
            data_manager.save_track(t)
            lignes.append(t)
        return lignes

    def test_le_scrape_remplace_chez_les_soeurs(self, data_manager, artiste):
        chez_isha, chez_kanye = self._famille(data_manager, artiste)
        chez_kanye.credits = [
            _c("Jungle City Studios, NYC", CreditRole.OTHER, detail="Recorded At"),
            _c("Kanye West", CreditRole.PRODUCER),
        ]
        chez_kanye._credits_genius_frais = True
        data_manager.save_track(chez_kanye)
        attendu = [("Jungle City Studios, NYC", "genius"), ("Kanye West", "genius")]
        assert _credits_en_base(data_manager, chez_kanye.id) == attendu
        assert _credits_en_base(data_manager, chez_isha.id) == attendu
        assert chez_kanye._credits_genius_frais is False

    def test_sans_scrape_l_union_reste_additive(self, data_manager, artiste):
        """Un save ordinaire (autre flux) ne retire rien chez les sœurs."""
        chez_isha, chez_kanye = self._famille(data_manager, artiste)
        chez_kanye.credits = [_c("Kanye West", CreditRole.PRODUCER)]
        data_manager.save_track(chez_kanye)
        assert ("NYC", "genius") in _credits_en_base(data_manager, chez_isha.id)

    def test_les_autres_sources_des_soeurs_restent(self, data_manager, artiste):
        chez_isha, chez_kanye = self._famille(data_manager, artiste)
        chez_isha.credits.append(_c("Mike Dean", CreditRole.MIXING_ENGINEER, "discogs"))
        data_manager.save_track(chez_isha)
        chez_kanye.credits = [_c("Kanye West", CreditRole.PRODUCER)]
        chez_kanye._credits_genius_frais = True
        data_manager.save_track(chez_kanye)
        assert _credits_en_base(data_manager, chez_isha.id) == [
            ("Kanye West", "genius"),
            ("Mike Dean", "discogs"),
        ]

    def test_le_scraper_pose_le_marqueur(self, scraper):
        t = Track(title="T", album="A")
        t.genius_url = "https://genius.com/x"
        scraper.scrape_track_credits(t, include_lyrics=False)
        assert t._credits_genius_frais is True


class TestValeursVides:
    """« N/A » et « ??? » tapés faute de savoir ne désignent personne (2026-09-26)."""

    @pytest.mark.parametrize("nom", ["N/A", "n/a", "???", "?", "-", " ", "Unknown", "TBD"])
    def test_ecartees(self, nom):
        from src.utils.credit_roles import valeur_vide

        assert valeur_vide(nom)

    @pytest.mark.parametrize("nom", ["NA", "None", "TMZ", "Studio Méga (Paris", "!!!Chk", "X"])
    def test_gardees(self, nom):
        from src.utils.credit_roles import valeur_vide

        assert not valeur_vide(nom)

    def test_au_scrape(self, scraper):
        page = _PAGE.replace("Cabo, Mexico; Pio Pico, Los Angeles, CA", "N/A")
        assert "N/A" not in {c.name for c in scraper._extract_fallback_bs4(page)}

    def test_a_l_api(self):
        detail = {"custom_performances": [{"label": "Studio", "artists": [{"name": "???"}]}]}
        assert cga.credits_du_detail(detail) == []


def test_les_relations_ne_deviennent_pas_des_credits_par_la_voie_html(scraper):
    """« Come as You Are by Nirvana » (Is A Cover Of) et les traductions
    passaient par `_append_credits`, qui ne filtrait pas : le re-scrape du
    2026-09-26 en a réintroduit 1 575."""
    nbsp = chr(160)
    page = _PAGE.replace(
        '<div class="Credit__Label-x">Released on</div>',
        '<div class="Credit__Label-x">Is A Cover Of</div>'
        f'<div class="Credit__Contributor-x"><a href="#">Come as You Are by{nbsp}Nirvana</a></div>'
        '</div><div class="Credit__Container-x"><div class="Credit__Label-x">Released on</div>',
    )
    noms = {c.name for c in scraper._extract_fallback_bs4(page)}
    assert "Kanye West" in noms
    assert not any("Nirvana" in n for n in noms)
