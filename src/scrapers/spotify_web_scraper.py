"""Scraper des pages web Spotify — compteurs de streams et auditeurs mensuels.

Complète `spotify_id_scraper_v2` (qui ne lit que des IDENTITÉS, jamais un
chiffre) et sert de **repli quand Kworb n'a pas de données** — Kworb ne couvre
pas tous les artistes, et sa page absente laissait jusqu'ici l'artiste sans
aucun stream.

Navigation : `CrawlAIScraperBase` en mode **session** (une page ouverte, N
navigations). Spotify n'a PAS de Cloudflare : l'échelle CDP → headless → fenêtre
visible ne le concerne pas, seul le contexte persistant est réutilisé — pour un
**profil dédié**, distinct de celui qui porte le `cf_clearance` de Genius/RIAA.

Économie du scrape (mesurée en session live, cf. `JOURNAL.md` 2026-09-04) :
  · **aucune page ne rend un album d'un coup** — la page album n'a PAS de
    compteur → une page par morceau ;
  · mais **une page titre en rend jusqu'à 16** (le morceau + 5 recommandés + 10
    titres populaires après dépliage) → chaque visite est une RÉCOLTE.

⚠️ Ce module ne décide RIEN : il rapporte ce que la page montre. Le filtrage par
identité, l'attribution des compteurs récoltés et les écritures appartiennent à
l'orchestrateur — les compteurs récoltés concernent souvent d'autres artistes.
"""

from pathlib import Path

from src.observability import source_usage
from src.observability.issues import IssueKind
from src.scrapers import spotify_web_parse as parse
from src.scrapers.crawl4ai_scraper_base import CrawlAIScraperBase
from src.utils.logger import get_logger

logger = get_logger(__name__)

#: Clé de `source_health.SOURCES` sous laquelle cet usage est compté. DISTINCTE
#: de `spotify_embed` : les deux tapent open.spotify.com mais cassent pour des
#: raisons sans rapport (SPA rendue vs `__NEXT_DATA__` server-rendered).
_SOURCE = "spotify_web"

#: Profil DÉDIÉ : une source sans Cloudflare n'a rien à faire dans le profil qui
#: porte le cookie `cf_clearance` de Genius et RIAA.
_PROFILE_DIR = str(Path.home() / ".music_credits_scraper" / "spotify_profile")

#: Locale ÉPINGLÉE. Spotify choisit sa langue sur `navigator.language` et redirige
#: l'URL en conséquence : sans épinglage, le libellé « auditeurs mensuels » — seul
#: point d'accroche de cette valeur, qui n'a pas de `data-testid` — change avec la
#: machine. Le préfixe d'URL en découle, il ne le pilote pas.
_LOCALE = "fr-FR"
_URL_PREFIX = "https://open.spotify.com/intl-fr"

#: Déplie les listes puis descend en bas de page. Les titres populaires sont
#: repliés à 5 ; « Afficher plus » les porte à 10. Un échec ici n'est PAS une
#: erreur : on récolte 5 lignes au lieu de 10, c'est une moisson réduite.
_EXPAND_JS = """
(async () => {
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  window.scrollTo(0, document.body.scrollHeight);
  await sleep(700);
  const btn = [...document.querySelectorAll('button')]
    .find(b => /^afficher plus$/i.test((b.textContent || '').trim()));
  if (btn) { btn.click(); await sleep(1800); }
  window.scrollTo(0, document.body.scrollHeight);
  await sleep(500);
})()
"""


