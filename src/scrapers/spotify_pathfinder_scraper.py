"""La passe Spotify de l'étape streams SANS navigateur, avec repli sur les pages.

`SpotifyPathfinderScraper` offre EXACTEMENT l'interface de `SpotifyWebScraper`
(`session()`, `afetch_artist`, `afetch_track`, `afetch_album`, mêmes dictionnaires
rendus) au-dessus des requêtes internes du lecteur web (`src/api/spotify_pathfinder`).
`update_spotify_streams` ne change donc pas : il reçoit un autre scraper.

Les observations restent écrites sous la source `spotify_web` : ce sont LES MÊMES
compteurs, durées et auditeurs que la page affiche (mesuré le 2026-11-20 : compteurs
égaux à la croissance près, durées à moins de 1 s, auditeurs à ± 1 %). Seul le
transport change ; il a sa propre clé d'observabilité (`spotify_pathfinder`), qui
dit sa santé à part. L'ordre de `DISCOGRAPHY_PRIORITIES`, la fraîcheur des relevés
et les constats « sans compteur » valent donc tels quels.

Ce que le JSON donne en plus de la page :
- la page album porte le compteur de CHAQUE piste (`playcounts`) : le total d'un
  disque coûte une requête par édition au lieu d'une page par piste ;
- la durée est celle de la piste, à la milliseconde (tronquée à la seconde, comme
  l'affichage « m:ss » que lisait la page : aucune observation ne bouge pour rien).

`SpotifyAvecRepli` : chaque lecture passe d'abord par les requêtes internes ; sur
un échec (`SansReponse` : bridage, panne, requête renommée), la page du navigateur
est lue à la place — la session navigateur ne s'ouvre qu'au premier repli. Après
`ECHECS_AVANT_BASCULE` échecs DE SUITE, le run bascule sur le navigateur seul.
"""

from __future__ import annotations

from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass

from src.api.spotify_pathfinder import PisteSpotify, SpotifyPathfinder
from src.observability.issues import SansReponse
from src.utils.logger import get_logger

logger = get_logger(__name__)

#: Échecs de suite des requêtes internes avant de passer au navigateur seul.
ECHECS_AVANT_BASCULE = 3


def _secondes(piste: PisteSpotify) -> int | None:
    """Durée en secondes ENTIÈRES, tronquées comme l'affichage « m:ss » de la
    page (213 573 ms → 213, que la page affiche « 3:33 »)."""
    return int(piste.duree_s) if piste.duree_s else None


class SpotifyPathfinderScraper:
    """`SpotifyWebScraper` sans navigateur. Une session = un client (la
    découverte des requêtes ne se fait qu'une fois par run)."""

    def __init__(self, client: SpotifyPathfinder | None = None) -> None:
        self._client_fourni = client

    @asynccontextmanager
    async def session(self):
        if self._client_fourni is not None:
            yield self._client_fourni
            return
        async with SpotifyPathfinder() as client:
            yield client

    def close(self) -> None:
        """Rien à fermer hors session (interface de `SpotifyWebScraper`)."""

    async def afetch_artist(self, sess: SpotifyPathfinder, artist_id: str) -> dict | None:
        """{'name', 'monthly_listeners', 'playcounts' (titres populaires),
        'albums': {album_id: titre}} — None si le profil n'existe pas."""
        artiste = await sess.artiste(artist_id)
        if artiste is None:
            return None
        if artiste.auditeurs_mensuels is None:
            logger.warning(f"Spotify {artist_id} : auditeurs mensuels absents de la réponse")
        return {
            "name": artiste.nom,
            "monthly_listeners": artiste.auditeurs_mensuels,
            "playcounts": dict(artiste.top_titres),
            "albums": dict(artiste.disques),
        }

    async def afetch_track(self, sess: SpotifyPathfinder, track_id: str) -> dict | None:
        """{'playcounts': {id: n}, 'durations': {id: s}, 'sans_compteur'} —
        None si la piste n'existe pas. Sans compteur, Spotify n'en affiche pas :
        un constat (`sans_compteur`), comme l'en-tête lu sans compteur."""
        piste = await sess.piste(track_id)
        if piste is None:
            return None
        secondes = _secondes(piste)
        return {
            "playcounts": {track_id: piste.compteur} if piste.compteur is not None else {},
            "durations": {track_id: secondes} if secondes else {},
            "sans_compteur": piste.compteur is None,
        }

    async def afetch_album(self, sess: SpotifyPathfinder, album_id: str) -> dict | None:
        """{'title', 'tracks', 'track_ids', 'announced', 'indisponibles',
        'durations', 'playcounts'} — None si l'album n'existe pas.

        Une piste INDISPONIBLE (`playable` faux) est la ligne grisée de la page :
        hors des pistes et du total, comptée à part (décision utilisateur
        2026-11-10). `title` : la requête ne le sert pas (l'appelant nomme le
        disque par sa base)."""
        pistes = await sess.pistes_album(album_id)
        if pistes is None:
            return None
        lisibles = [p for p in pistes if p.jouable is not False]
        return {
            "title": None,
            "tracks": [(p.id, p.titre) for p in lisibles],
            "track_ids": [p.id for p in lisibles],
            "announced": len(pistes),
            "indisponibles": len(pistes) - len(lisibles),
            "durations": {p.id: s for p in lisibles if (s := _secondes(p))},
            "playcounts": {p.id: p.compteur for p in lisibles if p.compteur is not None},
            # ID Spotify des artistes de chaque piste : un album trouvé par l'une de
            # ses pistes doit être celui de l'artiste (`part_de_l_artiste`).
            "credits": {p.id: tuple(i for _, i in p.artistes if i) for p in lisibles},
        }

    async def afetch_album_de_piste(self, sess: SpotifyPathfinder, track_id: str) -> str | None:
        """L'ID de l'album Spotify d'une piste (2026-11-23) ; None si la piste
        n'existe pas. La page ne le donne pas : pas d'équivalent navigateur."""
        piste = await sess.piste(track_id)
        return piste.album_id if piste else None


