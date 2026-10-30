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
tranche et non l'ordre : `_aligner_lien_youtube` pour le lien YouTube,
`reconcile_spotify_streams` pour les streams, les observations pour l'audio, les
paroles et — depuis le lot B — la durée, la date et l'ISRC.
"""

import json

from src.utils.logger import get_logger
from src.utils.youtube_utils import extract_video_id

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
    "deezer_checked_at",
    "deezer_url",
    "apple_music_id",
    "apple_music_checked_at",
    # Même page Genius (la clé des sœurs est `genius_id`) : même détail lu.
    "genius_detail_checked_at",
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
    "instrumental",
    # e34 : « pas encore sorti » décrit l'ENREGISTREMENT, pas le contexte d'un
    # artiste — les deux lignes d'un même inédit le sont toutes les deux.
    "unreleased",
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


#: Données lues sur la PAGE Genius (même page pour toutes les sœurs) : la
#: lecture la plus RÉCENTE l'emporte (décision utilisateur 2026-10-12). Le
#: comblement seul figeait la première valeur chez une sœur alors que l'autre
#: avait relu la page — 90 divergences (relations 64, anecdotes 21, paroles 5).
#: Chaque groupe : (colonnes transmises ensemble, horloges de la lecture).
DONNEES_DE_PAGE = (
    (("lyrics", "lyrics_source", "lyrics_scraped_at"), ("lyrics_scraped_at",)),
    (("anecdotes",), ("genius_detail_checked_at", "last_scraped")),
    (("relationships",), ("genius_detail_checked_at", "last_scraped")),
)


#: Marque d'une relation posée À LA MAIN (`appliquer_corrections_fiches`) :
#: elle n'est pas sur la page Genius, la relecture de la page ne la redonne pas.
ORIGINE_MANUELLE = "manuel"


def hors_page(relation: dict) -> bool:
    """PUR. Relation que la page Genius ne donne pas : posée à la main, ou par
    une règle (« regle:… », la relation `contient` des titres joints)."""
    origine = relation.get("origine") or ""
    return origine == ORIGINE_MANUELLE or origine.startswith("regle:")


def avec_relations_manuelles(relations_json: str | None, toutes: list) -> str | None:
    """PUR. Les relations de la page qui gagne, PLUS les relations manuelles de
    toute la famille qu'elles n'ont pas déjà (même type, même titre). Sans
    quoi « la lecture la plus récente gagne » effaçait la relation posée à la
    main sur une sœur (2026-10-12 : Kanye « Sky City » version de « SKY CITY »,
    perdue quand la ligne de Kid Cudi a relu la page)."""
    from src.utils.title_matching import normalize_title

    def lire(brut):
        try:
            return json.loads(brut) if brut else []
        except (TypeError, ValueError):
            return []

    def cle(r):
        return (r.get("type"), normalize_title(r.get("title") or ""))

    relations = lire(relations_json)
    vues = {cle(r) for r in relations}
    ajout = False
    for brut in toutes:
        for r in lire(brut):
            if hors_page(r) and cle(r) not in vues:
                relations.append(r)
                vues.add(cle(r))
                ajout = True
    if not ajout:
        return relations_json
    return serialiser_relations(relations)


def serialiser_relations(relations) -> str:
    """PUR. LA forme stockée des relations : JSON échappé (`ensure_ascii`, celle
    de 1 084 lignes sur 1 087 à caractères non ASCII). Deux sœurs se comparent
    comme des chaînes : « ¥$ » d'un côté et « \\u00a5$ » de l'autre suffisaient
    à les faire diverger (« Pressure (Original) », 2026-10-26)."""
    return json.dumps(relations)


#: Clés d'une relation propres à la MACHINE (un chemin d'image sur disque) :
#: dérivables (`cover_image_path(artiste, titre)`), jamais stockées. Posées
#: sur une seule ligne, elles faisaient diverger les sœurs (2026-10-19).
CLES_LOCALES_DES_RELATIONS = ("cover_path",)


def relations_a_stocker(relations) -> list:
    """PUR. Les relations sans leurs clés locales (`CLES_LOCALES_DES_RELATIONS`)."""
    return [
        (
            {k: v for k, v in r.items() if k not in CLES_LOCALES_DES_RELATIONS}
            if isinstance(r, dict)
            else r
        )
        for r in relations or []
    ]


def _aligner_sur_la_lecture_la_plus_recente(conn, famille: list[int]) -> int:
    """Pour chaque groupe de `DONNEES_DE_PAGE`, la ligne renseignée dont la
    lecture est la plus récente transmet sa valeur aux autres. Un REMPLACEMENT,
    jamais un vidage : une valeur vide ne se transmet pas, et une ligne sans
    horloge ne gagne que si aucune autre n'en a. Idempotent. Rend le nombre de
    lignes réécrites."""
    from sqlalchemy import text

    marques = ", ".join(f":f{n}" for n in range(len(famille)))
    params = {f"f{n}": tid for n, tid in enumerate(famille)}
    reecrites = 0
    for colonnes, horloges in DONNEES_DE_PAGE:
        lignes = (
            conn.execute(
                text(
                    f"SELECT id, {', '.join(dict.fromkeys(colonnes + horloges))} "  # noqa: S608
                    f"FROM tracks WHERE id IN ({marques})"
                ),
                params,
            )
            .mappings()
            .all()
        )
        # Une liste VIDE (« [] » : recalculée sans rien) n'est pas une valeur à
        # transmettre — la laisser gagner viderait la sœur qui en a une.
        candidates = [r for r in lignes if not _est_vide(r[colonnes[0]]) and r[colonnes[0]] != "[]"]
        if not candidates:
            continue

        def horloge(r, horloges=horloges):
            return max((str(r[h]) for h in horloges if not _est_vide(r[h])), default="")

        gagnante = max(candidates, key=lambda r: (horloge(r), -r["id"]))
        valeurs = {c: gagnante[c] for c in colonnes}
        if colonnes[0] == "relationships":
            valeurs["relationships"] = avec_relations_manuelles(
                gagnante["relationships"], [r["relationships"] for r in lignes]
            )
        for r in lignes:
            # Même valeur (seule l'horloge ou la provenance diffère) : rien à
            # transmettre — ce serait du bruit, pas une divergence.
            if r[colonnes[0]] == valeurs[colonnes[0]]:
                continue
            if r["id"] == gagnante["id"] and colonnes[0] != "relationships":
                continue
            conn.execute(
                text(
                    f"UPDATE tracks SET {', '.join(f'{c} = :{c}' for c in colonnes)} "  # noqa: S608
                    "WHERE id = :tid"
                ),
                {**valeurs, "tid": r["id"]},
            )
            reecrites += 1
    return reecrites


#: Le lien YouTube PRINCIPAL et ce qui le décrit : ils voyagent ensemble.
COLONNES_LIEN_YOUTUBE = (
    "youtube_url",
    "youtube_url_source",
    "youtube_video_kind",
    "youtube_video_views",
    "youtube_video_views_updated",
)

#: Rang d'une provenance de lien — l'ordre de `update_track_youtube_url` :
#: le choix de l'utilisateur, puis le lien de la page Genius, puis le reste.
_RANG_LIEN = {"manual": 2, "genius_media": 1}


def _rang_lien(source) -> int:
    return _RANG_LIEN.get(source or "", 0)


def _nature_connue(nature) -> bool:
    return not _est_vide(nature) and nature != "unknown"


def _aligner_lien_youtube(conn, famille: list[int]) -> int:
    """Le lien YouTube principal, aligné entre sœurs là où un arbitre existe.

    · MÊME vidéo partout : seule l'étiquette diffère — la provenance au plus
      haut rang (`manual` > `genius_media` > le reste) et la nature connue
      (« audio » plutôt que « unknown ») gagnent.
    · Vidéos DIFFÉRENTES : seul un choix `manual` UNIQUE se transmet — c'est
      déjà la décision de l'utilisateur, pour tout l'enregistrement. Sinon rien :
      l'une des deux est fausse, le panneau 🧬 le montre (décision 2026-10-12).

    Le comblement ne remplissait que les trous : un ✔️ posé sur une ligne ne
    gagnait jamais chez sa sœur, qui gardait son lien Genius (Kanye / Kid Cudi
    « Sky City », « Champions », « Reborn », « Ghost Town », 2026-10-26).
    Un remplacement, jamais un vidage. Idempotent. Rend le nombre de lignes
    réécrites."""
    from sqlalchemy import text

    marques = ", ".join(f":f{n}" for n in range(len(famille)))
    lignes = (
        conn.execute(
            text(
                f"SELECT id, {', '.join(COLONNES_LIEN_YOUTUBE)} "  # noqa: S608
                f"FROM tracks WHERE id IN ({marques})"
            ),
            {f"f{n}": tid for n, tid in enumerate(famille)},
        )
        .mappings()
        .all()
    )
    avec = [r for r in lignes if not _est_vide(r["youtube_url"])]

    def video(r):
        return extract_video_id(r["youtube_url"]) or r["youtube_url"]

    if len({video(r) for r in avec}) > 1:
        manuelles = [r for r in avec if r["youtube_url_source"] == "manual"]
        if len({video(r) for r in manuelles}) != 1:
            return 0
        valeurs = {c: manuelles[0][c] for c in COLONNES_LIEN_YOUTUBE}
        a_reecrire = [r for r in avec if video(r) != video(manuelles[0])]
    elif avec:
        gagnante = max(avec, key=lambda r: (_rang_lien(r["youtube_url_source"]), -r["id"]))
        natures = [r["youtube_video_kind"] for r in avec if _nature_connue(r["youtube_video_kind"])]
        valeurs = {
            "youtube_url_source": gagnante["youtube_url_source"],
            "youtube_video_kind": (
                gagnante["youtube_video_kind"]
                if _nature_connue(gagnante["youtube_video_kind"]) or not natures
                else natures[0]
            ),
        }
        a_reecrire = avec
    else:
        return 0
    reecrites = 0
    for r in a_reecrire:
        if all(r[c] == v for c, v in valeurs.items()):
            continue
        conn.execute(
            text(
                f"UPDATE tracks SET {', '.join(f'{c} = :{c}' for c in valeurs)} "  # noqa: S608
                "WHERE id = :tid"
            ),
            {**valeurs, "tid": r["id"]},
        )
        reecrites += 1
    return reecrites


def aligner_lien_youtube_des_soeurs(conn, track_id: int) -> int:
    """`_aligner_lien_youtube` sur la famille de `track_id` — pour l'écrivain
    qui pose le lien sur UNE ligne (`update_track_youtube_url`) sans passer par
    `synchroniser_soeurs`."""
    from sqlalchemy import text

    gid = conn.execute(
        text("SELECT genius_id FROM tracks WHERE id = :tid"), {"tid": track_id}
    ).scalar()
    jumelles, _ = ids_soeurs(conn, track_id, gid)
    return _aligner_lien_youtube(conn, [track_id, *jumelles]) if jumelles else 0


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


def unir_observations(conn, ids: list[int]) -> int:
    """Chaque ligne reçoit, par `(field, source)`, l'observation la plus FRAÎCHE.

    Les observations sont la provenance : les partager, c'est partager ce que
    chaque source a mesuré de cet ENREGISTREMENT — ce qui ne dépend pas de
    l'artiste sous lequel on le regarde. Chaque ligne ré-arbitre ensuite pour son
    propre compte, si bien que le verdict est identique partout sans qu'on ait eu
    à désigner un gagnant.

    À fraîcheur ÉGALE (`seen_at` est à la seconde), la ligne `ids[0]` l'emporte :
    c'est celle qu'un écrivain vient de toucher (`_recalculer_touchees`), deux
    lectures dans la même seconde ne doivent pas lui rendre l'ancienne valeur
    de sa sœur.
    """
    from sqlalchemy import text

    marques = ", ".join(f":i{n}" for n in range(len(ids)))
    params = {f"i{n}": tid for n, tid in enumerate(ids)}
    # `ytm_streams` n'est pas une mesure de l'ENREGISTREMENT : c'est le total
    # propre à CHAQUE ligne, qui dépend des vidéos partagées chez SON artiste et
    # que la ligne réécrit à chaque recalcul. L'unir ne faisait que tourner en
    # rond (176 « conflits » mesurés le 2026-10-12 après réalignement). Ses
    # compteurs sans mesure exacte (`ytm_compteurs`) aussi : le run de SON artiste.
    lignes = (
        conn.execute(
            text(
                "SELECT track_id, field, source, value, confidence, seen_at "
                f"FROM observations WHERE track_id IN ({marques}) "  # noqa: S608
                "AND field NOT IN ('ytm_streams', 'ytm_compteurs')"
            ),
            params,
        )
        .mappings()
        .all()
    )
    par_cle: dict = {}
    for obs in lignes:
        rang = (str(obs["seen_at"] or ""), obs["track_id"] == ids[0])
        cle = (obs["field"], obs["source"])
        if cle not in par_cle or rang > par_cle[cle][0]:
            par_cle[cle] = (rang, obs)
    meilleures = [obs for _, obs in par_cle.values()]
    ecrites = 0
    for obs in meilleures:
        for track_id in ids:
            params_obs = {
                "tid": track_id,
                "field": obs["field"],
                "value": obs["value"],
                "source": obs["source"],
                "conf": obs["confidence"],
                "seen": obs["seen_at"],
            }
            ecrites += conn.execute(
                text(
                    "INSERT INTO observations "
                    "(track_id, field, value, source, confidence, seen_at) "
                    "SELECT :tid, :field, :value, :source, :conf, :seen "
                    "WHERE NOT EXISTS (SELECT 1 FROM observations o "
                    "  WHERE o.track_id = :tid AND o.field = :field AND o.source = :source)"
                ),
                params_obs,
            ).rowcount
            # Une sœur qui a DÉJÀ une observation de cette source, plus ancienne
            # et différente, reçoit la plus fraîche : la même source a remesuré
            # le même enregistrement. Sans ce geste, les deux lignes gardaient
            # chacune la sienne et leurs verdicts divergeaient pour toujours
            # (290 conflits mesurés le 2026-10-11 ; LRC `ytmusic` trouvé par le
            # test property-based le 2026-10-12).
            ecrites += conn.execute(
                text(
                    "UPDATE observations SET value = :value, confidence = :conf, seen_at = :seen "
                    "WHERE track_id = :tid AND field = :field AND source = :source "
                    "AND value IS NOT :value AND (seen_at IS NULL OR seen_at <= :seen)"
                ),
                params_obs,
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


#: Colonnes d'une ÉDITION (`track_editions`) comblées d'une sœur à l'autre ; la
#: clé est le libellé (`UNIQUE(track_id, label)`).
COLONNES_D_EDITION = ("title", "source", "duration", "spotify_id", "deezer_id", "isrc", "genius_id")


def unir_editions(conn, ids: list[int]) -> int:
    """Chaque ligne reçoit les ÉDITIONS (`track_editions`) de ses sœurs.

    Une édition (radio edit, autre page Deezer, page Genius absorbée) décrit
    l'ENREGISTREMENT, pas la ligne d'un artiste : « Make Her Say » notée chez
    Kid Cudi et pas chez Kanye, « Mon pote » chez Flynt et pas chez OrelSan
    (2026-10-29, 25 familles sur 302). Sa durée entre dans le verdict de la
    durée : sans elle, deux sœurs pouvaient juger différemment.

    Comme `record_track_edition` : ADDITIF par libellé — un libellé absent est
    copié, une colonne vide d'un libellé présent est comblée (première valeur
    renseignée, `ids[0]` d'abord), une valeur renseignée n'est jamais
    remplacée. Deux sœurs en désaccord sur une même édition le restent :
    l'invariant `soeurs_divergentes` le montre. Les RETRAITS passent par
    `effacer_chez_les_soeurs` (`retirer_edition`, `deplacer_edition`), sans
    quoi cette union les rendrait.
    """
    from sqlalchemy import text

    marques = ", ".join(f":i{n}" for n in range(len(ids)))
    params = {f"i{n}": tid for n, tid in enumerate(ids)}
    colonnes = ", ".join(COLONNES_D_EDITION)
    lignes = (
        conn.execute(
            text(
                f"SELECT id, track_id, label, created_at, {colonnes} FROM track_editions "  # noqa: S608
                f"WHERE track_id IN ({marques}) ORDER BY id"
            ),
            params,
        )
        .mappings()
        .all()
    )
    if not lignes:
        return 0
    rang = {tid: n for n, tid in enumerate(ids)}
    lignes = sorted(lignes, key=lambda r: (rang[r["track_id"]], r["id"]))
    reference: dict[str, dict] = {}
    for ligne in lignes:
        ref = reference.setdefault(ligne["label"], {"created_at": ligne["created_at"]})
        for colonne in COLONNES_D_EDITION:
            if colonne not in ref and not _est_vide(ligne[colonne]):
                ref[colonne] = ligne[colonne]
    existantes = {(ligne["track_id"], ligne["label"]): ligne for ligne in lignes}
    ecrites = 0
    for label, ref in reference.items():
        for track_id in ids:
            ligne = existantes.get((track_id, label))
            if ligne is None:
                cols = ["track_id", "label", *ref]
                conn.execute(
                    text(
                        f"INSERT INTO track_editions ({', '.join(cols)}) "  # noqa: S608
                        f"VALUES ({', '.join(':' + c for c in cols)})"
                    ),
                    {"track_id": track_id, "label": label, **ref},
                )
                ecrites += 1
                continue
            trous = {
                c: v for c, v in ref.items() if c in COLONNES_D_EDITION and _est_vide(ligne[c])
            }
            if trous:
                conn.execute(
                    text(
                        "UPDATE track_editions SET "  # noqa: S608
                        + ", ".join(f"{c} = :{c}" for c in trous)
                        + " WHERE id = :id"
                    ),
                    {"id": ligne["id"], **trous},
                )
                ecrites += 1
    return ecrites


def synchroniser_soeurs(conn, track_id: int, genius_id=None) -> dict:
    """Aligne toutes les lignes d'un même enregistrement. IDEMPOTENT.

    Une seule fonction, appelée après chaque écriture de donnée
    d'enregistrement, plutôt qu'une propagation par écrivain : la règle est la
    même pour tous (« ne transporter que ce qui est renseigné »), et douze
    variantes de cette règle finiraient par diverger — c'est le défaut qui a
    coûté le plus cher à ce projet.

    Elle ne fait que des UNIONS et des comblements — plus UN remplacement, pour
    les données lues sur la page Genius (`DONNEES_DE_PAGE`) : la lecture la plus
    récente l'emporte (décision utilisateur 2026-10-12). Rien n'est jamais vidé ;
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
        "editions": 0,
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
    # Données de PAGE : la lecture la plus récente l'emporte (remplacement,
    # jamais vidage) — décision utilisateur 2026-10-12.
    rapport["donnees_de_page"] = _aligner_sur_la_lecture_la_plus_recente(conn, famille)
    rapport["lien_youtube"] = _aligner_lien_youtube(conn, famille)
    # `has_lyrics` n'est que le reflet de `lyrics` : `0` étant une VALEUR pour
    # le comblement, une ligne qui recevait les paroles gardait son `0`
    # (2026-10-11 : 72 fiches à paroles marquées « sans paroles »).
    marques = ", ".join(f":f{n}" for n in range(len(famille)))
    conn.execute(
        text(
            f"UPDATE tracks SET has_lyrics = 1 WHERE id IN ({marques}) "  # noqa: S608
            "AND lyrics IS NOT NULL AND lyrics <> '' AND COALESCE(has_lyrics, 0) = 0"
        ),
        {f"f{n}": tid for n, tid in enumerate(famille)},
    )

    # Un scrape chez une sœur vaut pour tout l'enregistrement : les crédits
    # PROVISOIRES de l'API partent AVANT l'union. Ce n'est pas un retrait de
    # donnée (le scrape en est le remplacement) et c'est idempotent ; après
    # l'union, ils bloqueraient la copie du crédit scrapé identique (la clé
    # d'union ignore la source) et survivraient à côté du scrape.
    from src.utils.credits_genius_api import purger_provisoires_couverts

    rapport["provisoires"] = purger_provisoires_couverts(conn, famille)
    for source in famille:
        for cible in famille:
            if source != cible:
                rapport["credits"] += _unir_credits(conn, source, cible)

    rapport["observations"] = unir_observations(conn, famille)
    rapport["videos"] = _unir_table_additive(conn, famille, "track_videos", "video_id")
    rapport["spotify_ids"] = _unir_table_additive(conn, famille, "track_spotify_ids", "spotify_id")
    rapport["editions"] = unir_editions(conn, famille)
    # `is_primary` RECOPIE `tracks.spotify_id` : une colonne comblée par une
    # sœur et un ID reçu par l'union n'étaient jamais marqués principaux
    # (2026-10-11 : 82 lignes sœurs sans ID principal dans la table).
    conn.execute(
        text(
            "UPDATE track_spotify_ids SET is_primary = "
            "(spotify_id = (SELECT t.spotify_id FROM tracks t WHERE t.id = track_id)) "
            f"WHERE track_id IN ({marques})"  # noqa: S608
        ),
        {f"f{n}": tid for n, tid in enumerate(famille)},
    )
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


