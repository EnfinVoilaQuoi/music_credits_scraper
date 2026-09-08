"""Lignes SŒURS : un enregistrement, plusieurs lignes, une seule vérité.

`UNIQUE(title, artist_id)` fait qu'un même **enregistrement** existe une fois par
artiste crédité — le morceau chez son auteur, la ligne « feat » chez l'invité.
Ces lignes étaient enrichies INDÉPENDAMMENT : le travail se faisait deux fois et
les copies divergeaient. Mesuré le 2026-09-08 : « Grünt #33 » portait 36 crédits,
les paroles et les streams chez Swing, et **0 crédit, 0 parole, 0 observation**
chez Isha ; 854 morceaux sur 300 artistes seraient dupliqués rien qu'en ajoutant
leurs artistes principaux, dont 394 déjà enrichis — 4 713 lignes de crédits à
re-scraper pour rien.

**La clé est `genius_id`**, qui identifie l'ENREGISTREMENT là où `tracks.id`
identifie la ligne d'un artiste. Vérifié : une variante (radio edit, live,
acoustique) porte un `genius_id` DIFFÉRENT — sur 10 familles « socle + variante »
chez un même artiste, une seule le partage, et c'est un vrai doublon.

**La règle tient en une phrase :**

    La propagation ne transporte que ce qui est RENSEIGNÉ.
    Elle ne vide jamais une sœur.

C'est ce qui rend l'ordre d'écriture inoffensif. Deux sœurs sont remplies par des
déclenchements différents (import, enrichissement, run de streams) ; sans cette
règle, la PREMIÈRE qui écrit fixerait la valeur commune et un save pauvre
effacerait ce que les autres savaient. Là où un arbitre existe, c'est lui qui
tranche et non l'ordre : `source_lien_retenue` pour le lien YouTube,
`reconcile_spotify_streams` pour les streams, les observations pour l'audio, les
paroles et — depuis le lot B — la durée, la date et l'ISRC.
"""

from src.utils.logger import get_logger

logger = get_logger(__name__)

#: Colonnes qui décrivent l'ENREGISTREMENT : elles valent pour toutes les lignes.
#: Les identifiants externes en font partie — c'est le même enregistrement chez
#: Spotify, Deezer ou Discogs, quel que soit l'artiste sous lequel on le regarde.
COLONNES_PARTAGEES = (
    "genre",
    "spotify_id",
    "spotify_id_checked_at",
    "spotify_page_title",
    "discogs_id",
    "deezer_id",
    "deezer_url",
    "explicit_lyrics",
    "genius_url",
    "spotify_url",
    "youtube_url",
    "youtube_url_source",
    "yt_thumbnail_path",
    "cover_path",
    "anecdotes",
    "relationships",
    "lyrics",
    "lyrics_scraped_at",
    "lyrics_source",
    "has_lyrics",
    "lyrics_synced",
    "lyrics_synced_source",
    "lyrics_synced_confidence",
    "youtube_video_kind",
    "youtube_video_views",
    "youtube_video_views_updated",
    # `duration`, `release_date` et `isrc` sont propagés par leurs OBSERVATIONS
    # (lot B) : la colonne n'est que la matérialisation du verdict. Les lister
    # ici en plus ne ferait pas de mal, mais ferait deux chemins pour une même
    # donnée — et c'est ainsi qu'on finit avec deux vérités.
)

#: Colonnes de CONTEXTE : elles décrivent la ligne d'UN artiste, pas
#: l'enregistrement. Vérifié empiriquement — elles divergent réellement
#: (`is_featuring` sur 5 familles, `primary_artist_name` sur 6, `track_number`
#: sur 3) : « À la base » est un feat chez Flynt et un morceau principal chez A2H.
#:
#: Les CERTIFICATIONS n'y sont pas par accident : elles ont un écrivain dédié qui
#: les RECALCULE par artiste (`certification_enricher.apply_certifications`, dont
#: `_extra_artists` fait déjà remonter la certif du principal sur la ligne du
#: feat). Les partager entrerait en conflit avec ce recalcul — et l'incident du
#: 2026-09-06 (20 rattachements fautifs) venait d'une attribution trop généreuse.
COLONNES_DE_CONTEXTE = (
    "title",
    "artist_id",
    "album",
    "album_override",
    "track_number",
    "is_featuring",
    "primary_artist_name",
    "secondary_role",
    "featured_artists",
    "last_scraped",
    "certifications",
    "album_certifications",
    # Les streams ne sont pas propagés EN COLONNE : on propage leurs
    # observations, et chaque ligne ré-arbitre. Le verdict est alors identique
    # partout sans qu'on ait eu à désigner un gagnant.
    "spotify_streams",
    "spotify_daily_streams",
    "spotify_streams_updated",
    "ytm_streams",
    "ytm_streams_updated",
)


