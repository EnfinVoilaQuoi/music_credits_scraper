"""Étape « Identité » : relier chaque fiche aux enregistrements des plateformes.

WIP § Étape Identité (2026-09-27) — l'ID Spotify d'une fiche était posé par
sept chemins répartis dans trois runs, chacun avec ce qu'il savait au moment où
il tournait : le résultat dépendait de l'ORDRE des runs. Cette étape appelle
les fonctions EXISTANTES dans l'ordre de la force de la preuve, et rien d'autre
— aucun gate, oracle ni seuil n'est neuf ici (plan `plan_etape_identite.md`).

    1a artiste Deezer      (oracle `deezer_identite`)
    1b MusicBrainz+Discogs (formations et alias PROPOSÉS, identité Discogs)
    2a catalogue Deezer    (écarts : liens prouvés écrits, le reste « à trancher »)
    3a Deezer par morceau  (provider ; B0 : la piste liée se lit par son id)
    1c artiste Spotify     (vote sur les IDs des non-feats — Kworb en a besoin)
    2c catalogue Kworb     (le vrai catalogue Spotify de l'artiste, ID compris ;
       déménagé du run Streams le 2026-09-28, qui ne fait plus que compter)
    3b Spotify par morceau (scraper + LLM, gate `valider_identite`, pour ce que
       Kworb n'a pas relié) — APRÈS 3a, pour qu'une durée INDÉPENDANTE existe au
       moment du jugement (73 variantes avaient hérité de l'ID de l'original
       quand la durée « suivait » l'ID)
    2b nature des disques  (catalogue > hits de 3a > recherche d'album)

Une panne d'une couche rend le run INCOMPLET mais n'empêche pas les suivantes ;
une ambiguïté (homonyme Deezer) est un résultat complet, « à trancher ».
`force` élargit la SÉLECTION (redemander), jamais ne passe `force_update` aux
providers : chez eux il court-circuite les contrôles de cohérence, et une MàJ
forcée redemande, elle ne vide pas (décision 2026-09-27).

Sans GUI ni tkinter (test structurel) ; `__init__` du package reste vide.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from src.models import Artist, Track
from src.services.runtime import Bilan, Hooks, Manque, Runtime, selection_morceaux
from src.utils.logger import get_logger

logger = get_logger(__name__)

#: Sources de durée INDÉPENDANTES de l'ID Spotify (`DISCOGRAPHY_PRIORITIES`
#: hors spotify_web et reccobeats, qui lisent la durée PAR l'ID).
DUREES_INDEPENDANTES = frozenset(
    {"deezer", "ytmusic", "youtube", "songbpm", "youtube_video", "manual"}
)


@dataclass(frozen=True)
class OptionsIdentite:
    """Une case par couche (CLI : `--no-x`). `force` : redemander aussi ce qui
    est déjà relié ou daté. Forçages d'artiste : homonyme tranché à la main."""

    deezer: bool = True  # 1a + 2a
    musicbrainz: bool = True  # 1b
    par_morceau: bool = True  # 3a + 3b
    spotify_artiste: bool = True  # 1c
    kworb: bool = True  # 2c
    nature_disques: bool = True  # 2b
    force: bool = False
    deezer_id: int | None = None
    spotify_id: str | None = None


@dataclass
class PropositionsIdentite:
    """Ce que MusicBrainz + Discogs proposent (rien n'est confirmé ici)."""

    formations: int = 0
    alias: int = 0
    infos: int = 0
    identite_mb: str = ""
    identite_discogs: str = ""
    #: Identité Discogs NON vérifiée (provisoire, ambiguë, inconnue) : le
    #: rattrapage de fin de cycle la retente quand des disques ont été lus.
    discogs_a_revoir: bool = True


@dataclass
class CatalogueDeezer:
    deezer_id: int | None = None
    ecarts: object = None  # BilanEcarts
    motif: str = ""
    #: Panne (Deezer injoignable, détection interrompue) — pas une ambiguïté.
    panne: bool = False


