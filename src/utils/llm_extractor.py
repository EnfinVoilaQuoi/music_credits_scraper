"""Wrapper Ollama générique pour extraction de données structurées par LLM."""

import json
import re

import ollama

from src.utils.logger import get_logger

logger = get_logger(__name__)

# Limites pour RTX 3050 Ti (4 GB VRAM) avec Llama 3.2 3B (Q4_K_M ~2.2 GB)
_DEFAULT_MAX_INPUT_CHARS = 6000  # ≈ 1500 tokens
_DEFAULT_MAX_TOKENS = 512


class LLMExtractor:
    """Wrapper Ollama réutilisable pour extraire des données structurées en JSON."""

    def __init__(self, model: str = "llama3.2", max_input_chars: int = _DEFAULT_MAX_INPUT_CHARS):
        self.model = model
        self.max_input_chars = max_input_chars

    def extract_json(self, prompt: str, max_tokens: int = _DEFAULT_MAX_TOKENS) -> dict | None:
        """
        Envoie un prompt à Ollama et retourne le JSON parsé, ou None si échec.
        force JSON mode via format="json".
        """
        if len(prompt) > self.max_input_chars:
            prompt = prompt[: self.max_input_chars]
            logger.debug(f"LLMExtractor: prompt tronqué à {self.max_input_chars} chars")

        try:
            response = ollama.chat(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                format="json",
                options={
                    "num_predict": max_tokens,
                    "temperature": 0.0,
                    "top_p": 1.0,
                },
            )
            raw = response.message.content
            logger.debug(f"LLMExtractor: réponse brute ({len(raw)} chars): {raw[:150]}")
            return json.loads(self.clean_json_response(raw))

        except ollama.ResponseError as e:
            logger.error(f"LLMExtractor: Ollama ResponseError — {e}")
            return None
        except json.JSONDecodeError as e:
            logger.warning(f"LLMExtractor: JSON invalide du LLM — {e}")
            return None
        except Exception as e:
            logger.error(f"LLMExtractor: erreur inattendue — {e}")
            return None

    def is_available(self) -> bool:
        """Vérifie qu'Ollama tourne et que le modèle est chargé."""
        try:
            models = ollama.list()
            model_names = [m.model for m in models.models]
            # Accepte "llama3.2" ou "llama3.2:3b", "llama3.2:latest", etc.
            base = self.model.split(":")[0]
            available = any(m.startswith(base) for m in model_names)
            if not available:
                logger.warning(
                    f"LLMExtractor: modèle '{self.model}' non trouvé. "
                    f"Modèles disponibles: {model_names}"
                )
            return available
        except Exception as e:
            logger.warning(f"LLMExtractor: Ollama non accessible — {e}")
            return False

    @staticmethod
    def clean_json_response(raw: str) -> str:
        """Retire les fences markdown ```json ... ``` si le LLM les a ajoutées."""
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\n?", "", cleaned)
            cleaned = re.sub(r"\n?```$", "", cleaned)
        return cleaned.strip()


def build_credits_prompt(credits_text: str) -> str:
    """
    Construit le prompt one-shot pour extraire des crédits musicaux.
    Réutilisable pour n'importe quelle source : Genius, Discogs, AllMusic, etc.

    Format attendu en sortie : {"credits": [{"role": str, "names": [str]}]}
    """
    return f"""Extract music production credits from the text below.
Return ONLY a JSON object with this exact structure:
{{"credits": [{{"role": "role name", "names": ["name1", "name2"]}}]}}

Rules:
- "role" is the credit type exactly as written (Producer, Writer, Mixing Engineer, Label, Distributor, Video Visualizer, Mixed At, etc.)
- "names" is the list of people, companies or studios for that role
- Extract EVERY role/names pair present in the text, do not skip any
- Exclude only: release dates, genres, album titles, streaming numbers, song lyrics, tracklists
- Split names separated by commas, "&" or "and" into separate list entries
- Omit roles that have no names

Example input:
**Producer**
Mike Dean
**Writer**
Kanye West & Mike Dean
**Mixing Engineer**
Andrew Dawson
**Label**
GOOD Music & Def Jam
**Released on**
June 1, 2010

Example output:
{{"credits": [{{"role": "Producer", "names": ["Mike Dean"]}}, {{"role": "Writer", "names": ["Kanye West", "Mike Dean"]}}, {{"role": "Mixing Engineer", "names": ["Andrew Dawson"]}}, {{"role": "Label", "names": ["GOOD Music", "Def Jam"]}}]}}

Now extract credits from:
{credits_text}"""


