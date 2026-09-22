"""Streams Spotify (Kworb / pages web) et YouTube Music (+ vues vidéos) — sans widget.

Extrait du worker `src/gui/workers/streams.py` (2026-09-14). Le provider
(`StreamsProvider`) possède les clients ; ici la boucle par source, l'épinglage
du canal YTM, le résumé et le hook de confirmation Kworb (GUI : dialog ;
headless : listé, jamais appliqué).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from src.config import settings
from src.enrichment.providers.streams import StreamsProvider
from src.models import Artist
from src.services.runtime import Bilan, Hooks, Runtime
from src.utils.logger import get_logger

logger = get_logger(__name__)


#: Les deux sources du champ `spotify_streams`. L'ordre n'a **aucun effet sur la
#: donnée** : chacune déclare ce qu'elle a vu, et `reconcile_spotify_streams`
#: (appliqué par le repository) désigne la valeur retenue selon
#: `settings.streams_master`. On garde la maître en tête par simple courtoisie
#: d'affichage — sa progression s'affiche en premier.
_STREAM_SOURCES = ("kworb", "spotify_web")


def _stream_sources() -> tuple[str, ...]:
    master = settings.streams_master
    return (master, *(s for s in _STREAM_SOURCES if s != master))


@dataclass(frozen=True)
class OptionsStreams:
    """Les cases du dialogue « Nb Streams ». Trois sources Spotify de coûts TRÈS
    différents : Kworb (une page), la page artiste Spotify (auditeurs mensuels +
    top 10) et le crawl complet (une page par morceau). `track_ids` restreint le
    volet YouTube (streams YTM et vues) ; il ne touche PAS aux sources Spotify —
    Kworb rend une page entière quoi qu'on demande."""

    kworb: bool = True
    spotify_web: bool = True
    spotify_full: bool = False
    ytm: bool = True
    ytm_channel: str = ""
    track_ids: frozenset[int] | None = None

    @property
    def spotify_web_effectif(self) -> bool:
        # Le crawl complet commence par la page artiste de toute façon.
        return self.spotify_web or self.spotify_full


@dataclass
class BilanStreams(Bilan):
    #: Les dicts rendus par les providers, clés `spotify` / `spotify_web` / `ytm`
    #: / `video_views` — c'est ce que `resume()` lit.
    results: dict = field(default_factory=dict)


def _epingler_canal_ytm(runtime: Runtime, artist: Artist, brut: str) -> None:
    """Canal YTM saisi (@handle, lien ou UC...) → épinglé comme MANUEL."""
    from src.api.ytmusic_api import YTMusicAPI

    resolved = YTMusicAPI().resolve_channel(brut)
    if resolved:
        runtime.data_manager.set_artist_ytm_channel(artist.id, resolved, source="manual")
    else:
        logger.warning(f"Canal YTM non résolu: {brut!r} — recherche automatique utilisée")