class SpotifyAvecRepli:
    """Les requêtes internes d'abord, la page du navigateur en repli."""

    def __init__(
        self, principal: SpotifyPathfinderScraper, repli, *, pages_max: int | None = None
    ) -> None:
        from src.config import settings

        self.principal = principal
        self.repli = repli
        self.echecs_de_suite = 0
        self.bascule = False
        self.replis = 0
        #: Pages du navigateur ouvertes en repli, sous LEUR plafond
        #: (`spotify_web_max_pages_per_run`) : le budget du run compte des
        #: requêtes (`plafonds`), trois fois moins chères qu'une page.
        self.pages_max = settings.spotify_web_max_pages_per_run if pages_max is None else pages_max
        self._plafond_dit = False

    @asynccontextmanager
    async def session(self):
        # Une session = un run (un artiste) : le provider garde le même scraper
        # d'un artiste à l'autre, les compteurs et la bascule repartent de zéro.
        self.echecs_de_suite = 0
        self.bascule = False
        self.replis = 0
        self._plafond_dit = False
        async with AsyncExitStack() as pile, self.principal.session() as sess:
            yield _SessionsAvecRepli(sess, pile, self.repli)

    def close(self) -> None:
        self.repli.close()

    async def _lire(self, sessions: _SessionsAvecRepli, cle: str, lecture):
        """`lecture(scraper, session)` : la même lecture chez l'un ou l'autre."""
        if not self.bascule:
            try:
                rendu = await lecture(self.principal, sessions.principale)
            except SansReponse as e:
                self.echecs_de_suite += 1
                logger.info(f"Spotify {cle} : requête interne sans réponse ({e.kind}) — repli page")
                if self.echecs_de_suite >= ECHECS_AVANT_BASCULE:
                    self.bascule = True
                    logger.warning(
                        f"🔁 Spotify : {self.echecs_de_suite} requêtes internes en échec de "
                        "suite — le reste du run passe par les pages (navigateur)"
                    )
            else:
                self.echecs_de_suite = 0
                return rendu
        if self.replis >= self.pages_max:
            if not self._plafond_dit:
                self._plafond_dit = True
                logger.warning(
                    f"Spotify : plafond de {self.pages_max} pages de repli atteint — "
                    "le reste attendra le prochain run"
                )
            return None
        self.replis += 1
        sess_repli = await sessions.repli_ouvert()
        return await lecture(self.repli, sess_repli)

    async def afetch_artist(self, sessions, artist_id: str) -> dict | None:
        return await self._lire(
            sessions, artist_id, lambda s, sess: s.afetch_artist(sess, artist_id)
        )

    async def afetch_track(self, sessions, track_id: str) -> dict | None:
        return await self._lire(sessions, track_id, lambda s, sess: s.afetch_track(sess, track_id))

    async def afetch_album(self, sessions, album_id: str) -> dict | None:
        return await self._lire(sessions, album_id, lambda s, sess: s.afetch_album(sess, album_id))

    async def afetch_album_de_piste(self, sessions, track_id: str) -> str | None:
        """Requêtes internes seules : un échec rend None (aucune page n'a l'ID
        d'album), sans compter comme un repli ni pousser à la bascule."""
        if self.bascule:
            return None
        try:
            return await self.principal.afetch_album_de_piste(sessions.principale, track_id)
        except SansReponse as e:
            logger.info(f"Spotify {track_id} : album de la piste illisible ({e.kind})")
            return None