def _est_vide(valeur) -> bool:
    """« Renseigné » au sens de la règle : ni NULL, ni chaîne vide.

    `0` et `False` sont des VALEURS — « Deezer dit que ce morceau n'est pas
    explicite » n'est pas « on n'a pas regardé ». Les confondre est l'erreur
    qu'e17 a réparée pour l'identifiant Spotify.
    """
    return valeur is None or valeur == ""


def ids_soeurs(conn, track_id: int, genius_id) -> tuple[list[int], list[int]]:
    """Les autres lignes du même enregistrement → `(jumelles, doublons)`.

    Deux situations que le même `genius_id` recouvre, et qui appellent des
    gestes OPPOSÉS :

    · **artistes différents** → lignes JUMELLES, à synchroniser. En fusionner
      deux serait une faute : elles vivent sous deux artistes, en supprimer une
      amputerait la discographie de l'un des deux.
    · **même artiste** → vrai DOUBLON, à fusionner à la main. On le SIGNALE, on
      n'y touche pas.

    Et voici l'effet pervers que ce partage rendrait possible : aujourd'hui les
    deux lignes d'un doublon DIVERGENT, et cette divergence est justement
    l'indice qui le trahit. Après synchronisation elles paraîtraient complètes et
    identiques — **le doublon deviendrait invisible**. D'où la séparation ici, et
    le signalement.

    Rend `([], [])` si `genius_id` est NULL : le champ est nullable et rien ne
    permet alors de rattacher la ligne à un enregistrement.
    """
    if genius_id is None or genius_id == "":
        return [], []
    from sqlalchemy import text

    lignes = (
        conn.execute(
            text(
                "SELECT t.id, t.artist_id FROM tracks t WHERE t.genius_id = :gid AND t.id != :tid"
            ),
            {"gid": genius_id, "tid": track_id},
        )
        .mappings()
        .all()
    )
    if not lignes:
        return [], []

    mien = conn.execute(
        text("SELECT artist_id FROM tracks WHERE id = :tid"), {"tid": track_id}
    ).scalar()
    jumelles = [r["id"] for r in lignes if r["artist_id"] != mien]
    doublons = [r["id"] for r in lignes if r["artist_id"] == mien]
    if doublons:
        logger.warning(
            f"🔀 Doublon INTRA-ARTISTE sur genius_id={genius_id} : "
            f"morceaux {[track_id, *doublons]} — à fusionner à la main, "
            f"le partage ne les touche pas (il rendrait le doublon invisible)"
        )
    return jumelles, doublons


def _colonnes_a_combler(conn, ids: list[int]) -> dict[int, dict]:
    """Pour chaque ligne de la famille, les colonnes partagées à REMPLIR.

    Le principe de la règle : on calcule la valeur la plus RENSEIGNÉE de la
    famille, colonne par colonne, puis on ne remplit que les trous. Aucune ligne
    ne perd jamais rien — c'est ce qui rend l'ordre d'écriture indifférent.

    En cas de valeurs concurrentes (deux sœurs renseignées, différemment), la
    PREMIÈRE trouvée l'emporte pour combler les autres, et les deux renseignées
    gardent la leur. Ce n'est pas un arbitrage : les champs qui en méritent un
    (streams, audio, paroles, durée, date, ISRC) passent par les observations et
    ne sont pas dans `COLONNES_PARTAGEES`.
    """
    from sqlalchemy import text

    colonnes = ", ".join(COLONNES_PARTAGEES)
    marques = ", ".join(f":i{n}" for n in range(len(ids)))
    lignes = (
        conn.execute(
            text(f"SELECT id, {colonnes} FROM tracks WHERE id IN ({marques})"),  # noqa: S608
            {f"i{n}": tid for n, tid in enumerate(ids)},
        )
        .mappings()
        .all()
    )
    reference = {}
    for colonne in COLONNES_PARTAGEES:
        for ligne in lignes:
            if not _est_vide(ligne[colonne]):
                reference[colonne] = ligne[colonne]
                break

    a_combler: dict[int, dict] = {}
    for ligne in lignes:
        trous = {
            colonne: valeur for colonne, valeur in reference.items() if _est_vide(ligne[colonne])
        }
        if trous:
            a_combler[ligne["id"]] = trous
    return a_combler


