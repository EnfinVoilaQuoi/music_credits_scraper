"""Streams Spotify lus sur open.spotify.com — le repli quand Kworb est muet.

Kworb ne couvre pas tous les artistes : page absente = artiste sans aucun stream,
définitivement (`update_kworb._scrape_validated`). Ce module comble ce trou, et
apporte au passage les **auditeurs mensuels**, dont la colonne existait depuis
toujours sans que rien ne l'écrive.

Économie du crawl (mesurée en session live le 2026-09-04, cf. `JOURNAL.md`) :

  · **une page par morceau** — aucune page Spotify ne rend les compteurs d'un
    album d'un coup, la page album n'en affiche aucun ;
  · **mais chaque page en rend jusqu'à 16** (le morceau + 5 recommandés + 10
    titres populaires) → chaque visite est une RÉCOLTE, et la file raccourcit
    d'elle-même ;
  · d'où un crawl **hiérarchisé, plafonné et reprenable** : la page artiste
    d'abord (auditeurs mensuels + top 10 pour une seule page), puis les pages
    titre par ordre de péremption, sous plafond.

Trois règles encadrent la récolte, sans lesquelles elle fabrique des faux :

  1. **Attribution par ID Spotify uniquement**, jamais par le nom affiché — mais
     sur TOUTE la base : les recommandations d'une page sont pleines de
     collaborateurs, et sur un corpus de rap français c'est loin d'être marginal.
  2. **Ce module ne décide RIEN de ce qui atterrit en colonne.** Il déclare ce
     que Spotify a montré ; l'arbitrage entre sources appartient à
     `reconcile_spotify_streams`, appliqué par le repository. C'est ce qui rend
     la récolte croisée sûre — noter le morceau d'un autre artiste ne risque pas
     d'écraser une valeur dont on ignore ici la fraîcheur — et ce qui rend
     l'ordre des deux passes sans effet sur le résultat.
  3. **Gate d'identité avant toute écriture.** Le projet a déjà écrit les streams
     de Limsa d'Aulnay sur Isha (2026-07-02) et des auditeurs mensuels sur un
     mauvais canal (E8) — ces derniers n'étant même pas purgeables après coup.
"""

import json
from datetime import datetime, timedelta

from src.concurrency import async_loop
from src.config import settings
from src.scrapers.spotify_web_scraper import SpotifyWebScraper
from src.utils.logger import get_logger
from src.utils.title_matching import base_album_key, normalize_title

logger = get_logger(__name__)

#: Provenance écrite dans les observations. Même `field` que Kworb
#: (`spotify_streams`), source différente : la clé d'upsert étant
#: `(track_id, field, source)`, les deux coexistent et deviennent comparables.
_SOURCE = "spotify_web"


def _parse_seen_at(raw) -> datetime | None:
    """`seen_at` d'une observation — rendu BRUT par le repository (string ou
    datetime selon le chemin d'écriture). Illisible ⇒ traité comme périmé :
    mieux vaut repayer une page que sauter un morceau sur une date douteuse."""
    if isinstance(raw, datetime):
        return raw
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw))
    except ValueError:
        return None


def _is_fresh(raw, now: datetime, max_age_days: int) -> bool:
    seen = _parse_seen_at(raw)
    return seen is not None and (now - seen) < timedelta(days=max_age_days)


def _identity_ok(page_name: str | None, artist_name: str) -> bool:
    """Le nom porté par la page Spotify est-il bien le nôtre ?

    Délègue à `update_kworb._names_match` — LE garde-fou d'identité du projet,
    corrigé le 2026-09-04 (l'inclusion ne compte qu'en mots entiers : « SCH »
    n'est plus accepté dans « ScHoolboy Q »). Le réimplémenter ici serait la
    faute déjà commise deux fois : deux copies d'un matcher, dont une seule
    reçoit le correctif suivant.
    """
    from src.utils.update_kworb import _names_match

    return _names_match(page_name, artist_name)