@dataclass
class BilanIdentite(Bilan):
    #: Une ligne par source d'artiste : forcée · mémorisée · votée · provisoire ·
    #: ambiguë · panne · non lancée (+ détail).
    artistes: dict[str, str] = field(default_factory=dict)
    propositions: PropositionsIdentite | None = None
    catalogue: CatalogueDeezer | None = None
    deezer_morceaux: int = 0
    spotify_morceaux: int = 0
    #: IDs Spotify posés au passage 3b sans AUCUNE durée indépendante sur la
    #: fiche — la contre-vérification (③) les rejugera quand une arrivera.
    spotify_sans_duree: int = 0
    kworb_ids: int = 0
    kworb_editions: int = 0
    types_albums: int = 0
    albums_ignores: list[str] = field(default_factory=list)
    avant: dict[str, int] = field(default_factory=dict)
    apres: dict[str, int] = field(default_factory=dict)


def compter_ids(tracks: Iterable[Track], albums: Iterable[dict] = ()) -> dict[str, int]:
    """La mesure avant/après d'un run — ce qu'exige la suite (②)."""
    tracks = list(tracks)
    return {
        "fiches": len(tracks),
        "deezer_id": sum(1 for t in tracks if t.deezer_id),
        "isrc": sum(1 for t in tracks if t.isrc),
        "spotify_id": sum(1 for t in tracks if t.spotify_id),
        "duration": sum(1 for t in tracks if t.duration),
        "record_type": sum(1 for a in albums if a.get("record_type")),
    }


# ── 1a + 2a : Deezer (artiste puis catalogue) ─────────────────────────────────


def catalogue_deezer(
    runtime: Runtime, artist: Artist, hooks: Hooks, *, force_id: int | None = None
) -> CatalogueDeezer:
    """Artiste Deezer (oracle) puis lecture du catalogue : les liens PROUVÉS
    sont écrits, le reste devient signalement « À trancher ». Une ambiguïté
    d'homonyme passe par `hooks.confirmer_ecarts` (GUI : la fenêtre des écarts,
    seule à savoir choisir l'artiste). Ne lève pas : une panne est DITE."""
    from src.concurrency import async_loop
    from src.services import deezer_identite, ecarts_deezer

    res = CatalogueDeezer()
    enricher = runtime.data_enricher
    if enricher is None or getattr(enricher, "deezer_client", None) is None:
        res.motif, res.panne = "client Deezer indisponible", True
        return res
    try:
        res.deezer_id = async_loop.run_sync(
            deezer_identite.resoudre_async(
                enricher.deezer_client,
                enricher.http,
                runtime.data_manager,
                artist,
                force_id=force_id,
            )
        )
    except deezer_identite.ArtisteDeezerAmbigu as e:
        res.motif = "artiste Deezer ambigu — à choisir dans la fenêtre « Écarts Deezer »"
        res.ecarts = ecarts_deezer.BilanEcarts(complete=False, motif=res.motif)
        res.ecarts.ambigu = e.candidats
        hooks.confirmer_ecarts(res.ecarts)
        return res
    except Exception as e:  # noqa: BLE001 — source dite, jamais bloquante
        logger.exception("Identité Deezer impossible")
        res.motif, res.panne = f"Deezer injoignable ({e})", True
        return res
    try:
        res.ecarts = ecarts_deezer.detecter(
            runtime,
            artist,
            deezer_id=res.deezer_id,
            should_stop=hooks.should_stop,
            genius_api=runtime.genius_api,
        )
    except Exception as e:  # noqa: BLE001 — idem
        logger.exception("Détection des écarts Deezer échouée")
        res.motif, res.panne = f"Deezer : {e}", True
        return res
    if not res.ecarts.complete:
        res.panne = True
    # Les liens PROUVÉS s'écrivent sans confirmation (décision 2026-09-22) —
    # AVANT les signalements : un lien écrit n'est pas un cas à trancher.
    ecarts_deezer.rattacher_liens_confirmes(
        runtime.data_manager, artist, res.ecarts, should_stop=hooks.should_stop
    )
    ecarts_deezer.enregistrer_signalements(runtime.data_manager, artist, res.ecarts)
    if res.ecarts.ecarts:
        hooks.confirmer_ecarts(res.ecarts)
    return res


