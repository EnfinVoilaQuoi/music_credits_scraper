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
from src.models import ReleaseObservation
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
from src.utils.version_descriptors import MOTS_DE_MEME_MORCEAU, Kind, meme_prise, parse_variant

logger = get_logger(__name__)

NATURES = (
    "absent",
    "album_absent",
    "version",
    "apparition",
    "link",
    "link_candidate",
    "link_review",
)
GLYPHES = {
    "absent": "✚",
    "album_absent": "💿",
    "version": "🎚️",
    "apparition": "👥",
    "link": "🔗",
    "link_candidate": "❓",
    "link_review": "🔓",
}
LIBELLES = {
    "absent": "morceau absent (disque connu)",
    "album_absent": "disque absent",
    "version": "version d'un morceau connu",
    "apparition": "apparition sur le disque d'un autre",
    "link": "parution à rattacher (morceau connu)",
    "link_candidate": "parution à confirmer (durée divergente ou titre générique)",
    "link_review": "lien à revoir (durée Deezer ≠ durée de la fiche) — cocher pour DÉLIER",
}

#: Un lien DÉJÀ écrit n'est remis en cause que par une durée venue d'AILLEURS
#: (songbpm, ytmusic, reccobeats, manual, legacy) qui s'en écarte de plus de
#: 5 s : 2 s est la tolérance « même fichier », 5 s celle de deux plateformes
#: (`spotify_identity`) — une REVUE ne doit pas crier sur un écart d'encodage.
ECART_DUREE_LIEN_A_REVOIR = 5

#: Preuves qui rattachent SANS confirmation. Mesuré le 2026-09-22 : l'ID Deezer
#: n'est posé que sur 6 % des fiches et l'ISRC sur 14 % — exiger l'un des deux
#: renvoyait 90 % des morceaux CONNUS en « à confirmer » à chaque run (EP,
#: best-of, single 2 titres). Un titre identique sans descripteur de version
#: sur un disque de l'artiste suffit ; la durée ne sert qu'à CONTREDIRE.
PREUVES_SURES = frozenset({"deezer_id", "isrc", "title_duration", "title"})


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
    #: Fiche existante à laquelle une parution doit être liée, jamais recréée.
    existing_track: Track | None = None
    #: `deezer_id`, `isrc` ou `title_duration` : preuve du rapprochement.
    matched_by: str | None = None
    #: Ce que le lien a apporté à la fiche (« id Deezer », « durée 203 s »…).
    apports: list[str] = field(default_factory=list)

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
    #: Liens prouvés écrits sans confirmation pendant cette détection.
    rattachements_auto: int = 0

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
            return normalize_title(piste.title_short or v.socle), not _edition_generique(v)
    v = parse_variant(piste.title)
    return normalize_title(v.socle), v.kind != Kind.NONE and not _edition_generique(v)


def _edition_generique(variant) -> bool:
    """Une mention éditoriale ne désigne pas une nouvelle prise musicale."""
    if variant.kind != Kind.RENDITION:
        return False
    return normalize_title(variant.descriptor or "") in {
        "version",
        "original version",
        "version originale",
        "album version",
        "version album",
    }


def _edition_generique_de(piste: PisteDeezer) -> bool:
    if piste.title_version:
        return _edition_generique(
            parse_variant(f"{piste.title_short} - {piste.title_version.strip('()[]')}")
        )
    return _edition_generique(parse_variant(piste.title))


def _cle_edition_generique(piste: PisteDeezer) -> str:
    """Titre du socle quand Deezer sépare seulement une mention éditoriale."""
    if piste.title_version:
        return normalize_title(piste.title_short or piste.title)
    return normalize_title(parse_variant(piste.title).socle)


