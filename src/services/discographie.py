"""Récupération de la discographie d'un artiste (API Genius) — sans widget.

Extrait du worker `src/gui/workers/retrieval.py` (2026-09-14) : la dédup à trois
niveaux, la préservation des données enrichies à la fusion, l'historique des
suppressions, le rematch des certifs, les images et la boucle de sauvegarde
vivaient dans une closure GUI. Ici, `fusionner()` est PURE (testable sans base)
et `run()` orchestre ; la GUI et la CLI l'appellent toutes deux.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.models import Artist, Track
from src.services.runtime import Bilan, Hooks, Runtime
from src.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class OptionsDisco:
    """Les cases du dialogue « Discographie ». `max_songs=None` = illimité :
    un plafond ampute la discographie, il ne sert qu'au debug."""

    max_songs: int | None = None
    include_features: bool = True
    prefill: bool = True
    update_only: bool = False
    include_secondary: bool = False
    respect_deleted: bool = True
    download_images: bool = True


@dataclass
class BilanDisco(Bilan):
    recuperes: int = 0
    nouveaux: int = 0
    mis_a_jour: int = 0
    doublons_evites: int = 0
    sauves: int = 0
    supprimes_ignores: int = 0
    albums_api: int = 0
    dates_api: int = 0
    images: int = 0
    featurings_total: int = 0
    total_en_base: int = 0
    #: Certifs/relations recalculées mais NON enregistrées (contrôle de fin de flux).
    oublies: list[str] = field(default_factory=list)


@dataclass
class Fusion:
    nouveaux: int = 0
    mis_a_jour: int = 0
    doublons_evites: int = 0


def _gid_int(t: Track) -> int | None:
    try:
        return int(t.genius_id) if t.genius_id else None
    except (TypeError, ValueError):
        return None


def known_genius_ids_pour_maj(tracks: list[Track]) -> set:
    """Mode MàJ : titres dont les données media sont DÉJÀ complètes, exclus du
    prefill API. Un lien YouTube 'search_auto' (recherche persistée) ne compte
    PAS comme complet : l'appel Genius doit pouvoir le remplacer par le lien
    officiel (genius_media). Cf. JOURNAL 2026-07-02."""
    return {
        t.genius_id
        for t in tracks
        if t.genius_id
        and (t.album or t.album_override)
        and t.spotify_id
        and t.youtube_url
        and t.youtube_url_source != "search_auto"
    }


def fusionner(
    nouveaux: list[Track], existants: list[Track], *, should_stop=lambda: False
) -> Fusion:
    """Rapproche chaque morceau fraîchement récupéré d'un morceau en base et
    lui transfère ce que l'API Genius ne fournit pas.

    Trois niveaux, du plus sûr au plus lâche : `genius_id` → (titre, album) →
    titre seul (doublons de casse : on prend le candidat le plus COMPLET).
    Quand un existant est trouvé : bpm / tonalité / paroles / crédits / certifs
    sont PRÉSERVÉS si le nouveau ne les porte pas, et `track.id` est repris —
    c'est lui qui fait de `save_track` un UPDATE. Mute `nouveaux`, pure sinon.
    """
    par_gid: dict = {}
    par_titre_album: dict = {}
    par_titre: dict[str, list[Track]] = {}
    for t in existants:
        if t.genius_id:
            par_gid[t.genius_id] = t
        par_titre_album[(t.title.lower().strip(), (t.album or "").lower().strip())] = t
        par_titre.setdefault(t.title.lower().strip(), []).append(t)

    fusion = Fusion()
    for track in nouveaux:
        if should_stop():
            logger.info("⏹️ Arrêt demandé — dédup interrompue")
            break
        existant = None
        if track.genius_id and track.genius_id in par_gid:
            existant = par_gid[track.genius_id]
        else:
            key = (track.title.lower().strip(), (track.album or "").lower().strip())
            existant = par_titre_album.get(key)
        if existant is None:
            candidats = par_titre.get(track.title.lower().strip())
            if candidats:
                if len(candidats) > 1:
                    logger.warning(
                        f"⚠️ Doublon de casse détecté pour '{track.title}': "
                        f"{len(candidats)} versions"
                    )
                    fusion.doublons_evites += 1
                existant = max(
                    candidats,
                    key=lambda t: (
                        bool(t.album),
                        bool(t.audio.bpm),
                        bool(t.lyrics.text),
                        len(t.credits),
                    ),
                )
        if existant is None:
            fusion.nouveaux += 1
            continue
        fusion.mis_a_jour += 1
        # L'API Genius ne fournit ni BPM, ni tonalité, ni paroles, ni certifs.
        if not track.audio.bpm and existant.audio.bpm:
            track.audio.bpm = existant.audio.bpm
        if not track.audio.musical_key and existant.audio.musical_key:
            track.audio.musical_key = existant.audio.musical_key
        if not track.lyrics.text and existant.lyrics.text:
            track.lyrics.text = existant.lyrics.text
            track.lyrics.present = existant.lyrics.present
        if track.lyrics.instrumental is None:
            track.lyrics.instrumental = existant.lyrics.instrumental
        if not track.credits and existant.credits:
            track.credits = existant.credits
        if not track.certs.entries and existant.certs.entries:
            track.certs.entries = existant.certs.entries
        track.id = existant.id
    return fusion


