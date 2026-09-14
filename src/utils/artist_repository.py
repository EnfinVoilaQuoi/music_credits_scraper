"""Repository des artistes.

Persistance liée aux `artists` : save/get/delete, détails, canal YTM, totaux
Kworb, auditeurs mensuels + historique. Utilisé comme base de `DataManager`,
qui fournit `self.engine` (moteur SQLAlchemy Core, délégué à `Database`) et, via
`TrackRepository`, `self.get_artist_tracks` (utilisé par get_artist_by_name).
Comportement constant vs l'ancien `data_manager.py` (refonte 1.5 puis bascule
Core phase E2).
"""

from datetime import datetime
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.exc import SQLAlchemyError

from src.models import Artist, ArtistRelation, Track
from src.persistence.binding import date_bind
from src.persistence.schema import artists, monthly_listeners_history
from src.utils.logger import get_logger
from src.utils.title_matching import names_match_as_words, normalize_name

logger = get_logger(__name__)


def _signe_par_une_formation(track, formations: set[str]) -> bool:
    """Ce morceau est-il signé par une formation dont l'artiste est membre ?

    On regarde l'artiste PRINCIPAL du morceau — celui qui le signe — et non
    l'artiste de la ligne : c'est justement le cas où les deux diffèrent
    (Shurik'n crédité « Additional Voices » sur un morceau d'IAM).

    Comparaison par MOTS ENTIERS, jamais par sous-chaîne : « IAM » ⊂ « Williams »
    (JOURNAL 2026-09-04).
    """
    signataire = track.primary_artist_name or (track.artist.name if track.artist else None)
    if not signataire or not formations:
        return False
    return any(names_match_as_words(signataire, nom) for nom in formations)