def _match_existing(
    piste: PisteDeezer,
    *,
    by_deezer_id: dict[int, Track],
    by_isrc: dict[str, list[Track]],
    by_title: dict[str, list[Track]],
    title_key: str | None = None,
) -> tuple[Track | None, str | None]:
    """La fiche que cette piste désigne, et la PREUVE du rapprochement.

    De la plus forte à la plus faible : `deezer_id`, `isrc` (compatible avec le
    titre — une donnée source fautive ne relie jamais deux fiches), puis le
    titre, où la durée ne sert qu'à CONTREDIRE : deux durées connues qui
    concordent (± 2 s) ⇒ `title_duration` ; qui divergent ⇒
    `title_duration_conflict` (à confirmer) ; une durée manquante ⇒ `title`,
    sauf titre générique (Intro/Outro/Interlude…) où deux morceaux distincts
    portent couramment le même mot ⇒ `title_generique` (à confirmer).
    `(None, None)` quand aucune fiche ne porte ce titre.
    """
    title_key = title_key or normalize_title(piste.title)
    if piste.id in by_deezer_id:
        return by_deezer_id[piste.id], "deezer_id"
    if piste.isrc:
        for track in by_isrc.get(piste.isrc.upper(), []):
            if normalize_title(track.title) == title_key:
                return track, "isrc"
    candidats = by_title.get(title_key, [])
    if not candidats:
        return None, None
    if piste.duration is not None:
        datees = [t for t in candidats if t.duration is not None]
        for track in datees:
            if abs(int(piste.duration) - int(track.duration)) <= 2:
                return track, "title_duration"
        if datees and len(datees) == len(candidats):
            return datees[0], "title_duration_conflict"
    if set(title_key.split()) & MOTS_DE_MEME_MORCEAU:
        return candidats[0], "title_generique"
    if len(candidats) > 1:
        # Doublon intra-artiste (Josman « BOSS »/« Boss ») : désigner l'un des
        # deux au hasard renseignerait la mauvaise fiche — à confirmer.
        return candidats[0], "title_ambigu"
    return candidats[0], "title"


def _motif_preuve(piste: PisteDeezer, track: Track, preuve: str) -> str:
    return {
        "deezer_id": "même identifiant Deezer",
        "isrc": "même ISRC et même titre",
        "title_duration": "même titre, durée compatible",
        "title": "même titre (durée non comparable)",
        "title_generique": "titre générique, durée non comparable — à confirmer",
        "title_ambigu": "plusieurs fiches portent ce titre — à confirmer",
        "title_duration_conflict": (
            f"même titre, durée différente (Deezer {piste.duration} s / base {track.duration} s)"
            " — à confirmer"
        ),
    }[preuve]


def classer(
    albums: list[AlbumDeezer],
    base: list[Track],
    albums_base: list[dict],
    notre_id: int,
    liens_connus: set[tuple[int, int]] | frozenset = frozenset(),
) -> tuple[list[Ecart], list[str]]:
    """Les pistes Deezer absentes de la base, classées. Rend `(ecarts, editions_jumelles)`.

    `base` = la discographie RÉUNIE (groupes/collectifs compris : un album de
    formation n'est pas une apparition). Un disque étranger n'apporte que les
    pistes dont les contributeurs (lus par l'appelant) comptent l'artiste.
    `liens_connus` = `(id d'album Deezer, track_id)` déjà dans le catalogue des
    parutions : une piste déjà rattachée n'est ni reproposée ni recomptée.
    """
    titres: dict[str, Track] = {}
    titres_tous: dict[str, list[Track]] = {}
    deezer_ids: dict[int, Track] = {}
    isrcs: dict[str, list[Track]] = {}
    socles: dict[str, Track] = {}
    for t in base:
        k = normalize_title(t.title)
        titres.setdefault(k, t)
        titres_tous.setdefault(k, []).append(t)
        if t.deezer_id:
            deezer_ids[int(t.deezer_id)] = t
        if t.isrc:
            isrcs.setdefault(str(t.isrc).upper(), []).append(t)
        if parse_variant(t.title).kind == Kind.NONE:
            socles.setdefault(k, t)
    cles_albums = {cle_album(t.album) for t in base if t.album}
    cles_albums |= {cle_album(a.get("title")) for a in albums_base if a.get("title")}
    ids_albums = {int(a["deezer_album_id"]) for a in albums_base if a.get("deezer_album_id")}
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
            # « Album Version » est parfois porté hors du titre principal : le
            # socle est alors `title_short`, pas la chaîne décorée Deezer.
            edition_generique = _edition_generique_de(p)
            cle_piste = _cle_edition_generique(p) if edition_generique else None
            existing, match_by = _match_existing(
                p,
                by_deezer_id=deezer_ids,
                by_isrc=isrcs,
                by_title=titres_tous,
                title_key=cle_piste,
            )
            present = est_a_nous or connu or notre_id in {cid for cid, _ in p.contributors}
            if existing is not None:
                # Un titre ne prouve rien sur le disque d'un AUTRE où l'artiste
                # n'est pas crédité : homonyme, on passe (comme avant e31).
                if not present and match_by not in ("deezer_id", "isrc"):
                    continue
                if (album.id, existing.id) in liens_connus:
                    revue = _lien_a_revoir(p, existing)
                    if revue is not None:
                        ecarts.append(
                            Ecart(
                                nature="link_review",
                                album=album,
                                piste=p,
                                existing_track=existing,
                                matched_by="known_link",
                                motifs=[revue],
                            )
                        )
                        continue
                    reste = _reste_a_renseigner(p, existing)
                    if not reste:
                        continue
                    # Le lien existe déjà, mais la fiche n'a pas reçu ce que la
                    # piste porte — les 314 liens écrits AVANT que le lien
                    # renseigne la fiche (2026-09-22). Repassé en 🔗 tant qu'il
                    # reste quelque chose à donner, muet ensuite : sans cette
                    # règle, `liens_connus` les aurait sautés pour toujours.
                    ecarts.append(
                        Ecart(
                            nature="link",
                            album=album,
                            piste=p,
                            existing_track=existing,
                            matched_by=match_by,
                            coche=True,
                            motifs=[f"parution déjà connue — fiche à compléter ({reste})"],
                        )
                    )
                    continue
                nature = "link" if match_by in PREUVES_SURES else "link_candidate"
                ecarts.append(
                    Ecart(
                        nature=nature,
                        album=album,
                        piste=p,
                        existing_track=existing,
                        matched_by=match_by,
                        coche=nature == "link",
                        motifs=[_motif_preuve(p, existing, match_by)],
                    )
                )
                continue
            if not present:
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
            if nature == "version" and socle is not None:
                e.motifs.append(f"version de « {socle.title} »")
            ecarts.append(e)
    _degrader_liens_divergents(ecarts)
    return ecarts, jumelles