def update_spotify_streams(
    artist,
    data_manager,
    scraper=None,
    stop_requested=None,
    full_crawl: bool = True,
) -> dict:
    """Scrape open.spotify.com et met à jour streams et auditeurs mensuels.

    Args:
        artist: objet Artist (`id`, `name`, `spotify_id`).
        data_manager: instance de DataManager.
        scraper: SpotifyWebScraper injecté (tests / provider) ; créé sinon.
        stop_requested: callable testé ENTRE deux morceaux (jamais au milieu
            d'une écriture), pour une fermeture propre du worker.
        full_crawl: à False, SEULE la page artiste est ouverte — auditeurs
            mensuels et top 10, pour UNE page. Les passes par morceau et par
            album, qui coûtent une page chacune, sont sautées. C'est le mode
            qu'on peut se permettre à chaque run.

    Returns:
        dict résumé {recorded, harvested_foreign, unknown_ids, pages,
                     monthly_listeners, artist_name, spotify_artist_id, aborted}
    """
    result = {
        "recorded": 0,  # morceaux de CET artiste observés
        "harvested_foreign": 0,  # récoltés au passage, autres artistes
        "unknown_ids": 0,  # vus sur les pages, inconnus de la base
        "albums_totalises": 0,  # totaux RÉELS écrits (toutes pistes)
        "pages": 0,
        "monthly_listeners": None,
        "artist_name": None,
        "spotify_artist_id": None,
        "aborted": None,
    }

    spotify_artist_id = _resolve_artist_id(artist, data_manager, result)
    if not spotify_artist_id:
        return result
    result["spotify_artist_id"] = spotify_artist_id

    own_scraper = scraper is None
    scraper = scraper or SpotifyWebScraper(headless=True)
    try:
        return async_loop.run_sync(
            _crawl(
                artist,
                data_manager,
                scraper,
                spotify_artist_id,
                stop_requested,
                result,
                full_crawl,
            )
        )
    finally:
        if own_scraper:
            scraper.close()


def _resolve_artist_id(artist, data_manager, result: dict) -> str | None:
    """ID artiste Spotify, voté si absent — AVANT d'entrer dans la boucle async.

    Le vote passe par `SpotifyIDScraper`, qui est en Playwright SYNCHRONE : le
    lancer depuis la boucle asyncio la bloquerait. Il se fait donc ici, dans le
    thread appelant.
    """
    if artist.spotify_id:
        return artist.spotify_id

    from src.utils.update_kworb import _vote_artist_spotify_id

    voted = _vote_artist_spotify_id(artist, data_manager)
    if not voted:
        result["aborted"] = "aucun ID artiste Spotify (vote infructueux)"
        logger.warning(f"Spotify web : pas d'ID artiste pour '{artist.name}' — abandon")
        return None
    data_manager.update_artist_spotify_id(artist.id, voted)
    artist.spotify_id = voted
    return voted


