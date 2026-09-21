"""Pilotage de l'application SANS GUI — `python -m src.cli <commande> …`.

Chaque sous-commande expose en drapeaux les cases du dialogue GUI correspondant
et appelle le MÊME service (`src/services/*`) que la fenêtre : un comportement
n'existe qu'une fois. `python src/main.py` reste la GUI.

Codes de sortie : 0 complet · 1 erreur · 2 partiel (arrêt demandé, étape en
échec) · 3 artiste ambigu (candidats listés, trancher avec --genius-id).

Ctrl-C lève le drapeau d'arrêt partagé (`lifecycle.request_stop`) : les boucles
s'arrêtent ENTRE deux morceaux, jamais au milieu d'une écriture ; un second
Ctrl-C sort brutalement.
"""

from __future__ import annotations

import argparse
import signal
import sys

# JAMAIS en re-wrappant (un second wrapper ferme le buffer partagé au GC) ;
# garde pytest : le stdout de capture ne se reconfigure pas.
if hasattr(sys.stdout, "reconfigure") and "pytest" not in sys.modules:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.concurrency import lifecycle  # noqa: E402
from src.observability import repository as usage_repository  # noqa: E402
from src.services import (  # noqa: E402
    artiste,
    certifs,
    credits,
    cycle,
    discographie,
    enrichissement,
    streams,
)
from src.services.runtime import Hooks, Runtime  # noqa: E402
from src.utils.logger import get_logger  # noqa: E402

logger = get_logger(__name__)

COMPLET, ERREUR, PARTIEL, AMBIGU = 0, 1, 2, 3


# ── Construction du parseur ──────────────────────────────────────────────────
def _bool_flags(p: argparse.ArgumentParser, nom: str, defaut: bool, aide: str) -> None:
    """`--x` / `--no-x` pour une case à cocher, défaut = celui de la GUI."""
    g = p.add_mutually_exclusive_group()
    g.add_argument(f"--{nom}", dest=nom.replace("-", "_"), action="store_true", help=aide)
    g.add_argument(f"--no-{nom}", dest=nom.replace("-", "_"), action="store_false")
    p.set_defaults(**{nom.replace("-", "_"): defaut})


def _artiste_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("nom", help="nom de l'artiste (tel qu'en base ou sur Genius)")
    p.add_argument(
        "--genius-id",
        type=int,
        help="ID Genius quand le slug ne suffit pas (la CLI ne choisit jamais un candidat)",
    )


def _manquants_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--manquants",
        action="store_true",
        help="ne traiter que les morceaux dont la donnée de CE flux est absente",
    )


def _disco_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--max-songs", type=int, help="plafond DUR (debug) ; défaut = illimité")
    _bool_flags(p, "features", True, "inclure les featurings")
    _bool_flags(p, "prefill", True, "appel API détail (album, Spotify ID, YouTube)")
    _bool_flags(p, "secondaires", False, "rôles secondaires (vérif détail)")
    _bool_flags(p, "respecter-supprimes", True, "ne pas réajouter les morceaux supprimés")
    _bool_flags(p, "images", True, "télécharger photos, pochettes, vignettes")
    p.add_argument("--maj", action="store_true", help="mode MàJ : prefill des seuls nouveaux")
    _bool_flags(p, "deezer", True, "compléter par Deezer (écarts listés, jamais créés)")
    p.add_argument("--deezer-id", type=int, help="ID Deezer de l'artiste quand l'oracle est ambigu")


def _deezer_args(p: argparse.ArgumentParser) -> None:
    _artiste_args(p)
    p.add_argument("--deezer-id", type=int, help="ID Deezer de l'artiste quand l'oracle est ambigu")
    p.add_argument("--creer", action="store_true", help="créer les écarts cochés d'office")
    p.add_argument("--versions", action="store_true", help="avec --creer : toutes les versions")
    p.add_argument("--apparitions", action="store_true", help="avec --creer : les apparitions")


def _credits_args(p: argparse.ArgumentParser) -> None:
    _bool_flags(p, "genius", True, "crédits Genius")
    _bool_flags(p, "discogs", True, "crédits Discogs")
    p.add_argument("--force-credits", action="store_true")
    _bool_flags(p, "paroles-genius", True, "paroles structurées Genius")
    _bool_flags(p, "paroles-ytm", True, "paroles YTM (repli texte)")
    p.add_argument("--force-paroles", action="store_true")
    _bool_flags(p, "lrclib", True, "timestamps LRCLIB (source 1)")
    _bool_flags(p, "ytm", True, "timestamps YTM (source 2)")
    _bool_flags(p, "musixmatch", False, "timestamps Musixmatch (dernier recours)")
    p.add_argument("--force-sync", action="store_true")