def _filtrer_supprimes(
    runtime: Runtime, artist: Artist, nouveaux: list[Track], respect_deleted: bool
) -> tuple[list[Track], int]:
    """Historique des suppressions : ne pas réajouter ce que l'utilisateur a
    retiré — ou, s'il l'a explicitement réautorisé, purger l'historique."""
    deleted_ids = runtime.deleted.load_deleted_ids(artist.name)
    if not deleted_ids:
        return nouveaux, 0
    if respect_deleted:
        gardes = [t for t in nouveaux if _gid_int(t) not in deleted_ids]
        return gardes, len(nouveaux) - len(gardes)
    for t in nouveaux:
        gid = _gid_int(t)
        if gid in deleted_ids:
            runtime.deleted.remove_deleted(artist.name, gid)
    return nouveaux, 0


def run(runtime: Runtime, artist: Artist, options: OptionsDisco, hooks: Hooks) -> BilanDisco:
    """Récupère, fusionne, rematch les certifs, télécharge les images, sauve.

    À appeler sous `run_scope(Flow.DISCO, …)` et HORS de la boucle asyncio
    (`get_artist_songs` est sync, `apply_images` aussi). `artist.tracks` est
    RECHARGÉ en fin de run (morceaux propres, `get_artist_tracks`)."""
    from src.utils.database_backup import get_backup_manager

    bilan = BilanDisco()
    dm = runtime.data_manager

    backup_path = get_backup_manager().create_backup("before_fetch_tracks")
    if backup_path:
        logger.info(f"💾 Backup créé: {backup_path.name}")
    logger.info(
        f"Début récupération: max_songs={options.max_songs}, "
        f"include_features={options.include_features}"
    )
    existants = list(artist.tracks or [])
    logger.info(f"📦 {len(existants)} morceaux déjà en base avant récupération")

    known_genius_ids = None
    if options.update_only and existants:
        known_genius_ids = known_genius_ids_pour_maj(existants)
        n_retry = sum(1 for t in existants if t.genius_id) - len(known_genius_ids)
        logger.info(
            f"🔄 MàJ : {len(known_genius_ids)} titres complets exclus du prefill API, "
            f"{n_retry} connus mais incomplets (album/Spotify/YouTube) à re-tenter"
        )

    nouveaux = runtime.genius_api.get_artist_songs(
        artist,
        max_songs=options.max_songs,
        include_features=options.include_features,
        prefill=options.prefill,
        known_genius_ids=known_genius_ids,
        include_secondary=options.include_secondary,
    )
    if not nouveaux:
        bilan.interrompu("aucun morceau trouvé")
        logger.warning("Aucun morceau trouvé")
        return bilan

    nouveaux, bilan.supprimes_ignores = _filtrer_supprimes(
        runtime, artist, nouveaux, options.respect_deleted
    )
    if bilan.supprimes_ignores:
        logger.info(f"🗂️ {bilan.supprimes_ignores} morceau(x) supprimé(s) ignoré(s) (historique)")
    bilan.recuperes = len(nouveaux)

    fusion = fusionner(nouveaux, existants, should_stop=hooks.should_stop)
    bilan.nouveaux, bilan.mis_a_jour, bilan.doublons_evites = (
        fusion.nouveaux,
        fusion.mis_a_jour,
        fusion.doublons_evites,
    )
    if hooks.should_stop():
        bilan.interrompu("arrêt demandé pendant la dédup")
        return bilan

    # E7h : rematch des certifications depuis les CSV clean AVANT le save.
    # Autorité = les CSV clean ; écrase la préservation ci-dessus (certifs =
    # données dérivées, pas saisies). Défensif : n'interrompt pas la récup.
    try:
        from src.utils.cert_matcher import get_cert_matcher
        from src.utils.certification_enricher import apply_certifications

        apply_certifications(artist, nouveaux, get_cert_matcher())
    except Exception:
        logger.exception("Rematch certifications échoué")
        bilan.erreurs.append("rematch certifications")

    # Media 6 : images AVANT la boucle save (apply_images MUTE les tracks et
    # l'artiste, que le save persiste ensuite). Idempotent, should_stop respecté.
    if options.download_images:
        try:
            from src.api.deezer_api import DeezerAPI
            from src.utils.media_enricher import apply_images

            rapport = apply_images(
                artist,
                nouveaux,
                deezer=DeezerAPI(),
                genius=runtime.genius_api,
                should_stop=hooks.should_stop,
            )
            if artist.image_path:
                dm.set_artist_image_path(artist.id, artist.image_path)
            bilan.images = rapport.total_downloaded()
            logger.info(f"🖼️ Images: {bilan.images} téléchargée(s)")
        except Exception:
            logger.exception("Téléchargement des images échoué")
            bilan.erreurs.append("images")

    total = len(nouveaux)
    for i, track in enumerate(nouveaux, 1):
        if hooks.should_stop():
            logger.info("⏹️ Arrêt demandé — sauvegarde interrompue entre deux morceaux")
            bilan.interrompu(f"arrêt demandé : {bilan.sauves}/{total} sauvés")
            break
        try:
            dm.save_track(track)
            # APRÈS le save : c'est lui qui attribue l'id d'un morceau neuf.
            # Écrit les certifs/relations, que save_track ne touche plus.
            dm.record_pending(track)
            bilan.sauves += 1
        except Exception:
            logger.exception(f"Erreur sauvegarde {track.title}")
            bilan.erreurs.append(f"save {track.title}")
        hooks.progress(i, total, track.title, "Sauvegarde")

    bilan.oublies = dm.certifications_non_enregistrees(nouveaux)
    if bilan.oublies:
        logger.error(
            f"Certifs/relations recalculées mais NON enregistrées "
            f"({len(bilan.oublies)}): {', '.join(bilan.oublies[:8])}"
        )

    artist.tracks = dm.get_artist_tracks(artist.id)
    bilan.total_en_base = len(artist.tracks)
    bilan.featurings_total = sum(1 for t in artist.tracks if t.is_featuring)
    bilan.albums_api = sum(1 for t in nouveaux if t.album)
    bilan.dates_api = sum(1 for t in nouveaux if t.release_date)
    logger.info(
        f"✅ Merge terminé : {bilan.nouveaux} nouveaux, {bilan.mis_a_jour} mis à jour, "
        f"{bilan.sauves} sauvegardés, {bilan.doublons_evites} doublons évités"
    )
    return bilan


