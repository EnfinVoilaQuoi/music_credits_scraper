"""Rapprochement et parsing du scraper SongBPM (`songbpm_scraper_v2`).

389 statements à 10 %, **23 fonctions sur 23 jamais exécutées** — c'était le plus
gros trou du dépôt. Pourtant l'essentiel n'a besoin ni de navigateur ni de
réseau : `__init__` est PARESSEUX (driver créé au premier usage), les helpers de
matching sont purs, et le parseur de résultats ne fait que descendre un arbre
d'éléments — remplaçable par un faux DOM.

Ce qui se joue ici : `_match_track` décide si un résultat SongBPM est bien NOTRE
morceau. Un faux positif écrit un BPM étranger en base, où plus rien ne le
distingue d'une vraie mesure.
"""

import pytest

from src.scrapers.songbpm_scraper_v2 import SongBPMScraper


@pytest.fixture
def scraper():
    """Scraper réel : l'init ne monte aucun navigateur (driver paresseux)."""
    return SongBPMScraper(headless=True)


# ─────────────────────────────────────────────────────────── faux DOM Playwright


class _Element:
    """Élément minimal : ce que `_get_search_results` appelle réellement."""

    def __init__(self, enfants=None, attrs=None, texte=""):
        self._enfants = enfants or {}
        self._attrs = attrs or {}
        self._texte = texte

    def query_selector_all(self, selecteur):
        return self._enfants.get(selecteur, [])

    def get_attribute(self, nom):
        return self._attrs.get(nom)

    def inner_text(self):
        return self._texte


class _Page:
    def __init__(self, conteneurs):
        self._conteneurs = conteneurs

    def query_selector_all(self, selecteur):
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


# ────────────────────────────────────────────────── page de détail (texte)


@pytest.fixture
def sans_llm(monkeypatch):
    """Neutralise le repli LLM (Ollama n'est pas disponible en test)."""
    monkeypatch.setattr("src.scrapers.songbpm_scraper_v2.get_shared_extractor", lambda: None)


class TestDetailsDepuisLeTexte:
    def test_mode_phrase_complete(self, scraper, sans_llm):
        texte = "This song has a F key and a major mode."
        assert scraper._details_from_text(texte)["mode"] == "major"

    def test_mode_forme_courte(self, scraper, sans_llm):
        assert scraper._details_from_text("It has a minor mode")["mode"] == "minor"

    def test_tonalite(self, scraper, sans_llm):
        """Le motif n'attrape que les tonalités ALTÉRÉES (dièse/bémol/slash)."""
        texte = "with a F# key and stuff"
        assert scraper._details_from_text(texte)["key_from_paragraph"] == "F#"

    def test_signature_rythmique(self, scraper, sans_llm):
        texte = "a time signature of 4 beats per bar"
        assert scraper._details_from_text(texte)["time_signature"] == 4

    def test_texte_muet(self, scraper, sans_llm):
        assert scraper._details_from_text("rien d'exploitable ici") == {}

    def test_llm_complete_les_manques(self, scraper, monkeypatch):
        """Le repli LLM ne comble QUE ce que les regex ont raté."""

        class _Llm:
            def extract_json(self, prompt, max_tokens=None):
                return {"mode": "minor", "key": "Bb", "time_signature": 3}

        monkeypatch.setattr("src.scrapers.songbpm_scraper_v2.get_shared_extractor", lambda: _Llm())
        details = scraper._details_from_text("This song has a major mode.")
        assert details["mode"] == "major"  # trouvé par regex : le LLM ne l'écrase pas
        assert details["key_from_paragraph"] == "Bb"
        assert details["time_signature"] == 3


