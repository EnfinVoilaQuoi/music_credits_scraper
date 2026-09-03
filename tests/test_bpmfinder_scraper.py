"""Logique pure de BPM Finder (`bpmfinder_scraper`) — dernier recours BPM/key.

318 statements à 16 %. Le pilotage Playwright (login, upload, attente) n'est pas
testable hors navigateur, mais tout ce qui DÉCIDE l'est : la lecture des cartes
de résultat, la conversion en dict, le cache par videoId, et surtout
`_note_issue` qui traduit un échec en verdict d'observabilité.

Ce dernier point compte plus qu'il n'y paraît : la règle du projet veut qu'une
vidéo non analysable soit une ABSENCE de donnée (dénominateur), pas une panne de
la source. S'y tromper ferait clignoter BPM Finder en rouge pour des vidéos
restreintes parfaitement normales.
"""

import pytest

from src.scrapers import bpmfinder_scraper as bf
from src.scrapers.bpmfinder_scraper import BPMFinderScraper


@pytest.fixture
def scraper(tmp_path, monkeypatch):
    """Scraper au cache isolé (le défaut écrit dans data/)."""
    monkeypatch.setattr(bf, "_CACHE_FILE", tmp_path / "cache.json")
    return BPMFinderScraper(headless=True)


class TestDisponibilite:
    def test_identifiants_presents(self, monkeypatch):
        monkeypatch.setattr(bf, "BPMFINDER_EMAIL", "a@b.c")
        monkeypatch.setattr(bf, "BPMFINDER_PASSWORD", "secret")
        assert BPMFinderScraper.credentials_or_session_available() is True

    def test_session_persistee_suffit(self, tmp_path, monkeypatch):
        """Une session déjà ouverte dispense des identifiants."""
        session = tmp_path / "session.json"
        session.write_text("{}", encoding="utf-8")
        monkeypatch.setattr(bf, "BPMFINDER_EMAIL", "")
        monkeypatch.setattr(bf, "BPMFINDER_PASSWORD", "")
        monkeypatch.setattr(bf, "BPMFINDER_SESSION_FILE", str(session))
        assert BPMFinderScraper.credentials_or_session_available() is True

    def test_rien_de_disponible(self, tmp_path, monkeypatch):
        monkeypatch.setattr(bf, "BPMFINDER_EMAIL", "")
        monkeypatch.setattr(bf, "BPMFINDER_PASSWORD", "")
        monkeypatch.setattr(bf, "BPMFINDER_SESSION_FILE", str(tmp_path / "absent.json"))
        assert BPMFinderScraper.credentials_or_session_available() is False

    def test_email_sans_mot_de_passe(self, tmp_path, monkeypatch):
        monkeypatch.setattr(bf, "BPMFINDER_EMAIL", "a@b.c")
        monkeypatch.setattr(bf, "BPMFINDER_PASSWORD", "")
        monkeypatch.setattr(bf, "BPMFINDER_SESSION_FILE", str(tmp_path / "absent.json"))
        assert BPMFinderScraper.credentials_or_session_available() is False


class TestCache:
    def test_cache_absent(self, scraper):
        assert scraper.cache == {}

    def test_aller_retour(self, tmp_path, monkeypatch):
        monkeypatch.setattr(bf, "_CACHE_FILE", tmp_path / "cache.json")
        s = BPMFinderScraper()
        s.cache["dQw4w9WgXcQ"] = {"bpm": 113}
        s._save_cache()
        assert BPMFinderScraper().cache == {"dQw4w9WgXcQ": {"bpm": 113}}

    def test_cache_corrompu(self, tmp_path, monkeypatch):
        chemin = tmp_path / "cache.json"
        chemin.write_text("{tronqué", encoding="utf-8")
        monkeypatch.setattr(bf, "_CACHE_FILE", chemin)
        assert BPMFinderScraper().cache == {}

    def test_ecriture_impossible_silencieuse(self, scraper, monkeypatch):
        """Une analyse réussie ne doit pas être perdue parce que le cache
        n'a pas pu s'écrire."""
        monkeypatch.setattr(
            bf.Path, "write_text", lambda *a, **k: (_ for _ in ()).throw(OSError("plein"))
        )
        scraper._save_cache()  # ne lève pas