def run(
    runtime: Runtime,
    artist: Artist,
    options: OptionsStreams,
    hooks: Hooks,
    provider: StreamsProvider | None = None,
) -> BilanStreams:
    """À appeler HORS de la boucle (le crawl Spotify passe par `run_sync`),
    sous `run_scope(Flow.STREAMS)`. Le provider est créé ici et FERMÉ ici."""
    bilan = BilanStreams()
    dm = runtime.data_manager
    provider = provider or StreamsProvider()
    results = bilan.results
    try:
        demandees = {"kworb": options.kworb, "spotify_web": options.spotify_web_effectif}
        if any(demandees.values()):
            for source in _stream_sources():
                if not demandees[source]:
                    continue
                if hooks.should_stop():
                    bilan.interrompu(f"arrêt demandé avant {source}")
                    break
                label = "Spotify (Kworb)" if source == "kworb" else "Spotify (pages web)"
                hooks.progress(0, 0, artist.name, label)
                cle = "spotify" if source == "kworb" else "spotify_web"
                try:
                    if source == "kworb":
                        results[cle] = provider.fetch_spotify(artist, dm)
                    else:
                        results[cle] = provider.fetch_spotify_web(
                            artist,
                            dm,
                            stop_requested=hooks.should_stop,
                            full_crawl=options.spotify_full,
                        )
                except Exception as e:
                    logger.exception(f"Source streams {source} en échec")
                    results[cle] = {"error": str(e)}

        if options.ytm and bilan.complete:
            if hooks.should_stop():
                bilan.interrompu("arrêt demandé avant YouTube Music")
            else:
                hooks.progress(0, 0, artist.name, "YouTube Music")
                try:
                    if options.ytm_channel:
                        _epingler_canal_ytm(runtime, artist, options.ytm_channel)
                    ids = set(options.track_ids) if options.track_ids is not None else None
                    results["ytm"] = provider.fetch_ytm(artist, dm, track_ids=ids)
                    # Media 5 : vues + nature (clip/show/audio) des vidéos — batch
                    # YT mutualisé avec les streams. SÉPARÉ de ytm_streams.
                    # Défensif : n'interrompt pas la récupération des streams.
                    if not hooks.should_stop():
                        try:
                            fresh = dm.get_artist_tracks(artist.id)
                            results["video_views"] = provider.fetch_video_views(
                                artist, fresh, dm, track_ids=ids
                            )
                        except Exception:
                            logger.exception("Vues clips échouées")
                            bilan.erreurs.append("vues vidéos")
                except Exception as e:
                    logger.exception("YouTube Music en échec")
                    results["ytm"] = {"error": str(e)}

        # Rapprochements INCERTAINS Kworb (ex. « Matrix » ≈ « Matrix (Intro) ») :
        # à faire confirmer par un humain — le hook décide comment.
        spotify = results.get("spotify") or {}
        suggestions = spotify.get("suggestions") or []
        if suggestions:
            hooks.confirmer_kworb(suggestions, spotify.get("kworb_updated"))
    finally:
        provider.close()
    return bilan


def track_ids_actifs(
    runtime: Runtime, artist: Artist, ids: Iterable[int] | None
) -> frozenset[int] | None:
    """Restreint des identifiants aux morceaux NON désactivés. `None` (pas de
    restriction) devient l'ensemble des actifs seulement s'il y a des
    désactivés — sinon reste `None` (aucun filtre côté updaters)."""
    desactives = runtime.disabled.load_disabled_tracks(artist.name)
    if ids is not None:
        return frozenset(i for i in ids if i not in desactives)
    if not desactives:
        return None
    return frozenset(t.id for t in artist.tracks if t.id is not None and t.id not in desactives)


def resume(bilan: BilanStreams, options: OptionsStreams) -> str:
    """Résumé affiché après une passe « Nb Streams ».

    Pure : ne lit que le dict `results` rempli par les providers. Extraite de
    la closure du worker le 2026-09-05 — elle porte une dizaine de décisions
    (échec, abandon du gate d'identité, troncature des listes à 8 entrées,
    verdicts du canal YTM) que rien ne vérifiait.
    """
    texte = build_summary(bilan.results, spotify_full_crawl=options.spotify_full)
    if not bilan.complete:
        texte += f"\n\n⚠️ Run INCOMPLET : {bilan.motif}"
    if bilan.erreurs:
        texte += f"\n⚠️ Erreurs : {', '.join(bilan.erreurs)}"
    return texte