def _enrich_args(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--sources",
        help="liste séparée par des virgules (reccobeats,getsongbpm,songbpm,bpmfinder,deezer,"
        "discogs) ; défaut = toutes les sources disponibles",
    )
    p.add_argument("--force", action="store_true", help="force_update")
    _bool_flags(p, "clear-on-failure", True, "effacer les données erronées en cas d'échec")
    _bool_flags(
        p, "musicbrainz", True, "identité en fin de run (alias PROPOSÉS, à arbitrer dans Groupes)"
    )


def _streams_args(p: argparse.ArgumentParser) -> None:
    _bool_flags(p, "kworb", True, "Spotify via Kworb (rapide)")
    _bool_flags(p, "spotify-web", True, "page artiste Spotify (auditeurs mensuels + top 10)")
    p.add_argument("--spotify-full", action="store_true", help="tous les morceaux et albums")
    _bool_flags(p, "ytm-streams", True, "YouTube Music")
    p.add_argument("--ytm-channel", default="", help="@handle, lien ou UC… à épingler")


def _certifs_sources_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--sources",
        default=",".join(certifs.SOURCES_PAR_ARTISTE),
        help="SNEP,RIAA,BPI (BRMA n'a pas de recherche par artiste)",
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m src.cli", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="commande", required=True)

    a = sub.add_parser("artiste", help="charger ou ajouter un artiste")
    a_sub = a.add_subparsers(dest="action", required=True)
    _artiste_args(a_sub.add_parser("add", help="ajoute (ou charge) l'artiste"))

    d = sub.add_parser("disco", help="discographie Genius (illimitée par défaut)")
    _artiste_args(d)
    _disco_args(d)

    _deezer_args(
        sub.add_parser(
            "deezer", help="écarts de discographie Deezer (rapport ; --creer pour écrire)"
        )
    )

    c = sub.add_parser("credits", help="crédits, paroles, timestamps sur tous les morceaux")
    _artiste_args(c)
    _manquants_arg(c)
    _credits_args(c)

    e = sub.add_parser("enrich", help="BPM/key/Spotify ID/… sur tous les morceaux")
    _artiste_args(e)
    _manquants_arg(e)
    _enrich_args(e)

    s = sub.add_parser("streams", help="streams Spotify / YouTube Music")
    _artiste_args(s)
    _manquants_arg(s)
    _streams_args(s)

    ce = sub.add_parser("certifs", help="certifications")
    ce_sub = ce.add_subparsers(dest="action", required=True)
    rech = ce_sub.add_parser("search", help="recherche par artiste puis application")
    _artiste_args(rech)
    _certifs_sources_arg(rech)
    _artiste_args(ce_sub.add_parser("apply", help="rematch offline depuis les CSV clean"))
    upd = ce_sub.add_parser("update", help="MàJ GLOBALE des magasins (longue)")
    upd.add_argument("sources", nargs="*", help="parmi SNEP BRMA RIAA BPI (défaut : les 4)")

    cy = sub.add_parser("cycle", help="l'enchaînement complet")
    _artiste_args(cy)
    _manquants_arg(cy)
    g = cy.add_mutually_exclusive_group()
    g.add_argument("--only", help=f"étapes à lancer, parmi {','.join(cycle.ETAPES)}")
    g.add_argument("--skip", help="étapes à sauter")
    _disco_args(cy)
    _credits_args(cy)
    _enrich_args(cy)
    _streams_args(cy)
    cy.add_argument(
        "--certifs-sources",
        default=",".join(certifs.SOURCES_PAR_ARTISTE),
        help="SNEP,RIAA,BPI",
    )
    return p


# ── args → options (fonctions PURES, testées) ───────────────────────────────
def _liste(v: str | None) -> tuple[str, ...]:
    return tuple(x.strip() for x in v.split(",") if x.strip()) if v else ()


def options_disco(a: argparse.Namespace) -> discographie.OptionsDisco:
    return discographie.OptionsDisco(
        max_songs=a.max_songs,
        include_features=a.features,
        prefill=a.prefill,
        update_only=a.maj,
        include_secondary=a.secondaires,
        respect_deleted=a.respecter_supprimes,
        download_images=a.images,
        deezer=a.deezer,
        deezer_id=a.deezer_id,
    )


def options_credits(a: argparse.Namespace) -> credits.OptionsCredits:
    return credits.OptionsCredits(
        genius=a.genius,
        discogs=a.discogs,
        force_credits=a.force_credits,
        paroles_genius=a.paroles_genius,
        paroles_ytm=a.paroles_ytm,
        force_paroles=a.force_paroles,
        sync_lrclib=a.lrclib,
        sync_ytm=a.ytm,
        sync_musixmatch=a.musixmatch,
        force_sync=a.force_sync,
    )