# ── 1b : MusicBrainz + Discogs ───────────────────────────────────────────────

_LIBELLES_DISCOGS = {
    "forcee": "forcée",
    "memorisee": "mémorisée",
    "disques": "par les disques",
    "musicbrainz": "lien MusicBrainz, à confirmer par les disques",
    "contredite": "CONTREDITE — MusicBrainz et les disques divergent (à trancher)",
    "annuaire": "provisoire, annuaire",
    "ambigu": "ambiguë",
}


def proposer_formations(runtime: Runtime, artist: Artist) -> PropositionsIdentite:
    """Formations ET alias PROPOSÉS (jamais confirmés) par MusicBrainz + Discogs
    — sync, bloquant (appeler hors de la boucle). Une saturation MusicBrainz
    est une PANNE (lève RuntimeError), pas « aucun alias »."""
    from src.utils.formations import chercher_formations, liens_a_proposer

    dm = runtime.data_manager
    rapport = chercher_formations(artist, dm)
    if rapport.panne_mb:
        raise RuntimeError(rapport.panne_mb)
    proposes, infos = liens_a_proposer(rapport, artist.name)
    res = PropositionsIdentite(
        formations=dm.propose_artist_relations(
            artist.id, [r for r in proposes if r.kind != "alias"]
        ),
        alias=dm.propose_artist_relations(artist.id, [r for r in proposes if r.kind == "alias"]),
        infos=dm.propose_artist_relations(artist.id, infos, "info"),
    )
    res.identite_mb = (
        f"MusicBrainz : {rapport.identite_mb or rapport.mbid}"
        if rapport.mbid
        else "MusicBrainz : artiste non résolu (rien proposé)"
    )
    idd = rapport.identite_discogs
    if idd is not None and idd.origine != "inconnu":
        libelle = _LIBELLES_DISCOGS.get(idd.origine, idd.origine)
        if idd.origine in ("disques", "musicbrainz", "annuaire", "ambigu") and idd.detail:
            libelle += f" ({idd.detail})"
        res.identite_discogs = f"Discogs : {libelle}"
    res.discogs_a_revoir = idd is None or idd.origine not in ("forcee", "memorisee", "disques")
    if idd is not None and idd.origine != "inconnu":
        _signaler_contradiction(dm, artist, idd)
    return res


def _signaler_contradiction(dm, artist: Artist, idd) -> None:
    """Garde-fou de l'identité MusicBrainz : une contradiction des disques est
    un cas « À trancher » (remplacé à chaque résolution, donc effacé dès que
    l'identité est tranchée ou confirmée)."""
    from src.services import revue

    cas = []
    if idd.origine == "contredite":
        cas.append(
            revue.cas_de_run(
                "discogs_contredit",
                None,
                f"Identité Discogs de {artist.name}",
                f"MusicBrainz lie la page Discogs {idd.selon_musicbrainz}, les disques "
                f"élisent {idd.selon_disques} — laquelle est « {artist.name} » ?",
                {"musicbrainz": idd.selon_musicbrainz, "disques": idd.selon_disques},
            )
        )
    dm.remplacer_signalements(artist.id, "discogs_contredit", cas)


def rattraper_discogs(runtime: Runtime, artist: Artist) -> PropositionsIdentite | None:
    """Fin de cycle : l'identité Discogs vote sur les `tracks.discogs_id`, posés
    par C&P et Enrich — APRÈS Identité. Sur un artiste neuf elle n'avait donc
    rien ; on la retente UNE fois, seulement si elle n'est pas mémorisée ET que
    des disques non-feat existent désormais (sinon rien de neuf à voter).
    Même fonction que la couche 1b, aucune copie."""
    if artist.discogs_id:
        return None
    tracks = runtime.data_manager.get_artist_tracks(artist.id)
    if not any(t.discogs_id and not t.is_featuring for t in tracks):
        return None
    logger.info(f"🔁 Identité Discogs retentée en fin de cycle pour {artist.name}")
    return proposer_formations(runtime, artist)


# ── 3a / 3b : par morceau ────────────────────────────────────────────────────