class _SessionsAvecRepli:
    """La session des requêtes internes, et celle du navigateur ouverte au
    premier besoin (un lancement de navigateur coûte ; un run sans échec n'en
    paie aucun)."""

    def __init__(self, principale, pile: AsyncExitStack, repli) -> None:
        self.principale = principale
        self._pile = pile
        self._repli = repli
        self._sess_repli = None

    async def repli_ouvert(self):
        if self._sess_repli is None:
            logger.info("Spotify : ouverture de la session navigateur (repli)")
            self._sess_repli = await self._pile.enter_async_context(self._repli.session())
        return self._sess_repli


def trouve_l_album_par_piste(scraper) -> bool:
    """Le scraper sait-il donner l'album d'une piste (requêtes internes) ?"""
    return isinstance(scraper, SpotifyAvecRepli | SpotifyPathfinderScraper)


#: Part minimale des pistes d'un album Spotify qui doivent créditer l'artiste pour
#: qu'un album trouvé par UNE piste soit le sien : une compilation (*Rap 2007*,
#: *100% Rap Français*) où il a une piste en crédite d'autres sur presque toutes.
PART_MIN_DE_L_ARTISTE = 0.5


def part_de_l_artiste(album: dict, ids_artiste: set[str]) -> float:
    """PUR. Part des pistes de l'album qui créditent un des profils Spotify de
    l'artiste (0 sans crédits lus)."""
    credits = album.get("credits") or {}
    if not credits:
        return 0.0
    return sum(1 for ids in credits.values() if ids_artiste & set(ids)) / len(credits)


@dataclass(frozen=True)
class Plafonds:
    """Lectures permises à un run de la passe Spotify (`update_spotify_streams`)."""

    par_run: int
    sans_stream: int


def chemin_pris(scraper, lectures: int) -> dict:
    """Ce que la passe a RÉELLEMENT fait, pour le récit et le bilan : `chemin`
    (« requetes » ou « pages »), `requetes` (lectures par les requêtes internes),
    `replis` (pages ouvertes en repli), `bascule` (passée aux pages seules après
    des échecs de suite). `lectures` = le total compté par le run (`pages`)."""
    if isinstance(scraper, SpotifyAvecRepli):
        return {
            "chemin": "requetes",
            "requetes": max(lectures - scraper.replis, 0),
            "replis": scraper.replis,
            "bascule": scraper.bascule,
        }
    if isinstance(scraper, SpotifyPathfinderScraper):
        return {"chemin": "requetes", "requetes": lectures, "replis": 0, "bascule": False}
    return {"chemin": "pages", "requetes": 0, "replis": 0, "bascule": False}


def libelle(chemin: str | None = None) -> str:
    """Nom de la passe Spotify dans le récit et le bilan. Sans `chemin` (avant
    le run), celui que le réglage fera prendre."""
    if chemin is None:
        from src.config import SPOTIFY_STREAMS_PAR_PATHFINDER

        chemin = "requetes" if SPOTIFY_STREAMS_PAR_PATHFINDER else "pages"
    return "Spotify (requêtes internes)" if chemin == "requetes" else "Spotify (pages web)"


def plafonds(scraper) -> Plafonds:
    """Les plafonds du SCRAPER employé : en requêtes internes pour
    `spotify_pathfinder` (repli compris, ses pages ayant leur propre plafond),
    en pages pour le navigateur seul (décision utilisateur 2026-11-20 : deux
    réglages distincts, un relevé pour les requêtes ne vaut pas pour les pages)."""
    from src.config import settings

    if isinstance(scraper, SpotifyAvecRepli | SpotifyPathfinderScraper):
        return Plafonds(
            settings.spotify_pathfinder_max_requetes_par_run,
            settings.spotify_pathfinder_requetes_sans_stream,
        )
    return Plafonds(settings.spotify_web_max_pages_per_run, settings.spotify_web_pages_sans_stream)


def scraper_des_streams():
    """Le scraper de la passe Spotify de l'étape streams : requêtes internes avec
    repli sur les pages si `SPOTIFY_STREAMS_PAR_PATHFINDER`, sinon les pages."""
    from src.config import SPOTIFY_STREAMS_PAR_PATHFINDER
    from src.scrapers.spotify_web_scraper import SpotifyWebScraper

    pages = SpotifyWebScraper(headless=True)
    if not SPOTIFY_STREAMS_PAR_PATHFINDER:
        return pages
    return SpotifyAvecRepli(SpotifyPathfinderScraper(), pages)
