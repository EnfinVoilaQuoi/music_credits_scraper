"""Charger ou ajouter un artiste — sans widget.

Extrait de `MainWindow._search_artist` (2026-09-14). Le cas AMBIGU (slug Genius
introuvable) n'est PAS tranché ici : `ArtisteAmbigu` remonte les candidats, la
GUI ouvre son dialog, la CLI les liste et attend `--genius-id`.
"""

from __future__ import annotations

from playwright.sync_api import Error as PlaywrightError

from src.models import Artist
from src.scrapers.playwright_manager import get_playwright
from src.services.runtime import Runtime
from src.utils.logger import get_logger

logger = get_logger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


class ArtisteAmbigu(Exception):
    """Aucun artiste sûr pour ce nom ; `candidats` = ce que Genius propose."""

    def __init__(self, nom: str, candidats: list[Artist]):
        super().__init__(f"Artiste introuvable par slug : {nom!r}")
        self.nom = nom
        self.candidats = candidats


def build_genius_slug(name: str) -> str:
    """Construit le slug Genius depuis un nom d'artiste.

    Règles :
    - Tout en minuscules
    - Supprime '.' et "'"
    - Remplace les espaces par '-'
    - Première lettre en majuscule

    Ex: 'Sofiane Pamart' → 'Sofiane-pamart'
        "L'Or du Commun" → 'Lor-du-commun'
        'NWA'            → 'Nwa'
    """
    slug = name.lower()
    for ch in (".", "'", "’"):  # point, apostrophe droite, apostrophe typographique
        slug = slug.replace(ch, "")
    slug = slug.replace(" ", "-")
    if slug:
        slug = slug[0].upper() + slug[1:]
    return slug


def fetch_artist_from_genius_url(url: str, fallback_name: str) -> Artist | None:
    """Charge la page Genius d'un artiste via Playwright et extrait l'ID depuis le meta tag.

    Utilise :
        JSON.parse(document.querySelector('meta[itemprop="page_data"]').content).artist.id

    Browser éphémère sur l'instance Playwright partagée (sync, thread-affine :
    à appeler depuis le thread qui la possède), config furtive (user-agent +
    masquage navigator.webdriver).
    """
    browser = None
    try:
        pw = get_playwright()
        browser = pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
        )
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=_USER_AGENT,
        )
        page = context.new_page()
        page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        page.goto(url, wait_until="domcontentloaded", timeout=15_000)
        result = page.evaluate("""() => {
            const meta = document.querySelector('meta[itemprop="page_data"]');
            if (!meta) return null;
            try {
                const data = JSON.parse(meta.content);
                if (!data.artist || !data.artist.id) return null;
                return {id: data.artist.id, name: data.artist.name};
            } catch(e) {
                return null;
            }
        }""")
        if result and result.get("id"):
            return Artist(name=result.get("name") or fallback_name, genius_id=result["id"])
    except (PlaywrightError, AttributeError, KeyError, TypeError, ValueError) as e:
        logger.debug(f"Fetch artiste depuis {url} échoué: {e}")
    finally:
        if browser:
            try:
                browser.close()  # ne PAS stopper l'instance Playwright partagée
            except PlaywrightError:
                pass
    return None


def charger(runtime: Runtime, nom: str) -> Artist | None:
    """L'artiste en base, avec sa discographie RÉUNIE (la sienne + celle de ses
    groupes + sa part dans ses collectifs). Câblé ici et non dans
    `get_artist_by_name` : le reset de données et les CLI de streams passent
    par la façade et ne doivent PAS voir les morceaux d'autrui."""
    artist = runtime.data_manager.get_artist_by_name(nom)
    if artist is None:
        return None
    artist.tracks = runtime.data_manager.discographie_reunie(artist)
    logger.info(f"✅ Artiste trouvé en base: {artist.name} avec {len(artist.tracks)} morceaux")
    return artist


def resoudre(runtime: Runtime, nom: str, *, genius_id: int | None = None) -> Artist:
    """Trouve l'identité Genius d'un artiste ABSENT de la base, sans le sauver.

    `genius_id` donné → on le croit. Sinon slug → page Genius. Sinon
    `ArtisteAmbigu` avec les candidats de l'API : on ne choisit jamais pour
    l'utilisateur (un homonyme rend une discographie parfaitement formée…
    de quelqu'un d'autre)."""
    if genius_id is not None:
        return Artist(name=nom, genius_id=genius_id)
    slug = build_genius_slug(nom)
    url = f"https://genius.com/artists/{slug}"
    logger.info(f"🌐 Artiste non trouvé en base, tentative : {url}")
    artist = fetch_artist_from_genius_url(url, nom)
    if artist and artist.genius_id:
        return artist
    try:
        candidats = runtime.genius_api.search_artist_candidates(nom)
    except Exception:  # dernier ressort : frontière réseau déjà gardée en amont
        logger.exception("Recherche de candidats Genius échouée")
        candidats = []
    raise ArtisteAmbigu(nom, candidats)


def charger_ou_ajouter(runtime: Runtime, nom: str, *, genius_id: int | None = None) -> Artist:
    """L'artiste chargé s'il existe, sinon résolu puis sauvé (discographie vide)."""
    artist = charger(runtime, nom)
    if artist is not None:
        return artist
    artist = resoudre(runtime, nom, genius_id=genius_id)
    runtime.data_manager.save_artist(artist)
    artist.tracks = []
    logger.info(f"✅ Artiste ajouté : {artist.name} (ID Genius {artist.genius_id})")
    return artist
