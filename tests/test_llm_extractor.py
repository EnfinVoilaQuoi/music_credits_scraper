"""Extraction LLM locale (`llm_extractor`) — Ollama + llama3.2 3B.

À 32 % de couverture. Le modèle tourne sur une RTX 3050 Ti (4 Go de VRAM) : le
budget d'entrée est plafonné à 6 000 caractères et la sortie à 512 tokens. Ces
plafonds ne sont pas cosmétiques — les dépasser fait déborder la VRAM.

Ollama est remplacé par un faux module : aucun test ne dépend d'un serveur local
ni d'un modèle chargé.
"""

import json

import pytest

from src.utils import llm_extractor as le
from src.utils.llm_extractor import (
    LLMExtractor,
    build_certifications_prompt,
    build_credits_prompt,
    build_songbpm_prompt,
    build_spotify_match_prompt,
    build_streams_table_prompt,
)


class _Reponse:
    def __init__(self, contenu):
        self.message = type("M", (), {"content": contenu})()


class _FauxOllama:
    """Remplace le module `ollama` : enregistre les appels, sert des réponses."""

    # Le vrai module expose les DEUX ; le faux doit les refléter, sans quoi un
    # `except ollama.RequestError` lève AttributeError au lieu de rattraper.
    RequestError = type("RequestError", (Exception,), {})
    ResponseError = type("ResponseError", (Exception,), {})

    def __init__(self, contenu="{}", modeles=("llama3.2:3b",), leve=None):
        self._contenu = contenu
        self._modeles = modeles
        self._leve = leve
        self.appels = []

    def chat(self, **kwargs):
        self.appels.append(kwargs)
        if self._leve is not None:
            raise self._leve
        return _Reponse(self._contenu)

    def list(self):
        if isinstance(self._modeles, Exception):
            raise self._modeles
        modeles = [type("M", (), {"model": m})() for m in self._modeles]
        return type("L", (), {"models": modeles})()


@pytest.fixture
def ollama(monkeypatch):
    """Installe le faux Ollama et rend une fabrique de configuration."""
    etat = {}

    def _poser(**kw):
        faux = _FauxOllama(**kw)
        monkeypatch.setattr(le, "ollama", faux)
        etat["faux"] = faux
        return faux

    _poser()
    return _poser


class TestExtractionJson:
    def test_json_valide(self, ollama):
        ollama(contenu='{"mode": "minor", "key": "F#"}')
        assert LLMExtractor().extract_json("prompt") == {"mode": "minor", "key": "F#"}

    def test_mode_json_force(self, ollama):
        """`format="json"` évite les préambules bavards d'un petit modèle."""
        faux = ollama(contenu="{}")
        LLMExtractor().extract_json("prompt")
        assert faux.appels[0]["format"] == "json"

    def test_temperature_nulle(self, ollama):
        """Déterminisme : la même page doit donner la même extraction."""
        faux = ollama(contenu="{}")
        LLMExtractor().extract_json("prompt")
        assert faux.appels[0]["options"]["temperature"] == 0.0

    def test_budget_de_sortie(self, ollama):
        faux = ollama(contenu="{}")
        LLMExtractor().extract_json("prompt", max_tokens=128)
        assert faux.appels[0]["options"]["num_predict"] == 128

    def test_prompt_tronque_au_budget(self, ollama):
        """Contrainte matérielle (4 Go de VRAM) : le prompt est COUPÉ, jamais
        envoyé en entier."""
        faux = ollama(contenu="{}")
        LLMExtractor(max_input_chars=100).extract_json("x" * 5000)
        assert len(faux.appels[0]["messages"][0]["content"]) == 100

    def test_prompt_court_intact(self, ollama):
        faux = ollama(contenu="{}")
        LLMExtractor(max_input_chars=100).extract_json("court")
        assert faux.appels[0]["messages"][0]["content"] == "court"

    def test_json_invalide(self, ollama):
        """Un 3B produit parfois du JSON cassé : on rend None, on ne lève pas."""
        ollama(contenu="{ceci n'est pas du JSON")
        assert LLMExtractor().extract_json("prompt") is None

    def test_ollama_en_erreur(self, ollama):
        faux = _FauxOllama()
        ollama(leve=_FauxOllama.ResponseError("modèle absent"))
        assert LLMExtractor().extract_json("prompt") is None
        assert faux is not None

    def test_erreur_inattendue(self, ollama):
        """Le serveur local peut être éteint en plein enrichissement : c'est un
        repli, jamais une interruption."""
        ollama(leve=OSError("connexion refusée"))
        assert LLMExtractor().extract_json("prompt") is None