def build_summary(results: dict, *, spotify_full_crawl: bool) -> str:
    lines = ["Récupération terminée !\n"]
    if "spotify" in results:
        r = results["spotify"]
        if "error" in r:
            lines.append(f"Spotify : ❌ {r['error']}")
        else:
            lines.append(
                f"Spotify : {r.get('matched', 0)} matchés, "
                f"{r.get('unmatched', 0)} non matchés, "
                f"{r.get('albums_updated', 0)} albums"
            )
            # Rapprochements FLOUS (coquilles/ponctuation) à vérifier :
            # le stream est écrit mais le match n'est pas exact.
            fuzzy = r.get("fuzzy_matched") or []
            if fuzzy:
                lines.append("\n≈ Rapprochés (vérifie que c'est le bon morceau) :")
                for kw, db, score in fuzzy[:8]:
                    lines.append(f"   • Kworb « {kw} » → base « {db} » ({score:.0%})")
                if len(fuzzy) > 8:
                    lines.append(f"   … et {len(fuzzy) - 8} autre(s) (voir logs)")
            # Renditions (Live, Bonus Track, Unplugged…) rattachées à leur
            # morceau souche — hors total, visibles dans l'onglet Streams.
            renditions = r.get("renditions_rattachees") or []
            if renditions:
                total = sum(st for _, _, st in renditions)
                lines.append(
                    f"\n🎚️ {len(renditions)} version(s) alternative(s) rattachée(s) "
                    f"({total:,} streams, hors totaux) :".replace(",", " ")
                )
                for kw, parent, streams in renditions[:8]:
                    lines.append(f"   • « {kw} » → {parent} — {streams:,}".replace(",", " "))
                if len(renditions) > 8:
                    lines.append(f"   … et {len(renditions) - 8} autre(s) (voir logs)")
            # Un ID Spotify sur DEUX lignes de l'artiste : rien n'est écrit pour
            # lui, c'est à fusionner (ou à départager) à la main.
            partages = r.get("ids_partages") or []
            if partages:
                lines.append(
                    "\n⚠️ ID Spotify porté par plusieurs morceaux (rien écrit, à départager) :"
                )
                for sid, titres in partages[:8]:
                    lines.append(f"   • {sid} : {' | '.join(titres)}")
            doublons = r.get("doublons_evidents") or []
            if doublons:
                lines.append(
                    f"🔁 {len(doublons)} doublon(s) évident(s) de fiche (même titre ou même "
                    "prise) : streams écrits sur la première, à fusionner — "
                    + " ; ".join(" | ".join(titres) for _, titres in doublons[:6])
                )
            # L'ID en base désigne une AUTRE version que le titre de la ligne :
            # signature d'un ID mal attribué, à passer par « Vérifier les
            # identifiants Spotify ».
            suspectes = r.get("variantes_suspectes") or []
            if suspectes:
                lines.append("\n🔀 ID dont le titre Spotify est une autre version (à vérifier) :")
                for base, spotify, _sid in suspectes[:8]:
                    lines.append(f"   • base « {base} » ↔ Spotify « {spotify} »")
                if len(suspectes) > 8:
                    lines.append(f"   … et {len(suspectes) - 8} autre(s) (voir logs)")
            ecartees = r.get("lignes_ecartees") or []
            if ecartees:
                lines.append("\n⤫ Lignes écartées (autre enregistrement, même titre) :")
                for kw, streams, base in ecartees[:8]:
                    lines.append(f"   • « {kw} » — {streams:,} ≠ « {base} »".replace(",", " "))
            # Morceaux présents sur Kworb mais introuvables en base : soit un
            # raté de matching, soit un titre absent de la discographie.
            details = r.get("unmatched_details") or []
            if details:
                lines.append("\n⚠️ Sur Kworb mais pas reliés en base :")
                for title, streams in details[:8]:
                    lines.append(f"   • {title} — {streams:,} streams".replace(",", " "))
                if len(details) > 8:
                    lines.append(f"   … et {len(details) - 8} autre(s) (voir logs)")
    if "spotify_web" in results:
        r = results["spotify_web"]
        if "error" in r:
            lines.append(f"Spotify (pages web) : ❌ {r['error']}")
        elif r.get("aborted"):
            # Gate d'identité : rien n'a été écrit, et c'est voulu.
            lines.append(f"🚨 Spotify (pages web) : {r['aborted']} — aucune écriture.")
        else:
            lines.append(
                f"Spotify (pages web) : {r.get('recorded', 0)} morceau(x) relevé(s), "
                f"{r.get('albums_totalises', 0)} album(s) totalisé(s), "
                f"{r.get('pages', 0)} page(s) ouverte(s)"
            )
            if not spotify_full_crawl:
                lines.append(
                    "   • page artiste seule — coche « tous les morceaux » "
                    "pour les streams par titre et les totaux d'album"
                )
            if r.get("monthly_listeners") is not None:
                lines.append(
                    f"   • Auditeurs mensuels : {r['monthly_listeners']:,}".replace(",", " ")
                )
            if r.get("harvested_foreign"):
                lines.append(
                    f"   • {r['harvested_foreign']} compteur(s) récolté(s) au passage "
                    "pour d'autres artistes de la base"
                )
    if "ytm" in results:
        r = results["ytm"]
        if "error" in r:
            lines.append(f"YouTube Music : ❌ {r['error']}")
        else:
            lines.append(
                f"YouTube Music : {r.get('matched', 0)} matchés, "
                f"{r.get('unmatched', 0)} non matchés, "
                f"{r.get('albums_processed', 0)} albums"
            )
            # Verdict du gate d'identité (canal homonyme / divergent).
            identity = r.get("identity") or {}
            status = identity.get("status")
            matched = identity.get("matched", 0)
            ytm_titles = identity.get("ytm_titles", 0)
            if status == "aborted":
                lines.append(
                    f"🚨 Canal YTM suspect ({matched}/{ytm_titles} titres) — "
                    "rien n'a été écrit. Renseigne le champ « Canal YTM » "
                    "(@handle) dans cette fenêtre et relance."
                )
            elif status == "warning":
                lines.append(
                    f"⚠️ Canal YTM manuel divergent ({matched}/{ytm_titles} titres) "
                    "— écriture maintenue (saisie manuelle prioritaire)."
                )
            # Vidéos rattachées à plusieurs morceaux : écartées de la somme,
            # parce qu'attribuer les mêmes vues à chacun les multiplierait.
            evidents = r.get("partages_evidents") or 0
            if evidents:
                lines.append(
                    f"\n🎬 {evidents} vidéo(s) partagée(s) à l'évidence (clip double nommant "
                    "chaque morceau, ou doublon de fiche) : non comptées, rien à vérifier."
                )
            partagees = r.get("videos_partagees") or []
            if partagees:
                non_attribuees = r.get("vues_non_attribuees") or 0
                lines.append(
                    f"\n🔗 {len(partagees)} vidéo(s) rattachée(s) à plusieurs morceaux — "
                    f"non comptées ({non_attribuees:,} vues laissées de côté) :".replace(",", " ")
                )
                for v in partagees[:8]:
                    nom = v.get("titre_video") or v["url"]
                    lines.append(f"   • « {nom} » → {', '.join(v['morceaux'])}")
                if len(partagees) > 8:
                    lines.append(f"   … et {len(partagees) - 8} autre(s) (voir logs)")
                lines.append(
                    "   Clip double = normal, on le laisse. Sinon, ✖️ Rejeter le "
                    "mauvais lien dans la fiche : la vidéo recomptera pour l'autre."
                )
    if "video_views" in results:
        v = results["video_views"]
        by_kind = v.get("by_kind") or {}
        kinds = ", ".join(f"{k}: {n}" for k, n in sorted(by_kind.items())) or "—"
        # `by_kind` compte des VIDÉOS, `updated` des MORCEAUX : un morceau porte
        # souvent son clip ET son audio, les deux nombres ne coïncident pas.
        lines.append(
            f"Vues vidéos : {v.get('updated', 0)} morceau(x), "
            f"{v.get('videos', 0)} vidéo(s) ({kinds})"
        )
    return "\n".join(lines)