def resume(bilan: BilanDisco, artist: Artist) -> str:
    """Le compte rendu de fin — texte unique pour la fenêtre GUI et la CLI."""
    if bilan.recuperes == 0 and bilan.motif:
        return (
            "Aucun morceau trouvé.\n\n"
            "Vérifiez le nom de l'artiste ou essayez avec les features activées."
        )
    msg = f"✅ {bilan.recuperes} morceaux récupérés pour {artist.name}"
    msg += f"\n🆕 {bilan.nouveaux} nouveaux morceaux"
    msg += f"\n🔄 {bilan.mis_a_jour} morceaux mis à jour"
    if bilan.doublons_evites:
        msg += f"\n🚫 {bilan.doublons_evites} doublons évités"
    if bilan.supprimes_ignores:
        msg += f"\n🗂️ {bilan.supprimes_ignores} morceaux supprimés ignorés (historique)"
    if bilan.featurings_total:
        msg += f"\n🎤 {bilan.featurings_total} morceaux en featuring (total)"
    msg += f"\n💿 {bilan.albums_api} albums récupérés via l'API"
    msg += f"\n📅 {bilan.dates_api} dates de sortie récupérées via l'API"
    msg += f"\n💾 {bilan.sauves} morceaux sauvegardés en base"
    msg += f"\n📊 Total en base : {bilan.total_en_base} morceaux"
    if not bilan.complete:
        msg += f"\n\n⚠️ Run INCOMPLET : {bilan.motif}"
    if bilan.erreurs:
        msg += f"\n⚠️ Erreurs : {', '.join(bilan.erreurs[:8])}"
    return msg
