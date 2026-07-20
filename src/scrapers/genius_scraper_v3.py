"""
Scraper Genius — VERSION 3 (Crawl4AI + Ollama/Llama 3.2)
Remplace les sélecteurs CSS fragiles de v2 par une extraction via LLM.
"""

import re
import time
from datetime import datetime
from typing import Any

from bs4 import BeautifulSoup

from src.config import DELAY_BETWEEN_REQUESTS
from src.models import Credit, CreditRole, Track
from src.scrapers.crawl4ai_scraper_base import CrawlAIScraperBase
from src.utils.llm_extractor import LLMExtractor, build_credits_prompt
from src.utils.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# JS injecté dans la page Genius pour révéler la section crédits complète
# ---------------------------------------------------------------------------

_JS_EXPAND_CREDITS = """
(function() {
    // Stratégie 1 : heading "Credits" → chercher un bouton Expand dans les siblings
    const allElements = Array.from(document.querySelectorAll('div, h2, h3, h4, span'));
    for (const el of allElements) {
        if (el.textContent.trim() === 'Credits' && el.children.length === 0) {
            let node = el.parentElement;
            for (let i = 0; i < 8; i++) {
                if (!node) break;
                const btn = node.querySelector('button');
                if (btn && btn.textContent.includes('Expand')) {
                    btn.click();
                    return 'clicked_via_heading';
                }
                node = node.nextElementSibling;
            }
        }
    }
    // Stratégie 2 : tout bouton visible contenant "Expand"
    const buttons = Array.from(document.querySelectorAll('button'));
    for (const btn of buttons) {
        if (btn.textContent.includes('Expand') && btn.offsetParent !== null) {
            btn.click();
            return 'clicked_via_text';
        }
    }
    // Stratégie 3 : heuristique par classe
    const expandBtn = document.querySelector(
        '[class*="ExpandableContent"] button, button[class*="ExpandableContent"]'
    );
    if (expandBtn) { expandBtn.click(); return 'clicked_via_class'; }
    return 'not_found';
})();
"""

# Condition JS : retourne true quand la section crédits est peuplée
# (supporte l'ancien DOM SongInfo__ et le nouveau Credit__)
_JS_WAIT_CREDITS = (
    "() => document.querySelectorAll('[class*=\"Credit__Label\"]').length > 1"
    " || document.querySelectorAll('[class*=\"SongInfo__Credit\"]').length > 2"
    " || document.querySelectorAll('[class*=\"SongInfo__Label\"]').length > 1"
)


