"""Client pour l'API Discogs - Enrichissement des crédits et métadonnées"""

import os
import time
from typing import Any

import discogs_client
import requests
from discogs_client.exceptions import DiscogsAPIError, HTTPError

from src.models import Artist, ArtistRelation, Credit, CreditRole, Track
from src.observability import source_usage
from src.observability.issues import IssueKind
from src.utils.discogs_identity import nom_sans_suffixe, release_concorde, titre_de_piste
from src.utils.discogs_positions import concerne_la_piste
from src.utils.logger import get_logger, log_api
from src.utils.title_matching import normalize_name

__all__ = ["DiscogsClient", "nom_sans_suffixe", "release_concorde", "token_discogs"]

logger = get_logger(__name__)


def _sans_tiret(libelle: str) -> str:
    """Graphie commune aux libellés Discogs : minuscules, tirets en espaces.

    Discogs panache « Written-By » et « Mixed By » dans le même formulaire ;
    la table, elle, ne peut en porter qu'une graphie.
    """
    return (libelle or "").lower().strip().replace("-", " ").replace("_", " ")


#: Clé de `source_health.SOURCES` sous laquelle cet usage est compté.
_SOURCE = "discogs"


def token_discogs() -> str | None:
    """Token personnel Discogs, ou None (l'API reste lisible sans, à 25 req/min).

    DEUX noms de variable sont acceptés, pour une raison purement historique.
    La lecture vivait en double, à l'octet près, dans `data_enricher` et le
    worker de scraping ; un troisième appelant (les formations, lot 3) allait en
    faire un triplé. Le nom des clés n'a pas à être connu de trois modules.
    """
    return os.getenv("DISCOGS_TOKEN") or os.getenv("DISCOGS_USER_TOKEN")


# ── Formations : groupes, membres, alias (lot 3) ──────────────────────────────

# Discogs désambiguïse les homonymes par un SUFFIXE NUMÉRIQUE : notre rappeur
# belge s'appelle « Swing (20) », pas « Swing ». Mesuré le 2026-09-08, et le
# piège est vicieux : sans retirer ce suffixe, la recherche « Swing » ne rend
# qu'UN homonyme exact — « Swing » tout court, qui n'est PAS le nôtre. On
# obtiendrait donc une confirmation confiante et fausse, ce qui est pire que
# pas de confirmation du tout. `nom_sans_suffixe` vit dans
# `src.utils.discogs_identity` (partagé avec l'oracle et l'audit) et reste
# importable d'ici.