def options_enrich(a: argparse.Namespace) -> enrichissement.OptionsEnrich:
    return enrichissement.OptionsEnrich(
        sources=_liste(a.sources) or None,
        force_update=a.force,
        clear_on_failure=a.clear_on_failure,
        musicbrainz=a.musicbrainz,
    )


def options_streams(a: argparse.Namespace) -> streams.OptionsStreams:
    return streams.OptionsStreams(
        kworb=a.kworb,
        spotify_web=a.spotify_web,
        spotify_full=a.spotify_full,
        ytm=a.ytm_streams,
        ytm_channel=a.ytm_channel,
    )


def options_cycle(a: argparse.Namespace) -> cycle.OptionsCycle:
    return cycle.OptionsCycle(
        genius_id=a.genius_id,
        only=_liste(a.only),
        skip=_liste(a.skip),
        manquants=a.manquants,
        disco=options_disco(a),
        credits=options_credits(a),
        enrich=options_enrich(a),
        streams=options_streams(a),
        certifs_sources=_liste(a.certifs_sources),
    )


def options_etape_seule(a: argparse.Namespace, etape: str) -> cycle.OptionsCycle:
    """Une sous-commande = le cycle restreint à UNE étape, mêmes règles."""
    return cycle.OptionsCycle(
        genius_id=a.genius_id,
        manquants=getattr(a, "manquants", False),
        disco=options_disco(a) if etape == "disco" else discographie.OptionsDisco(),
        credits=options_credits(a) if etape == "credits" else credits.OptionsCredits(),
        enrich=options_enrich(a) if etape == "enrich" else enrichissement.OptionsEnrich(),
        streams=options_streams(a) if etape == "streams" else streams.OptionsStreams(),
        certifs_sources=(
            _liste(a.sources) or certifs.SOURCES_PAR_ARTISTE
            if etape == "certifs"
            else certifs.SOURCES_PAR_ARTISTE
        ),
    )


def code_de(bilan) -> int:
    return COMPLET if bilan.complete else PARTIEL


# ── Exécution ────────────────────────────────────────────────────────────────
def _progres_console(courant: int, total: int, libelle: str, tache: str = "") -> None:
    if total:
        print(f"  [{tache}] {courant}/{total} — {libelle}", flush=True)
    else:
        print(f"  [{tache}] {libelle}", flush=True)


def _hooks() -> Hooks:
    return Hooks(progress=_progres_console, should_stop=lifecycle.stop_requested)


def _installer_ctrl_c() -> None:
    etat = {"n": 0}

    def handler(signum, frame):
        etat["n"] += 1
        if etat["n"] == 1:
            print("\n⏹️ Arrêt demandé — fin du morceau en cours, puis bilan…", flush=True)
            lifecycle.request_stop()
        else:
            print("\n⛔ Second Ctrl-C : sortie immédiate.", flush=True)
            raise KeyboardInterrupt

    signal.signal(signal.SIGINT, handler)


def _fermer(runtime: Runtime) -> None:
    """Le même ordre que `MainWindow._on_closing` : workers et boucle, puis les
    ressources SYNC des providers et Playwright sur le fil principal."""
    lifecycle.shutdown_workers()
    try:
        runtime.data_enricher.close()
    except Exception:  # noqa: BLE001 — fermeture best-effort
        logger.warning("Fermeture DataEnricher", exc_info=True)
    try:
        from src.scrapers.playwright_manager import stop_playwright

        stop_playwright()
    except Exception:  # noqa: BLE001 — fermeture best-effort
        logger.warning("Arrêt Playwright", exc_info=True)


def deezer_ambigu():
    from src.services.deezer_identite import ArtisteDeezerAmbigu

    return ArtisteDeezerAmbigu


def _charger(runtime: Runtime, a: argparse.Namespace, *, creer: bool):
    if creer:
        return artiste.charger_ou_ajouter(runtime, a.nom, genius_id=a.genius_id)
    art = artiste.charger(runtime, a.nom)
    if art is None:
        raise SystemExit(f"Artiste {a.nom!r} absent de la base — `artiste add` d'abord.")
    return art


def _imprimer_bilan(titre: str, texte: str) -> None:
    print(f"\n──── {titre} ────\n{texte}\n", flush=True)