def _passage(runtime, artist, tracks, source: str, hooks: Hooks):
    """UN provider sur une sélection, par le pipeline d'enrichissement (teardown
    et gardes réutilisés tels quels) — sources EXPLICITES, donc le passage
    Deezer n'embarque pas le scrape Spotify, qu'il précède exprès."""
    from src.services import enrichissement

    options = enrichissement.OptionsEnrich(sources=(source,))
    return enrichissement.run(runtime, artist, tracks, options, hooks)


def _recharger(runtime: Runtime, artist: Artist) -> None:
    artist.tracks = runtime.data_manager.discographie_reunie(artist)


# ── Orchestration ────────────────────────────────────────────────────────────


def run(runtime: Runtime, artist: Artist, options: OptionsIdentite, hooks: Hooks) -> BilanIdentite:
    """Les couches dans l'ordre de la preuve. Sync : appelée depuis le fil du
    worker ou de la CLI, jamais depuis la boucle asyncio."""
    dm = runtime.data_manager
    bilan = BilanIdentite()
    bilan.avant = compter_ids(artist.tracks, dm.get_albums_for_artist(artist.id))

    def panne(couche: str, motif: str) -> None:
        bilan.erreurs.append(f"{couche} : {motif}")
        bilan.complete = False
        if not bilan.motif:
            bilan.motif = f"{couche} en panne"

    arrete: list[str] = []

    def arret(avant: str) -> bool:
        # Le PREMIER arrêt fait foi : le motif nomme la couche non lancée.
        if not arrete and hooks.should_stop():
            arrete.append(avant)
            bilan.interrompu(f"arrêt demandé avant {avant}")
        return bool(arrete)

    # 1a + 2a — Deezer
    if options.deezer and not arret("Deezer"):
        hooks.progress(0, 1, "Artiste et catalogue", "Deezer")
        bilan.catalogue = catalogue_deezer(runtime, artist, hooks, force_id=options.deezer_id)
        c = bilan.catalogue
        if c.panne:
            bilan.artistes["deezer"] = f"panne — {c.motif}"
            panne("Deezer", c.motif)
        elif c.deezer_id is None:
            bilan.artistes["deezer"] = f"ambiguë — {c.motif}"
        else:
            bilan.artistes["deezer"] = (
                f"{'forcée' if options.deezer_id else 'résolue'} ({c.deezer_id})"
            )
        _recharger(runtime, artist)

    # 1b — MusicBrainz + Discogs
    if options.musicbrainz and not arret("MusicBrainz"):
        hooks.progress(0, 1, "Formations et alias", "MusicBrainz")
        try:
            bilan.propositions = proposer_formations(runtime, artist)
            bilan.artistes["musicbrainz"] = bilan.propositions.identite_mb
            if bilan.propositions.identite_discogs:
                bilan.artistes["discogs"] = bilan.propositions.identite_discogs
        except Exception as e:  # noqa: BLE001 — une couche en panne n'arrête pas les suivantes
            logger.exception("Identité (MusicBrainz) : échec")
            bilan.artistes["musicbrainz"] = f"panne — {e}"
            panne("MusicBrainz", str(e))

    # 3a — Deezer par morceau (APRÈS le catalogue : ce qu'il a relié sort de la sélection)
    hits_albums: dict[int, int] = {}
    if options.par_morceau and options.deezer and not arret("Deezer par morceau"):
        kinds = () if options.force else (Manque.IDENTITE_DEEZER,)
        tracks = selection_morceaux(runtime, artist, manquants=kinds)
        if tracks:
            b = _passage(runtime, artist, tracks, "deezer", hooks)
            bilan.deezer_morceaux = b.traites
            if not b.complete:
                panne("Deezer par morceau", b.motif)
            # Les hits d'album du provider sont TRANSITOIRES : gardés pour 2b.
            hits_albums = {t.id: t._deezer_album_id for t in tracks if t._deezer_album_id}
        _recharger(runtime, artist)

    # 1c — artiste Spotify (avant Kworb, qui en a besoin pour trouver la page)
    if options.spotify_artiste and not arret("artiste Spotify"):
        bilan.artistes["spotify"] = _artiste_spotify(runtime, artist, options, hooks)
        if bilan.artistes["spotify"].startswith("panne"):
            panne("artiste Spotify", bilan.artistes["spotify"])

    # 2c — catalogue Spotify via Kworb (② 2026-09-28) : la page Kworb est le
    # vrai catalogue de l'artiste, ID compris ; APRÈS 3a (durée Deezer connue
    # au jugement) et AVANT le scraper, qui ne cherche plus que le reste.
    if options.kworb and not arret("Kworb"):
        hooks.progress(0, 1, "Catalogue Spotify", "Kworb")
        try:
            from src.utils.update_kworb import relier_ids_kworb

            k = relier_ids_kworb(artist, runtime.data_manager)
            bilan.kworb_ids, bilan.kworb_editions = k["ids_poses"], k["editions"]
            if not k["page"]:
                bilan.artistes["kworb"] = (
                    "aucune page validée — artiste absent de Kworb (sous son seuil), "
                    "page d'un homonyme, ou ID Spotify d'artiste inconnu"
                )
        except Exception as e:  # noqa: BLE001 — une couche en panne n'arrête pas les suivantes
            logger.exception("Identité (Kworb) : échec")
            bilan.artistes["kworb"] = f"panne — {e}"
            panne("Kworb", str(e))
        _recharger(runtime, artist)

    # 3b — Spotify par morceau (APRÈS 3a : une durée indépendante existe)
    if options.par_morceau and not arret("Spotify par morceau"):
        kinds = (Manque.IDENTITE_SPOTIFY_FORCE,) if options.force else (Manque.IDENTITE_SPOTIFY,)
        tracks = selection_morceaux(runtime, artist, manquants=kinds)
        sans_duree = {
            t.id for t in tracks if not (set(t.durations_observees) & DUREES_INDEPENDANTES)
        }
        if tracks:
            b = _passage(runtime, artist, tracks, "spotify_id", hooks)
            bilan.spotify_morceaux = b.traites
            if not b.complete:
                panne("Spotify par morceau", b.motif)
        _recharger(runtime, artist)
        bilan.spotify_sans_duree = sum(
            1 for t in artist.tracks if t.id in sans_duree and t.spotify_id
        )

    # 2b — nature des disques
    if options.nature_disques and options.deezer and not arret("nature des disques"):
        _nature_des_disques(runtime, artist, options, bilan, hits_albums)

    _recharger(runtime, artist)
    bilan.apres = compter_ids(artist.tracks, dm.get_albums_for_artist(artist.id))
    return bilan


