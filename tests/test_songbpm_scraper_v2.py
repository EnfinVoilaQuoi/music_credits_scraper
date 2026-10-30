"""Rapprochement et parsing du scraper SongBPM (`songbpm_scraper_v2`).

L'essentiel n'a besoin ni de navigateur ni de réseau : `__init__` est PARESSEUX
(driver créé au premier usage), les helpers de matching sont purs, et le parseur
de résultats (dans la variante async, la seule qui navigue) ne fait que descendre
un arbre d'éléments — remplaçable par un faux DOM.

Ce qui se joue ici : `_match_track` décide si un résultat SongBPM est bien NOTRE
morceau. Un faux positif écrit un BPM étranger en base, où plus rien ne le
distingue d'une vraie mesure.
"""

import asyncio

import pytest

from src.scrapers.songbpm_scraper_async import SongBPMScraperAsync
from src.scrapers.songbpm_scraper_v2 import SongBPMScraper


@pytest.fixture
def scraper():
    """Scraper réel : l'init ne monte aucun navigateur (driver paresseux)."""
    return SongBPMScraper(headless=True)


@pytest.fixture
def scraper_async():
    """Variante async (celle qui navigue), sans navigateur monté."""
    return SongBPMScraperAsync(headless=True)


# ─────────────────────────────────────────────────────────── faux DOM Playwright


class _Element:
    """Élément minimal : ce que `_get_search_results_async` appelle réellement."""

    def __init__(self, enfants=None, attrs=None, texte=""):
        self._enfants = enfants or {}
        self._attrs = attrs or {}
        self._texte = texte

    async def query_selector_all(self, selecteur):
        return self._enfants.get(selecteur, [])

    async def get_attribute(self, nom):
        return self._attrs.get(nom)

    async def inner_text(self):
        return self._texte


class _Page:
    def __init__(self, conteneurs):
        self._conteneurs = conteneurs

    async def query_selector_all(self, selecteur):
        return self._conteneurs if selecteur == "div.bg-card" else []


def _metrique(label, valeur):
    return _Element(enfants={"span": [_Element(texte=label), _Element(texte=valeur)]})


def _resultat(
    artiste="Jul",
    titre="Bande organisée",
    href="https://songbpm.com/@jul/bande-organisee",
    metriques=(("BPM", "140"), ("KEY", "F"), ("DURATION", "3:34")),
    spotify_url=None,
):
    infos = _Element(
        enfants={
            "p": [
                _Element(attrs={"class": "text-sm text-muted"}, texte=artiste),
                _Element(attrs={"class": "text-lg font-bold"}, texte=titre),
            ]
        }
    )
    lien = _Element(
        attrs={"href": href},
        enfants={
            "div.flex-1": [infos],
            "div.flex.flex-1.flex-col.items-center": [_metrique(*m) for m in metriques],
        },
    )
    enfants = {"a[href*='/@']": [lien]}
    if spotify_url:
        enfants["a[href*='spotify.com/track/']"] = [_Element(attrs={"href": spotify_url})]
    return _Element(enfants=enfants)


# ───────────────────────────────────────────────────────────── helpers purs


class TestIdSpotify:
    def test_url_standard(self, scraper):
        url = "https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT"
        assert scraper._extract_spotify_id_from_url(url) == "4cOdK2wGLETKBW3PvgPWqT"

    def test_url_localisee(self, scraper):
        """Spotify insère parfois un segment de langue (`/intl-fr/`)."""
        url = "https://open.spotify.com/intl-fr/track/4cOdK2wGLETKBW3PvgPWqT"
        assert scraper._extract_spotify_id_from_url(url) == "4cOdK2wGLETKBW3PvgPWqT"

    def test_url_sans_id(self, scraper):
        assert scraper._extract_spotify_id_from_url("https://open.spotify.com/album/x") is None

    def test_url_vide(self, scraper):
        assert scraper._extract_spotify_id_from_url("") is None
        assert scraper._extract_spotify_id_from_url(None) is None