def executer(a: argparse.Namespace, runtime: Runtime) -> int:
    hooks = _hooks()
    if a.commande == "artiste":
        art = _charger(runtime, a, creer=True)
        print(f"✅ {art.name} — ID Genius {art.genius_id} — {len(art.tracks)} morceau(x) en base")
        return COMPLET

    if a.commande == "certifs" and a.action == "update":
        bilan = certifs.mettre_a_jour(
            a.sources or None, should_stop=lifecycle.stop_requested, progres=print
        )
        _imprimer_bilan("Certifs — mise à jour globale", "\n".join(bilan.lignes))
        return code_de(bilan)

    if a.commande == "certifs" and a.action == "apply":
        art = _charger(runtime, a, creer=False)
        bilan = certifs.appliquer(runtime, art)
        _imprimer_bilan("Certifs — application", bilan.rapport)
        return code_de(bilan)

    if a.commande == "deezer":
        return _executer_deezer(a, runtime, hooks)

    if a.commande == "cycle":
        options = options_cycle(a)
        bilan = cycle.run(runtime, a.nom, options, hooks)
        art = artiste.charger(runtime, a.nom)
        for etape, b in bilan.etapes.items():
            _imprimer_bilan(etape, cycle.resume_etape(etape, b, options, art))
        _imprimer_bilan("Cycle", cycle.resume(bilan, options))
        return code_de(bilan)

    # Une étape seule : le cycle restreint à elle, avec les MÊMES règles.
    etape = "certifs" if a.commande == "certifs" else a.commande  # certifs → action "search"
    options = options_etape_seule(a, etape)
    art = _charger(runtime, a, creer=(etape == "disco"))
    b = cycle.executer_etape(runtime, art, etape, options, hooks)
    _imprimer_bilan(etape, cycle.resume_etape(etape, b, options, art))
    return code_de(b)


def _executer_deezer(a: argparse.Namespace, runtime: Runtime, hooks) -> int:
    """Rapport des écarts Deezer ; `--creer` écrit ce qui est coché d'office
    (élargi par `--versions` / `--apparitions`). L'artiste doit être en base."""
    from src.concurrency import async_loop
    from src.observability import source_usage
    from src.observability.registry import Flow
    from src.services import deezer_identite, ecarts_deezer

    art = _charger(runtime, a, creer=False)
    enricher = runtime.data_enricher
    with source_usage.run_scope(Flow.DISCO, artist_id=art.id):
        deezer_id = async_loop.run_sync(
            deezer_identite.resoudre_async(
                enricher.deezer_client,
                enricher.http,
                runtime.data_manager,
                art,
                force_id=a.deezer_id,
            )
        )
        bilan = ecarts_deezer.detecter(
            runtime,
            art,
            deezer_id=deezer_id,
            should_stop=hooks.should_stop,
            genius_api=runtime.genius_api,
        )
        _imprimer_bilan("Deezer — écarts de discographie", ecarts_deezer.resume(bilan, art.name))
        if a.creer:
            choisis = [
                e
                for e in bilan.ecarts
                if e.coche
                or (a.versions and e.nature == "version")
                or (a.apparitions and e.nature == "apparition")
            ]
            client, http = enricher.deezer_client, enricher.http
            comptes = ecarts_deezer.creer_lignes(
                runtime.data_manager,
                art,
                choisis,
                lire_piste=lambda tid: async_loop.run_sync(client.get_track_async(http, tid)),
            )
            _imprimer_bilan("Deezer — création", "\n".join(comptes) or "rien à créer")
    return code_de(bilan)


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    _installer_ctrl_c()
    runtime = Runtime.build()
    # Persistance de l'usage des sources (sans elle, les verdicts sont jetés).
    usage_repository.attach(runtime.data_manager.engine)
    try:
        return executer(a, runtime)
    except deezer_ambigu() as amb:
        print(f"❓ Artiste Deezer {amb.nom!r} ambigu. Candidats :")
        for c in amb.candidats:
            print(f"   • {c.id}  {c.name}  ({c.nb_album} disques, {c.nb_fan} fans, {c.detail})")
        if not amb.candidats:
            print("   (aucun homonyme exact) — donne --deezer-id")
        return AMBIGU
    except artiste.ArtisteAmbigu as amb:
        print(f"❓ Artiste {amb.nom!r} introuvable par slug Genius. Candidats :")
        for c in amb.candidats:
            print(f"   • {c.name}  (--genius-id {c.genius_id})")
        if not amb.candidats:
            print("   (aucun) — vérifie l'orthographe ou donne --genius-id")
        return AMBIGU
    except SystemExit as e:
        if isinstance(e.code, str):
            print(f"❌ {e.code}")
            return ERREUR
        raise
    except KeyboardInterrupt:
        print("⛔ Interrompu.")
        return PARTIEL
    except Exception:
        logger.exception("Échec de la commande")
        return ERREUR
    finally:
        _fermer(runtime)


if __name__ == "__main__":
    sys.exit(main())