def _artiste_spotify(runtime, artist, options: OptionsIdentite, hooks: Hooks) -> str:
    """Forcé > mémorisé > vote (`update_kworb._vote_artist_spotify_id`, Playwright
    SYNC : sur le fil appelant, jamais dans la boucle)."""
    dm = runtime.data_manager
    if options.spotify_id:
        dm.update_artist_spotify_id(artist.id, options.spotify_id)
        artist.spotify_id = options.spotify_id
        return f"forcée ({options.spotify_id})"
    if artist.spotify_id and not options.force:
        return f"mémorisée ({artist.spotify_id})"
    hooks.progress(0, 1, "Artiste", "Spotify")
    from src.utils.update_kworb import _vote_artist_spotify_id

    comment: dict = {}
    try:
        vote = _vote_artist_spotify_id(artist, dm, detail=comment)
    except Exception as e:  # noqa: BLE001 — une couche en panne n'arrête pas les suivantes
        logger.exception("Identité (artiste Spotify) : échec")
        return f"panne — {e}"
    if not vote:
        return "non résolue (vote infructueux)"
    if artist.spotify_id and artist.spotify_id != vote:
        # Forcer REDEMANDE, ne remplace pas : un désaccord se dit.
        return f"mémorisée ({artist.spotify_id}) — le vote rend {vote}, non appliqué"
    dm.update_artist_spotify_id(artist.id, vote)
    artist.spotify_id = vote
    if comment.get("methode") == "vote":
        return f"votée ({vote}, {comment['voix']}/{comment['total']} voix)"
    # Repli sur une recherche par NOM (voix insuffisantes) : homonymes possibles.
    return f"cherchée par NOM, faute de voix ({vote}) — à vérifier"


