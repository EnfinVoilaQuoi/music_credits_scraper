"""Le cycle complet d'un artiste, étape par étape — sans widget.

add/load → disco (illimitée) → credits (crédits + paroles + timestamps) →
enrich → streams → certifs (recherche par artiste puis application). Chaque
étape tourne sous SON `run_scope` (l'usage des sources est compté POUR cet
artiste), continue sur l'échec d'une étape (consigné) et s'arrête sur
`should_stop`. `complete` ne vaut que si TOUTES les étapes le sont.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from src.models import Artist
from src.observability import source_usage
from src.observability.registry import Flow
from src.services import artiste, certifs, credits, discographie, enrichissement, streams
from src.services.runtime import Bilan, Hooks, Manque, Runtime, selection_morceaux
from src.utils.logger import get_logger

logger = get_logger(__name__)

ETAPES: tuple[str, ...] = ("disco", "credits", "enrich", "streams", "certifs")
_FLOWS = {
    "disco": Flow.DISCO,
    "credits": Flow.ENRICHMENT,
    "enrich": Flow.ENRICHMENT,
    "streams": Flow.STREAMS,
    "certifs": Flow.CERTS,
}


@dataclass(frozen=True)
class OptionsCycle:
    genius_id: int | None = None
    only: tuple[str, ...] = ()
    skip: tuple[str, ...] = ()
    #: Ne traiter que les morceaux à donnée ABSENTE (définition PAR flux).
    manquants: bool = False
    disco: discographie.OptionsDisco = discographie.OptionsDisco()
    credits: credits.OptionsCredits = credits.OptionsCredits()
    enrich: enrichissement.OptionsEnrich = enrichissement.OptionsEnrich()
    streams: streams.OptionsStreams = streams.OptionsStreams()
    certifs_sources: tuple[str, ...] = certifs.SOURCES_PAR_ARTISTE

    def etapes(self) -> list[str]:
        inconnues = (set(self.only) | set(self.skip)) - set(ETAPES)
        if inconnues:
            raise ValueError(f"étape(s) inconnue(s) : {sorted(inconnues)}")
        retenues = [e for e in ETAPES if not self.only or e in self.only]
        return [e for e in retenues if e not in self.skip]


@dataclass
class BilanCycle(Bilan):
    artist_name: str = ""
    etapes: dict[str, Bilan] = field(default_factory=dict)
    #: Étapes non lancées (arrêt demandé avant elles).
    non_lancees: list[str] = field(default_factory=list)


def manques_credits(o: credits.OptionsCredits) -> list[Manque]:
    kinds = []
    if o.genius:
        kinds.append(Manque.CREDITS_GENIUS)
    if o.discogs:
        kinds.append(Manque.CREDITS_DISCOGS)
    if o.paroles:
        kinds.append(Manque.PAROLES)
    if o.sync:
        kinds.append(Manque.TIMESTAMPS)
    return kinds


def _recharger(runtime: Runtime, artist: Artist) -> None:
    """Après une étape qui écrit, relire la discographie RÉUNIE : les services
    travaillent sur des `Track` chargés, jamais reconstruits."""
    artist.tracks = runtime.data_manager.discographie_reunie(artist)


def executer_etape(
    runtime: Runtime, artist: Artist, etape: str, options: OptionsCycle, hooks: Hooks
) -> Bilan:
    """UNE étape, sous son scope d'observabilité. C'est aussi ce qu'appelle
    chaque sous-commande de la CLI."""
    with source_usage.run_scope(_FLOWS[etape], artist_id=artist.id, artist_name=artist.name):
        if etape == "disco":
            return discographie.run(runtime, artist, options.disco, hooks)
        if etape == "credits":
            kinds = manques_credits(options.credits) if options.manquants else ()
            tracks = selection_morceaux(runtime, artist, manquants=kinds)
            return credits.run(runtime, artist, tracks, options.credits, hooks)
        if etape == "enrich":
            kinds = (Manque.AUDIO,) if options.manquants else ()
            tracks = selection_morceaux(runtime, artist, manquants=kinds)
            return enrichissement.run(runtime, artist, tracks, options.enrich, hooks)
        if etape == "streams":
            ids = options.streams.track_ids
            if options.manquants:
                voulus = {
                    t.id
                    for t in selection_morceaux(runtime, artist, manquants=(Manque.STREAMS,))
                    if t.id is not None
                }
                ids = frozenset(voulus if ids is None else voulus & set(ids))
            opts = streams.OptionsStreams(
                kworb=options.streams.kworb,
                spotify_web=options.streams.spotify_web,
                spotify_full=options.streams.spotify_full,
                ytm=options.streams.ytm,
                ytm_channel=options.streams.ytm_channel,
                track_ids=streams.track_ids_actifs(runtime, artist, ids),
            )
            return streams.run(runtime, artist, opts, hooks)
        if etape == "certifs":
            noms = certifs.noms_de_recherche_pour(runtime, artist)
            bilan = certifs.rechercher_artiste(
                noms,
                sources=options.certifs_sources,
                progres=lambda msg: hooks.progress(0, 0, msg, "Certifs"),
            )
            _recharger(runtime, artist)
            appli = certifs.appliquer(runtime, artist)
            bilan.certifies = appli.certifies
            bilan.erreurs.extend(appli.erreurs)
            bilan.rapport += f"\n\n{appli.rapport}"
            return bilan
        raise ValueError(etape)


def run(runtime: Runtime, nom: str, options: OptionsCycle, hooks: Hooks) -> BilanCycle:
    artist = artiste.charger_ou_ajouter(runtime, nom, genius_id=options.genius_id)
    bilan = BilanCycle(artist_name=artist.name)
    etapes = options.etapes()
    for i, etape in enumerate(etapes):
        if hooks.should_stop():
            bilan.non_lancees = etapes[i:]
            bilan.interrompu(f"arrêt demandé avant {etape}")
            break
        logger.info(f"═══ Cycle {artist.name} — étape {i + 1}/{len(etapes)} : {etape}")
        try:
            b = executer_etape(runtime, artist, etape, options, hooks)
        except Exception as e:
            # Boucle résiliente : une étape qui plante ne prive pas des suivantes,
            # mais le cycle n'est PAS complet. Trace complète.
            logger.exception(f"Cycle — étape {etape} en échec")
            b = Bilan(complete=False, motif=f"exception : {e}")
        bilan.etapes[etape] = b
        if etape != "certifs":
            _recharger(runtime, artist)
    if any(not b.complete for b in bilan.etapes.values()) and bilan.complete:
        rates = [e for e, b in bilan.etapes.items() if not b.complete]
        bilan.interrompu(f"étape(s) incomplète(s) : {', '.join(rates)}")
    return bilan


def resume(bilan: BilanCycle, options: OptionsCycle) -> str:
    lignes = [f"Cycle — {bilan.artist_name}", ""]
    for etape, b in bilan.etapes.items():
        etat = "✅" if b.complete else f"⚠️ {b.motif}"
        lignes.append(f"{etape:<8} {etat}")
    for etape in bilan.non_lancees:
        lignes.append(f"{etape:<8} ⏹️ non lancée")
    lignes.append("")
    lignes.append("COMPLET" if bilan.complete else f"INCOMPLET — {bilan.motif}")
    return "\n".join(lignes)


def resume_etape(etape: str, b: Bilan, options: OptionsCycle, artist: Artist) -> str:
    """Le compte rendu détaillé d'UNE étape, celui de son service."""
    if etape == "disco":
        return discographie.resume(b, artist)
    if etape == "credits":
        return credits.resume(b, options.credits)
    if etape == "enrich":
        return enrichissement.resume(b, options.enrich)
    if etape == "streams":
        return streams.resume(b, options.streams)
    if etape == "certifs":
        return b.rapport or "\n".join(b.lignes)
    return str(b)


def etapes_disponibles() -> Iterable[str]:
    return ETAPES
