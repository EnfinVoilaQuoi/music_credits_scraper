"""Écarts de discographie : ce que Deezer a et que la base n'a pas (2026-09-21).

Genius a des TROUS (mesuré : Freeze Corleone 1 « Chopped & $crewed » en base sur
42 lignes Kworb, Diam's 8 « Live 2006 » sur 28, Lucio Bukowski 19 pistes sur 142
absentes) et on ne les voit qu'en creusant. Deezer est le catalogue des
DISTRIBUTEURS — ce que le label a publié — avec une API libre. Ce module :

  1. lit la discographie Deezer de l'artiste (identifié par l'oracle
     `deezer_identite`, jamais par le rang de recherche) ;
  2. CLASSE chaque piste absente de la base (fonction pure `classer`) :
       · `absent`         — morceau inconnu sur un disque connu (« Coal lla ») ;
       · `album_absent`   — disque entier inconnu dont l'artiste est PRINCIPAL ;
       · `version`        — socle connu + descripteur (Live 2006, Radio Edit,
                            Instrumental — `title_version` de Deezer, sinon
                            `parse_variant`) ; un disque inconnu fait surtout de
                            versions est `version` au niveau album ;
       · `apparition`     — disque d'un AUTRE artiste (AD$ (Chopped & $crewed)
                            est un album d'Ocho, Freeze y contribue) ;
  3. CRÉE les lignes validées (`creer_lignes`) — sur validation seulement, GUI
     ou `--creer` : titre Deezer, album, piste, date, durée, ISRC, feats et label
     en crédits (`deezer`), observations, accrochage Genius quand la page existe
     sous une autre graphie, héritage transversal depuis le socle pour une
     version, et la mécanique e29 si la version était déjà une variante Kworb.

`/artist/{id}/albums` liste les disques où l'artiste APPARAÎT : la fiche
`/album/{id}` dit qui en est l'artiste ; `/album/{id}/tracks` ne donne pas les
contributeurs par piste, `/track/{id}` oui — lu seulement pour les pistes
inconnues d'un disque étranger (pour ne garder que celles où l'artiste est).

Deux titres jumeaux d'EP (« … » / « ... ») normalisent à la chaîne VIDE :
`cle_album` s'en garde. Module sans GUI ; `Bilan.complete` est honnête.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

from src.enrichment.observation import Observation
from src.models.track import Credit, CreditRole, Track
from src.services.deezer_identite import cle_album
from src.services.runtime import Bilan
from src.utils import version_heritage
from src.utils.logger import get_logger
from src.utils.title_matching import (
    base_album_key,
    clean_stored_title,
    names_match_as_words,
    normalize_name,
    normalize_title,
)
from src.utils.version_descriptors import Kind, meme_prise, parse_variant

logger = get_logger(__name__)

NATURES = ("absent", "album_absent", "version", "apparition")
GLYPHES = {"absent": "✚", "album_absent": "💿", "version": "🎚️", "apparition": "👥"}
LIBELLES = {
    "absent": "morceau absent (disque connu)",
    "album_absent": "disque absent",
    "version": "version d'un morceau connu",
    "apparition": "apparition sur le disque d'un autre",
}


@dataclass
class PisteDeezer:
    id: int
    title: str
    title_short: str = ""
    title_version: str = ""
    isrc: str | None = None
    duration: int | None = None
    position: int | None = None
    disk: int | None = None
    explicit: bool | None = None
    artist_id: int | None = None
    artist_name: str = ""
    link: str | None = None
    #: Contributeurs `(id, nom)` — remplis par `/track/{id}` quand on les a lus.
    contributors: list[tuple[int, str]] = field(default_factory=list)

    @classmethod
    def depuis(cls, d: dict) -> PisteDeezer:
        art = d.get("artist") or {}
        return cls(
            id=int(d["id"]),
            title=d.get("title") or "",
            title_short=d.get("title_short") or "",
            title_version=(d.get("title_version") or "").strip(),
            isrc=d.get("isrc") or None,
            duration=int(d["duration"]) if d.get("duration") else None,
            position=d.get("track_position"),
            disk=d.get("disk_number"),
            explicit=d.get("explicit_lyrics"),
            artist_id=int(art["id"]) if art.get("id") else None,
            artist_name=art.get("name") or "",
            link=d.get("link"),
            contributors=[
                (int(c["id"]), c.get("name") or "")
                for c in d.get("contributors") or []
                if c.get("id")
            ],
        )


@dataclass
class AlbumDeezer:
    id: int
    title: str
    record_type: str | None = None
    artist_id: int | None = None
    artist_name: str = ""
    contributors: list[tuple[int, str]] = field(default_factory=list)
    nb_tracks: int | None = None
    release_date: str | None = None
    label: str | None = None
    link: str | None = None
    pistes: list[PisteDeezer] = field(default_factory=list)

    @classmethod
    def depuis(cls, fiche: dict, pistes: list[dict]) -> AlbumDeezer:
        art = fiche.get("artist") or {}
        rt = str(fiche.get("record_type") or "").strip().lower() or None
        return cls(
            id=int(fiche["id"]),
            title=fiche.get("title") or "",
            record_type=rt,
            artist_id=int(art["id"]) if art.get("id") else None,
            artist_name=art.get("name") or "",
            contributors=[
                (int(c["id"]), c.get("name") or "")
                for c in fiche.get("contributors") or []
                if c.get("id")
            ],
            nb_tracks=fiche.get("nb_tracks"),
            release_date=fiche.get("release_date") or None,
            label=fiche.get("label") or None,
            link=fiche.get("link"),
            pistes=[PisteDeezer.depuis(p) for p in pistes if p.get("id")],
        )


@dataclass
class Ecart:
    nature: str
    album: AlbumDeezer
    piste: PisteDeezer
    #: Le morceau SOUCHE en base quand la piste en est une version.
    socle: Track | None = None
    coche: bool = False
    motifs: list[str] = field(default_factory=list)
    #: Page Genius trouvée sous une autre graphie (`{id, title, url}`), sinon None.
    genius: dict | None = None
    #: ID Spotify d'une variante Kworb déjà rattachée au socle (mécanique e29).
    kworb_spotify_id: str | None = None

    @property
    def titre(self) -> str:
        return self.piste.title


@dataclass
class BilanEcarts(Bilan):
    deezer_id: int | None = None
    albums_lus: int = 0
    pistes_lues: int = 0
    ecarts: list[Ecart] = field(default_factory=list)
    #: Disques Deezer dont TOUTES les pistes sont connues sous un autre titre
    #: de disque (éditions jumelles « … » / « ... ») — information, pas écart.
    editions_jumelles: list[str] = field(default_factory=list)
    ambigu: list = field(default_factory=list)

    def compteurs(self) -> Counter:
        return Counter(e.nature for e in self.ecarts)

    def coches(self) -> list[Ecart]:
        return [e for e in self.ecarts if e.coche]


# ── Classification (pure) ────────────────────────────────────────────────────


def _variante_de(piste: PisteDeezer):
    """(socle normalisé, est une version ?) — `title_version` de Deezer d'abord
    (structuré), `parse_variant` en repli."""
    if piste.title_version:
        v = parse_variant(f"{piste.title_short} - {piste.title_version.strip('()[]')}")
        if v.kind != Kind.NONE:
            return normalize_title(piste.title_short or v.socle), True
    v = parse_variant(piste.title)
    return normalize_title(v.socle), v.kind != Kind.NONE


def classer(
    albums: list[AlbumDeezer], base: list[Track], albums_base: list[dict], notre_id: int
) -> tuple[list[Ecart], list[str]]:
    """Les pistes Deezer absentes de la base, classées. Rend `(ecarts, editions_jumelles)`.

    `base` = la discographie RÉUNIE (groupes/collectifs compris : un album de
    formation n'est pas une apparition). Un disque étranger n'apporte que les
    pistes dont les contributeurs (lus par l'appelant) comptent l'artiste.
    """
    titres: dict[str, Track] = {}
    socles: dict[str, Track] = {}
    for t in base:
        k = normalize_title(t.title)
        titres.setdefault(k, t)
        if parse_variant(t.title).kind == Kind.NONE:
            socles.setdefault(k, t)
    cles_albums = {cle_album(t.album) for t in base if t.album}
    cles_albums |= {cle_album(a.get("title")) for a in albums_base if a.get("title")}
    ids_albums = {int(a["deezer_album_id"]) for a in albums_base if a.get("deezer_album_id")}
    isrcs = {t.isrc for t in base if t.isrc}

    ecarts: list[Ecart] = []
    jumelles: list[str] = []
    for album in albums:
        est_a_nous = album.artist_id == notre_id
        cle = cle_album(album.title)
        connu = (
            album.id in ids_albums
            or cle in cles_albums
            or base_album_key(normalize_title(album.title), cles_albums) is not None
        )
        inconnues: list[tuple[PisteDeezer, Track | None, bool]] = []
        for p in album.pistes:
            if normalize_title(p.title) in titres or (p.isrc and p.isrc in isrcs):
                continue
            if not est_a_nous and not connu and notre_id not in {cid for cid, _ in p.contributors}:
                continue
            socle_key, est_version = _variante_de(p)
            socle = socles.get(socle_key) or titres.get(socle_key)
            inconnues.append((p, socle, est_version and socle is not None))
        if not inconnues:
            if album.pistes and not connu:
                jumelles.append(album.title)
            continue
        versions = sum(1 for _, _, v in inconnues if v)
        disque_de_versions = not connu and versions * 2 >= len(inconnues)
        for p, socle, est_version in inconnues:
            if not est_a_nous and not connu:
                # Un disque CONNU de la base reste le nôtre même si Deezer le
                # crédite au beatmaker (« Chardons Bleus » — Mani Deiz) : ses
                # pistes manquantes sont des trous, pas des apparitions.
                nature = "apparition"
            elif est_version:
                nature = "version"
            elif connu:
                nature = "absent"
            elif disque_de_versions:
                nature = "version"
            else:
                nature = "album_absent"
            e = Ecart(nature=nature, album=album, piste=p, socle=socle)
            e.coche = nature in ("absent", "album_absent")
            if nature == "apparition":
                e.motifs.append(f"disque de {album.artist_name}")
            if socle is not None:
                e.motifs.append(f"version de « {socle.title} »")
            ecarts.append(e)
    return ecarts, jumelles


def cocher_par_defaut(ecarts: list[Ecart], base: list[Track]) -> None:
    """Une version est cochée d'office si Genius la connaît ou si Kworb lui a
    déjà un ID Spotify (rendition sur le socle) — ReccoBeats suivra."""
    for e in ecarts:
        if e.nature != "version" or e.socle is None:
            continue
        v = parse_variant(e.titre)
        for entree in getattr(e.socle, "spotify_id_entries", None) or []:
            if (
                entree.est_rendition
                and not entree.variant_track_id
                and entree.label
                and (
                    normalize_title(entree.label) == normalize_title(e.titre)
                    or meme_prise(parse_variant(entree.label), v)
                )
            ):
                e.kworb_spotify_id = entree.spotify_id
                e.motifs.append("Kworb la connaît (ID Spotify, streams)")
                break
        if e.genius:
            e.motifs.append(f"page Genius : {e.genius.get('title')}")
        e.coche = bool(e.genius or e.kworb_spotify_id)


def accrocher_genius(ecarts: list[Ecart], artist, genius_api) -> None:
    """Cherche la page Genius de chaque écart (artiste principal + titre
    normalisé identiques, hit UNIQUE) ; sans hit sûr, rien n'est accroché."""
    if genius_api is None:
        return
    cible = normalize_name(artist.name)
    for e in ecarts:
        if e.nature == "apparition":
            continue
        try:
            hits = genius_api.search_songs(f"{artist.name} {e.piste.title_short or e.titre}")
        except Exception:  # noqa: BLE001 — une recherche ratée n'accroche rien, c'est tout
            logger.exception("Recherche Genius échouée")
            continue
        voulu = normalize_title(e.titre)
        bons = [
            h
            for h in hits
            if normalize_name((h.get("primary_artist") or {}).get("name") or "") == cible
            and normalize_title(h["title"]) == voulu
        ]
        if len(bons) == 1:
            e.genius = bons[0]


# ── Détection (async) ────────────────────────────────────────────────────────


async def detecter_async(
    client, http, dm, artist, *, deezer_id: int, should_stop=None, genius_api=None
) -> BilanEcarts:
    """Lit la discographie Deezer et classe les écarts. `client` = `DeezerAPI`."""
    bilan = BilanEcarts(deezer_id=deezer_id)
    should_stop = should_stop or (lambda: False)
    base = dm.discographie_reunie(artist)
    albums_base = dm.get_albums_for_artist(artist.id)
    albums: list[AlbumDeezer] = []
    try:
        for brut in await client.get_artist_albums_async(http, deezer_id):
            if should_stop():
                bilan.interrompu("arrêt demandé")
                break
            fiche = await client.get_album_async(http, int(brut["id"]))
            if not fiche:
                bilan.erreurs.append(f"fiche album {brut.get('title')} illisible")
                continue
            pistes = await client.get_album_tracks_async(http, int(brut["id"]))
            album = AlbumDeezer.depuis(fiche, pistes)
            bilan.albums_lus += 1
            bilan.pistes_lues += len(album.pistes)
            if album.artist_id != deezer_id:
                # Disque d'un autre : les contributeurs par piste disent si
                # l'artiste y est — lus seulement pour les pistes inconnues.
                connus = {normalize_title(t.title) for t in base}
                for p in album.pistes:
                    if normalize_title(p.title) in connus:
                        continue
                    detail = await client.get_track_async(http, p.id)
                    if detail:
                        p.contributors = [
                            (int(c["id"]), c.get("name") or "")
                            for c in detail.get("contributors") or []
                            if c.get("id")
                        ]
            albums.append(album)
    except Exception as e:  # noqa: BLE001 — le bilan porte l'échec, il ne le cache pas
        logger.exception("Lecture Deezer interrompue")
        bilan.interrompu(f"Deezer : {e}")
    ecarts, jumelles = classer(albums, base, albums_base, deezer_id)
    accrocher_genius(ecarts, artist, genius_api)
    cocher_par_defaut(ecarts, base)
    bilan.ecarts = ecarts
    bilan.editions_jumelles = jumelles
    return bilan


def detecter(runtime, artist, *, deezer_id: int, should_stop=None, genius_api=None) -> BilanEcarts:
    """Pont sync (CLI, worker GUI) : la session async vit sur la boucle unique."""
    from src.concurrency import async_loop

    client = runtime.data_enricher.deezer_client
    http = runtime.data_enricher.http
    return async_loop.run_sync(
        detecter_async(
            client,
            http,
            runtime.data_manager,
            artist,
            deezer_id=deezer_id,
            should_stop=should_stop,
            genius_api=genius_api,
        )
    )


# ── Création (sur validation) ────────────────────────────────────────────────


def _existant(dm, artist, titre: str) -> Track | None:
    cle = normalize_title(titre)
    return next(
        (t for t in dm.get_artist_tracks(artist.id) if normalize_title(t.title) == cle), None
    )


def _date(texte: str | None) -> datetime | None:
    if not texte or texte.startswith("0000"):
        return None
    try:
        return datetime.strptime(texte[:10], "%Y-%m-%d")
    except ValueError:
        return None


def _ligne(artist, e: Ecart, contributeurs: list[tuple[int, str]]) -> Track:
    """La ligne de morceau d'un écart, renseignée par Deezer."""
    a, p = e.album, e.piste
    track = Track(title=clean_stored_title(p.title), artist=artist)
    est_apparition = e.nature == "apparition"
    track.album = None if est_apparition else a.title
    track.track_number = p.position
    track.release_date = _date(a.release_date)
    track.duration = p.duration
    track.isrc = p.isrc
    track.deezer_id = p.id
    track.deezer_url = p.link
    track.lyrics.explicit = p.explicit
    track._deezer_album_id = a.id
    if est_apparition:
        track.is_featuring = True
        track.primary_artist_name = a.artist_name or None
    credits: list[Credit] = []
    for _cid, nom in contributeurs or a.contributors:
        if nom and not names_match_as_words(nom, artist.name):
            if est_apparition and names_match_as_words(nom, a.artist_name):
                continue
            credits.append(Credit(name=nom, role=CreditRole.FEATURED, source="deezer"))
    if a.label:
        credits.append(Credit(name=a.label, role=CreditRole.LABEL, source="deezer"))
    track.credits = credits
    if e.socle is not None:
        v = parse_variant(p.title)
        track.relationships = [
            {
                "type": "remix_of" if v.est_remix else "version_of",
                "title": e.socle.title,
                "artist": artist.name,
                "url": e.socle.genius_url,
                "track_id": e.socle.id,
            }
        ]
        track._relationships_pending = True
    if e.genius:
        track.genius_id = e.genius.get("id")
        track.genius_url = e.genius.get("url")
    obs = []
    if p.duration:
        obs.append(Observation("duration", p.duration, "deezer"))
    if track.release_date:
        obs.append(Observation("release_date", track.release_date.strftime("%Y-%m-%d"), "deezer"))
    if p.isrc:
        obs.append(Observation("isrc", p.isrc, "deezer"))
    track.observations = obs
    return track


def creer_lignes(dm, artist, ecarts: list[Ecart], *, lire_piste=None, heriter=None) -> list[str]:
    """Crée les lignes des écarts fournis (déjà VALIDÉS). Rend une ligne de
    compte rendu par écart. `lire_piste(id) -> dict|None` (fiche `/track/{id}`,
    pour les contributeurs) est injectable ; `heriter(version, socle)` l'est
    aussi (défaut : `version_heritage.heriter` — paroles/écriture/production
    du socle selon la famille de la version).
    """
    comptes: list[str] = []
    albums_poses: set[int] = set()
    for e in ecarts:
        if _existant(dm, artist, e.titre) is not None:
            comptes.append(f"« {e.titre} » existe déjà — rien")
            continue
        contributeurs = e.piste.contributors
        if not contributeurs and lire_piste is not None:
            detail = lire_piste(e.piste.id) or {}
            contributeurs = [
                (int(c["id"]), c.get("name") or "")
                for c in detail.get("contributors") or []
                if c.get("id")
            ]
        track = _ligne(artist, e, contributeurs)
        if e.socle is not None:
            (heriter or version_heritage.heriter)(track, e.socle)
        dm.save_track(track)
        dm.record_pending(track)
        if track.observations:
            dm.upsert_observations(track.id, track.observations)
        if e.nature != "apparition" and e.album.id not in albums_poses:
            dm.set_album_record_type(
                artist.id, e.album.title, e.album.record_type, deezer_album_id=e.album.id
            )
            albums_poses.add(e.album.id)
        # La version était une variante Kworb du socle : la fiche reprend ses
        # streams, le socle garde l'indication pointée vers elle (e29).
        if e.kworb_spotify_id and e.socle is not None:
            entree = next(
                (
                    x
                    for x in (e.socle.spotify_id_entries or [])
                    if x.spotify_id == e.kworb_spotify_id
                ),
                None,
            )
            if entree is not None:
                dm.record_variant_streams(
                    e.socle.id,
                    entree.spotify_id,
                    entree.streams or 0,
                    entree.daily_streams,
                    entree.streams_at,
                    label=entree.label,
                    variant_track_id=track.id,
                )
                if entree.streams:
                    dm.record_spotify_streams(
                        track.id,
                        entree.streams,
                        "kworb",
                        updated_at=entree.streams_at,
                        daily_streams=entree.daily_streams,
                    )
                dm.update_track_spotify_id(track.id, entree.spotify_id)
        comptes.append(
            f"« {track.title} » créé ({LIBELLES[e.nature]}"
            + (", page Genius accrochée" if e.genius else "")
            + ")"
        )
        logger.info(comptes[-1])
    return comptes


# ── Rendu ─────────────────────────────────────────────────────────────────────


def resume(bilan: BilanEcarts, artist_name: str = "") -> str:
    c = bilan.compteurs()
    lignes = [
        f"Deezer — {artist_name} (id {bilan.deezer_id}) : {bilan.albums_lus} disques lus, "
        f"{bilan.pistes_lues} pistes, {len(bilan.ecarts)} écart(s) dont {len(bilan.coches())} coché(s)"
    ]
    if not bilan.complete:
        lignes.append(f"⚠️ INCOMPLET : {bilan.motif}")
    for nature in NATURES:
        if c.get(nature):
            lignes.append(f"   {GLYPHES[nature]} {c[nature]} {LIBELLES[nature]}")
    par_album: dict[int, list[Ecart]] = {}
    for e in bilan.ecarts:
        par_album.setdefault(e.album.id, []).append(e)
    for ecarts in par_album.values():
        a = ecarts[0].album
        lignes.append(
            f"\n{a.record_type or '?':7} « {a.title} » — {a.artist_name}"
            + (f" ({a.release_date})" if a.release_date else "")
        )
        for e in ecarts:
            lignes.append(
                f"   [{'x' if e.coche else ' '}] {GLYPHES[e.nature]} {e.titre}"
                + (f"  · {' · '.join(e.motifs)}" if e.motifs else "")
            )
    if bilan.editions_jumelles:
        lignes.append(
            "\nÉditions jumelles (toutes les pistes connues) : "
            + ", ".join(bilan.editions_jumelles)
        )
    if bilan.erreurs:
        lignes.append("\nErreurs : " + " ; ".join(bilan.erreurs))
    return "\n".join(lignes)