class GeniusScraperV3(CrawlAIScraperBase):
    """
    Scraper Genius v3 — Crawl4AI pour le rendu + Llama 3.2 pour le parsing.
    API publique identique à GeniusScraper (v2).
    """

    def __init__(self, headless: bool = True):
        super().__init__(headless)
        self._llm = LLMExtractor(model="llama3.2")
        if not self._llm.is_available():
            logger.warning(
                "GeniusScraperV3: Ollama/llama3.2 non disponible — "
                "le fallback BeautifulSoup sera utilisé automatiquement"
            )
        logger.info("GeniusScraperV3 initialisé (Crawl4AI + Ollama)")

    # -------------------------------------------------------------------------
    # API publique
    # -------------------------------------------------------------------------

    def scrape_track_credits(self, track: Track, include_lyrics: bool = True) -> list[Credit]:
        """
        Point d'entrée principal — identique à GeniusScraper.scrape_track_credits().
        Bonus v3 : les paroles structurées sont extraites du MÊME crawl
        (aucune requête supplémentaire) si include_lyrics=True.
        """
        if not track.genius_url:
            logger.warning(f"GeniusScraperV3: pas d'URL Genius pour '{track.title}'")
            return []

        markdown, html = self._crawl_page(
            url=track.genius_url,
            js_before_wait=_JS_EXPAND_CREDITS,
            wait_for="js:" + _JS_WAIT_CREDITS,
            wait_timeout=15_000,
            page_timeout=30_000,
            delay_before_return=1.5,
        )

        # Paroles + anecdotes depuis le même HTML (gratuit)
        if include_lyrics and html:
            try:
                self._apply_lyrics_from_html(html, track)
            except Exception as e:
                logger.warning(f"GeniusScraperV3: erreur extraction paroles '{track.title}': {e}")

        # Nom d'album depuis la page (l'API /artists/songs ne le fournit pas)
        if html and not getattr(track, "album", None):
            try:
                album = self._extract_album_bs4(html)
                if album:
                    track.album = album
                    logger.info(f"💿 Album détecté pour '{track.title}': {album}")
            except Exception as e:
                logger.debug(f"GeniusScraperV3: album introuvable pour '{track.title}': {e}")

        credits: list[Credit] = []

        # 1. Extraction structurée du HTML (déterministe et complète)
        if html:
            credits = self._extract_fallback_bs4(html)

        # 2. Fallback LLM si le parsing HTML n'a rien donné (DOM Genius modifié)
        if not credits and markdown:
            logger.info(
                f"GeniusScraperV3: parsing HTML sans résultat, fallback LLM "
                f"pour '{track.title}'"
            )
            credits = self._extract_with_llm(markdown, track)

        if not credits:
            logger.warning(f"GeniusScraperV3: aucun crédit trouvé pour '{track.title}'")

        if credits:
            # Purger les anciens crédits Genius (évite que des erreurs d'anciens
            # runs — ex: titres de tracklist en Writer — persistent en base)
            before = len(track.credits)
            track.credits = [c for c in track.credits if c.source != "genius"]
            purged = before - len(track.credits)
            if purged:
                logger.info(f"GeniusScraperV3: {purged} ancien(s) crédit(s) Genius purgé(s)")

        for credit in credits:
            track.add_credit(credit)

        logger.info(f"GeniusScraperV3: {len(credits)} crédit(s) pour '{track.title}'")
        time.sleep(DELAY_BETWEEN_REQUESTS)
        return credits

    def scrape_multiple_tracks(self, tracks, progress_callback=None) -> dict:
        """Scrape plusieurs morceaux — même interface que GeniusScraper.scrape_multiple_tracks()."""
        results = {"success": 0, "failed": 0, "errors": [], "albums_scraped": set()}
        total = len(tracks)
        for i, track in enumerate(tracks):
            try:
                logger.info(f"V3: scraping {i+1}/{total}: {track.title}")
                self.scrape_track_credits(track)
                if track.credits:
                    results["success"] += 1
                else:
                    results["failed"] += 1
                    logger.warning(f"V3: aucun crédit pour '{track.title}'")
            except Exception as e:
                results["failed"] += 1
                results["errors"].append({"track": track.title, "error": str(e)})
                logger.error(f"V3: erreur sur '{track.title}': {e}")
            if progress_callback:
                progress_callback(i + 1, total, track.title)
        return results

    def scrape_track_lyrics(self, track: Track) -> str:
        """Scrape uniquement les paroles d'un morceau — même API que GeniusScraper (v2)."""
        if not track.genius_url:
            logger.warning(f"GeniusScraperV3: pas d'URL Genius pour '{track.title}'")
            return ""

        _, html = self._crawl_page(
            url=track.genius_url,
            wait_for="css:[data-lyrics-container='true']",
            wait_timeout=12_000,
            page_timeout=30_000,
            delay_before_return=1.0,
        )
        if not html:
            return ""

        lyrics = self._apply_lyrics_from_html(html, track)
        time.sleep(DELAY_BETWEEN_REQUESTS)
        return lyrics

    def scrape_multiple_tracks_with_lyrics(
        self, tracks: list[Track], progress_callback=None, include_lyrics: bool = True
    ) -> dict[str, Any]:
        """Scrape crédits + paroles — même interface que GeniusScraper (v2)."""
        results = {
            "success": 0,
            "failed": 0,
            "errors": [],
            "albums_scraped": set(),
            "lyrics_scraped": 0,
            "structures_analyzed": 0,
        }
        total = len(tracks)
        for i, track in enumerate(tracks):
            try:
                logger.info(f"V3: scraping {i+1}/{total}: {track.title}")
                self.scrape_track_credits(track, include_lyrics=include_lyrics)
                if track.has_lyrics:
                    results["lyrics_scraped"] += 1
                    results["structures_analyzed"] += 1
                if track.credits:
                    results["success"] += 1
                else:
                    results["failed"] += 1
            except Exception as e:
                results["failed"] += 1
                results["errors"].append({"track": track.title, "error": str(e)})
                logger.error(f"V3: erreur sur '{track.title}': {e}")
            if progress_callback:
                progress_callback(i + 1, total, track.title)
        logger.info(
            f"V3: scraping terminé — {results['success']} réussis, "
            f"{results['lyrics_scraped']} paroles récupérées"
        )
        return results

    def scrape_lyrics_batch(self, tracks: list[Track], progress_callback=None) -> dict[str, Any]:
        """
        Scrape uniquement les paroles — même interface que GeniusScraper (v2).
        Optimisation v3 : les morceaux dont les paroles ont déjà été récupérées
        lors du scrape crédits (même crawl) ne sont pas re-crawlés.
        """
        results = {"success": 0, "failed": 0, "errors": [], "lyrics_scraped": 0}
        total = len(tracks)
        for i, track in enumerate(tracks):
            try:
                if track.has_lyrics and track.lyrics:
                    # Déjà récupérées pendant le scrape crédits — pas de re-crawl
                    results["success"] += 1
                    results["lyrics_scraped"] += 1
                    logger.debug(f"V3: paroles déjà présentes pour '{track.title}' — skip")
                else:
                    lyrics = self.scrape_track_lyrics(track)
                    if lyrics:
                        results["success"] += 1
                        results["lyrics_scraped"] += 1
                    else:
                        track.has_lyrics = False
                        results["failed"] += 1
                        logger.warning(f"V3: aucune parole trouvée pour '{track.title}'")
            except Exception as e:
                results["failed"] += 1
                results["errors"].append({"track": track.title, "error": str(e)})
                logger.error(f"V3: erreur paroles sur '{track.title}': {e}")
            if progress_callback:
                progress_callback(i + 1, total, track.title)
        logger.info(
            f"V3: paroles terminées — {results['lyrics_scraped']} récupérées, "
            f"{results['failed']} échecs"
        )
        return results

    # -------------------------------------------------------------------------
    # Extraction des paroles (structurée, depuis le HTML du crawl crédits)
    # -------------------------------------------------------------------------

    def _apply_lyrics_from_html(self, html: str, track: Track) -> str:
        """Extrait paroles + anecdotes du HTML et les applique au track."""
        soup = BeautifulSoup(html, "html.parser")

        anecdotes = self._extract_anecdotes_bs4(soup)
        if anecdotes:
            track.anecdotes = anecdotes
            logger.info(f"📝 Anecdote extraite ({len(anecdotes)} caractères)")

        lyrics = self._extract_lyrics_bs4(soup)
        if lyrics:
            # Ajoute l'artiste aux en-têtes de section sans attribution (Genius ne
            # le met qu'en cas de feat). Ex. [Couplet 1] → [Couplet 1 : Isha].
            if getattr(track, "is_featuring", False) and getattr(
                track, "primary_artist_name", None
            ):
                artist_name = track.primary_artist_name
            elif track.artist and hasattr(track.artist, "name"):
                artist_name = track.artist.name
            else:
                artist_name = None
            lyrics = self._inject_section_artist(lyrics, artist_name)

            track.lyrics = lyrics
            track.has_lyrics = True
            track.lyrics_scraped_at = datetime.now()
            logger.info(f"✅ Paroles récupérées pour '{track.title}' ({len(lyrics.split())} mots)")
        return lyrics

    @staticmethod
    def _inject_section_artist(lyrics: str, artist_name: str | None) -> str:
        """
        Ajoute ` : <artiste>` aux en-têtes de section `[...]` qui n'ont pas déjà
        d'attribution. N'agit que sur des en-têtes seuls sur leur ligne.
        """
        if not lyrics or not artist_name:
            return lyrics

        def repl(m):
            inside = m.group(1)
            if ":" in inside:  # déjà attribué (feat) → ne pas toucher
                return m.group(0)
            return f"[{inside.rstrip()} : {artist_name}]"

        return re.sub(r"(?m)^\[([^\]\n]+)\]\s*$", repl, lyrics)

    def _extract_lyrics_bs4(self, soup) -> str:
        """
        Extraction structurée des paroles :
          - conteneurs div[data-lyrics-container="true"] uniquement
            (les blocs "You might also like" sont HORS conteneurs → ignorés)
          - header contributeurs retiré via data-exclude-from-selection
          - <br> convertis en sauts de ligne, liens Referent aplatis en texte
        """
        containers = soup.find_all("div", {"data-lyrics-container": "true"})
        if not containers:
            # Fallback si les attributs data-* ont été retirés (HTML nettoyé)
            containers = soup.select("div[class*='Lyrics__Container']")
        if not containers:
            return ""

        parts = []
        for container in containers:
            # Retirer header contributeurs et blocs marqués hors-sélection
            for excluded in container.select("[data-exclude-from-selection='true']"):
                excluded.decompose()
            # Retirer toute pub injectée dans le conteneur
            for ad in container.find_all(
                "div",
                class_=re.compile(r"(InreadAd|SidebarAd|RightSidebar|LyricsHeader|Ad__Container)"),
            ):
                ad.decompose()
            # <br> → sauts de ligne (les liens/annotations restent du texte inline)
            for br in container.find_all("br"):
                br.replace_with("\n")
            text = container.get_text()
            if text.strip():
                parts.append(text.strip("\n"))

        lyrics = "\n\n".join(parts)

        # Nettoyage final
        lyrics = re.sub(r"^You might also like.*$", "", lyrics, flags=re.MULTILINE)
        lyrics = re.sub(r"\n?\d*Embed\s*$", "", lyrics)
        lyrics = re.sub(r"\n{3,}", "\n\n", lyrics)
        return lyrics.strip()

    def _extract_album_bs4(self, html: str) -> str | None:
        """
        Extrait le nom de l'album depuis la page Genius (lien /albums/).
        L'API /artists/{id}/songs ne fournit pas l'album — la page, si.
        """
        soup = BeautifulSoup(html, "html.parser")
        # 1. Zone header/tracklist (la plus fiable)
        for selector in (
            "div[class*='HeaderArtistAndTracklist'] a[href*='/albums/']",
            "div[class*='AlbumTracklist'] a[href*='/albums/']",
            "a[href*='/albums/']",
        ):
            link = soup.select_one(selector)
            if link:
                text = link.get_text(separator=" ", strip=True)
                # Nettoyer les suffixes type "Drôle d'oiseau (2025)"
                text = re.sub(r"\s*\(\d{4}\)\s*$", "", text).strip()
                if text and len(text) < 150:
                    return text
        return None

    def _extract_anecdotes_bs4(self, soup) -> str | None:
        """Extrait la section About/description (mêmes sélecteurs que v2)."""
        bio_selectors = [
            "div[class*='SongDescription__Content']",
            "div[class*='RichText__Container']",
        ]
        for selector in bio_selectors:
            bio = soup.select_one(selector)
            if not bio:
                continue
            for embed in bio.find_all("div", class_=lambda x: x and "embedly" in x):
                embed.decompose()
            text = bio.get_text(separator=" ", strip=True)
            if text and len(text) > 50:
                return re.sub(r"\s+", " ", text).strip()
        return None

    # -------------------------------------------------------------------------
    # Extraction via LLM
    # -------------------------------------------------------------------------

    def _extract_with_llm(self, markdown: str, track: Track) -> list[Credit]:
        """Isole la section crédits du markdown, envoie au LLM, retourne List[Credit]."""
        credits_section = self._extract_credits_section(markdown)
        if not credits_section:
            logger.debug(
                f"GeniusScraperV3: section crédits introuvable dans le markdown "
                f"de '{track.title}'"
            )
            return []

        prompt = build_credits_prompt(credits_section)
        data = self._llm.extract_json(prompt)

        if not data:
            return []

        return self._parse_llm_response(data)

    def _extract_credits_section(self, markdown: str) -> str | None:
        """
        Isole la section Credits du markdown Genius.
        La structure typique après Crawl4AI :
            ## Credits
            **Producer**\\nMike Dean\\n**Writer**\\n...
        """
        # Stratégie 1 : header markdown "Credits"
        patterns = [
            r"(?i)#+\s*credits\b(.*?)(?=\n#+\s|\Z)",
            r"(?i)\*\*credits\*\*(.*?)(?=\n\*\*[A-Z]|\Z)",
        ]
        for pattern in patterns:
            m = re.search(pattern, markdown, re.DOTALL)
            if m:
                section = m.group(0).strip()
                if len(section) > 50:
                    return section

        # Stratégie 2 : densité de mots-clés crédit (quand pas de heading explicite)
        credit_keywords = [
            "Producer",
            "Writer",
            "Engineer",
            "Mixing",
            "Mastering",
            "Executive Producer",
            "Co-Producer",
            "Vocals",
            "Composer",
            "Featuring",
            "Drums",
            "Guitar",
            "Piano",
        ]
        lines = markdown.split("\n")
        keyword_indices = [
            i
            for i, line in enumerate(lines)
            if any(kw.lower() in line.lower() for kw in credit_keywords)
        ]
        if len(keyword_indices) >= 2:
            start = max(0, keyword_indices[0] - 2)
            end = min(len(lines), keyword_indices[-1] + 10)
            return "\n".join(lines[start:end])

        return None

    def _parse_llm_response(self, data: dict) -> list[Credit]:
        """Convertit la réponse JSON du LLM en List[Credit]."""
        credits: list[Credit] = []
        raw_credits = data.get("credits", [])

        if not isinstance(raw_credits, list):
            logger.warning("GeniusScraperV3: format JSON inattendu (credits n'est pas une liste)")
            return []

        for entry in raw_credits:
            if not isinstance(entry, dict):
                continue
            role_str = str(entry.get("role", "")).strip()
            names = entry.get("names", [])
            if not role_str or not isinstance(names, list):
                continue

            role_enum = self._map_genius_role_to_enum(role_str)

            for name in names:
                name = str(name).strip()
                if len(name) < 2:
                    continue
                credits.append(
                    Credit(
                        name=name,
                        role=role_enum,
                        role_detail=role_str if role_enum == CreditRole.OTHER else None,
                        source="genius",
                    )
                )
                logger.debug(f"GeniusScraperV3 LLM: {name} — {role_enum.value}")

        return self._deduplicate_credits(credits)

    # -------------------------------------------------------------------------
    # Fallback BeautifulSoup (logique v2)
    # -------------------------------------------------------------------------

    # Labels Genius qui ne sont pas des crédits de personnes
    _NON_CREDIT_LABELS = ("album", "released on", "release date", "genre", "tags")

    def _extract_fallback_bs4(self, html: str) -> list[Credit]:
        """
        Extraction BeautifulSoup sur le HTML brut.
        Supporte les deux générations du DOM Genius :
          - nouveau : div.Credit__Container > div.Credit__Label + div.Credit__Contributor
          - ancien  : div.SongInfo__Credit > div.SongInfo__Label + sibling
        """
        credits: list[Credit] = []
        try:
            soup = BeautifulSoup(html, "html.parser")

            # ── Nouveau DOM Genius (Credit__Container) ────────────────────────
            for container in soup.select("div[class*='Credit__Container']"):
                label_div = container.find("div", class_=re.compile(r"Credit__Label"))
                contributor_div = container.find("div", class_=re.compile(r"Credit__Contributor"))
                if not label_div or not contributor_div:
                    continue
                role_text = label_div.get_text(strip=True)
                if role_text.lower() in self._NON_CREDIT_LABELS:
                    continue
                names = self._extract_names_intelligently(contributor_div)
                self._append_credits(credits, role_text, names)

            # ── Ancien DOM Genius (SongInfo__Credit) ──────────────────────────
            if not credits:
                for credit_element in soup.select("div[class*='SongInfo__Credit']"):
                    label_div = credit_element.find("div", class_=re.compile(r"SongInfo__Label"))
                    if not label_div:
                        continue
                    role_text = label_div.get_text(strip=True)
                    if role_text.lower() in self._NON_CREDIT_LABELS:
                        continue
                    container_div = label_div.find_next_sibling("div")
                    if not container_div:
                        continue
                    names = self._extract_names_intelligently(container_div)
                    self._append_credits(credits, role_text, names)
        except Exception as e:
            logger.error(f"GeniusScraperV3: erreur extraction BeautifulSoup: {e}")

        return self._deduplicate_credits(credits)

    def _append_credits(self, credits: list[Credit], role_text: str, names: list[str]) -> None:
        """Ajoute un Credit par nom pour un rôle donné."""
        role_enum = self._map_genius_role_to_enum(role_text)
        for name in names:
            name = name.strip()
            if name:
                credits.append(
                    Credit(
                        name=name,
                        role=role_enum,
                        role_detail=role_text if role_enum == CreditRole.OTHER else None,
                        source="genius",
                    )
                )

    # -------------------------------------------------------------------------
    # Utilitaires (copiés depuis genius_scraper_v2.py — découplage volontaire)
    # -------------------------------------------------------------------------

    def _extract_names_intelligently(self, container_div) -> list[str]:
        names = []
        try:
            for link in container_div.select("a"):
                name = link.get_text(strip=True)
                if name and name not in names:
                    names.append(name)

            for text_node in container_div.find_all(string=True, recursive=False):
                text = text_node.strip()
                if text and text not in names:
                    for separator in [" & ", ", ", " and ", " + ", " / "]:
                        if separator in text:
                            parts = text.split(separator)
                            for part in parts:
                                clean_part = part.strip()
                                if clean_part and clean_part not in names:
                                    names.append(clean_part)
                            break
                    else:
                        if text not in names:
                            names.append(text)

            return [n.replace("&amp;", "&").strip() for n in names if n and len(n) > 1]
        except Exception as e:
            logger.debug(f"Erreur extraction noms: {e}")
            return []

    def _map_genius_role_to_enum(self, genius_role: str) -> CreditRole:
        role_mapping = {
            "Producer": CreditRole.PRODUCER,
            "Co-Producer": CreditRole.CO_PRODUCER,
            "Executive Producer": CreditRole.EXECUTIVE_PRODUCER,
            "Vocal Producer": CreditRole.VOCAL_PRODUCER,
            "Additional Production": CreditRole.ADDITIONAL_PRODUCTION,
            "Writer": CreditRole.WRITER,
            "Writers": CreditRole.WRITER,
            "Songwriter": CreditRole.WRITER,
            "Songwriters": CreditRole.WRITER,
            "Composer": CreditRole.COMPOSER,
            "Composers": CreditRole.COMPOSER,
            "Lyricist": CreditRole.LYRICIST,
            "Lyricists": CreditRole.LYRICIST,
            "Arranger": CreditRole.ARRANGER,
            "Arrangers": CreditRole.ARRANGER,
            "Producers": CreditRole.PRODUCER,
            "Mixing Engineer": CreditRole.MIXING_ENGINEER,
            "Mix Engineer": CreditRole.MIXING_ENGINEER,
            "Mastering Engineer": CreditRole.MASTERING_ENGINEER,
            "Recording Engineer": CreditRole.RECORDING_ENGINEER,
            "Engineer": CreditRole.ENGINEER,
            "Vocals": CreditRole.VOCALS,
            "Lead Vocals": CreditRole.LEAD_VOCALS,
            "Background Vocals": CreditRole.BACKGROUND_VOCALS,
            "Additional Vocals": CreditRole.ADDITIONAL_VOCALS,
            "Choir": CreditRole.CHOIR,
            "Label": CreditRole.LABEL,
            "Publisher": CreditRole.PUBLISHER,
            "Distributor": CreditRole.DISTRIBUTOR,
            "Guitar": CreditRole.GUITAR,
            "Bass Guitar": CreditRole.BASS_GUITAR,
            "Acoustic Guitar": CreditRole.ACOUSTIC_GUITAR,
            "Electric Guitar": CreditRole.ELECTRIC_GUITAR,
            "Drums": CreditRole.DRUMS,
            "Piano": CreditRole.PIANO,
            "Keyboard": CreditRole.KEYBOARD,
            "Synthesizer": CreditRole.SYNTHESIZER,
            "Bass": CreditRole.BASS,
            "Art Direction": CreditRole.ART_DIRECTION,
            "Artwork": CreditRole.ARTWORK,
            "Graphic Design": CreditRole.GRAPHIC_DESIGN,
            "Photography": CreditRole.PHOTOGRAPHY,
            "Illustration": CreditRole.ILLUSTRATION,
            "Video Director": CreditRole.VIDEO_DIRECTOR,
            "Video Producer": CreditRole.VIDEO_PRODUCER,
            "Video Director of Photography": CreditRole.VIDEO_DIRECTOR_OF_PHOTOGRAPHY,
            "Video Cinematographer": CreditRole.VIDEO_CINEMATOGRAPHER,
            "Video Digital Imaging Technician": CreditRole.VIDEO_DIGITAL_IMAGING_TECHNICIAN,
            "Video Camera Operator": CreditRole.VIDEO_CAMERA_OPERATOR,
            "Video Drone Operator": CreditRole.VIDEO_DRONE_OPERATOR,
            "Video Set Decorator": CreditRole.VIDEO_SET_DECORATOR,
            "Video Editor": CreditRole.VIDEO_EDITOR,
            "Video Colorist": CreditRole.VIDEO_COLORIST,
            "Featuring": CreditRole.FEATURED,
            "Sample": CreditRole.SAMPLE,
            "A&R": CreditRole.A_AND_R,
        }

        if genius_role in role_mapping:
            return role_mapping[genius_role]

        lower = genius_role.lower()
        for key, value in role_mapping.items():
            if key.lower() == lower:
                return value

        # Rôles vidéo : traités AVANT les règles floues pour que
        # "Video Line Producer" ne devienne pas un Producer musical
        if "video" in lower:
            if "director" in lower and "photography" in lower:
                return CreditRole.VIDEO_DIRECTOR_OF_PHOTOGRAPHY
            if "director" in lower:
                return CreditRole.VIDEO_DIRECTOR
            if "producer" in lower:
                return CreditRole.VIDEO_PRODUCER
            if "cinematographer" in lower:
                return CreditRole.VIDEO_CINEMATOGRAPHER
            if "camera" in lower:
                return CreditRole.VIDEO_CAMERA_OPERATOR
            if "drone" in lower:
                return CreditRole.VIDEO_DRONE_OPERATOR
            if "editor" in lower:
                return CreditRole.VIDEO_EDITOR
            if "colorist" in lower:
                return CreditRole.VIDEO_COLORIST
            if "set decorator" in lower:
                return CreditRole.VIDEO_SET_DECORATOR
            return CreditRole.OTHER

        if "producer" in lower:
            if "co" in lower:
                return CreditRole.CO_PRODUCER
            if "executive" in lower:
                return CreditRole.EXECUTIVE_PRODUCER
            if "vocal" in lower:
                return CreditRole.VOCAL_PRODUCER
            return CreditRole.PRODUCER

        if "engineer" in lower:
            if "mix" in lower:
                return CreditRole.MIXING_ENGINEER
            if "master" in lower:
                return CreditRole.MASTERING_ENGINEER
            if "record" in lower:
                return CreditRole.RECORDING_ENGINEER
            return CreditRole.ENGINEER

        if "vocal" in lower:
            if "lead" in lower:
                return CreditRole.LEAD_VOCALS
            if "background" in lower or "backing" in lower:
                return CreditRole.BACKGROUND_VOCALS
            if "additional" in lower:
                return CreditRole.ADDITIONAL_VOCALS
            return CreditRole.VOCALS

        if "guitar" in lower:
            if "bass" in lower:
                return CreditRole.BASS_GUITAR
            if "acoustic" in lower:
                return CreditRole.ACOUSTIC_GUITAR
            if "electric" in lower:
                return CreditRole.ELECTRIC_GUITAR
            return CreditRole.GUITAR

        return CreditRole.OTHER

    def _deduplicate_credits(self, credits: list[Credit]) -> list[Credit]:
        """Supprime les doublons de crédits"""
        seen = set()
        unique_credits = []
        for credit in credits:
            key = (credit.name.lower().strip(), credit.role.value)
            if key not in seen:
                seen.add(key)
                unique_credits.append(credit)
            else:
                logger.debug(f"Doublon ignoré: {credit.name} - {credit.role.value}")
        return unique_credits