async def _crawl(
    artist,
    data_manager,
    scraper,
    spotify_artist_id: str,
    stop_requested,
    result: dict,
    full_crawl: bool = True,
) -> dict:
    """Les deux passes, sous UNE session navigateur.

    Une session au lieu d'un contexte par page : `acrawl_page` relance un
    navigateur à chaque appel, ce qui sur trois cents morceaux ferait trois cents
    lancements.
    """
    now = datetime.now()
    id_map = data_manager.get_track_ids_by_spotify_id()
    seen_dates = data_manager.get_stream_observation_dates(_SOURCE)
    # Carte des compteurs vus PENDANT ce run. C'est un dict et non un set :
    # le total d'un album se somme sur ses pistes, y compris celles qui ne
    # sont pas en base (un album commun en contient toujours).
    releves: dict[str, int] = {}

    async with scraper.session() as sess:
        # ── Passe A : la page artiste (une seule page, toujours) ─────────────
        page = await scraper.afetch_artist(sess, spotify_artist_id)
        result["pages"] += 1
        if not page:
            result["aborted"] = "page artiste illisible"
            return result

        if not _identity_ok(page["name"], artist.name):
            result["aborted"] = f"page Spotify = '{page['name']}' ≠ '{artist.name}'"
            logger.error(
                f"🚨 {result['aborted']} — ID artiste erroné ? "
                f"AUCUNE écriture (streams comme auditeurs mensuels)"
            )
            return result
        result["artist_name"] = page["name"]

        if page["monthly_listeners"] is not None:
            data_manager.update_artist_monthly_listeners(
                artist.id, spotify_listeners=page["monthly_listeners"]
            )
            result["monthly_listeners"] = page["monthly_listeners"]

        _record(page["playcounts"], artist, data_manager, id_map, releves, result)

        if not full_crawl:
            # Mode léger : la page artiste seule. Elle rend déjà les auditeurs
            # mensuels — que Kworb ne donne pas du tout — et le top 10, soit
            # justement les morceaux que Kworb couvre le mieux, donc la
            # comparaison des deux sources. Tout le reste se paie une page par
            # morceau : c'est un choix, pas un défaut.
            logger.info(f"Spotify web '{artist.name}' : page artiste seule (mode léger)")
            return result

        # ── Passe B : les pages titre, par péremption, sous plafond ──────────
        queue = _build_queue(artist, data_manager, seen_dates, now, releves)
        budget = settings.spotify_web_max_pages_per_run - result["pages"]
        logger.info(
            f"Spotify web '{artist.name}' : {len(queue)} morceau(x) à visiter, "
            f"plafond {max(budget, 0)} page(s)"
        )
        for spotify_id in queue:
            if budget <= 0:
                logger.info("Plafond de pages atteint — le reste attendra le prochain run")
                break
            if stop_requested and stop_requested():
                logger.info("Arrêt demandé — interruption entre deux morceaux")
                break
            if spotify_id in releves:
                continue  # déjà récolté sur une page précédente : page économisée

            data = await scraper.afetch_track(sess, spotify_id)
            budget -= 1
            result["pages"] += 1
            if not data:
                continue
            _record(data["playcounts"], artist, data_manager, id_map, releves, result)

        # ── Passe C : les totaux d'album ────────────────────────────────
        budget = await _totaliser_albums(
            sess,
            scraper,
            artist,
            data_manager,
            page["albums"],
            releves,
            budget,
            stop_requested,
            result,
        )

    return result


def _build_queue(artist, data_manager, seen_dates: dict, now: datetime, releves: dict) -> list[str]:
    """Morceaux de l'artiste à visiter, du plus périmé au moins.

    Sont écartés : ceux sans ID Spotify (rien à ouvrir) et ceux dont
    l'observation est encore fraîche — **quel que soit le run qui l'a écrite**.
    Sur une base où les artistes se croisent, chaque run allège donc les suivants.

    Rien n'est écarté au motif qu'une autre source a la donnée : c'est
    l'arbitrage qui tranche, et une observation Spotify garde sa valeur de
    comparaison même quand Kworb gagne la colonne.
    """
    max_age = settings.spotify_web_freshness_days
    candidates = []
    for track in data_manager.get_artist_tracks(artist.id):
        if not track.spotify_id or track.spotify_id in releves:
            continue
        raw = seen_dates.get(track.id)
        if _is_fresh(raw, now, max_age):
            continue
        # Jamais vu → tout en haut (None trie avant n'importe quelle date).
        candidates.append((_parse_seen_at(raw) or datetime.min, track.spotify_id))
    candidates.sort(key=lambda c: c[0])
    return [spotify_id for _, spotify_id in candidates]