def fusionner_constat_instrumental(
    garde: bool | None, supprime: bool | None, paroles_conservees: bool
) -> bool | None:
    """Le constat `instrumental` de la ligne SURVIVANTE d'une fusion.

    Contrairement à `unreleased`, où un simple `MIN` suffit (0 = « sorti,
    prouvé » l'emporte toujours), ce constat n'est PAS symétrique : il doit
    rester cohérent avec les paroles que la ligne conserve. Mesuré sur la base
    entière le 2026-09-23 — **0 ligne à `instrumental=1` porte des paroles, 0
    ligne à `0` en est dépourvue** : cette cohérence est un invariant de fait,
    et une fusion ne doit pas être ce qui le casse.

    D'où la règle, dans cet ordre :

      · des paroles survivent ⇒ **0**, ce qui est littéralement ce que 0 veut
        dire (« des paroles ont été trouvées ») ;
      · sinon un constat d'instrumental d'un côté ou de l'autre ⇒ **1** : la
        page Genius l'a dit, et rien ici ne le contredit ;
      · sinon la valeur de la ligne gardée, à défaut celle de l'autre — un
        constat ne se perd pas parce qu'il vivait sur la ligne supprimée.
    """
    if paroles_conservees:
        return False
    if garde is True or supprime is True:
        return True
    return garde if garde is not None else supprime