def _nature_des_disques(runtime, artist, options, bilan: BilanIdentite, hits_albums) -> None:
    from src.concurrency import async_loop
    from src.enrichment.album_types import fiches_du_catalogue, types_albums_deezer

    enricher, dm = runtime.data_enricher, runtime.data_manager
    ecarts = bilan.catalogue.ecarts if bilan.catalogue else None
    fiches, motifs = fiches_du_catalogue(
        getattr(ecarts, "albums", ()) or (),
        bilan.catalogue.deezer_id if bilan.catalogue else None,
        artist.tracks,
    )
    for t in artist.tracks:
        if t.id in hits_albums:
            t._deezer_album_id = hits_albums[t.id]
    try:
        deja = {a["title"]: a for a in dm.get_albums_for_artist(artist.id)}
        resultat = async_loop.run_sync(
            types_albums_deezer(
                enricher.deezer_client,
                enricher.http,
                artist.tracks,
                deja,
                lambda title, rt, did: dm.set_album_record_type(
                    artist.id, title, rt, deezer_album_id=did
                ),
                force=options.force,
                artist_name=artist.name,
                catalogue=fiches,
            )
        )
    except Exception as e:  # noqa: BLE001 — une couche en panne n'arrête pas les suivantes
        logger.exception("Nature des disques (Deezer) : échec")
        bilan.erreurs.append(f"Deezer (nature des disques) : {e}")
        bilan.complete = False
        return
    bilan.types_albums = resultat.renseignes
    bilan.albums_ignores = motifs + list(resultat.motifs)


def resume(bilan: BilanIdentite, artist: Artist) -> str:
    """Le compte rendu de fin — texte unique GUI/CLI."""
    lignes = [f"🪪 Identité — {artist.name}"]
    for source, etat in bilan.artistes.items():
        lignes.append(f"   {source} : {etat}")
    p = bilan.propositions
    if p is not None and (p.formations or p.alias or p.infos):
        lignes.append(
            f"👥 Proposés : {p.formations} formation(s), {p.alias} alias "
            f"({p.infos} pour info) — à arbitrer dans « Groupes »"
        )
    c = bilan.catalogue
    if c is not None and c.ecarts is not None and not c.ecarts.ambigu:
        e = c.ecarts
        if e.rattachements_auto:
            lignes.append(
                f"🔗 Deezer : {e.rattachements_auto} morceau(x) rattaché(s) à une parution"
            )
        if e.ecarts:
            lignes.append(f"🎧 Deezer : {len(e.ecarts)} écart(s) → bouton « À trancher »")
    if bilan.kworb_ids or bilan.kworb_editions:
        lignes.append(
            f"🔗 Kworb : {bilan.kworb_ids} ID Spotify posé(s), "
            f"{bilan.kworb_editions} édition(s) rattachée(s)"
        )
    if bilan.deezer_morceaux or bilan.spotify_morceaux:
        lignes.append(
            f"🔎 Par morceau : {bilan.deezer_morceaux} via Deezer, "
            f"{bilan.spotify_morceaux} via Spotify"
        )
    if bilan.spotify_sans_duree:
        lignes.append(
            f"⚠️ {bilan.spotify_sans_duree} ID Spotify jugé(s) sans durée indépendante "
            "(à revoir quand une durée arrivera)"
        )
    if bilan.types_albums:
        lignes.append(f"💿 Nature des disques : {bilan.types_albums} album(s) qualifié(s)")
    if bilan.avant and bilan.apres:
        diffs = [
            f"{k} {bilan.avant[k]} → {bilan.apres[k]}"
            for k in ("deezer_id", "isrc", "spotify_id", "duration", "record_type")
            if bilan.apres.get(k) != bilan.avant.get(k)
        ]
        lignes.append("📊 " + (" · ".join(diffs) if diffs else "aucune colonne d'identité changée"))
    if not bilan.complete:
        lignes.append(f"\n⚠️ Run INCOMPLET : {bilan.motif}")
    if bilan.erreurs:
        lignes.append(f"⚠️ Erreurs : {', '.join(bilan.erreurs[:8])}")
    return "\n".join(lignes)