class SpotifyWebScraper(CrawlAIScraperBase):
    """Lit les compteurs des pages Spotify rendues. Async natif (session)."""

    HEALTH_KEY = _SOURCE

    def __init__(self, headless: bool = True):
        super().__init__(
            headless=headless,
            health_key=_SOURCE,
            profile_dir=_PROFILE_DIR,
            locale=_LOCALE,
        )

    # ── URLs ────────────────────────────────────────────────────────────────

    @staticmethod
    def track_url(track_id: str) -> str:
        return f"{_URL_PREFIX}/track/{track_id}"

    @staticmethod
    def artist_url(artist_id: str) -> str:
        return f"{_URL_PREFIX}/artist/{artist_id}"

    @staticmethod
    def album_url(album_id: str) -> str:
        return f"{_URL_PREFIX}/album/{album_id}"

    # ── Récupérations (une observation = une page = un verdict) ─────────────

    async def afetch_artist(self, sess, artist_id: str) -> dict | None:
        """Page artiste : auditeurs mensuels, top titres récoltés, IDs d'album.

        La passe la plus rentable du scrape : **une seule page** rend les
        auditeurs mensuels ET les compteurs des ~10 titres les plus écoutés —
        justement ceux que Kworb couvre le mieux, donc la comparaison entre les
        deux sources sort d'ici sans dépenser une page de plus.

        Returns:
            {'name', 'monthly_listeners', 'playcounts': {track_id: int},
             'albums': {album_id: titre}}, ou None si la page n'est pas venue.
        """
        url = self.artist_url(artist_id)
        with source_usage.observe(_SOURCE, label=url) as obs:
            html = await sess.fetch(
                url,
                wait_for='css:[data-testid="tracklist-row"]',
                js_before_wait=_EXPAND_JS,
            )
            if not html:
                obs.fail(IssueKind.UNREACHABLE, "page artiste non rendue")
                return None

            data = {
                "name": parse.parse_page_artist_name(html),
                "monthly_listeners": parse.parse_monthly_listeners(html),
                "playcounts": parse.parse_row_playcounts(html),
                "albums": parse.parse_albums(html),
            }
            # Une page artiste rendue porte TOUJOURS un nom et des titres
            # populaires : n'avoir ni l'un ni l'autre n'est pas une absence de
            # donnée, c'est une structure qui a changé (ou une page non hydratée).
            if not data["name"] and not data["playcounts"]:
                obs.parse_error("ni nom ni titre populaire (structure changée ?)")
                return None
            if data["monthly_listeners"] is None:
                logger.warning(
                    f"Spotify {artist_id} : auditeurs mensuels introuvables "
                    f"(libellé changé ? locale non appliquée ?)"
                )
            obs.ok()
            return data

    async def afetch_track(self, sess, track_id: str) -> dict | None:
        """Page titre : le compteur du morceau + la récolte des listes.

        Returns:
            {'playcounts': {track_id: int}, 'durations': {track_id: int}} —
            inclut le morceau lui-même quand son compteur est lisible. Les
            autres entrées sont des recommandations et des titres populaires :
            elles peuvent appartenir à n'importe quel artiste, c'est à
            l'appelant de trancher. `durations` (lot 4) = la durée de l'en-tête
            pour le morceau de la page (`parse_track_identity`, même HTML, zéro
            page de plus) et celles des lignes listées.
        """
        url = self.track_url(track_id)
        with source_usage.observe(_SOURCE, label=url) as obs:
            html = await sess.fetch(
                url,
                wait_for='css:[data-testid="playcount"]',
                js_before_wait=_EXPAND_JS,
            )
            if not html:
                obs.fail(IssueKind.UNREACHABLE, "page titre non rendue")
                return None

            playcounts = parse.harvest_playcounts(html, page_track_id=track_id)
            if not playcounts:
                obs.parse_error("aucun compteur sur la page (structure changée ?)")
                return None
            if track_id not in playcounts:
                # La récolte a marché mais pas la lecture directe : on rend ce
                # qu'on a plutôt que rien, en le disant.
                logger.warning(f"Spotify {track_id} : compteur principal illisible")
            obs.ok()
            durations = parse.parse_row_durations(html)
            identite = parse.parse_track_identity(html)
            if identite and identite.get("duration"):
                durations[track_id] = identite["duration"]
            return {"playcounts": playcounts, "durations": durations}

    async def afetch_track_identity(self, sess, track_id: str) -> dict | None:
        """Ce que la page titre dit du morceau, sans connexion (2026-09-21).

        Sert à ce qu'une ligne créée depuis Kworb (un remix, une version) ne
        soit pas VIDE : artistes crédités, disque, date de sortie, durée, label.
        Cf. `parse.parse_track_identity`. Une page par appel, un verdict.
        """
        url = self.track_url(track_id)
        with source_usage.observe(_SOURCE, label=url) as obs:
            html = await sess.fetch(url, wait_for='css:[data-testid="entityTitle"]')
            if not html:
                obs.fail(IssueKind.UNREACHABLE, "page titre non rendue")
                return None
            identite = parse.parse_track_identity(html)
            if identite is None:
                obs.parse_error("page titre sans en-tête (structure changée ?)")
                return None
            obs.ok()
            return identite

    async def afetch_album(self, sess, album_id: str) -> dict | None:
        """Composition d'un album : son titre et l'ordre de ses pistes.

        La page album n'affiche **aucun compteur** (vérifié en session live sur un
        single et sur un album de 10+ titres) : elle ne sert qu'à connaître la
        composition du disque, dont le total réel se paie ensuite en une page par
        piste — y compris les pistes où l'artiste n'apparaît pas.

        ⚠️ La tracklist est VIRTUALISÉE : sans dérouler la page, le nombre de
        lignes rendues dépend du hasard du rendu (mesuré le 2026-09-05 : 15
        pistes à un appel, 7 au suivant, sur la MÊME page). D'où le déroulement
        systématique, et surtout le `announced` renvoyé — c'est lui qui permet à
        l'appelant de savoir qu'il n'a pas tout vu.

        Returns:
            {'title', 'tracks': [(track_id, titre)], 'track_ids', 'announced',
            'durations': {track_id: secondes}}, ou None si rien n'est venu.
            Les TITRES sont indispensables : deux
            éditions d'un même album portent des `track_id` différents pour le
            même enregistrement (cf. `parse_album_tracks`).
        """
        url = self.album_url(album_id)
        with source_usage.observe(_SOURCE, label=url) as obs:
            html = await sess.fetch(
                url,
                wait_for='css:[data-testid="tracklist-row"]',
                js_before_wait=_EXPAND_JS,
                delay_before_return=1.0,
            )
            if not html:
                obs.fail(IssueKind.UNREACHABLE, "page album non rendue")
                return None

            tracks = parse.parse_album_tracks(html)
            if not tracks:
                obs.parse_error("0 piste listée (structure changée ?)")
                return None
            annonce = parse.parse_album_track_count(html)
            if annonce is not None and len(tracks) < annonce:
                # Pas une erreur de source : la page a répondu, c'est le rendu qui
                # n'a pas tout déroulé. On le DIT au lieu de rendre une liste
                # tronquée dont personne ne verrait qu'elle l'est.
                logger.warning(
                    f"Album {album_id} : {len(tracks)} piste(s) rendues sur {annonce} annoncées"
                )
            obs.ok()
            return {
                "title": parse.parse_album_title(html),
                "tracks": tracks,
                "track_ids": [tid for tid, _ in tracks],
                "announced": annonce,
                "durations": parse.parse_row_durations(html),
            }


def lire_identite_page(track_id: str, scraper: "SpotifyWebScraper | None" = None) -> dict | None:
    """Pont SYNC : l'identité d'une page titre depuis un thread ordinaire.

    Ouvre une session le temps d'une page (le dialogue Kworb en demande une à
    la fois). Jamais depuis la boucle asyncio (`run_sync` s'en garde).
    """
    from src.concurrency import async_loop

    own = scraper is None
    scraper = scraper or SpotifyWebScraper(headless=True)

    async def _une_page():
        async with scraper.session() as sess:
            return await scraper.afetch_track_identity(sess, track_id)

    try:
        return async_loop.run_sync(_une_page())
    finally:
        if own:
            scraper.close()