def _lien_a_revoir(piste: PisteDeezer, track: Track) -> str | None:
    """Motif de revue d'un lien connu, ou None.

    Seule une durée INDÉPENDANTE de Deezer peut contredire le lien — une durée
    écrite par ce lien le validerait par sa propre conséquence. Et comme
    `deezer` est en tête de l'arbitrage, après un lien c'est LUI qui gagne la
    colonne : la durée d'une autre source ne survit que dans
    `durations_observees` (mapper), c'est là qu'on regarde ; à défaut, la
    colonne arbitrée quand sa source est indépendante. `legacy` compte —
    origine inconnue, ça vaut un regard.
    """
    if piste.duration is None:
        return None
    independantes = {
        src: v for src, v in (track.durations_observees or {}).items() if src != "deezer" and v
    }
    if not independantes and track.duration and track.duration_source not in (None, "deezer"):
        independantes = {track.duration_source: track.duration}
    for source, duree in sorted(independantes.items()):
        if abs(int(piste.duration) - int(duree)) > ECART_DUREE_LIEN_A_REVOIR:
            return (
                f"lien existant : Deezer {piste.duration} s / fiche {duree} s "
                f"({source}) — cocher pour délier"
            )
    return None


def _reste_a_renseigner(piste: PisteDeezer, track: Track) -> str:
    """Ce que cette piste peut encore apporter à la fiche (« id Deezer, durée »),
    ou "" si elle n'a plus rien à donner. Prédicat PUR, lu sur l'objet : la
    durée `deezer` vit dans `durations_observees` (posé par le mapper)."""
    manques = []
    if piste.id and track.deezer_id is None:
        manques.append("id Deezer")
    if piste.isrc and not track.isrc:
        manques.append("ISRC")
    if piste.duration and not (track.durations_observees or {}).get("deezer"):
        manques.append("durée")
    return ", ".join(manques)


def _degrader_liens_divergents(ecarts: list[Ecart]) -> None:
    """Deux pistes liées au TITRE à la même fiche, à durées Deezer divergentes
    (> 2 s) : l'une des deux n'est pas cet enregistrement, et écrire les deux
    dans le même run ferait écraser la première durée par la seconde en
    silence. Les deux passent en ❓."""
    par_fiche: dict[int, list[Ecart]] = {}
    for e in ecarts:
        if e.nature == "link" and e.matched_by == "title" and e.existing_track is not None:
            par_fiche.setdefault(id(e.existing_track), []).append(e)
    for groupe in par_fiche.values():
        durees = {e.piste.duration for e in groupe if e.piste.duration is not None}
        if len(durees) < 2 or max(durees) - min(durees) <= 2:
            continue
        for e in groupe:
            e.nature, e.coche = "link_candidate", False
            e.motifs.append(
                "durées Deezer divergentes entre parutions "
                f"({' / '.join(f'{d} s' for d in sorted(durees))}) — à confirmer"
            )


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
    ecarts, jumelles = classer(
        albums, base, albums_base, deezer_id, liens_connus=dm.get_deezer_release_links(artist.id)
    )
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
    scope = (
        "own" if artist.deezer_id is not None and a.artist_id == artist.deezer_id else "appearance"
    )
    if artist.deezer_id is None and names_match_as_words(a.artist_name, artist.name):
        scope = "own"
    track.release_observations.append(
        ReleaseObservation(
            title=a.title,
            credited_artist_name=a.artist_name or None,
            release_date=track.release_date,
            record_type=a.record_type,
            scope=scope,
            source="deezer",
            external_release_id=a.id,
            external_track_id=p.id,
            disc_number=p.disk,
            track_number=p.position,
            confidence=e.matched_by or "identified",
        )
    )
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