class ArtistRepository:
    """Persistance des artistes. Requiert `self.engine` et
    `self.get_artist_tracks` (fournis par DataManager/TrackRepository)."""

    def save_artist(self, artist: Artist) -> int:
        """Sauvegarde ou met à jour un artiste"""
        with self.engine.begin() as conn:
            if artist.id:
                # Mise à jour
                conn.execute(
                    text(
                        "UPDATE artists SET name = :name, genius_id = :genius_id, "
                        "spotify_id = :spotify_id, discogs_id = :discogs_id, updated_at = :now "
                        "WHERE id = :id"
                    ),
                    {
                        "name": artist.name,
                        "genius_id": artist.genius_id,
                        "spotify_id": artist.spotify_id,
                        "discogs_id": artist.discogs_id,
                        "now": datetime.now(),
                        "id": artist.id,
                    },
                )
            else:
                # Insertion (OR IGNORE si l'artiste existe déjà par contrainte UNIQUE)
                result = conn.execute(
                    text(
                        "INSERT OR IGNORE INTO artists (name, genius_id, spotify_id, "
                        "discogs_id, created_at, updated_at) "
                        "VALUES (:name, :genius_id, :spotify_id, :discogs_id, :now, :now)"
                    ),
                    {
                        "name": artist.name,
                        "genius_id": artist.genius_id,
                        "spotify_id": artist.spotify_id,
                        "discogs_id": artist.discogs_id,
                        "now": datetime.now(),
                    },
                )
                # rowcount == 1 → ligne insérée (nouvel id) ; 0 → déjà présente
                # (IGNORE) : on relit l'id par nom. Signal plus fiable que
                # lastrowid après un INSERT OR IGNORE ignoré.
                if result.rowcount:
                    artist.id = result.lastrowid
                else:
                    row = (
                        conn.execute(
                            text("SELECT id FROM artists WHERE name = :name"),
                            {"name": artist.name},
                        )
                        .mappings()
                        .first()
                    )
                    if row:
                        artist.id = row["id"]

            logger.info(f"Artiste sauvegardé: {artist.name} (ID: {artist.id})")
            return artist.id

    def get_artist_names(self) -> list[str]:
        """Tous les noms d'artistes en base (référence pour repérer les fichiers
        annexes orphelins : désactivations, suppressions…)."""
        with self.engine.connect() as conn:
            return [r[0] for r in conn.execute(select(artists.c.name)).fetchall()]

    def get_artist_by_name(self, name: str) -> Artist | None:
        """Récupère un artiste par son nom - VERSION CORRIGÉE"""
        try:
            logger.debug(f"🔍 Recherche de l'artiste: '{name}'")

            with self.engine.connect() as conn:
                row = (
                    conn.execute(
                        select(
                            artists.c.id,
                            artists.c.name,
                            artists.c.genius_id,
                            artists.c.spotify_id,
                            artists.c.discogs_id,
                            artists.c.image_path,
                        ).where(artists.c.name == name)
                    )
                    .mappings()
                    .first()
                )

            if not row:
                logger.debug(f"❌ Aucun artiste trouvé pour: '{name}'")
                return None

            artist = Artist(
                id=row["id"],
                name=row["name"],
                genius_id=row["genius_id"],
                spotify_id=row["spotify_id"],
                discogs_id=row["discogs_id"],
                image_path=row["image_path"],
            )

            logger.debug(f"🎤 Objet Artist créé: {artist.name} (ID: {artist.id})")

            # Charger les tracks (ouvre sa propre connexion moteur)
            try:
                artist.tracks = self.get_artist_tracks(artist.id)
                logger.info(f"🎵 {len(artist.tracks)} morceaux chargés pour {artist.name}")
            except SQLAlchemyError as tracks_error:
                logger.error(f"⚠️ Erreur chargement tracks: {tracks_error}")
                artist.tracks = []

            return artist

        except SQLAlchemyError as e:
            logger.error(f"❌ Erreur dans get_artist_by_name: {e}")
            return None

    def delete_artist(self, artist_name: str) -> bool:
        """Supprime un artiste et toutes ses données associées"""
        try:
            with self.engine.begin() as conn:
                # Récupérer l'ID de l'artiste
                artist_row = (
                    conn.execute(
                        text("SELECT id FROM artists WHERE name = :name"), {"name": artist_name}
                    )
                    .mappings()
                    .first()
                )

                if not artist_row:
                    logger.warning(f"Artiste non trouvé: {artist_name}")
                    return False

                artist_id = artist_row["id"]

                # Supprimer dans l'ordre (contraintes de clés étrangères)

                # 1. Supprimer les erreurs de scraping
                deleted_errors = conn.execute(
                    text(
                        "DELETE FROM scraping_errors WHERE track_id IN "
                        "(SELECT id FROM tracks WHERE artist_id = :aid)"
                    ),
                    {"aid": artist_id},
                ).rowcount

                # 2. Supprimer les crédits
                deleted_credits = conn.execute(
                    text(
                        "DELETE FROM credits WHERE track_id IN "
                        "(SELECT id FROM tracks WHERE artist_id = :aid)"
                    ),
                    {"aid": artist_id},
                ).rowcount

                # 2b. Supprimer les observations (pas de cascade FK, E4)
                conn.execute(
                    text(
                        "DELETE FROM observations WHERE track_id IN "
                        "(SELECT id FROM tracks WHERE artist_id = :aid)"
                    ),
                    {"aid": artist_id},
                )

                # 2c. Supprimer les vidéos des morceaux (e20) — même absence de
                # cascade FK : sans ça, des lignes pointeraient sur des morceaux
                # supprimés et resteraient invisibles autant qu'indélébiles.
                conn.execute(
                    text(
                        "DELETE FROM track_videos WHERE track_id IN "
                        "(SELECT id FROM tracks WHERE artist_id = :aid)"
                    ),
                    {"aid": artist_id},
                )

                # 3. Supprimer les morceaux
                deleted_tracks = conn.execute(
                    text("DELETE FROM tracks WHERE artist_id = :aid"), {"aid": artist_id}
                ).rowcount

                # 3b. Supprimer les liens de formation (e22), DANS LES DEUX SENS :
                # l'artiste supprimé est aussi cité comme `related_artist_id`
                # chez ses coéquipiers, dont la discographie réunie irait alors
                # chercher un artiste qui n'existe plus.
                conn.execute(
                    text(
                        "DELETE FROM artist_relations "
                        "WHERE artist_id = :aid OR related_artist_id = :aid"
                    ),
                    {"aid": artist_id},
                )

                # 4. Supprimer l'artiste
                deleted_artist = conn.execute(
                    text("DELETE FROM artists WHERE id = :aid"), {"aid": artist_id}
                ).rowcount

                logger.info(f"Artiste '{artist_name}' supprimé avec succès:")
                logger.info(f"  - {deleted_tracks} morceaux")
                logger.info(f"  - {deleted_credits} crédits")
                logger.info(f"  - {deleted_errors} erreurs de scraping")

                return deleted_artist > 0

        except SQLAlchemyError as e:
            logger.error(f"Erreur lors de la suppression de l'artiste: {e}")
            return False

    def get_artist_details(self, artist_name: str) -> dict[str, Any]:
        """Récupère les détails complets d'un artiste"""
        # Agrégats multi-tables (LEFT JOIN, GROUP BY, HAVING) exécutés en `text()`
        # verbatim via le moteur Core : fidélité comportementale maximale (une
        # traduction en `select()` n'apporterait rien ici et risquerait un écart
        # de tri/regroupement). Paramètres nommés (`:name`/`:aid`).
        try:
            with self.engine.connect() as conn:
                # Informations de base de l'artiste
                artist_row = (
                    conn.execute(
                        text(
                            "SELECT id, name, genius_id, spotify_id, discogs_id, "
                            "created_at, updated_at FROM artists WHERE name = :name"
                        ),
                        {"name": artist_name},
                    )
                    .mappings()
                    .first()
                )
                if not artist_row:
                    return {}

                artist_id = artist_row["id"]

                # Compter les morceaux et crédits
                counts = (
                    conn.execute(
                        text(
                            "SELECT COUNT(DISTINCT t.id) as tracks_count, "
                            "COUNT(DISTINCT c.id) as credits_count "
                            "FROM tracks t LEFT JOIN credits c ON t.id = c.track_id "
                            "WHERE t.artist_id = :aid"
                        ),
                        {"aid": artist_id},
                    )
                    .mappings()
                    .first()
                )

                # Morceaux récents
                recent_tracks = [
                    {
                        "title": row["title"],
                        "album": row["album"],
                        "release_date": row["release_date"],
                        "credits_count": row["credits_count"],
                    }
                    for row in conn.execute(
                        text(
                            "SELECT t.title, t.album, t.release_date, "
                            "COUNT(c.id) as credits_count "
                            "FROM tracks t LEFT JOIN credits c ON t.id = c.track_id "
                            "WHERE t.artist_id = :aid "
                            "GROUP BY t.id, t.title, t.album, t.release_date "
                            "ORDER BY t.updated_at DESC LIMIT 20"
                        ),
                        {"aid": artist_id},
                    ).mappings()
                ]

                # Crédits par rôle
                credits_by_role = {
                    row["role"]: row["count"]
                    for row in conn.execute(
                        text(
                            "SELECT role, COUNT(*) as count "
                            "FROM credits c JOIN tracks t ON c.track_id = t.id "
                            "WHERE t.artist_id = :aid GROUP BY role ORDER BY count DESC"
                        ),
                        {"aid": artist_id},
                    ).mappings()
                }

                return {
                    "name": artist_row["name"],
                    "genius_id": artist_row["genius_id"],
                    "spotify_id": artist_row["spotify_id"],
                    "discogs_id": artist_row["discogs_id"],
                    "created_at": artist_row["created_at"],
                    "updated_at": artist_row["updated_at"],
                    "tracks_count": counts["tracks_count"] if counts else 0,
                    "credits_count": counts["credits_count"] if counts else 0,
                    "recent_tracks": recent_tracks,
                    "credits_by_role": credits_by_role,
                }

        except SQLAlchemyError as e:
            logger.error(f"Erreur lors de la récupération des détails: {e}")
            return {}

    def get_artist_ytm_channel(self, artist_id: int):
        """Canal YTMusic épinglé pour cet artiste (UC...), ou None."""
        try:
            with self.engine.connect() as conn:
                row = (
                    conn.execute(select(artists.c.ytm_channel_id).where(artists.c.id == artist_id))
                    .mappings()
                    .first()
                )
                return row["ytm_channel_id"] if row and row["ytm_channel_id"] else None
        except SQLAlchemyError as e:
            logger.error(f"Erreur get_artist_ytm_channel: {e}")
            return None

    def get_artist_ytm_channel_info(self, artist_id: int) -> tuple:
        """Canal YTM épinglé + son origine : `(channel_id|None, source|None)`.

        `source ∈ {'manual', 'inferred'}` distingue une saisie GUI (jamais
        écrasée/bloquée) d'un vote (ré-effaçable si le gate le juge suspect).
        Artiste sans canal → `(None, None)`.
        """
        try:
            with self.engine.connect() as conn:
                row = (
                    conn.execute(
                        select(artists.c.ytm_channel_id, artists.c.ytm_channel_source).where(
                            artists.c.id == artist_id
                        )
                    )
                    .mappings()
                    .first()
                )
                if not row or not row["ytm_channel_id"]:
                    return (None, None)
                return (row["ytm_channel_id"], row["ytm_channel_source"])
        except SQLAlchemyError as e:
            logger.error(f"Erreur get_artist_ytm_channel_info: {e}")
            return (None, None)

    def set_artist_ytm_channel(
        self, artist_id: int, channel_id: str, source: str = "manual"
    ) -> bool:
        """Épingle le canal YTMusic d'un artiste (résout les homonymes).

        `source` par défaut = 'manual' (compat ascendante : la GUI épingle
        toujours du manuel). Le flux d'inférence passe `source='inferred'`.
        """
        try:
            stmt = (
                update(artists)
                .where(artists.c.id == artist_id)
                .values(ytm_channel_id=channel_id, ytm_channel_source=source)
            )
            with self.engine.begin() as conn:
                conn.execute(stmt)
            logger.info(f"📌 Canal YTM épinglé pour artist_id={artist_id}: {channel_id} ({source})")
            return True
        except SQLAlchemyError as e:
            logger.error(f"Erreur set_artist_ytm_channel: {e}")
            return False

    def clear_artist_ytm_channel(self, artist_id: int) -> bool:
        """Dé-épingle le canal YTM (les deux colonnes à NULL).

        Utilisé quand le gate d'identité juge suspect un canal INFÉRÉ : on
        efface pour ne pas figer l'erreur (au prochain run, ré-inférence)."""
        try:
            stmt = (
                update(artists)
                .where(artists.c.id == artist_id)
                .values(ytm_channel_id=None, ytm_channel_source=None)
            )
            with self.engine.begin() as conn:
                conn.execute(stmt)
            logger.info(f"📌 Canal YTM dé-épinglé pour artist_id={artist_id}")
            return True
        except SQLAlchemyError as e:
            logger.error(f"Erreur clear_artist_ytm_channel: {e}")
            return False

    # ──────────────────────────────────────────────────────────────────────────
    # Appartenance à une formation (table `artist_relations`, e22)
    # ──────────────────────────────────────────────────────────────────────────

    def record_artist_relations(self, artist_id: int, relations) -> int:
        """Enregistre des liens CONFIRMÉS. Écrivain dédié, jamais `save_artist`.

        La règle du 2026-09-06 s'applique telle quelle : une façade générique ne
        peut pas écrire une donnée que la plupart de ses appelants ignorent, sous
        peine de rendre tout retrait impossible.

        **Additif**, comme les vidéos : MusicBrainz et Discogs ne voient pas les
        mêmes formations, et une passe qui remplacerait tout ferait perdre ce que
        l'autre a trouvé. Le retrait est explicite (`forget_artist_relation`) —
        c'est le geste que fait l'utilisateur quand il décoche un lien.

        `related_artist_id` est résolu ICI par le NOM, en base : le groupe entre
        souvent dans la discothèque APRÈS le lien qui le mentionne, et on veut
        que la jointure se fasse alors sans avoir à re-confirmer quoi que ce soit.

        Returns:
            Nombre de liens écrits (insérés ou mis à jour).
        """
        relations = [r for r in (relations or []) if r and r.related_name and r.kind]
        if not relations:
            return 0
        maintenant = datetime.now()
        ecrits = 0
        try:
            with self.engine.begin() as conn:
                for rel in relations:
                    lie = rel.related_artist_id or self._id_par_nom(conn, rel.related_name)
                    deja = conn.execute(
                        text(
                            "SELECT id FROM artist_relations WHERE artist_id = :aid "
                            "AND related_name = :nom AND kind = :kind"
                        ),
                        {"aid": artist_id, "nom": rel.related_name, "kind": rel.kind},
                    ).first()
                    params = {
                        "aid": artist_id,
                        "lie": lie,
                        "nom": rel.related_name,
                        "kind": rel.kind,
                        "source": rel.source,
                        "formation": rel.formation,
                        "debut": rel.begin_date,
                        "fin": rel.end_date,
                        "quand": maintenant,
                    }
                    if deja:
                        conn.execute(
                            text(
                                "UPDATE artist_relations SET related_artist_id = :lie, "
                                "source = :source, formation = :formation, "
                                "begin_date = :debut, end_date = :fin, "
                                "confirmed_at = :quand WHERE artist_id = :aid "
                                "AND related_name = :nom AND kind = :kind"
                            ),
                            params,
                        )
                    else:
                        conn.execute(
                            text(
                                "INSERT INTO artist_relations (artist_id, related_artist_id, "
                                "related_name, kind, source, formation, begin_date, "
                                "end_date, confirmed_at, created_at) VALUES (:aid, :lie, "
                                ":nom, :kind, :source, :formation, :debut, :fin, :quand, :quand)"
                            ),
                            params,
                        )
                    ecrits += 1
            return ecrits
        except SQLAlchemyError as e:
            logger.error(f"Erreur record_artist_relations({artist_id}): {e}")
            return 0

    @staticmethod
    def _id_par_nom(conn, nom: str) -> int | None:
        """id de l'artiste portant CE nom, ou None s'il n'est pas en base.

        Comparaison par nom NORMALISÉ : MusicBrainz écrit « Shurik’n » là où
        notre base écrit « Shurik'N ». Une égalité brute raterait la jointure et
        laisserait le lien orphelin alors que l'artiste est là.
        """
        cible = normalize_name(nom)
        if not cible:
            return None
        for ligne in conn.execute(text("SELECT id, name FROM artists")).mappings():
            if normalize_name(ligne["name"]) == cible:
                return ligne["id"]
        return None

    def forget_artist_relation(self, artist_id: int, related_name: str, kind: str) -> bool:
        """Retire UN lien (l'utilisateur le décoche). Pendant de l'additivité."""
        try:
            with self.engine.begin() as conn:
                n = conn.execute(
                    text(
                        "DELETE FROM artist_relations WHERE artist_id = :aid "
                        "AND related_name = :nom AND kind = :kind"
                    ),
                    {"aid": artist_id, "nom": related_name, "kind": kind},
                ).rowcount
            return n > 0
        except SQLAlchemyError as e:
            logger.error(f"Erreur forget_artist_relation({artist_id}, {related_name!r}): {e}")
            return False

    def get_artist_relations(self, artist_id: int) -> list[ArtistRelation]:
        """Liens confirmés d'un artiste, ordre stable (nature puis nom).

        **L'identifiant de l'autre bout est résolu À LA LECTURE** quand il
        manque. C'est le cas NORMAL, et pas un accident : on confirme presque
        toujours « Swing est membre de L'Or du Commun » avant d'avoir ajouté le
        groupe à la discothèque. Sans cette résolution, le lien resterait mort
        jusqu'à ce que quelqu'un pense à rouvrir la fenêtre pour re-confirmer —
        un no-op silencieux, et l'utilisateur conclurait à juste titre que la
        fonctionnalité ne marche pas (constaté le 2026-09-08).

        La clé est donc le NOM ; l'identifiant n'est qu'un cache. Le résoudre
        ici le fait apparaître dès que l'artiste entre en base, sans geste.
        """
        try:
            with self.engine.connect() as conn:
                lignes = conn.execute(
                    text(
                        "SELECT related_artist_id, related_name, kind, source, "
                        "formation, begin_date, end_date FROM artist_relations "
                        "WHERE artist_id = :aid ORDER BY kind, related_name"
                    ),
                    {"aid": artist_id},
                ).mappings()
                relations = [
                    ArtistRelation(
                        related_name=r["related_name"],
                        kind=r["kind"],
                        related_artist_id=r["related_artist_id"],
                        source=r["source"],
                        formation=r["formation"],
                        begin_date=r["begin_date"],
                        end_date=r["end_date"],
                    )
                    for r in lignes
                ]
                for relation in relations:
                    if relation.related_artist_id is None:
                        relation.related_artist_id = self._id_par_nom(conn, relation.related_name)
                return relations
        except SQLAlchemyError as e:
            logger.error(f"Erreur get_artist_relations({artist_id}): {e}")
            return []

    def nature_connue_pour(self, related_name: str) -> str | None:
        """Nature déjà choisie pour CETTE formation, quel qu'en soit le membre.

        La nature est une propriété de la FORMATION : L'Animalerie est un
        collectif pour tout le monde. Elle est pourtant stockée sur le lien —
        parce que la formation n'est pas toujours en base — donc rien
        n'empêcherait structurellement qu'un membre la déclare « groupe » et un
        autre « collectif ». La fenêtre pré-remplit avec ce que renvoie cette
        méthode : la divergence devient un geste délibéré au lieu d'un oubli.

        Comparaison par nom NORMALISÉ, comme le rattachement.
        """
        cible = normalize_name(related_name)
        if not cible:
            return None
        try:
            with self.engine.connect() as conn:
                lignes = conn.execute(
                    text(
                        "SELECT related_name, formation FROM artist_relations "
                        "WHERE formation IS NOT NULL"
                    )
                ).mappings()
                for ligne in lignes:
                    if normalize_name(ligne["related_name"]) == cible:
                        return ligne["formation"]
        except SQLAlchemyError as e:
            logger.error(f"Erreur nature_connue_pour({related_name!r}): {e}")
        return None

    def ids_discographie_reunie(self, artist_id: int) -> list[int]:
        """`artist_id` + ceux de ses GROUPES, pour une lecture par UNION.

        La discographie d'un membre inclut ce qu'il a sorti avec ses groupes ; on
        l'obtient en élargissant la LECTURE, jamais en recopiant des morceaux —
        `UNIQUE(title, artist_id)` l'interdirait, et cela doublerait streams et
        certifications.

        **Ne concerne que les `groupe`.** Un COLLECTIF n'apporte pas tous ses
        morceaux : seulement ceux où le membre est présent, ce qui n'est pas une
        union d'identifiants mais un filtrage morceau par morceau — voir
        `discographie_reunie`.

        Seuls les liens `member_of` élargissent : un groupe ne récupère pas les
        albums solo de ses membres. IAM n'est pas l'auteur de « Où je vis ».

        Un seul niveau, volontairement : pas de transitivité. Un membre d'un
        groupe dont un membre est dans un autre groupe n'hérite pas du troisième.
        L'ordre est stable, l'artiste lui-même toujours en tête.
        """
        ids = [artist_id]
        for rel in self.get_artist_relations(artist_id):
            if (
                rel.kind == "member_of"
                and rel.formation != "collectif"
                and rel.related_artist_id not in (None, *ids)
            ):
                ids.append(rel.related_artist_id)
        return ids

    def ids_collectifs(self, artist_id: int) -> list[int]:
        """Identifiants des COLLECTIFS dont l'artiste est membre (et qui sont en base)."""
        return [
            rel.related_artist_id
            for rel in self.get_artist_relations(artist_id)
            if rel.kind == "member_of"
            and rel.formation == "collectif"
            and rel.related_artist_id is not None
        ]

    def noms_des_formations(self, artist_id: int) -> set[str]:
        """Les noms des GROUPES et COLLECTIFS dont l'artiste est membre.

        Sert à savoir si un morceau est signé par une de ses formations — ce qui
        change le SENS d'un rôle secondaire, sans rien changer à la donnée.
        """
        return {
            rel.related_name
            for rel in self.get_artist_relations(artist_id)
            if rel.kind == "member_of" and rel.related_name
        }

    def noms_de_lartiste(self, artist_id: int, nom: str) -> set[str]:
        """Le nom de l'artiste et ses alias confirmés — sous lesquels le chercher.

        Un membre est crédité tantôt sous son nom, tantôt sous un alias : ne
        chercher que le premier raterait les morceaux du collectif signés de
        l'autre.
        """
        noms = {nom} if nom else set()
        noms |= {
            rel.related_name
            for rel in self.get_artist_relations(artist_id)
            if rel.kind == "alias" and rel.related_name
        }
        return noms

    def discographie_reunie(self, artist: "Artist") -> list["Track"]:
        """Morceaux de l'artiste, de ses GROUPES, et sa part dans ses COLLECTIFS.

        **Le cœur du lot 3.** Les deux natures de formation ne se lisent pas de
        la même façon, et c'est la seule chose qui les distingue vraiment :

          · un **groupe** (IAM, L'Or du Commun) apporte TOUS ses morceaux — ses
            membres font la formation, ce qu'elle sort est à eux ;
          · un **collectif** (L'Animalerie) n'apporte que les morceaux où le
            membre est PRÉSENT (écriture, production, performance). Un collectif
            est une maison : tout le monde n'y travaille pas toujours ensemble.

        Aucune ligne n'est dupliquée : on lit plus large, on n'écrit rien. Les
        doublons de morceaux (un même titre lu deux fois par deux chemins) sont
        écartés sur l'identité métier de `Track`.
        """
        vus, resultat = set(), []
        formations = self.noms_des_formations(artist.id)
        for aid in self.ids_discographie_reunie(artist.id):
            for track in self.get_artist_tracks(aid):
                if track not in vus:
                    track.membre_de_la_formation = _signe_par_une_formation(track, formations)
                    # Marqué dès qu'il vient d'ailleurs : sans ça, on ne
                    # distinguerait plus ce que l'artiste a sorti de ce que sa
                    # formation a sorti, et le tableau mentirait par omission.
                    track.via_group = None if aid == artist.id else track.artist.name
                    vus.add(track)
                    resultat.append(track)

        noms = self.noms_de_lartiste(artist.id, artist.name)
        for collectif_id in self.ids_collectifs(artist.id):
            for track in self.get_artist_tracks(collectif_id):
                if track not in vus and track.personne_presente(noms):
                    track.via_group = track.artist.name
                    vus.add(track)
                    resultat.append(track)
        return resultat

    def update_artist_kworb_totals(
        self,
        artist_id: int,
        total: int = None,
        daily: int = None,
        lead: int = None,
        feat: int = None,
        kworb_date=None,
    ) -> bool:
        """Stocke les totaux du tableau récap Kworb (page songs de l'artiste)."""
        try:
            c = artists.c
            stmt = (
                update(artists)
                .where(c.id == artist_id)
                .values(
                    kworb_total_streams=func.coalesce(total, c.kworb_total_streams),
                    kworb_daily_streams=func.coalesce(daily, c.kworb_daily_streams),
                    kworb_lead_streams=func.coalesce(lead, c.kworb_lead_streams),
                    kworb_feat_streams=func.coalesce(feat, c.kworb_feat_streams),
                    kworb_updated=func.coalesce(date_bind(kworb_date), c.kworb_updated),
                    updated_at=datetime.now(),
                )
            )
            with self.engine.begin() as conn:
                conn.execute(stmt)
            return True
        except SQLAlchemyError as e:
            logger.error(f"Erreur update_artist_kworb_totals (artist_id={artist_id}): {e}")
            return False

    def update_artist_monthly_listeners(
        self,
        artist_id: int,
        spotify_listeners: int | None = None,
        ytm_listeners: int | None = None,
    ) -> bool:
        """Met à jour les auditeurs mensuels d'un artiste et enregistre l'historique."""
        try:
            from src.utils.streams_calculator import calculate_total_monthly_listeners

            total = calculate_total_monthly_listeners(spotify_listeners, ytm_listeners)
            c = artists.c
            upd = (
                update(artists)
                .where(c.id == artist_id)
                .values(
                    spotify_monthly_listeners=func.coalesce(
                        spotify_listeners, c.spotify_monthly_listeners
                    ),
                    ytm_monthly_listeners=func.coalesce(ytm_listeners, c.ytm_monthly_listeners),
                    updated_at=datetime.now(),
                )
            )
            ins = monthly_listeners_history.insert().values(
                artist_id=artist_id,
                spotify_listeners=spotify_listeners,
                ytm_listeners=ytm_listeners,
                total_estimated=total,
                recorded_at=datetime.now(),
            )
            with self.engine.begin() as conn:
                conn.execute(upd)
                conn.execute(ins)
            return True
        except SQLAlchemyError as e:
            logger.error(f"Erreur update_artist_monthly_listeners (id={artist_id}): {e}")
            return False

    def get_monthly_listeners_history(self, artist_id: int) -> list[dict[str, Any]]:
        """Retourne l'historique des auditeurs mensuels d'un artiste."""
        try:
            # `text()` brut : `recorded_at` (TIMESTAMP) doit revenir en STRING
            # verbatim comme au temps du legacy sqlite3 — un `select()` typé la
            # parserait en datetime (cf. get_artist_tracks / piège E2).
            with self.engine.connect() as conn:
                rows = (
                    conn.execute(
                        text(
                            "SELECT spotify_listeners, ytm_listeners, total_estimated, "
                            "recorded_at FROM monthly_listeners_history "
                            "WHERE artist_id = :aid ORDER BY recorded_at DESC"
                        ),
                        {"aid": artist_id},
                    )
                    .mappings()
                    .all()
                )
                return [
                    {
                        "spotify_listeners": r["spotify_listeners"],
                        "ytm_listeners": r["ytm_listeners"],
                        "total_estimated": r["total_estimated"],
                        "recorded_at": r["recorded_at"],
                    }
                    for r in rows
                ]
        except SQLAlchemyError as e:
            logger.error(f"Erreur get_monthly_listeners_history (id={artist_id}): {e}")
            return []

    def update_artist_spotify_id(self, artist_id: int, spotify_id: str) -> bool:
        """Met à jour le spotify_id d'un artiste."""
        try:
            stmt = (
                update(artists)
                .where(artists.c.id == artist_id)
                .values(spotify_id=spotify_id, updated_at=datetime.now())
            )
            with self.engine.begin() as conn:
                conn.execute(stmt)
            logger.info(f"spotify_id artiste #{artist_id} mis à jour: {spotify_id}")
            return True
        except SQLAlchemyError as e:
            logger.error(f"Erreur update_artist_spotify_id (artist_id={artist_id}): {e}")
            return False

    def set_artist_image_path(self, artist_id: int, path: str) -> bool:
        """Persiste le chemin (relatif à IMAGES_DIR) de la photo de profil.

        Chantier « Media » : posé par `media_enricher.apply_images` puis sauvé
        (l'enricher mute l'objet, l'appelant sauve — pattern `apply_certifications`)."""
        try:
            stmt = (
                update(artists)
                .where(artists.c.id == artist_id)
                .values(image_path=path, updated_at=datetime.now())
            )
            with self.engine.begin() as conn:
                conn.execute(stmt)
            return True
        except SQLAlchemyError as e:
            logger.error(f"Erreur set_artist_image_path (artist_id={artist_id}): {e}")
            return False