class TestNormalisation:
    @pytest.mark.parametrize("apostrophe", ["’", "‘", "`", "´"])
    def test_apostrophes_unifiees(self, scraper, apostrophe):
        assert scraper._normalize_string(f"L{apostrophe}empire") == "l'empire"

    def test_casse_et_espaces(self, scraper):
        assert scraper._normalize_string("  LE   TITRE  ") == "le titre"

    @pytest.mark.parametrize(
        "titre",
        [
            "Titre (feat. SCH)",
            "Titre (ft. SCH)",
            "Titre [feat. SCH]",
            "Titre feat. SCH",
            "Titre ft. SCH",
        ],
    )
    def test_featurings_retires(self, scraper, titre):
        """Les cinq écritures doivent donner le MÊME titre : sinon le même
        morceau est trouvé ou non selon la façon dont SongBPM l'écrit.
        Régression corrigée le 2026-09-03 (ordre des motifs : les formes
        crochetées passaient après les formes nues, qui laissaient un « [ »)."""
        assert scraper._normalize_title_for_matching(titre) == "Titre"

    def test_titre_court_avec_featuring_crochete(self, scraper):
        """Cas qui ÉCHOUAIT : sur un titre court, le « [ » orphelin empêchait
        même le rattrapage par inclusion (min 4 caractères)."""
        assert scraper._match_track("Ok [feat. SCH]", "Jul", "Ok", "Jul") is True

    def test_parentheses_et_crochets(self, scraper):
        assert scraper._remove_parentheses_and_brackets("Titre (Remix) [Live]") == "Titre"

    def test_cle_de_titre(self, scraper):
        """La clé rapproche des écritures très différentes du même morceau —
        c'est elle qui permet « Booska Pogo » ↔ « FREESTYLE BOOSKA-POGO »."""
        assert scraper._title_key("FREESTYLE BOOSKA-POGO") == "freestyle booska pogo"
        assert scraper._title_key("Booska Pogo") == "booska pogo"

    def test_cle_ponctuation_aplatie(self, scraper):
        assert scraper._title_key("A.B,C:D!E?F") == "a b c d e f"


class TestRapprochement:
    def _match(self, scraper, **kw):
        base = {
            "result_title": "Bande organisée",
            "result_artist": "Jul",
            "search_title": "Bande organisée",
            "search_artist": "Jul",
        }
        base.update(kw)
        return scraper._match_track(**base)

    def test_spotify_id_fait_foi(self, scraper):
        """Quand les deux côtés ont un ID, il tranche seul — titre et artiste
        ne sont même pas consultés."""
        assert (
            self._match(
                scraper,
                result_title="Rien à voir",
                result_artist="Quelqu'un d'autre",
                result_spotify_id="SP1",
                search_spotify_id="SP1",
            )
            is True
        )

    def test_spotify_ids_differents_rejettent(self, scraper):
        assert self._match(scraper, result_spotify_id="SP1", search_spotify_id="SP2") is False

    def test_artiste_est_une_ancre_stricte(self, scraper):
        """Le titre peut varier, l'artiste non : c'est ce qui évite d'attribuer
        le BPM d'une reprise homonyme."""
        assert self._match(scraper, result_artist="Ninho") is False

    def test_titre_exact(self, scraper):
        assert self._match(scraper) is True

    def test_titre_par_inclusion(self, scraper):
        assert (
            self._match(scraper, result_title="FREESTYLE BOOSKA-POGO", search_title="Booska Pogo")
            is True
        )

    def test_inclusion_trop_courte_refusee(self, scraper):
        """Garde-fou de longueur : « ok » ⊂ presque tout."""
        assert self._match(scraper, result_title="Ok la vie", search_title="Ok") is False

    def test_titre_par_similarite(self, scraper):
        assert self._match(scraper, result_title="Bande organisee") is True

    def test_titre_trop_different(self, scraper):
        assert self._match(scraper, result_title="Absolument autre chose") is False

    def test_titre_vide_apres_normalisation(self, scraper):
        """Un titre tout-symbole ne prouve rien : on refuse plutôt que de
        matcher deux chaînes vides."""
        assert self._match(scraper, result_title="(feat. X)", search_title="Titre") is False

    def test_featuring_ignore_dans_la_comparaison(self, scraper):
        assert self._match(scraper, result_title="Bande organisée (feat. SCH)") is True

    def test_une_autre_partie_refusee(self, scraper):
        """Par inclusion, « Solo Dolo, Pt. IV » prenait le BPM, la tonalité et
        la durée de « Solo Dolo (Nightmare) » — le rejeu du brut (2026-10-25)."""
        assert (
            self._match(
                scraper,
                result_title="Solo Dolo (Nightmare)",
                result_artist="Kid Cudi",
                search_title="Solo Dolo, Pt. IV",
                search_artist="Kid Cudi",
            )
            is False
        )

    def test_la_meme_partie_autrement_ecrite_retenue(self, scraper):
        assert (
            self._match(
                scraper,
                result_title="Solo Dolo Part III",
                search_title="Solo Dolo, Pt. III",
            )
            is True
        )