def _renseigner_fiche(dm, e: Ecart, track: Track) -> list[str]:
    """Ce que la piste Deezer apporte à la fiche qu'elle désigne (lot 2).

    Le lien dit « même enregistrement » ; refuser sa durée ou son id serait
    deux vérités pour un même fait. Les identités se REMPLISSENT sans jamais
    remplacer (`fill_track_identities`) ; la durée est DÉCLARÉE en observation
    `deezer` et la colonne arbitrée (`record_discography_observations`) — la
    voie des 40 % de fiches sans durée, que la recherche par morceau ne sert
    plus. L'ISRC n'est observé que s'il ne CONTREDIT pas la fiche : une
    observation divergente remplacerait la colonne d'identité à l'arbitrage.
    Pas de `release_date` : la date d'une parution est celle de l'ÉDITION (un
    best-of 2020 pour un titre de 2005). Rend ce qui a été écrit (compte rendu)
    et met l'objet en phase.
    """
    p = e.piste
    apports: list[str] = []
    isrc_compatible = bool(p.isrc) and (
        not track.isrc or str(track.isrc).strip().upper() == p.isrc.strip().upper()
    )
    ecrites = dm.fill_track_identities(
        track.id,
        deezer_id=p.id,
        deezer_url=p.link,
        isrc=p.isrc if isrc_compatible else None,
    )
    if ecrites.get("deezer_id"):
        track.deezer_id = p.id
        apports.append("id Deezer")
    if ecrites.get("deezer_url"):
        track.deezer_url = p.link
    if ecrites.get("isrc"):
        track.isrc = p.isrc
        apports.append("ISRC")
    observations = []
    if p.duration:
        observations.append(Observation("duration", p.duration, "deezer"))
    if isrc_compatible:
        observations.append(Observation("isrc", p.isrc, "deezer"))
    if observations:
        valeurs = dm.record_discography_observations(track.id, observations)
        if "duration" in valeurs:
            if track.duration != valeurs["duration"]:
                apports.append(f"durée {valeurs['duration']} s")
            track.duration = valeurs["duration"]
            track.duration_source = "deezer" if valeurs["duration"] == p.duration else None
        if "isrc" in valeurs:
            track.isrc = valeurs["isrc"]
    return apports


def _lier_parution(dm, artist, e: Ecart, track: Track) -> bool:
    """Enregistre l'apparition Deezer sans modifier `tracks.album`, puis
    renseigne la fiche (`_renseigner_fiche`).

    La création d'une fiche neuve pose encore son album de référence dans
    `_ligne`; rattacher une fiche existante ne le fait jamais. C'est le garde
    qui empêche une compilation de voler l'album original au prochain run.
    """
    scope = (
        "own"
        if artist.deezer_id is not None and e.album.artist_id == artist.deezer_id
        else "appearance"
    )
    if artist.deezer_id is None and names_match_as_words(e.album.artist_name, artist.name):
        scope = "own"
    dm.record_release_observations(
        track.id,
        [
            ReleaseObservation(
                title=e.album.title,
                credited_artist_name=e.album.artist_name or None,
                release_date=_date(e.album.release_date),
                record_type=e.album.record_type,
                scope=scope,
                source="deezer",
                external_release_id=e.album.id,
                external_track_id=e.piste.id,
                disc_number=e.piste.disk,
                track_number=e.piste.position,
                confidence=e.matched_by or "identified",
            )
        ],
    )
    e.apports = _renseigner_fiche(dm, e, track)
    return True


def _rang_de_liaison(e: Ecart, artist) -> tuple:
    """Ordre d'écriture des liens d'une même fiche : la parution qui EST son
    album repère d'abord, puis les siennes, puis les apparitions — c'est le
    premier lien qui renseigne `deezer_id`, il doit être celui du disque que la
    fiche revendique déjà."""
    track = e.existing_track
    repere = bool(track and track.album and cle_album(e.album.title) == cle_album(track.album))
    a_nous = artist.deezer_id is not None and e.album.artist_id == artist.deezer_id
    return (0 if repere else 1, 0 if a_nous else 1, e.album.id)