def _record(
    playcounts: dict,
    artist,
    data_manager,
    id_map: dict,
    releves: dict,
    result: dict,
) -> None:
    """Écrit ce qu'une page a rendu, en n'attribuant QUE par ID.

    Ce module ne décide PAS de ce qui atterrit en colonne : il déclare ce que
    Spotify a montré, et l'arbitrage (`reconcile_spotify_streams`, appliqué par
    le repository) tranche entre les sources. C'est ce qui permet de récolter au
    passage les morceaux d'AUTRES artistes de la base sans risquer d'écraser une
    valeur dont on ignore la fraîcheur ici.

    Un ID inconnu de la base n'écrit rien : il est seulement compté. On ne
    reconnaît un morceau que par son ID Spotify, jamais par le nom affiché — les
    recommandations d'une page appartiennent souvent à quelqu'un d'autre.

    Aucun `daily_streams` : Spotify ne publie aucun chiffre quotidien, et en
    écrire un effacerait celui de Kworb, seule source à en donner.
    """
    seen_at = datetime.now()
    for spotify_id, streams in playcounts.items():
        releves[spotify_id] = streams
        entry = id_map.get(spotify_id)
        if entry is None:
            result["unknown_ids"] += 1
            continue
        track_id, owner_id = entry
        data_manager.record_spotify_streams(track_id, streams, _SOURCE, seen_at)
        if owner_id == artist.id:
            result["recorded"] += 1
        else:
            result["harvested_foreign"] += 1