class TestReplLLM:
    def _scraper_avec_llm(self, monkeypatch, payload):
        class _Llm:
            def extract_json(self, prompt, max_tokens=None):
                return payload

        monkeypatch.setattr("src.scrapers.songbpm_scraper_v2.get_shared_extractor", lambda: _Llm())
        return SongBPMScraper(headless=True)

    def test_valeurs_valides(self, monkeypatch):
        s = self._scraper_avec_llm(monkeypatch, {"mode": "MINOR", "key": "F#", "time_signature": 4})
        assert s._extract_details_with_llm("texte") == {
            "mode": "minor",
            "key_from_paragraph": "F#",
            "time_signature": 4,
        }

    @pytest.mark.parametrize("mode", ["lydien", "", 42, None])
    def test_mode_hallucine_rejete(self, monkeypatch, mode):
        """Un 3B invente : chaque valeur est validée STRICTEMENT avant d'entrer."""
        s = self._scraper_avec_llm(monkeypatch, {"mode": mode})
        assert "mode" not in s._extract_details_with_llm("texte")

    @pytest.mark.parametrize("key", ["H", "Do dièse", "F##", 7])
    def test_tonalite_hallucinee_rejetee(self, monkeypatch, key):
        s = self._scraper_avec_llm(monkeypatch, {"key": key})
        assert "key_from_paragraph" not in s._extract_details_with_llm("texte")

    @pytest.mark.parametrize("key", ["C", "F#", "Bb", "C/D"])
    def test_tonalites_acceptees(self, monkeypatch, key):
        s = self._scraper_avec_llm(monkeypatch, {"key": key})
        assert s._extract_details_with_llm("texte")["key_from_paragraph"] == key

    @pytest.mark.parametrize("ts", [1, 13, "4", 4.0])
    def test_signature_hors_bornes_rejetee(self, monkeypatch, ts):
        s = self._scraper_avec_llm(monkeypatch, {"time_signature": ts})
        assert "time_signature" not in s._extract_details_with_llm("texte")

    def test_llm_indisponible(self, scraper, sans_llm):
        assert scraper._extract_details_with_llm("texte") == {}

    def test_texte_vide(self, monkeypatch):
        s = self._scraper_avec_llm(monkeypatch, {"mode": "minor"})
        assert s._extract_details_with_llm("") == {}

    def test_llm_muet(self, monkeypatch):
        s = self._scraper_avec_llm(monkeypatch, None)
        assert s._extract_details_with_llm("texte") == {}


# ───────────────────────────────────────────────── parseur de la page de résultats


class TestParseurDeResultats:
    def _resultats(self, scraper, conteneurs):
        scraper.page = _Page(conteneurs)
        return scraper._get_search_results()

    def test_resultat_complet(self, scraper):
        res = self._resultats(scraper, [_resultat()])
        assert len(res) == 1
        r = res[0]
        assert r["artist"] == "Jul"
        assert r["title"] == "Bande organisée"
        assert r["bpm"] == 140
        assert r["key"] == "F"
        assert r["duration"] == "3:34"
        assert r["detail_url"].endswith("/bande-organisee")

    def test_identifiant_spotify_capte(self, scraper):
        url = "https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT"
        res = self._resultats(scraper, [_resultat(spotify_url=url)])
        assert res[0]["spotify_id"] == "4cOdK2wGLETKBW3PvgPWqT"
        assert res[0]["spotify_url"] == url

    def test_liens_de_plateformes_ecartes(self, scraper):
        """La page mêle des liens vers Apple Music / Spotify aux vrais morceaux."""
        for plateforme in ("/apple-music", "/spotify", "/amazon", "/youtube"):
            href = f"https://songbpm.com/@jul/titre{plateforme}"
            assert self._resultats(scraper, [_resultat(href=href)]) == []

    def test_lien_trop_court_ecarte(self, scraper):
        assert self._resultats(scraper, [_resultat(href="/@jul")]) == []

    def test_conteneur_sans_lien(self, scraper):
        assert self._resultats(scraper, [_Element()]) == []

    def test_bpm_illisible_ignore(self, scraper):
        """Un BPM non numérique est sauté, le reste du résultat survit."""
        res = self._resultats(scraper, [_resultat(metriques=(("BPM", "n/a"), ("KEY", "F")))])
        assert "bpm" not in res[0]
        assert res[0]["key"] == "F"

    def test_titre_ou_artiste_manquant(self, scraper):
        """Sans les deux, le résultat n'est pas exploitable."""
        lien = _Element(
            attrs={"href": "https://songbpm.com/@jul/titre"}, enfants={"div.flex-1": []}
        )
        conteneur = _Element(enfants={"a[href*='/@']": [lien]})
        assert self._resultats(scraper, [conteneur]) == []

    def test_classes_inattendues_ignorees(self, scraper):
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
        assert self._resultats(scraper, [_Element(enfants={"a[href*='/@']": [lien]})]) == []

    def test_plusieurs_resultats(self, scraper):
        res = self._resultats(scraper, [_resultat(), _resultat(titre="Autre")])
        assert [r["title"] for r in res] == ["Bande organisée", "Autre"]

    def test_page_vide(self, scraper):
        assert self._resultats(scraper, []) == []

    def test_conteneur_en_erreur_saute(self, scraper):
        """Un conteneur qui lève ne doit pas faire perdre les suivants."""

        class _Casse(_Element):
            def query_selector_all(self, selecteur):
                raise AttributeError("élément détaché")

        res = self._resultats(scraper, [_Casse(), _resultat()])
        assert len(res) == 1

    def test_page_en_erreur(self, scraper):
        class _PageCasse:
            def query_selector_all(self, selecteur):
                raise AttributeError("page fermée")

        scraper.page = _PageCasse()
        assert scraper._get_search_results() == []