# ────────────────────────────────────────────────── page de détail (texte)


class TestDetailsDepuisLeTexte:
    def test_mode_phrase_complete(self, scraper):
        texte = "This song has a F key and a major mode."
        assert scraper._details_from_text(texte)["mode"] == "major"

    def test_mode_forme_courte(self, scraper):
        assert scraper._details_from_text("It has a minor mode")["mode"] == "minor"

    @pytest.mark.parametrize("cle", ["C", "G", "F#", "Bb", "C#/Db", "C♯/D♭"])
    def test_tonalite(self, scraper, cle):
        """Toutes les formes (2026-10-12) : l'ancien motif exigeait une
        altération et refusait la double notation — « C », « G », « C#/Db »
        partaient au LLM, pour un champ que rien ne relit."""
        texte = f"runs 3 minutes long with a {cle} key and a minor mode"
        assert scraper._details_from_text(texte)["key_from_paragraph"] == cle

    def test_aucun_appel_au_llm(self, scraper, monkeypatch):
        """Le repli LLM est retiré : un texte muet reste muet."""
        assert not hasattr(scraper, "_extract_details_with_llm")
        assert scraper._details_from_text("rien d'exploitable ici") == {}

    def test_signature_rythmique(self, scraper):
        texte = "a time signature of 4 beats per bar"
        assert scraper._details_from_text(texte)["time_signature"] == 4

    def test_texte_muet(self, scraper):
        assert scraper._details_from_text("rien d'exploitable ici") == {}