# ──────────────────────────────────────────────────────────────────────────────
# Instance partagée (évite de re-vérifier Ollama à chaque scraper)
# ──────────────────────────────────────────────────────────────────────────────

_shared_extractor: LLMExtractor | None = None
_shared_available: bool | None = None


def get_shared_extractor(model: str = "llama3.2") -> LLMExtractor | None:
    """
    Retourne l'instance LLMExtractor partagée, ou None si Ollama/le modèle
    est indisponible. La disponibilité n'est vérifiée qu'une seule fois.
    """
    global _shared_extractor, _shared_available
    if _shared_available is None:
        _shared_extractor = LLMExtractor(model=model)
        _shared_available = _shared_extractor.is_available()
        if not _shared_available:
            logger.warning("LLMExtractor partagé indisponible — les fallbacks LLM seront ignorés")
    return _shared_extractor if _shared_available else None


# ──────────────────────────────────────────────────────────────────────────────
# Prompts spécialisés par scraper (fallbacks quand le parsing classique échoue)
# ──────────────────────────────────────────────────────────────────────────────


def build_songbpm_prompt(page_text: str) -> str:
    """Prompt pour extraire BPM/key/mode/signature depuis le texte d'une page SongBPM."""
    return f"""Extract song metadata from the text below (from songbpm.com).
Return ONLY a JSON object with this exact structure:
{{"bpm": number or null, "key": "string or null", "mode": "major" or "minor" or null, "time_signature": number or null, "duration_seconds": number or null}}

Rules:
- "bpm" is the tempo in beats per minute (a number, usually 60-200)
- "key" is the musical key (like "C", "F#", "Bb")
- "mode" is "major" or "minor"
- "time_signature" is beats per bar (usually 3 or 4)
- "duration_seconds" is the track duration converted to seconds (e.g. "3:25" -> 205)
- Use null for any value not present in the text
- Never invent values

Example input:
Song Title is a song by Artist with a tempo of 140 BPM. It can also be used half-time at 70 BPM.
The track runs 3 minutes and 25 seconds long with a F#/Gb key and a minor mode. It has a time signature of 4 beats per bar.

Example output:
{{"bpm": 140, "key": "F#/Gb", "mode": "minor", "time_signature": 4, "duration_seconds": 205}}

Now extract from:
{page_text}"""


def build_spotify_match_prompt(artist: str, title: str, candidates: list) -> str:
    """
    Prompt pour choisir le meilleur résultat de recherche Spotify.
    candidates : liste de dicts {"index": int, "text": str} (texte brut du résultat)
    Sortie attendue : {"best_index": int ou null}
    """
    lines = "\n".join(f'{c["index"]}. {c["text"]}' for c in candidates)
    return f"""You are matching a song against Spotify search results.
Target song: "{title}" by {artist}

Search results:
{lines}

Return ONLY a JSON object: {{"best_index": N}} where N is the number of the result
that is THE SAME song by THE SAME artist (ignore case, accents, "feat." suffixes).
If none of the results match, return {{"best_index": null}}.
Never pick a remix, cover or different song."""


def build_streams_table_prompt(table_text: str) -> str:
    """Prompt pour extraire des lignes titre/streams depuis un tableau kworb en texte."""
    return f"""Extract song streaming data from the table text below (from kworb.net).
Return ONLY a JSON object with this exact structure:
{{"tracks": [{{"title": "song title", "streams": total_number, "daily": daily_number_or_null}}]}}

Rules:
- "streams" is the TOTAL stream count (the larger number), digits only, no commas
- "daily" is the daily stream count if present, else null
- Copy titles exactly as written, without the artist prefix
- Only include rows that clearly contain a song title and a stream count
- Never invent or round numbers

Now extract from:
{table_text}"""


def build_certifications_prompt(page_text: str, source: str = "certification") -> str:
    """Prompt pour extraire des certifications (RIAA, BRMA, ...) depuis du texte."""
    return f"""Extract music certification entries from the text below (from a {source} database).
Return ONLY a JSON object with this exact structure:
{{"certifications": [{{"artist": "name", "title": "song or album title", "certification": "Gold/Platinum/Diamond/etc", "date": "YYYY-MM-DD or null"}}]}}

Rules:
- One entry per certified song or album
- "certification" is the award level exactly as written (Gold, Platinum, 2x Platinum, Diamond, Or, Platine...)
- "date" is the certification date in YYYY-MM-DD format, or null if not present
- Never invent entries or dates

Now extract from:
{page_text}"""