def rattacher_liens_confirmes(dm, artist, bilan: BilanEcarts, *, should_stop=lambda: False) -> int:
    """Écrit les rattachements d'identité forte, sans passer par une case GUI.

    Ces lignes ne sont pas des créations de morceaux : Deezer a seulement vu
    le même enregistrement sur une autre parution. Après succès elles sortent
    du bilan utilisateur ; un échec reste visible afin de ne jamais disparaître
    silencieusement.
    """
    restants: list[Ecart] = []
    rattaches = 0
    liens = [e for e in bilan.ecarts if e.nature == "link" and e.existing_track is not None]
    ordre = {
        id(e): i for i, e in enumerate(sorted(liens, key=lambda e: _rang_de_liaison(e, artist)))
    }
    for ecart in sorted(bilan.ecarts, key=lambda e: ordre.get(id(e), -1)):
        if should_stop():
            restants.append(ecart)
            continue
        if ecart.nature != "link" or ecart.existing_track is None:
            restants.append(ecart)
            continue
        try:
            if _lier_parution(dm, artist, ecart, ecart.existing_track):
                rattaches += 1
                continue
        except Exception:  # noqa: BLE001
            logger.exception("Rattachement Deezer automatique échoué : %s", ecart.titre)
        restants.append(ecart)
    bilan.ecarts = restants
    bilan.rattachements_auto += rattaches
    return rattaches


def delier(dm, artist, e: Ecart) -> str:
    """Défait un lien de parution jugé faux (❓ `link_review` coché).

    Le lien part ; si l'id Deezer de la fiche est celui de cette piste, il
    part avec tout ce qu'il a écrit (`clear_track_deezer_id` : observations
    deezer, colonnes ré-arbitrées). L'album REPÈRE n'est jamais réécrit ici
    (`needs_replacement`) : c'est un geste humain, dans la vue Albums.
    """
    track = e.existing_track
    release_id = dm.get_release_id_for_deezer_album(artist.id, e.album.id)
    if release_id is None:
        return f"« {e.titre} » : parution « {e.album.title} » introuvable — rien"
    statut = dm.unlink_track_from_release(release_id, track.id)
    if statut == "needs_replacement":
        return (
            f"« {e.titre} » : « {e.album.title} » est son album repère — "
            "à traiter dans la vue Albums (choisir un autre repère)"
        )
    if statut != "removed":
        return f"« {e.titre} » : lien avec « {e.album.title} » déjà absent"
    compte = f"« {e.titre} » délié de « {e.album.title} »"
    if track.deezer_id is not None and int(track.deezer_id) == int(e.piste.id):
        rapport = dm.clear_track_deezer_id(track.id, e.piste.id)
        track.deezer_id = None
        compte += (
            f" — id Deezer {e.piste.id} retiré, "
            f"{len(rapport['observations_retirees'])} observation(s) deezer purgée(s)"
        )
    return compte


def creer_lignes(
    dm, artist, ecarts: list[Ecart], *, lire_piste=None, heriter=None, should_stop=lambda: False
) -> list[str]:
    """Crée les lignes des écarts fournis (déjà VALIDÉS). Rend une ligne de
    compte rendu par écart. `lire_piste(id) -> dict|None` (fiche `/track/{id}`,
    pour les contributeurs) est injectable ; `heriter(version, socle)` l'est
    aussi (défaut : `version_heritage.heriter` — paroles/écriture/production
    du socle selon la famille de la version).
    """
    comptes: list[str] = []
    albums_poses: set[int] = set()
    for e in ecarts:
        # Le stop est testé ENTRE deux unités seulement : une fiche déjà en
        # écriture reste atomique, la suivante ne démarre pas à la fermeture.
        if should_stop():
            logger.info("⏹️ Création Deezer interrompue à la demande")
            break
        if e.nature == "link_review":
            comptes.append(delier(dm, artist, e))
            continue
        if e.existing_track is not None:
            if _lier_parution(dm, artist, e, e.existing_track):
                comptes.append(
                    f"« {e.titre} » rattaché à « {e.album.title} » ({e.matched_by})"
                    + (f" — {', '.join(e.apports)}" if e.apports else "")
                )
            else:
                comptes.append(f"« {e.titre} » : rattachement impossible")
            continue
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
    if bilan.rattachements_auto:
        lignes.append(
            f"   🔗 {bilan.rattachements_auto} rattachement(s) automatique(s) à une parution"
        )
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