class TestParseurDeResultats:
    def _resultats(self, scraper_async, conteneurs):
        scraper_async.page = _Page(conteneurs)
        return asyncio.run(scraper_async._get_search_results_async())

    def test_resultat_complet(self, scraper_async):
        res = self._resultats(scraper_async, [_resultat()])
        assert len(res) == 1
        r = res[0]
        assert r["artist"] == "Jul"
        assert r["title"] == "Bande organisée"
        assert r["bpm"] == 140
        assert r["key"] == "F"
        assert r["duration"] == "3:34"
        assert r["detail_url"].endswith("/bande-organisee")

    def test_identifiant_spotify_capte(self, scraper_async):
        url = "https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT"
        res = self._resultats(scraper_async, [_resultat(spotify_url=url)])
        assert res[0]["spotify_id"] == "4cOdK2wGLETKBW3PvgPWqT"
        assert res[0]["spotify_url"] == url

    def test_liens_de_plateformes_ecartes(self, scraper_async):
        """La page mêle des liens vers Apple Music / Spotify aux vrais morceaux."""
        for plateforme in ("/apple-music", "/spotify", "/amazon", "/youtube"):
            href = f"https://songbpm.com/@jul/titre{plateforme}"
            assert self._resultats(scraper_async, [_resultat(href=href)]) == []

    def test_lien_trop_court_ecarte(self, scraper_async):
        assert self._resultats(scraper_async, [_resultat(href="/@jul")]) == []

    def test_conteneur_sans_lien(self, scraper_async):
        assert self._resultats(scraper_async, [_Element()]) == []

    def test_bpm_illisible_ignore(self, scraper_async):
        """Un BPM non numérique est sauté, le reste du résultat survit."""
        res = self._resultats(scraper_async, [_resultat(metriques=(("BPM", "n/a"), ("KEY", "F")))])
        assert "bpm" not in res[0]
        assert res[0]["key"] == "F"

    def test_titre_ou_artiste_manquant(self, scraper_async):
        """Sans les deux, le résultat n'est pas exploitable."""
        lien = _Element(
            attrs={"href": "https://songbpm.com/@jul/titre"}, enfants={"div.flex-1": []}
        )
        conteneur = _Element(enfants={"a[href*='/@']": [lien]})
        assert self._resultats(scraper_async, [conteneur]) == []

    def test_classes_inattendues_ignorees(self, scraper_async):
        """Le parseur s'appuie sur les classes Tailwind : si elles changent, on
        n'extrait rien plutôt que d'inventer un couple artiste/titre."""
        infos = _Element(
            enfants={
                "p": [
                    _Element(attrs={"class": "autre-classe"}, texte="Jul"),
                    _Element(attrs={"class": "encore-autre"}, texte="Titre"),
                ]
            }
        )
        lien = _Element(
            attrs={"href": "https://songbpm.com/@jul/titre"},
            enfants={"div.flex-1": [infos]},
        )
        assert self._resultats(scraper_async, [_Element(enfants={"a[href*='/@']": [lien]})]) == []

    def test_plusieurs_resultats(self, scraper_async):
        res = self._resultats(scraper_async, [_resultat(), _resultat(titre="Autre")])
        assert [r["title"] for r in res] == ["Bande organisée", "Autre"]

    def test_page_vide(self, scraper_async):
        assert self._resultats(scraper_async, []) == []

    def test_conteneur_en_erreur_saute(self, scraper_async):
        """Un conteneur qui lève ne doit pas faire perdre les suivants."""

        class _Casse(_Element):
            async def query_selector_all(self, selecteur):
                raise AttributeError("élément détaché")

        res = self._resultats(scraper_async, [_Casse(), _resultat()])
        assert len(res) == 1

    def test_page_en_erreur(self, scraper_async):
        class _PageCasse:
            async def query_selector_all(self, selecteur):
                raise AttributeError("page fermée")

        scraper_async.page = _PageCasse()
        assert asyncio.run(scraper_async._get_search_results_async()) == []


# ───────────── repli « sans parenthèses » : chercher court, juger complet (2026-09-27)


class _Obs:
    def absent(self, *_):
        pass


class TestRepliSansParentheses:
    """« Blues (Live at AK Studios) » était recherché comme « Blues » ET jugé
    comme « Blues » : la règle « même version » voyait deux titres identiques et
    posait le BPM, la tonalité et la durée de l'original sur la version live."""

    def test_la_recherche_juge_avec_le_titre_complet(self, monkeypatch):
        s = SongBPMScraperAsync(headless=True)
        appels = []

        async def perform(**kw):
            appels.append(kw)
            return None

        monkeypatch.setattr(s, "_perform_search_async", perform)
        asyncio.run(
            s._search_track_async_body(_Obs(), "Blues (Live at AK Studios)", "A2H", None, 5, True)
        )
        assert appels[1]["track_title"] == "Blues"
        assert appels[1]["titre_juge"] == "Blues (Live at AK Studios)"

    def test_l_original_n_est_pas_la_version_live(self, scraper):
        assert not scraper._match_track("Blues", "A2H", "Blues (Live at AK Studios)", "A2H")


# ───────────────────────────────────────────── verdict d'usage (observabilité)


class _PageAsync:
    """Page dont la navigation échoue."""

    async def goto(self, *_, **__):
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError

        raise PlaywrightTimeoutError("goto")


async def _rien(*_, **__):
    return None