async def _totaliser_albums(
    sess,
    scraper,
    artist,
    data_manager,
    albums_spotify: dict,
    releves: dict,
    budget: int,
    stop_requested,
    result: dict,
) -> int:
    """Totaux d'album — la somme des ENREGISTREMENTS DISTINCTS du disque.

    Deux décisions se superposent ici, et elles ne disent pas la même chose.

    **Toutes les pistes, pas seulement celles de l'artiste** (arbitrage
    utilisateur) : un album de duo (Limsa d'Aulnay × ISHA) ou de groupe (L'Or du
    Commun pour Swing) EST l'album de l'artiste, tous ses morceaux le concernent.
    La convention de Kworb — ne sommer que sa part — rend une donnée incomplète,
    et ces albums sont la majorité du corpus.

    **Mais surtout : pas la somme des éditions.** Mesuré le 2026-09-05 sur
    « Bitume Caviar (vol.1) » — l'originale fait 11 pistes, la réédition 15 (elle
    ajoute un « Disque 2 » de 4 inédits). Les deux n'ont **aucun `track_id`
    commun**, et pourtant « Clio 4 », présent sur les deux, y affiche le même
    compteur. Spotify compte par ENREGISTREMENT, pas par identifiant : une
    réédition ne repart pas de zéro. Additionner les éditions compterait donc
    deux fois les 11 titres partagés (+81 % sur ce disque), alors que le bon
    total est celui des 15 enregistrements distincts. L'identité d'un
    enregistrement passe par son TITRE, seul lien entre deux éditions.

    Deux garde-fous, sans lesquels cette passe ferait plus de mal que de bien :

    · **On ne totalise QUE des albums déjà en base.** Spotify complète la
      discographie, il ne l'invente pas : une compilation extérieure portant un
      titre de l'artiste n'est pas son album. Le filtre s'appuie sur le titre lu
      dans la page ARTISTE — donc avant d'ouvrir quoi que ce soit.
    · **Le total, ou rien.** Une somme partielle est un nombre faux qui a l'air
      juste. Si le budget de pages ne couvre pas les pistes manquantes, on
      n'écrit pas — l'album attendra le prochain run.

    Retourne le budget de pages restant.
    """
    if budget <= 0 or not albums_spotify:
        return budget

    connus: dict[str, str] = {}
    groupes: dict[str, list[str]] = {}
    for album in data_manager.get_albums_for_artist(artist.id):
        if not album.get("title"):
            continue
        cle = normalize_title(album["title"])
        connus[cle] = album["title"]
        # Éditions DÉJÀ connues de la base — Kworb en voit que la page artiste
        # ne liste pas : celle-ci ne montre qu'une vingtaine d'albums, et une
        # réédition confidentielle n'y figure pas. Sans cette amorce, son total
        # restait amputé sans que rien ne le signale (constaté sur « DOM
        # PERIGNON CRYING », ~10 % sous son vrai total).
        connues = [
            i.strip() for i in (album.get("spotify_album_ids") or "").split(",") if i.strip()
        ]
        if connues:
            groupes[cle] = connues

    # Puis les entrées de la page artiste, qui peuvent en révéler d'autres.
    for album_id, titre in albums_spotify.items():
        cle = base_album_key(normalize_title(titre or ""), connus)
        if cle and album_id not in groupes.setdefault(cle, []):
            groupes[cle].append(album_id)

    for cle, editions in groupes.items():
        if budget <= 0 or (stop_requested and stop_requested()):
            break

        compositions = {}
        tronquee = False
        for album_id in editions:
            if budget <= 0:
                break
            album = await scraper.afetch_album(sess, album_id)
            budget -= 1
            result["pages"] += 1
            if album:
                compositions[album_id] = album
                # Tracklist VIRTUALISÉE : si la page annonce plus de pistes
                # qu'elle n'en a rendues, on ne sait pas ce qui manque. Sommer
                # ce qu'on voit donnerait un total plausible et FAUX — le pire
                # des résultats, puisqu'il ne se signale pas. Mesuré le
                # 2026-09-05 : la même page a rendu 15 pistes puis 7.
                annonce = album.get("announced")
                if annonce is not None and len(album["tracks"]) < annonce:
                    tronquee = True
        if len(compositions) != len(editions) or tronquee:
            logger.warning(f"Album '{connus[cle]}' : composition incomplète — total NON écrit")
            continue

        # Un enregistrement = un TITRE. La première édition qui le porte fournit
        # l'ID à visiter ; les autres portent le même compteur.
        par_enregistrement: dict[str, str] = {}
        for album in compositions.values():
            for track_id, titre_piste in album["tracks"]:
                par_enregistrement.setdefault(normalize_title(titre_piste), track_id)

        manquants = [tid for tid in par_enregistrement.values() if tid not in releves]
        if len(manquants) > budget:
            logger.info(
                f"Album '{connus[cle]}' : {len(manquants)} piste(s) manquante(s) pour "
                f"{budget} page(s) de budget — total NON écrit (le prochain run reprendra)"
            )
            continue

        for track_id in manquants:
            data = await scraper.afetch_track(sess, track_id)
            budget -= 1
            result["pages"] += 1
            if data:
                releves.update(data["playcounts"])

        if any(tid not in releves for tid in par_enregistrement.values()):
            logger.warning(f"Album '{connus[cle]}' : piste illisible — total NON écrit")
            continue

        total = sum(releves[tid] for tid in par_enregistrement.values())
        # Détail PAR ÉDITION conservé à part : c'est une donnée en soi, et sans
        # elle on ne pourrait plus reconstituer ce que chaque pressage a fait.
        detail = {
            album_id: sum(releves[tid] for tid, _ in album["tracks"] if tid in releves)
            for album_id, album in compositions.items()
        }
        data_manager.upsert_album(
            artist.id,
            connus[cle],
            total,
            None,  # Spotify ne publie aucun quotidien ; `upsert_album` préserve.
            spotify_album_ids=",".join(compositions),
            updated_at=datetime.now(),
            source=_SOURCE,
            editions_json=json.dumps(detail),
        )
        result["albums_totalises"] += 1
        detail_txt = (
            f" ({len(compositions)} éditions, {sum(len(a['tracks']) for a in compositions.values())}"
            f" lignes dédupliquées)"
            if len(compositions) > 1
            else ""
        )
        logger.info(
            f"Album '{connus[cle]}' : {total:,} streams sur "
            f"{len(par_enregistrement)} enregistrements{detail_txt}".replace(",", " ")
        )
    return budget