def _unir_credits(conn, source_id: int, cible_id: int) -> int:
    """Recopie sur la cible les crédits qu'elle n'a pas. UNION, jamais remplacement.

    ⚠️ Le piège de tout le lot : `save_track` fait `DELETE FROM credits WHERE
    track_id` PUIS réinsertion. Un save venu d'un flux qui ne porte pas les
    crédits les EFFACE — donc une propagation qui remplacerait au lieu d'unir
    viderait la sœur au premier save pauvre.

    La clé de dédup est celle de la table : `(name, role, role_detail)`.
    """
    from sqlalchemy import text

    return conn.execute(
        text(
            "INSERT INTO credits (track_id, name, role, role_detail, tracks, source) "
            "SELECT :cible, c.name, c.role, c.role_detail, c.tracks, c.source "
            "FROM credits c WHERE c.track_id = :source AND NOT EXISTS ("
            "  SELECT 1 FROM credits d WHERE d.track_id = :cible AND d.name = c.name "
            "    AND d.role = c.role "
            "    AND IFNULL(d.role_detail, '') = IFNULL(c.role_detail, ''))"
        ),
        {"source": source_id, "cible": cible_id},
    ).rowcount


def _unir_observations(conn, ids: list[int]) -> int:
    """Chaque ligne reçoit, par `(field, source)`, l'observation la plus FRAÎCHE.

    Les observations sont la provenance : les partager, c'est partager ce que
    chaque source a mesuré de cet ENREGISTREMENT — ce qui ne dépend pas de
    l'artiste sous lequel on le regarde. Chaque ligne ré-arbitre ensuite pour son
    propre compte, si bien que le verdict est identique partout sans qu'on ait eu
    à désigner un gagnant.
    """
    from sqlalchemy import text

    marques = ", ".join(f":i{n}" for n in range(len(ids)))
    params = {f"i{n}": tid for n, tid in enumerate(ids)}
    meilleures = (
        conn.execute(
            text(
                "SELECT field, source, value, confidence, MAX(seen_at) AS seen_at "
                f"FROM observations WHERE track_id IN ({marques}) "  # noqa: S608
                "GROUP BY field, source"
            ),
            params,
        )
        .mappings()
        .all()
    )
    ecrites = 0
    for obs in meilleures:
        for track_id in ids:
            ecrites += conn.execute(
                text(
                    "INSERT INTO observations "
                    "(track_id, field, value, source, confidence, seen_at) "
                    "SELECT :tid, :field, :value, :source, :conf, :seen "
                    "WHERE NOT EXISTS (SELECT 1 FROM observations o "
                    "  WHERE o.track_id = :tid AND o.field = :field AND o.source = :source)"
                ),
                {
                    "tid": track_id,
                    "field": obs["field"],
                    "value": obs["value"],
                    "source": obs["source"],
                    "conf": obs["confidence"],
                    "seen": obs["seen_at"],
                },
            ).rowcount
    return ecrites