class TestVerdictDUsage:
    """Avant le 2026-09-29 : un succès n'était jamais déclaré (186
    `indeterminate` le 29/09) et une panne navigateur, avalée par
    `_perform_search`, finissait en `absent` — une panne MUETTE."""

    @pytest.fixture(autouse=True)
    def _propre(self, scraper_async, monkeypatch):
        from src.observability import source_usage as su

        su.reset()
        su.set_sink(None)
        monkeypatch.setattr(scraper_async, "_ensure_driver_async", _rien)
        monkeypatch.setattr(scraper_async, "_reset_browser_on_error_async", _rien)
        yield
        su.reset()
        su.set_sink(None)

    @staticmethod
    def _issues():
        from src.observability import source_usage as su

        return [v.issue for v in su.flush()]

    def test_succes_declare_ok(self, scraper_async, monkeypatch):
        from src.observability.issues import IssueKind

        async def _trouve(**_):
            return {"bpm": 140}

        scraper_async.page = object()
        monkeypatch.setattr(scraper_async, "_perform_search_async", _trouve)
        assert asyncio.run(scraper_async.search_track_async("Titre", "Jul")) == {"bpm": 140}
        assert self._issues() == [IssueKind.OK]

    def test_aucun_resultat_reste_absent(self, scraper_async, monkeypatch):
        from src.observability.issues import IssueKind

        async def _vide(**_):
            return None

        scraper_async.page = object()
        monkeypatch.setattr(scraper_async, "_perform_search_async", _vide)
        assert asyncio.run(scraper_async.search_track_async("Titre", "Jul")) is None
        assert self._issues() == [IssueKind.ABSENT]

    def test_panne_navigateur_n_est_pas_une_absence(self, scraper_async):
        from src.observability.issues import IssueKind

        scraper_async.page = _PageAsync()
        assert asyncio.run(scraper_async.search_track_async("Titre", "Jul")) is None
        (issue,) = self._issues()
        assert issue not in (IssueKind.ABSENT, IssueKind.INDETERMINATE)


class TestBrutGarde:
    """Chantier E : SongBPM se lit par le DOM — la page RENDUE va au brut."""

    def test_la_page_de_detail_est_gardee(self, scraper_async):
        from src.persistence import brut

        html = "<main>with a F key and a minor mode, time signature of 4 beats per bar</main>"

        class _Reponse:
            status = 200
            url = "https://songbpm.com/@jul/bande-organisee"

        class _Principal:
            async def is_visible(self):
                return True

            async def inner_text(self):
                return html

        class _PageDetail:
            url = _Reponse.url

            async def goto(self, *a, **k):
                return _Reponse()

            async def query_selector(self, selecteur):
                return _Principal() if selecteur == "main" else None

            async def content(self):
                return html

        scraper_async.page = _PageDetail()
        details = asyncio.run(scraper_async._extract_track_details_async("/@jul/bande-organisee"))
        assert details["mode"] == "minor"
        (lecture,) = brut.lectures("songbpm", _Reponse.url)
        assert lecture["statut"] == 200 and lecture["corps"] == html.encode("utf-8")


class TestPagesRefusees:
    RESULTATS = [
        {"title": "Dangerous", "artist": "Kid Cudi", "detail_url": "/@kid-cudi/dangerous"},
        {
            "title": "Dangerous",
            "artist": "Kid Cudi",
            "detail_url": "https://songbpm.com/@kid-cudi/dangerous-2?x=1",
        },
    ]

    def test_la_page_refusee_n_est_plus_candidate(self, scraper):
        assert scraper.choisir_resultat(self.RESULTATS, "Dangerous", "Kid Cudi")[0] == 1
        _, retenu = scraper.choisir_resultat(
            self.RESULTATS, "Dangerous", "Kid Cudi", pages_refusees={"/@kid-cudi/dangerous"}
        )
        assert retenu["detail_url"].endswith("dangerous-2?x=1")

    def test_toutes_refusees_rien_n_est_retenu(self, scraper):
        refusees = {"/@kid-cudi/dangerous", "/@kid-cudi/dangerous-2"}
        choix = scraper.choisir_resultat(
            self.RESULTATS, "Dangerous", "Kid Cudi", pages_refusees=refusees
        )
        assert choix is None

    def test_la_recherche_transmet_les_pages(self, scraper_async, monkeypatch):
        class _O:
            def ok(self):
                pass

            def absent(self, *_):
                pass

        vus = []

        async def perform(**kw):
            vus.append(kw)

        monkeypatch.setattr(scraper_async, "_perform_search_async", perform)
        asyncio.run(
            scraper_async._search_track_async_body(
                _O(), "Dangerous", "Kid Cudi", None, 5, True, {"/@a/b"}
            )
        )
        assert [kw["pages_refusees"] for kw in vus] == [{"/@a/b"}]