class TestLectureDesCartes:
    """Le site affiche « Key: F minor … 113 BPM … 4A » ; le regex en fait un tuple."""

    def _carte(self, note="F", mode="minor", bpm="113", camelot="4A"):
        """Ordre RÉEL de la page : Key, puis BPM, puis Camelot (optionnel)."""
        texte = f"Key: {note} {mode} BPM: {bpm}"
        return texte + (f" Camelot: {camelot}" if camelot else "")

    def test_carte_simple(self):
        cartes = BPMFinderScraper._cards_from_text(self._carte())
        assert cartes == {("F", "minor", "113", "4A")}

    @pytest.mark.parametrize("note", ["C", "F#", "Bb", "A♯", "E♭"])
    def test_alterations(self, note):
        cartes = BPMFinderScraper._cards_from_text(self._carte(note=note, mode="major"))
        assert next(iter(cartes))[0] == note

    def test_camelot_optionnel(self):
        """Le groupe Camelot est facultatif : sans lui, la carte reste lisible."""
        cartes = BPMFinderScraper._cards_from_text(self._carte(camelot=None))
        assert next(iter(cartes))[:3] == ("F", "minor", "113")

    def test_texte_intercale_tolere(self):
        """Le motif tolère jusqu'à 80 caractères entre les champs — la page en
        insère (icônes, libellés) et ils changent sans prévenir."""
        texte = "Key: F minor  ·  Detected key  ·  BPM: 113"
        assert BPMFinderScraper._cards_from_text(texte) == {("F", "minor", "113", None)}

    def test_champs_trop_eloignes_non_apparies(self):
        """Au-delà de la fenêtre, deux champs qui se suivent dans la page ne
        sont plus supposés appartenir à la même carte."""
        texte = "Key: F minor" + " x" * 60 + " BPM: 113"
        assert BPMFinderScraper._cards_from_text(texte) == set()

    def test_plusieurs_cartes_dedupliquees(self):
        """Le retour est un SET : la même carte affichée deux fois ne compte
        qu'une fois (c'est ce qui permet de détecter la NOUVELLE carte)."""
        une = self._carte() + " "
        assert len(BPMFinderScraper._cards_from_text(une + une)) == 1

    def test_cartes_distinctes_conservees(self):
        cartes = BPMFinderScraper._cards_from_text(
            self._carte() + " " + self._carte(note="C", mode="major", bpm="90", camelot="8B")
        )
        assert len(cartes) == 2

    def test_texte_sans_carte(self):
        assert BPMFinderScraper._cards_from_text("page en cours de chargement") == set()

    def test_texte_vide(self):
        assert BPMFinderScraper._cards_from_text("") == set()
        assert BPMFinderScraper._cards_from_text(None) == set()


class TestConversionEnResultat:
    def test_carte_complete(self):
        res = BPMFinderScraper._card_to_result(("F", "minor", "113", "4A"), "test")
        assert res["bpm"] == 113
        assert res["key_name"] == "F minor"
        assert res["camelot"] == "4A"
        assert res["mode"] == 0  # mineur
        assert res["key"] == 5  # F

    def test_majeur(self):
        res = BPMFinderScraper._card_to_result(("C", "major", "120", "8B"), "test")
        assert res["mode"] == 1
        assert res["key"] == 0

    def test_camelot_absent(self):
        """Champ optionnel : la chaîne vide devient None, pas ''."""
        res = BPMFinderScraper._card_to_result(("C", "major", "120", ""), "test")
        assert res["camelot"] is None

    def test_tonalite_illisible_signalee_sans_planter(self, caplog):
        """Une note inconnue donne key/mode None — le BPM, lui, reste
        exploitable : mieux vaut un résultat partiel que rien."""
        res = BPMFinderScraper._card_to_result(("H", "lydien", "120", "1A"), "test")
        assert res["bpm"] == 120
        assert res["key"] is None
        assert res["mode"] is None
        assert "non parsée" in caplog.text


class TestIdentifiantVideo:
    @pytest.mark.parametrize(
        "url",
        [
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://youtu.be/dQw4w9WgXcQ",
            "https://music.youtube.com/watch?v=dQw4w9WgXcQ",
        ],
    )
    def test_formats_reconnus(self, url):
        assert BPMFinderScraper._video_id(url) == "dQw4w9WgXcQ"

    def test_lien_invalide(self):
        assert BPMFinderScraper._video_id("https://example.com/x") is None
        assert BPMFinderScraper._video_id("") is None


class _Obs:
    """Observation de test : enregistre le verdict au lieu de l'émettre."""

    def __init__(self):
        self.verdict = None

    def ok(self):
        self.verdict = ("ok", None, None)

    def fail(self, kind, detail=""):
        self.verdict = ("fail", kind, detail)

    def absent(self, detail=""):
        self.verdict = ("absent", None, detail)


class TestVerdictDObservabilite:
    """Traduction d'un échec en verdict — la règle centrale du projet : une
    ABSENCE de donnée n'est pas une panne de la source."""

    def _verdict(self, scraper, resultat=None, raison=None, status=None):
        obs = _Obs()
        scraper.last_failure_reason = raison
        scraper._last_api_error = status
        scraper._note_issue(obs, resultat)
        return obs.verdict

    def test_succes(self, scraper):
        assert self._verdict(scraper, resultat={"bpm": 113})[0] == "ok"

    def test_login_refuse(self, scraper):
        from src.observability.issues import IssueKind

        genre, kind, _ = self._verdict(scraper, raison="login")
        assert (genre, kind) == ("fail", IssueKind.AUTH)

    def test_timeout(self, scraper):
        from src.observability.issues import IssueKind

        genre, kind, _ = self._verdict(scraper, raison="timeout")
        assert (genre, kind) == ("fail", IssueKind.UNREACHABLE)

    def test_backend_en_panne(self, scraper):
        """5xx : le site est réellement en panne → ça compte comme un échec."""
        from src.observability.issues import IssueKind

        genre, kind, detail = self._verdict(scraper, raison="backend", status=503)
        assert (genre, kind) == ("fail", IssueKind.UNREACHABLE)
        assert "503" in detail

    def test_video_non_analysable(self, scraper):
        """4xx : la vidéo est restreinte ou indisponible. C'est une ABSENCE, pas
        une panne — sinon BPM Finder clignoterait en rouge pour des vidéos
        parfaitement normales."""
        genre, _, detail = self._verdict(scraper, raison="backend", status=404)
        assert genre == "absent"
        assert "404" in detail

    def test_statut_inconnu_traite_en_absence(self, scraper):
        assert self._verdict(scraper, raison="backend", status=None)[0] == "absent"

    def test_echec_sans_cause(self, scraper):
        assert self._verdict(scraper)[0] == "absent"