def _unir_table_additive(conn, ids: list[int], table: str, cle: str) -> int:
    """Réunit une table ADDITIVE (`track_videos`, `track_spotify_ids`).

    Mêmes tables, même raison : aucun producteur n'en connaît la liste complète,
    donc chaque ligne sœur en connaît un morceau. `is_primary` n'est PAS recopié
    — il matérialise `tracks.spotify_id`, qui est propre à chaque ligne.
    """
    from sqlalchemy import text

    marques = ", ".join(f":i{n}" for n in range(len(ids)))
    params = {f"i{n}": tid for n, tid in enumerate(ids)}
    colonnes = [
        r[1]
        for r in conn.execute(text(f"PRAGMA table_info({table})"))  # noqa: S608
        if r[1] not in ("id", "track_id", "is_primary")
    ]
    liste = ", ".join(colonnes)
    connues = (
        conn.execute(
            text(
                f"SELECT {cle}, {liste} FROM {table} WHERE track_id IN ({marques}) GROUP BY {cle}"
            ),  # noqa: S608
            params,
        )
        .mappings()
        .all()
    )
    ecrites = 0
    for ligne in connues:
        for track_id in ids:
            valeurs = {"tid": track_id, **{c: ligne[c] for c in colonnes}}
            ecrites += conn.execute(
                text(
                    f"INSERT INTO {table} (track_id, {liste}) "  # noqa: S608
                    f"SELECT :tid, {', '.join(':' + c for c in colonnes)} "
                    f"WHERE NOT EXISTS (SELECT 1 FROM {table} x "
                    f"  WHERE x.track_id = :tid AND x.{cle} = :{cle})"
                ),
                valeurs,
            ).rowcount
    return ecrites


def synchroniser_soeurs(conn, track_id: int, genius_id=None) -> dict:
    """Aligne toutes les lignes d'un même enregistrement. IDEMPOTENT.

    Une seule fonction, appelée après chaque écriture de donnée
    d'enregistrement, plutôt qu'une propagation par écrivain : la règle est la
    même pour tous (« ne transporter que ce qui est renseigné »), et douze
    variantes de cette règle finiraient par diverger — c'est le défaut qui a
    coûté le plus cher à ce projet.

    Elle ne fait que des UNIONS et des comblements, jamais de remplacement :
    l'appeler deux fois, ou dans n'importe quel ordre, donne le même résultat.

    Les RETRAITS ne passent pas par ici — effacer une donnée chez une sœur est un
    geste explicite, porté par l'écrivain qui l'efface (`clear_track_spotify_id`,
    `forget_track_video`). Une synchronisation qui saurait vider ne serait plus
    inoffensive.
    """
    from sqlalchemy import text

    if genius_id is None:
        genius_id = conn.execute(
            text("SELECT genius_id FROM tracks WHERE id = :tid"), {"tid": track_id}
        ).scalar()
    jumelles, doublons = ids_soeurs(conn, track_id, genius_id)
    rapport = {
        "jumelles": jumelles,
        "doublons": doublons,
        "colonnes": 0,
        "credits": 0,
        "observations": 0,
        "videos": 0,
        "spotify_ids": 0,
    }
    if not jumelles:
        return rapport

    famille = [track_id, *jumelles]

    for cible, trous in _colonnes_a_combler(conn, famille).items():
        assignations = ", ".join(f"{c} = :{c}" for c in trous)
        conn.execute(
            text(f"UPDATE tracks SET {assignations} WHERE id = :tid"),  # noqa: S608
            {**trous, "tid": cible},
        )
        rapport["colonnes"] += len(trous)

    for source in famille:
        for cible in famille:
            if source != cible:
                rapport["credits"] += _unir_credits(conn, source, cible)

    rapport["observations"] = _unir_observations(conn, famille)
    rapport["videos"] = _unir_table_additive(conn, famille, "track_videos", "video_id")
    rapport["spotify_ids"] = _unir_table_additive(conn, famille, "track_spotify_ids", "spotify_id")
    return rapport


def effacer_chez_les_soeurs(conn, track_id: int, effacement) -> int:
    """Applique un RETRAIT à toutes les lignes jumelles.

    Pendant explicite de `synchroniser_soeurs`, qui ne sait que remplir. Un ID
    Spotify fautif ou une vidéo rejetée doivent partir de TOUTES les lignes de
    l'enregistrement : les laisser chez une sœur, c'est les voir revenir à la
    première synchronisation — le rejet donnerait l'impression de n'avoir servi
    à rien, exactement comme un rejet de lien YouTube sans purge du cache.

    `effacement` est appelé avec chaque `track_id` jumeau.
    """
    from sqlalchemy import text

    genius_id = conn.execute(
        text("SELECT genius_id FROM tracks WHERE id = :tid"), {"tid": track_id}
    ).scalar()
    jumelles, _ = ids_soeurs(conn, track_id, genius_id)
    for jumelle in jumelles:
        effacement(jumelle)
    return len(jumelles)