class TestNettoyageDeReponse:
    def test_fences_markdown_retirees(self):
        assert LLMExtractor.clean_json_response('```json\n{"a": 1}\n```') == '{"a": 1}'

    def test_fences_sans_langage(self):
        assert LLMExtractor.clean_json_response('```\n{"a": 1}\n```') == '{"a": 1}'

    def test_json_nu_inchange(self):
        assert LLMExtractor.clean_json_response('{"a": 1}') == '{"a": 1}'

    def test_espaces_retires(self):
        assert LLMExtractor.clean_json_response('  {"a": 1}  ') == '{"a": 1}'

    def test_reponse_avec_fences_parsee_de_bout_en_bout(self, ollama):
        ollama(contenu='```json\n{"mode": "major"}\n```')
        assert LLMExtractor().extract_json("prompt") == {"mode": "major"}


class TestDisponibilite:
    def test_modele_present(self, ollama):
        ollama(modeles=("llama3.2:3b",))
        assert LLMExtractor(model="llama3.2").is_available() is True

    def test_variante_de_tag_acceptee(self, ollama):
        """« llama3.2 » doit reconnaître « llama3.2:latest » comme le même modèle."""
        ollama(modeles=("llama3.2:latest",))
        assert LLMExtractor(model="llama3.2:3b").is_available() is True

    def test_modele_absent(self, ollama, caplog):
        ollama(modeles=("mistral:7b",))
        assert LLMExtractor(model="llama3.2").is_available() is False
        assert "non trouvé" in caplog.text

    def test_ollama_eteint(self, ollama):
        ollama(modeles=OSError("connexion refusée"))
        assert LLMExtractor().is_available() is False


class TestInstancePartagee:
    @pytest.fixture(autouse=True)
    def _reinitialiser(self, monkeypatch):
        """Le singleton est un état module : on l'isole entre les tests."""
        monkeypatch.setattr(le, "_shared_extractor", None)
        monkeypatch.setattr(le, "_shared_available", None)

    def test_instance_reutilisee(self, ollama):
        ollama(modeles=("llama3.2:3b",))
        premier = le.get_shared_extractor()
        assert premier is not None
        assert le.get_shared_extractor() is premier

    def test_disponibilite_verifiee_une_seule_fois(self, ollama):
        """`ollama.list()` est un aller-retour : le refaire à chaque morceau
        coûterait des secondes sur une discographie entière."""
        faux = ollama(modeles=("llama3.2:3b",))
        appels = []
        faux.list = lambda: (appels.append(1), type("L", (), {"models": []})())[1]
        le.get_shared_extractor()
        le.get_shared_extractor()
        le.get_shared_extractor()
        assert len(appels) == 1

    def test_indisponible_rend_none(self, ollama, caplog):
        ollama(modeles=("mistral:7b",))
        assert le.get_shared_extractor() is None
        assert "indisponible" in caplog.text


class TestPrompts:
    """Chaque prompt doit porter sa consigne de format : un 3B sans structure
    imposée renvoie de la prose."""

    def test_credits(self):
        p = build_credits_prompt("Produced by Kore")
        assert "credits" in p
        assert "Kore" in p

    def test_songbpm(self):
        p = build_songbpm_prompt("a major mode")
        assert "a major mode" in p

    def test_certifications(self):
        assert build_certifications_prompt("texte") is not None

    def test_streams(self):
        assert build_streams_table_prompt("texte") is not None

    def test_spotify_match(self):
        """Les candidats sont numérotés : le modèle répond un INDEX, pas un
        titre — impossible d'halluciner un morceau qui n'était pas proposé."""
        p = build_spotify_match_prompt(
            "Jul", "Bande organisée", [{"index": 1, "text": "Jul - Bande organisée"}]
        )
        assert "Jul" in p
        assert "1. Jul - Bande organisée" in p
        assert "best_index" in p

    def test_spotify_match_sans_candidat(self):
        p = build_spotify_match_prompt("Jul", "Titre", [])
        assert "best_index" in p

    @pytest.mark.parametrize(
        "fabrique",
        [
            build_credits_prompt,
            build_songbpm_prompt,
            build_certifications_prompt,
            build_streams_table_prompt,
        ],
    )
    def test_json_exige(self, fabrique):
        assert "JSON" in fabrique("texte quelconque").upper()


def test_extraction_de_bout_en_bout(ollama):
    """Chaîne complète : prompt construit → LLM → JSON nettoyé et parsé."""
    ollama(contenu='```json\n{"credits": [{"role": "Producer", "names": ["Kore"]}]}\n```')
    data = LLMExtractor().extract_json(build_credits_prompt("Produced by Kore"))
    assert data["credits"][0]["names"] == ["Kore"]
    assert json.dumps(data)  # sérialisable, donc exploitable en aval