class DiscogsClient:
    """Client pour interagir avec l'API Discogs"""

    def __init__(self, user_token: str | None = None):
        """
        Initialise le client Discogs

        Args:
            user_token: Token personnel Discogs (optionnel mais recommandé pour + de requêtes/min)
        """
        self.client = None
        self.rate_limit_remaining = 60
        self.rate_limit_used = 0

        try:
            if user_token:
                # Authentification avec token personnel (60 req/min)
                self.client = discogs_client.Client(
                    "MusicCreditsScraper/1.0", user_token=user_token
                )
                logger.info("✅ Discogs API initialisée avec token personnel (60 req/min)")
            else:
                # Sans authentification (25 req/min)
                self.client = discogs_client.Client("MusicCreditsScraper/1.0")
                logger.warning("⚠️ Discogs API initialisée SANS token (25 req/min - limité)")

        except Exception as e:
            logger.error(f"❌ Erreur initialisation Discogs API: {e}")
            raise

    def _check_rate_limit(self):
        """Vérifie et gère le rate limit de l'API Discogs"""
        try:
            # L'API Discogs retourne les headers de rate limit après chaque requête
            # On peut vérifier via self.client._fetcher.last_request si besoin
            if hasattr(self.client, "_fetcher") and hasattr(
                self.client._fetcher, "rate_limit_remaining"
            ):
                self.rate_limit_remaining = self.client._fetcher.rate_limit_remaining
                self.rate_limit_used = self.client._fetcher.rate_limit_used

                if self.rate_limit_remaining < 5:
                    logger.warning(
                        f"⚠️ Rate limit Discogs faible: {self.rate_limit_remaining} requêtes restantes"
                    )
                    # Attendre 60 secondes pour reset
                    logger.info("⏸️ Pause de 60s pour reset du rate limit...")
                    time.sleep(60)

        except (AttributeError, TypeError) as e:
            logger.debug(f"Impossible de vérifier le rate limit: {e}")

    def search_track(
        self,
        track_title: str,
        artist_name: str,
        album_name: str | None = None,
        *,
        noms_attendus: set[str] | None = None,
        artist_discogs_id: int | None = None,
    ) -> dict[str, Any] | None:
        """
        Recherche un morceau sur Discogs

        Args:
            track_title: Titre du morceau
            artist_name: Nom de l'artiste
            album_name: Nom de l'album (optionnel mais améliore la précision)
            noms_attendus: noms sous lesquels le disque (ou la piste) doit être
                crédité — défaut : `artist_name` seul. Pour un feat, y ajouter
                l'artiste principal.
            artist_discogs_id: identité Discogs de l'artiste, si connue.

        Returns:
            Dictionnaire avec les infos du morceau ou None
        """
        if not self.client:
            logger.error("❌ Client Discogs non initialisé")
            return None

        attendus = set(noms_attendus or ()) | {artist_name}
        with source_usage.observe(_SOURCE, label=f"{artist_name} — {track_title}") as obs:
            return self._search_track_body(
                obs, track_title, artist_name, album_name, attendus, artist_discogs_id
            )

    def _search_track_body(
        self,
        obs,
        track_title: str,
        artist_name: str,
        album_name: str | None,
        noms_attendus: set[str] | None = None,
        artist_discogs_id: int | None = None,
    ) -> dict[str, Any] | None:
        """Corps de `search_track`, sous l'observation ouverte par elle."""
        noms_attendus = noms_attendus or {artist_name}
        etrangers = 0
        try:
            self._check_rate_limit()

            # Construire la requête de recherche
            # Format: "artist - title" ou "artist - album - title"
            if album_name:
                query = f"{artist_name} {album_name} {track_title}"
                logger.info(
                    f"🔍 Discogs: Recherche '{track_title}' de {artist_name} (album: {album_name})"
                )
            else:
                query = f"{artist_name} {track_title}"
                logger.info(f"🔍 Discogs: Recherche '{track_title}' de {artist_name}")

            # Rechercher des releases (albums/singles) contenant ce track
            # `attempt` : discogs_client fait ses requêtes lui-même, aucun de
            # nos capteurs transport ne les voit.
            with source_usage.attempt(_SOURCE, detail="search"):
                results = self.client.search(query, type="release", artist=artist_name)

            if not results:
                logger.warning(f"❌ Aucun résultat Discogs pour '{track_title}'")
                log_api("Discogs", f"search/{track_title}", False)
                obs.absent("aucun résultat de recherche")
                return None

            # Discogs results is a paginated object, not a list
            # We can't use len() or slice directly
            logger.info("📊 Discogs: Résultats trouvés, analyse des premiers...")

            # Analyser les premiers résultats (max 5)
            checked_count = 0
            for i, release in enumerate(results, 1):
                if checked_count >= 5:
                    break
                checked_count += 1
                try:
                    logger.debug(f"Vérification résultat #{i}: {release.title}")

                    # Vérifier si le release contient le track recherché
                    track_data = self._extract_track_from_release(release, track_title, artist_name)

                    # Le disque est-il celui de NOTRE artiste ? (2026-09-23) La
                    # recherche est libre : Django « Nuages » rapprochait le disque
                    # de Django Reinhardt, et en importait les crédits. Un disque
                    # étranger est sauté — le candidat suivant peut être le bon.
                    if track_data and not release_concorde(
                        track_data.pop("artistes_release", []),
                        track_data.pop("artistes_piste", []),
                        noms_attendus,
                        artist_discogs_id,
                    ):
                        etrangers += 1
                        logger.info(
                            f"↪️ Discogs: disque « {release.title} » crédité à un autre "
                            f"artiste — écarté pour '{track_title}'"
                        )
                        continue

                    if track_data:
                        logger.info(f"✅ Discogs: Correspondance trouvée (résultat #{i})")
                        log_api("Discogs", f"search/{track_title}", True)
                        obs.ok()
                        return track_data

                # release.* est lazy-loadé par discogs_client → accès = fetch réseau
                # possible (DiscogsAPIError). Best-effort par candidat : on continue.
                except (DiscogsAPIError, AttributeError, TypeError, ValueError, KeyError) as e:
                    logger.debug(f"Erreur analyse résultat #{i}: {e}")
                    continue

            logger.warning(
                f"❌ Aucune correspondance exacte trouvée sur Discogs pour '{track_title}'"
            )
            log_api("Discogs", f"search/{track_title}", False)
            obs.absent(
                "aucune correspondance exacte"
                + (f" ({etrangers} disque(s) d'un autre artiste écarté(s))" if etrangers else "")
            )
            return None

        except HTTPError as e:
            if e.status_code == 429:
                logger.error("⏰ Rate limit Discogs atteint, pause de 60s...")
                obs.fail(IssueKind.THROTTLED, "rate limit Discogs (429)")
                time.sleep(60)
            else:
                logger.error(f"❌ Erreur HTTP Discogs: {e}")
                obs.note_status(e.status_code or 0)
            log_api("Discogs", f"search/{track_title}", False)
            return None

        except (DiscogsAPIError, KeyError, TypeError, ValueError) as e:
            if isinstance(e, (KeyError, TypeError, ValueError)):
                obs.parse_error(f"réponse inexploitable : {e}")
            else:
                obs.fail(IssueKind.UNREACHABLE, f"API Discogs : {e}")
            logger.error(f"❌ Erreur recherche Discogs: {e}")
            log_api("Discogs", f"search/{track_title}", False)
            return None

    def _extract_track_from_release(
        self, release, track_title: str, artist_name: str
    ) -> dict[str, Any] | None:
        """
        Extrait les données d'un track depuis un release Discogs

        Args:
            release: Objet Release de discogs_client
            track_title: Titre du track recherché
            artist_name: Nom de l'artiste

        Returns:
            Dictionnaire avec les données du track ou None
        """
        try:
            # Récupérer la tracklist
            if not hasattr(release, "tracklist") or not release.tracklist:
                return None

            # Normaliser le titre recherché pour comparaison
            normalized_search_title = self._normalize_string(track_title)

            # Chercher le track dans la tracklist
            for track in release.tracklist:
                track_name = track.title if hasattr(track, "title") else str(track)
                normalized_track_name = self._normalize_string(track_name)

                if normalized_track_name == normalized_search_title:
                    logger.info(f"✅ Track trouvé: '{track_name}' dans release '{release.title}'")

                    # Extraire les données
                    track_data = {
                        "title": track_title,
                        "artist": artist_name,
                        "album": release.title if hasattr(release, "title") else None,
                        "discogs_id": release.id if hasattr(release, "id") else None,
                        "discogs_url": release.url if hasattr(release, "url") else None,
                        "position": track.position if hasattr(track, "position") else None,
                        "duration": track.duration if hasattr(track, "duration") else None,
                        # Retirés par `_search_track_body` après le contrôle
                        # d'identité (`release_concorde`) : ils ne sont pas des
                        # données du morceau.
                        "artistes_release": self._artistes_de(release),
                        "artistes_piste": self._artistes_de(track),
                    }

                    # Extraire les métadonnées du release
                    if hasattr(release, "genres") and release.genres:
                        track_data["genres"] = release.genres

                    if hasattr(release, "styles") and release.styles:
                        track_data["styles"] = release.styles

                    if hasattr(release, "year") and release.year:
                        track_data["year"] = release.year

                    if hasattr(release, "labels") and release.labels:
                        labels = [label.name for label in release.labels if hasattr(label, "name")]
                        track_data["labels"] = labels

                    # Extraire les crédits
                    credits = self._extract_credits_from_release(release)
                    if credits:
                        track_data["credits"] = credits

                    return track_data

            return None

        except (DiscogsAPIError, AttributeError, TypeError, KeyError, ValueError) as e:
            logger.warning(f"Erreur extraction track depuis release: {e}")
            return None

    @staticmethod
    def _artistes_de(objet) -> list[tuple[int | None, str]]:
        """`(id, nom)` des artistes crédités d'un disque ou d'une piste de sa
        tracklist (`artists`, lazy-loadé — frontière externe). Une piste d'album
        n'en a souvent aucun : c'est le disque qui la crédite."""
        artistes = getattr(objet, "artists", None) or []
        return [(getattr(a, "id", None), a.name) for a in artistes if getattr(a, "name", None)]

    def _extract_credits_from_release(self, release) -> list[dict[str, str]]:
        """
        Extrait tous les crédits depuis un release Discogs

        Args:
            release: Objet Release de discogs_client

        Returns:
            Liste de dictionnaires avec les crédits
        """
        credits = []

        try:
            # Les crédits sont dans release.credits ou release.extraartists
            credit_sources = []

            if hasattr(release, "credits") and release.credits:
                credit_sources.extend(release.credits)

            if hasattr(release, "extraartists") and release.extraartists:
                credit_sources.extend(release.extraartists)

            for credit in credit_sources:
                try:
                    # Credits are Artist objects with a 'data' dictionary containing the actual credit info
                    name = credit.name if hasattr(credit, "name") else str(credit)

                    # Extract role from data dictionary
                    role = "Unknown"
                    tracks = None

                    if hasattr(credit, "data") and isinstance(credit.data, dict):
                        # Role is in credit.data['role']
                        role = credit.data.get("role", "Unknown")
                        # Tracks info (like "A1, B4") is in credit.data['tracks']
                        tracks = credit.data.get("tracks")

                    # La clé s'appelait `role_detail` alors qu'elle portait les
                    # PISTES : c'est cette confusion de nom qui a fait cohabiter
                    # deux données dans une colonne (e18). Nommée pour ce qu'elle est.
                    credit_dict = {"name": name, "role": role, "tracks": tracks}

                    credits.append(credit_dict)
                    logger.debug(f"Crédit Discogs: {name} - {role}")

                except (AttributeError, TypeError, KeyError) as e:
                    logger.debug(f"Erreur extraction crédit: {e}")
                    continue

            logger.info(f"✅ {len(credits)} crédit(s) extrait(s) de Discogs")
            return credits

        except (DiscogsAPIError, AttributeError, TypeError, KeyError) as e:
            logger.warning(f"⚠️ Erreur extraction crédits Discogs: {e}")
            return []

    @staticmethod
    def _normalize_string(s: str) -> str:
        """Normalise une chaîne pour la comparaison (`discogs_identity.titre_de_piste`,
        partagée avec l'audit des disques)."""
        return titre_de_piste(s)

    def _map_discogs_role_to_enum(self, role: str) -> CreditRole:
        """
        Mappe un rôle Discogs vers un CreditRole

        Args:
            role: Rôle Discogs (ex: "Producer", "Mixed By", etc.)

        Returns:
            CreditRole correspondant
        """
        # Discogs écrit ses libellés au TIRET (« Written-By », « Co-Producer »)
        # et la table en mélangeait les deux graphies. Le rapprochement se
        # faisant par SOUS-CHAÎNE, « written by » ne reconnaissait pas
        # « Written-By » : **504 crédits d'écriture** rangés en `Other` pour un
        # caractère (mesuré 2026-09-22), et autant de morceaux déclarés sans
        # auteur. Les deux côtés sont donc ramenés à la même graphie — la
        # longueur ne change pas, le tri du plus spécifique au plus général
        # reste intact.
        role_lower = _sans_tiret(role)

        # Mapping des rôles Discogs vers CreditRole
        role_mapping = {
            # Production
            "producer": CreditRole.PRODUCER,
            "co-producer": CreditRole.CO_PRODUCER,
            "executive producer": CreditRole.EXECUTIVE_PRODUCER,
            "vocal producer": CreditRole.VOCAL_PRODUCER,
            "programmed by": CreditRole.PROGRAMMER,
            "arranged by": CreditRole.ARRANGER,
            # Engineering
            "mixed by": CreditRole.MIXING_ENGINEER,
            "mastered by": CreditRole.MASTERING_ENGINEER,
            "recorded by": CreditRole.RECORDING_ENGINEER,
            "engineer": CreditRole.ENGINEER,
            "assistant engineer": CreditRole.ASSISTANT_ENGINEER,
            # Writing
            "written by": CreditRole.WRITER,
            "composed by": CreditRole.COMPOSER,
            "lyrics by": CreditRole.LYRICIST,
            # Performance
            "vocals": CreditRole.VOCALS,
            "lead vocals": CreditRole.LEAD_VOCALS,
            "backing vocals": CreditRole.BACKGROUND_VOCALS,
            "choir": CreditRole.CHOIR,
            # Instruments
            "guitar": CreditRole.GUITAR,
            "bass": CreditRole.BASS,
            "drums": CreditRole.DRUMS,
            "piano": CreditRole.PIANO,
            "keyboards": CreditRole.KEYBOARD,
            "saxophone": CreditRole.SAXOPHONE,
            # Artwork
            "artwork": CreditRole.ARTWORK,
            "design": CreditRole.GRAPHIC_DESIGN,
            "photography": CreditRole.PHOTOGRAPHY,
            # ── Alias ajoutés le 2026-09-03 ──────────────────────────────────
            # Relevés sur les libellés réellement rencontrés (les crédits rangés
            # en OTHER conservent leur libellé Discogs dans `role_detail`, ce qui
            # a permis de les inventorier en base). Chaque entrée est soit
            # lexicalement non ambiguë, soit confirmée par l'ORACLE Genius : ce
            # que Genius dit de la MÊME personne sur le MÊME morceau.
            # Cf. scripts/discogs_genius_oracle.py pour rejouer la mesure.
            "songwriter": CreditRole.WRITER,
            "graphics": CreditRole.GRAPHIC_DESIGN,
            "logo": CreditRole.GRAPHIC_DESIGN,
            "cover": CreditRole.ARTWORK,
            "scratches": CreditRole.SCRATCHES,
            # Gravure/mastering vinyle. « direct metal mastering by » est
            # confirmé par l'oracle (6/6 « Mastering Engineer » chez Genius) ;
            # « lacquer cut by » est le même métier sans recoupement disponible.
            "direct metal mastering by": CreditRole.MASTERING_ENGINEER,
            "lacquer cut by": CreditRole.MASTERING_ENGINEER,
            "editor": CreditRole.VIDEO_EDITOR,
            # 2026-09-21 : « Remix » (20 crédits en base) — la personne qui a
            # retravaillé le morceau, lexicalement non ambigu.
            "remix": CreditRole.REMIXER,
            "remixed by": CreditRole.REMIXER,
            # 2026-09-22 : libellés que la table GENIUS connaissait déjà et que
            # celle-ci ignorait — un crédit ne doit pas dépendre de la source
            # qui l'a lu. Lexicalement non ambigus, chacun a sa valeur d'enum.
            "art direction": CreditRole.ART_DIRECTION,
            "featuring": CreditRole.FEATURED,
            "illustration": CreditRole.ILLUSTRATION,
            "a&r": CreditRole.A_AND_R,
        }

        # NON mappés DÉLIBÉRÉMENT (décision 2026-09-03) — ne pas « compléter »
        # sans mesurer d'abord. `music by` (109 crédits) et `realization` (42)
        # ont un oracle PARTAGÉ : Genius appelle ces personnes Producer,
        # Mixing Engineer ou Recording Engineer selon les cas. Les forcer vers
        # un rôle unique inventerait une précision que la donnée n'a pas ; le
        # libellé reste lisible dans `role_detail`. Idem pour `project manager`,
        # `management`, `production manager` et `stylist`, sans équivalent dans
        # l'enum. Verrouillé par tests/test_discogs_api.py.

        # Chercher une correspondance, de la clé la PLUS SPÉCIFIQUE à la plus
        # générale. Jusqu'au 2026-09-03 la table était parcourue dans son ordre
        # d'insertion et rendait la PREMIÈRE sous-chaîne trouvée : « producer »
        # arrivant avant « co-producer », six rôles sur vingt-sept étaient
        # INATTEIGNABLES malgré leur présence dans la table —
        #   co-producer / executive producer / vocal producer → PRODUCER
        #   assistant engineer                                → ENGINEER
        #   lead vocals / backing vocals                      → VOCALS
        # Le tri par longueur décroissante est stable : à longueur égale l'ordre
        # d'insertion (donc le comportement historique) est conservé.
        for discogs_role in sorted(role_mapping, key=len, reverse=True):
            if _sans_tiret(discogs_role) in role_lower:
                return role_mapping[discogs_role]

        # Pas de correspondance → OTHER
        return CreditRole.OTHER

    # ── Formations (lot 3) ───────────────────────────────────────────────────

    def _candidats_formation(self, nom: str) -> list:
        """Artistes Discogs dont le nom, SUFFIXE RETIRÉ, est exactement le nôtre."""
        cible = normalize_name(nom)
        if not cible:
            return []
        with source_usage.observe(_SOURCE, label=f"recherche artiste {nom}") as obs:
            resultats = list(self.client.search(nom, type="artist").page(0))
            exacts = [a for a in resultats if normalize_name(nom_sans_suffixe(a.name)) == cible]
            if not exacts:
                obs.absent()
            return exacts

    def candidats_artiste(self, nom: str) -> list[tuple[int, str]]:
        """`(id, nom)` des homonymes EXACTS (suffixe retiré) — le repli annuaire
        de l'oracle `services/discogs_identite`."""
        return [(int(a.id), a.name) for a in self._candidats_formation(nom)]

    def artistes_du_disque(self, release_id: int) -> list[tuple[int, str]] | None:
        """`(id, nom)` des artistes crédités d'un disque (pas les `extraartists`),
        ou `None` s'il est illisible."""
        disque = self.lire_disque(release_id)
        return None if disque is None else disque["artistes"]

    def lire_disque(self, release_id: int) -> dict | None:
        """Ce qu'il faut pour juger l'identité d'un disque : ses artistes et,
        piste par piste, le titre et les artistes crédités (compilations).

        `release.*` est lazy-loadé par discogs_client : le premier accès EST la
        requête (une seule par disque). Un disque illisible rend `None` (on ne
        conclut pas), jamais une liste vide — qui voudrait dire « personne
        n'est crédité ».

        Returns:
            ``{"titre", "artistes": [(id, nom)], "pistes": [(titre, [(id, nom)])]}``
        """
        with source_usage.observe(_SOURCE, label=f"disque {release_id}") as obs:
            try:
                with source_usage.attempt(_SOURCE, detail="release"):
                    release = self.client.release(int(release_id))
                    out = {
                        "titre": release.title,
                        "artistes": self._artistes_de(release),
                        "pistes": [
                            (piste.title, self._artistes_de(piste))
                            for piste in (release.tracklist or [])
                        ],
                    }
            except HTTPError as e:
                obs.note_status(e.status_code or 0)
                logger.warning(f"Discogs : disque {release_id} illisible ({e})")
                return None
            except (DiscogsAPIError, requests.RequestException) as e:
                obs.fail(IssueKind.UNREACHABLE, f"disque {release_id} : {e}")
                logger.warning(f"Discogs : disque {release_id} illisible ({e})")
                return None
            except (AttributeError, TypeError, ValueError) as e:
                obs.parse_error(f"disque {release_id} : {e}")
                logger.warning(f"Discogs : disque {release_id} inexploitable ({e})")
                return None
            obs.ok()
            return out

    def get_artist_groups(
        self,
        nom: str,
        attendues: set[str] | None = None,
        artist_id: int | None = None,
    ) -> dict:
        """Ce que Discogs sait des formations de cet artiste.

        **Identité connue** (`artist_id`, tranché par l'oracle
        `services/discogs_identite` — disques déjà rattachés, ou mémorisé) :
        la fiche est lue DIRECTEMENT (une requête, aucune recherche), ses liens
        sont des propositions et `confirmees` = liens ∩ `attendues`. C'est ce
        qui débloque les 14 artistes sur 25 qui ont des homonymes Discogs
        (mesuré 2026-09-23) : sans identité, ils ne recevaient rien.

        **Sans identité**, Discogs ne peut pas trancher seul — « Swing » rend
        SEPT homonymes exacts une fois le suffixe retiré :

          · **un seul candidat** (« Shurik'n ») → ses groupes, membres et alias
            deviennent des PROPOSITIONS — sauf **contradiction** : si
            MusicBrainz a nommé des formations (`attendues` non vides) et
            qu'AUCUN de ses liens ne les recoupe, ce candidat unique n'est
            probablement pas le nôtre, et rien n'est proposé ;
          · **plusieurs candidats** → on ne propose rien, mais on peut encore
            CONFIRMER : si UN d'eux déclare une des formations `attendues`,
            l'ambiguïté est levée par la chose même qu'on cherchait à vérifier.
            Si DEUX homonymes confirment, elle ne l'est pas : rien (l'ancienne
            boucle gardait silencieusement les liens du dernier).

        Returns:
            ``{"proposees": [ArtistRelation], "confirmees": {noms},
            "candidats": n, "diagnostic": str | None}``
        """
        attendues_norm = {normalize_name(f) for f in (attendues or set())}

        if artist_id is not None:
            with (
                source_usage.observe(_SOURCE, label=f"formations #{artist_id}"),
                source_usage.attempt(_SOURCE, detail="artist"),
            ):
                liens = self._liens_de(self.client.artist(int(artist_id)))
            noms_lies = {normalize_name(rel.related_name) for rel in liens}
            return {
                "proposees": liens,
                "confirmees": noms_lies & attendues_norm,
                "candidats": 1,
                "diagnostic": None,
            }

        candidats = self._candidats_formation(nom)
        proposees: list[ArtistRelation] = []
        confirmees: set[str] = set()
        diagnostic = None
        confirmants = 0

        for artiste in candidats:
            with source_usage.observe(_SOURCE, label=f"formations {artiste.name}"):
                liens = self._liens_de(artiste)
            noms_lies = {normalize_name(rel.related_name) for rel in liens}
            communs = noms_lies & attendues_norm
            if communs:
                confirmants += 1
                confirmees |= communs
                if len(candidats) > 1:
                    # L'ambiguïté est levée : ce candidat-là est le bon, ses
                    # autres liens valent donc aussi comme propositions.
                    proposees = liens
            elif len(candidats) == 1:
                if attendues_norm:
                    diagnostic = (
                        f"Discogs : l'unique « {artiste.name} » ne déclare aucune des "
                        "formations nommées par MusicBrainz — probablement un autre "
                        "artiste, rien n'en est tiré."
                    )
                else:
                    proposees = liens

        if confirmants > 1:
            diagnostic = (
                f"Discogs : {confirmants} homonymes de « {nom} » déclarent les formations "
                "attendues — ambigu, rien n'en est tiré."
            )
            proposees, confirmees = [], set()
        elif len(candidats) > 1 and not confirmees:
            logger.info(
                f"Discogs : {len(candidats)} homonymes exacts pour « {nom} » et aucun ne "
                "déclare les formations attendues — aucune confirmation, et rien d'inventé."
            )
        if diagnostic:
            logger.info(diagnostic)
        return {
            "proposees": proposees,
            "confirmees": confirmees,
            "candidats": len(candidats),
            "diagnostic": diagnostic,
        }

    @staticmethod
    def _liens_de(artiste) -> list:
        """Groupes, membres et alias d'un artiste Discogs, en `ArtistRelation`.

        **Le numéro d'homonyme est retiré ICI, à l'entrée** (2026-09-23). La
        version précédente gardait le nom verbatim en promettant de retirer le
        suffixe « à la comparaison » — ce qui n'arrivait jamais : `normalize_name`
        garde « (4) ». Mesuré en base : `667 (4)` en double de `667`, `CFR (2)`
        et `Moon Man (9)`, liens jamais résolus vers un artiste de la base et
        envoyés tels quels à la recherche de certifs. Deux liens qui ne
        diffèrent que par le suffixe n'en font qu'un.
        """
        liens: list[ArtistRelation] = []
        vus: set[tuple[str, str]] = set()

        def ajouter(nom: str, kind: str, detail: str | None = None) -> None:
            propre = nom_sans_suffixe(nom)
            cle = (normalize_name(propre), kind)
            if not cle[0] or cle in vus:
                return
            vus.add(cle)
            liens.append(
                ArtistRelation(related_name=propre, kind=kind, source="discogs", detail=detail)
            )

        for groupe in artiste.groups or []:
            ajouter(groupe.name, "member_of")
        for membre in artiste.members or []:
            ajouter(membre.name, "has_member")
        for alias in artiste.aliases or []:
            ajouter(alias.name, "alias")
        # Variantes de graphie (« ISHA ») : pas des identités, de simples
        # orthographes — gardées POUR INFO, jamais proposées.
        for variante in artiste.name_variations or []:
            ajouter(str(variante), "alias", "name_variation")
        return liens

    def enrich_track_data(self, track: Track, force_update: bool = False) -> bool | str:
        """
        Enrichit un track avec les données depuis Discogs

        Args:
            track: Objet Track à enrichir
            force_update: Si True, écrase les données existantes

        Returns:
            True si des données NOUVELLES ont été ajoutées ;
            "not_needed" si la release a matché mais que rien de nouveau n'était
            à poser (données déjà présentes, typiquement prises à la phase
            scraping) — ce n'est PAS un échec (exclu du calcul `all_failed`, log
            neutre) ;
            False si aucune release ne matche (vrai échec / absence).
        """
        try:
            artist_name = track.artist.name if hasattr(track.artist, "name") else str(track.artist)
            album_name = track.album

            # Rechercher le track sur Discogs. Pour un feat, le disque est celui
            # de l'artiste PRINCIPAL : il est accepté aussi. L'identité Discogs
            # de l'artiste (oracle `discogs_identite`) ne vaut que hors feat.
            attendus = {artist_name}
            if track.primary_artist_name:
                attendus.add(track.primary_artist_name)
            artist_discogs_id = (
                track.artist.discogs_id
                if isinstance(track.artist, Artist) and not track.is_featuring
                else None
            )
            track_data = self.search_track(
                track.title,
                artist_name,
                album_name,
                noms_attendus=attendus,
                artist_discogs_id=artist_discogs_id,
            )

            if not track_data:
                logger.warning(f"⚠️ Aucune donnée Discogs pour '{track.title}'")
                return False

            updated = False

            # Discogs ID
            if track_data.get("discogs_id") and (force_update or not track.discogs_id):
                track.discogs_id = track_data["discogs_id"]
                logger.info(f"💿 Discogs ID ajouté: {track.discogs_id}")
                updated = True

            # Genre (si pas déjà présent)
            if track_data.get("genres") and (force_update or not track.genre):
                # Joindre genres + styles
                genres = track_data["genres"]
                if track_data.get("styles"):
                    genres = genres + track_data["styles"]
                track.genre = ", ".join(genres[:3])  # Max 3 genres
                logger.info(f"🎵 Genre ajouté depuis Discogs: {track.genre}")
                updated = True

            # Labels
            if track_data.get("labels"):
                labels_str = ", ".join(track_data["labels"])
                logger.info(f"🏷️ Labels Discogs: {labels_str}")
                # Peut être stocké dans un champ custom ou ignoré

            # Crédits
            if track_data.get("credits"):
                # IDEMPOTENCE : purger nos propres crédits AVANT de réécrire.
                # `Track.add_credit` dédoublonne sur (name, role, role_detail) —
                # un ré-import avec des rôles CORRIGÉS ajoutait donc le nouveau
                # crédit À CÔTÉ de l'ancien au lieu de le remplacer, puisque la
                # clé de dédup avait justement changé. Purger nos lignes rend
                # l'opération rejouable ; les crédits des AUTRES sources ne sont
                # pas touchés (Discogs est additif, il ne confirme pas Genius —
                # mesuré le 2026-09-03 : 818 paires propres à Discogs contre 22
                # concordantes).
                track.credits = [c for c in track.credits if c.source != "discogs"]

                # Discogs crédite au niveau du DISQUE et dit à quelles PISTES
                # chaque crédit se rapporte (`tracks`). Ce champ était stocké et
                # jamais lu : « Mr Hudson — tracks: C2 » atterrissait sur les 14
                # morceaux de *808s & Heartbreak*, et les 16 auteurs de *Man on
                # the Moon II* sur ses 15 titres — 1 460 attributions en trop
                # mesurées le 2026-09-22. Un champ vide vaut toujours pour tout
                # le disque (producteur exécutif, label, graphiste).
                position = track_data.get("position")
                credits_added = 0
                ignores = 0
                for credit_dict in track_data["credits"]:
                    if not concerne_la_piste(credit_dict.get("tracks"), position):
                        ignores += 1
                        continue
                    try:
                        role_enum = self._map_discogs_role_to_enum(credit_dict["role"])

                        credit = Credit(
                            name=credit_dict["name"],
                            role=role_enum,
                            # `role_detail` qualifie le RÔLE — donc le libellé brut
                            # quand il tombe en OTHER, et rien sinon (le rôle mappé
                            # dit déjà tout). Convention commune aux trois écrivains
                            # (Genius scraper ×2, `Track.add_credit_from_role`).
                            #
                            # Les PISTES ont leur propre champ depuis e18. Les deux
                            # se disputaient la colonne : `pistes or (libellé si OTHER)`
                            # perdait le libellé, l'inverse perdait les pistes. Mesuré
                            # sur la base réelle : 209 lignes portaient une référence de
                            # piste dans `role_detail`, dont 21 en OTHER — les seules
                            # nuisibles, `Track.get_video_credits` reclassant un OTHER
                            # en crédit VIDÉO d'après les MOTS de `role_detail`.
                            role_detail=(
                                credit_dict["role"] if role_enum == CreditRole.OTHER else None
                            ),
                            tracks=credit_dict.get("tracks"),
                            source="discogs",
                        )

                        track.add_credit(credit)
                        credits_added += 1

                    except (KeyError, TypeError, ValueError) as e:
                        logger.debug(f"Erreur ajout crédit: {e}")
                        continue

                if ignores:
                    logger.debug(
                        f"Discogs : {ignores} crédit(s) de disque écarté(s) pour "
                        f"'{track.title}' (position {position!r} hors de leurs pistes)"
                    )
                if credits_added > 0:
                    logger.info(f"✅ {credits_added} crédit(s) Discogs ajouté(s) à '{track.title}'")
                    updated = True

            if updated:
                return True

            # Release matchée mais rien de NOUVEAU à poser (données déjà
            # présentes) : ce n'est pas un échec — distinguer de l'absence de
            # match (False plus haut) pour ne pas fausser le nettoyage.
            logger.info(
                f"ℹ️ Discogs : release matchée, aucune donnée nouvelle pour '{track.title}'"
            )
            return "not_needed"

        except Exception:
            # Dernier ressort : search_track et les boucles de crédits gèrent déjà
            # leurs frontières ; un bug de notre logique remonte ici → trace complète.
            logger.exception(f"❌ Erreur enrichissement Discogs pour '{track.title}'")
            return False
