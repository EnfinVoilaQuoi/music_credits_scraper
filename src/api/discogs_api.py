"""Client pour l'API Discogs - Enrichissement des crédits et métadonnées"""

import os
import re
import time
from typing import Any

import discogs_client
from discogs_client.exceptions import DiscogsAPIError, HTTPError

from src.models import ArtistRelation, Credit, CreditRole, Track
from src.observability import source_usage
from src.observability.issues import IssueKind
from src.utils.logger import get_logger, log_api
from src.utils.title_matching import normalize_name

logger = get_logger(__name__)

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

#: Discogs désambiguïse les homonymes par un SUFFIXE NUMÉRIQUE : notre rappeur
#: belge s'appelle « Swing (20) », pas « Swing ». Mesuré le 2026-09-08, et le
#: piège est vicieux : sans retirer ce suffixe, la recherche « Swing » ne rend
#: qu'UN homonyme exact — « Swing » tout court, qui n'est PAS le nôtre. On
#: obtiendrait donc une confirmation confiante et fausse, ce qui est pire que
#: pas de confirmation du tout. Le motif est volontairement étroit (un entier
#: nu, en fin de nom) pour ne pas amputer un titre légitimement parenthésé.
_SUFFIXE_HOMONYME = re.compile(r"\s*\(\d+\)$")


def nom_sans_suffixe(nom: str) -> str:
    """« Swing (20) » → « Swing ». Fonction pure."""
    return _SUFFIXE_HOMONYME.sub("", nom or "").strip()


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
        self, track_title: str, artist_name: str, album_name: str | None = None
    ) -> dict[str, Any] | None:
        """
        Recherche un morceau sur Discogs

        Args:
            track_title: Titre du morceau
            artist_name: Nom de l'artiste
            album_name: Nom de l'album (optionnel mais améliore la précision)

        Returns:
            Dictionnaire avec les infos du morceau ou None
        """
        if not self.client:
            logger.error("❌ Client Discogs non initialisé")
            return None

        with source_usage.observe(_SOURCE, label=f"{artist_name} — {track_title}") as obs:
            return self._search_track_body(obs, track_title, artist_name, album_name)

    def _search_track_body(
        self, obs, track_title: str, artist_name: str, album_name: str | None
    ) -> dict[str, Any] | None:
        """Corps de `search_track`, sous l'observation ouverte par elle."""
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
            obs.absent("aucune correspondance exacte")
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

    def _normalize_string(self, s: str) -> str:
        """Normalise une chaîne pour la comparaison"""
        import re

        # Unifier les apostrophes typographiques (' ' ` ´) → apostrophe droite
        for apo in ("’", "‘", "`", "´"):
            s = s.replace(apo, "'")
        # Minuscules, sans accents, sans caractères spéciaux
        s = s.lower().strip()
        # Retirer les parenthèses/crochets
        s = re.sub(r"\s*[\(\)\[\]].*?[\(\)\[\]]", "", s)
        s = re.sub(r"\s*[\(\)\[\]]", "", s)
        # Retirer feat/ft
        s = re.sub(r"\s*\(?f(ea)?t\.?\s+.*", "", s)
        return " ".join(s.split())

    def _map_discogs_role_to_enum(self, role: str) -> CreditRole:
        """
        Mappe un rôle Discogs vers un CreditRole

        Args:
            role: Rôle Discogs (ex: "Producer", "Mixed By", etc.)

        Returns:
            CreditRole correspondant
        """
        role_lower = role.lower().strip()

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
            if discogs_role in role_lower:
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

    def get_artist_groups(self, nom: str, attendues: set[str] | None = None) -> dict:
        """Ce que Discogs sait des formations de cet artiste.

        Discogs est la source de **confirmation** du lot 3, pas la source
        primaire — et sa façon de désambiguïser lui interdit de trancher seul :
        « Swing » rend SEPT homonymes exacts une fois le suffixe retiré. Choisir
        parmi eux sans oracle reviendrait à jouer à pile ou face sur l'identité
        de quelqu'un.

        D'où deux régimes, et c'est tout l'intérêt :

          · **un seul candidat** (« Shurik'n ») → on lit ses groupes, ses membres
            et ses alias, qui deviennent des PROPOSITIONS ;
          · **plusieurs candidats** → on ne propose rien, mais on peut encore
            CONFIRMER : si l'un d'eux déclare une des formations `attendues`
            (celles que MusicBrainz vient de nommer), l'ambiguïté est levée par
            la chose même qu'on cherchait à vérifier.

        Returns:
            ``{"proposees": [ArtistRelation], "confirmees": {noms}, "candidats": n}``
        """
        candidats = self._candidats_formation(nom)
        attendues_norm = {normalize_name(f) for f in (attendues or set())}
        proposees: list[ArtistRelation] = []
        confirmees: set[str] = set()

        for artiste in candidats:
            with source_usage.observe(_SOURCE, label=f"formations {artiste.name}"):
                liens = self._liens_de(artiste)
            noms_lies = {normalize_name(nom_sans_suffixe(rel.related_name)) for rel in liens}
            communs = noms_lies & attendues_norm
            if communs:
                confirmees |= communs
                if len(candidats) > 1:
                    # L'ambiguïté est levée : ce candidat-là est le bon, ses
                    # autres liens valent donc aussi comme propositions.
                    proposees = liens
            elif len(candidats) == 1:
                proposees = liens

        if len(candidats) > 1 and not confirmees:
            logger.info(
                f"Discogs : {len(candidats)} homonymes exacts pour « {nom} » et aucun ne "
                "déclare les formations attendues — aucune confirmation, et rien d'inventé."
            )
        return {"proposees": proposees, "confirmees": confirmees, "candidats": len(candidats)}

    @staticmethod
    def _liens_de(artiste) -> list:
        """Groupes, membres et alias d'un artiste Discogs, en `ArtistRelation`.

        Le nom est conservé VERBATIM (suffixe compris) : c'est ce que Discogs
        affiche, et le retirer ici ferait perdre l'information qui distingue
        « Swing (20) » de « Swing (6) ». Le retrait n'a lieu qu'à la COMPARAISON.
        """
        liens = []
        for groupe in artiste.groups or []:
            liens.append(
                ArtistRelation(related_name=groupe.name, kind="member_of", source="discogs")
            )
        for membre in artiste.members or []:
            liens.append(
                ArtistRelation(related_name=membre.name, kind="has_member", source="discogs")
            )
        for alias in artiste.aliases or []:
            liens.append(ArtistRelation(related_name=alias.name, kind="alias", source="discogs"))
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

            # Rechercher le track sur Discogs
            track_data = self.search_track(track.title, artist_name, album_name)

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

                credits_added = 0
                for credit_dict in track_data["credits"]:
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
