"""Certifications — mise à jour des magasins, recherche par artiste, application.

Extrait de `src/gui/certification_update_gui.py` (2026-09-14). Les scripts
`src/utils/update_<source>.py` tournent en SOUS-PROCESSUS (ils ont leur propre
`script_scope(Flow.CERTS)`) ; `MISES_A_JOUR` déclare UNE fois ce qu'il faut
lancer par source. La GUI n'affiche plus que ce qui sort d'ici.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple

from src.models import Artist
from src.services.runtime import Bilan, Runtime
from src.utils.logger import get_logger

logger = get_logger(__name__)

RACINE = Path(__file__).resolve().parents[2]
SCRIPTS = RACINE / "src" / "utils"

#: Sources qui s'interrogent PAR ARTISTE. BRMA n'en fait pas partie : tout
#: Ultratop est derrière Cloudflare sauf les pages par année — il contribue par
#: LECTURE du corpus, et on le DIT plutôt que de laisser croire à une requête.
SOURCES_PAR_ARTISTE: tuple[str, ...] = ("SNEP", "RIAA", "BPI")


class MiseAJour(NamedTuple):
    """Ce qu'il faut lancer pour mettre à jour une source.

    UNE déclaration, consommée par le bouton individuel, par « 🔄 Tout mettre à
    jour » et par la CLI. Avant elle, le global lançait les scripts SANS
    argument : BRMA et RIAA partaient en mode interactif, BPI affichait son aide
    en sortant en 0 (lot 6, 2026-09-10).
    """

    script: str
    args: tuple[str, ...] = ()
    #: Chrome de debug préparé EN AMONT (BRMA : le Cloudflare d'ultratop fait
    #: boucler tout navigateur d'automation, le CDP n'y est pas un repli).
    cdp_amont: bool = False
    #: Chrome de debug en REPLI d'un échec (RIAA : le headless passe, et un
    #: repli qui réussit dit que c'était un problème d'accès, pas un parseur).
    repli_cdp: bool = False


MISES_A_JOUR: dict[str, MiseAJour] = {
    "SNEP": MiseAJour("update_snep.py"),
    "BRMA": MiseAJour("update_brma.py", ("--mode", "once", "--years-back", "1"), cdp_amont=True),
    "RIAA": MiseAJour("update_riaa.py", ("--auto",), repli_cdp=True),
    "BPI": MiseAJour("update_bpi.py", ("--auto",)),
}


def ligne_bilan(source: str, code: int, sortie: str) -> str:
    """Une ligne de bilan par source, qui DIT si le script a échoué.

    La dernière ligne de sortie était reprise telle quelle, code de retour
    ignoré : un script planté produisait « SNEP : <dernière ligne quelconque> »,
    indiscernable d'un succès.
    """
    derniere = (sortie.strip().splitlines()[-1:] or ["ok"])[0]
    return (
        f"{source} : {derniere}" if code == 0 else f"{source} : ❌ ÉCHEC (code {code}) — {derniere}"
    )


def _rien(_: str) -> None:
    return None


def run_streaming(
    cmd: list[str], tag: str, env: dict | None = None, progres: Callable[[str], None] = _rien
) -> tuple[int, str]:
    """Lance un script de certifs en RELAYANT sa sortie ligne à ligne.

    `subprocess.run(capture_output=True)` avalait tout jusqu'à la fin : pendant
    une MàJ RIAA de plusieurs minutes, rien ne s'affichait. Chaque ligne passe
    par le logger (console + fichier du jour) et, au plus toutes les 0,3 s, par
    `progres` (le bandeau GUI ne suit pas chaque ligne).

    `-u` est INDISPENSABLE : la sortie d'un Python dont stdout est un tuyau est
    bufferisée par blocs, et « relayer » l'aurait livrée d'un coup à la fin.
    """
    proc = subprocess.Popen(
        [cmd[0], "-u", *cmd[1:]],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        cwd=RACINE,
        env=env,
    )
    lignes: list[str] = []
    dernier = 0.0
    with proc.stdout:
        for ligne in proc.stdout:
            ligne = ligne.rstrip()
            if not ligne:
                continue
            lignes.append(ligne)
            logger.info(f"[{tag}] {ligne}")
            maintenant = time.monotonic()
            if maintenant - dernier > 0.3:
                dernier = maintenant
                progres(f"{tag} : {ligne[:70]}")
    return proc.wait(), "\n".join(lignes)


def preparer_cdp() -> str | None:
    """Lance (ou retrouve) un Chrome de debug et rend son URL CDP."""
    try:
        from src.scrapers.cdp_chrome import ensure_cdp_chrome

        return ensure_cdp_chrome()
    except Exception:
        logger.exception("Préparation du Chrome de debug (CDP)")
        return None


def _env_cdp(url: str) -> dict:
    return {**os.environ, "GENIUS_CDP_URL": url}


def executer_maj(
    nom: str,
    *,
    progres: Callable[[str], None] = _rien,
    sur_cdp_absent: Callable[[str], None] | None = None,
    lancer: Callable[..., tuple[int, str]] = run_streaming,
) -> tuple[int, str]:
    """Met à jour UNE source, de façon SYNCHRONE. Rend (code, sortie).

    Séparé du worker pour que « Tout mettre à jour » et la CLI enchaînent les
    sources DANS UN SEUL fil (quatre sous-processus écrivant leurs CSV en même
    temps ne se surveillent pas l'un l'autre). `sur_cdp_absent(nom)` est appelé
    quand la source exige Chrome et qu'il est introuvable (la GUI avertit ; par
    défaut, un log).
    """
    maj = MISES_A_JOUR[nom]
    script = SCRIPTS / maj.script
    if not script.exists():
        raise FileNotFoundError(f"Script non trouvé: {script}")
    commande = [sys.executable, str(script), *maj.args]
    env = None

    if maj.cdp_amont:
        # Le Cloudflare d'ultratop fait boucler tout navigateur lancé par de
        # l'automation, même le vrai Chrome (JOURNAL 2026-06-29) : ici le CDP
        # n'est pas un repli, c'est la seule route qui passe.
        progres(f"🌐 {nom} : préparation de Chrome (Cloudflare)…")
        url = preparer_cdp()
        if url:
            env = _env_cdp(url)
        elif sur_cdp_absent is not None:
            sur_cdp_absent(nom)
        else:
            logger.warning(
                f"[{nom}] Chrome de debug introuvable (CHROME_PATH ?) — la MàJ tente "
                "quand même, mais risque de boucler sur le challenge Cloudflare"
            )

    progres(f"Mise à jour {nom} en cours...")
    code, sortie = lancer(commande, nom, env=env, progres=progres)

    if code != 0 and maj.repli_cdp:
        progres(f"{nom} : échec en headless — seconde tentative via Chrome…")
        logger.warning(
            f"[{nom}] échec en headless, repli sur la route CDP "
            "(si elle réussit, c'était un problème d'accès et non un parseur cassé)"
        )
        url = preparer_cdp()
        if url:
            code, sortie_cdp = lancer(commande, f"{nom} (CDP)", env=_env_cdp(url), progres=progres)
            sortie = sortie_cdp or sortie
        else:
            logger.error(
                f"[{nom}] repli CDP impossible : Chrome introuvable "
                "(installe Google Chrome ou définis CHROME_PATH)"
            )
    return code, sortie


@dataclass
class BilanCertifs(Bilan):
    #: Une ligne par source (`ligne_bilan`), dans l'ordre d'exécution.
    lignes: list[str] = field(default_factory=list)
    #: Recherche par artiste : le rapport complet (apport du run, répartition, récap).
    rapport: str = ""
    #: Application : morceaux certifiés.
    certifies: int = 0

    @property
    def echecs(self) -> list[str]:
        return [ligne for ligne in self.lignes if "❌" in ligne or "interrompu" in ligne]


def mettre_a_jour(
    noms: Iterable[str] | None = None,
    *,
    should_stop: Callable[[], bool] = lambda: False,
    progres: Callable[[str], None] = _rien,
    sur_cdp_absent: Callable[[str], None] | None = None,
) -> BilanCertifs:
    """MàJ GLOBALE des magasins, EN SÉRIE. Un run coupé ou une source en échec
    rendent `complete=False`."""
    bilan = BilanCertifs()
    for nom in list(noms) if noms else list(MISES_A_JOUR):
        if should_stop():
            bilan.lignes.append(f"{nom} : interrompu")
            bilan.interrompu(f"arrêt demandé avant {nom}")
            break
        try:
            code, sortie = executer_maj(nom, progres=progres, sur_cdp_absent=sur_cdp_absent)
            bilan.lignes.append(ligne_bilan(nom, code, sortie))
        except Exception as e:
            logger.exception(f"Mise à jour globale — {nom}")
            bilan.lignes.append(f"{nom} : ❌ {e}")
    if bilan.echecs and bilan.complete:
        bilan.interrompu(f"{len(bilan.echecs)}/{len(bilan.lignes)} source(s) en échec")
    return bilan


def noms_de_recherche_pour(runtime: Runtime, artist: Artist) -> list[str]:
    """L'artiste, puis les formations sous lesquelles il est crédité.

    Seuls les liens `member_of` et `alias` CONFIRMÉS comptent : on cherche les
    certifications de l'artiste sous le nom de SES groupes, pas sous celui de
    ses membres. Une formation devinée par un rapprochement de noms n'a rien à
    faire dans une URL de recherche.
    """
    from src.utils.cert_artist import noms_de_recherche

    formations: list[str] = []
    if artist.id:
        try:
            formations = [
                rel.related_name
                for rel in runtime.data_manager.get_artist_relations(artist.id)
                if rel.kind in ("member_of", "alias")
            ]
        except Exception:
            logger.exception("Formations indisponibles pour la recherche de certifs")
    return noms_de_recherche(artist.name, formations)


def rechercher_artiste(
    noms: list[str],
    *,
    sources: Iterable[str] = SOURCES_PAR_ARTISTE,
    progres: Callable[[str], None] = _rien,
    lancer: Callable[..., tuple[int, str]] = run_streaming,
) -> BilanCertifs:
    """Certifs d'un artiste sur les sources qui s'interrogent par artiste.

    `--artist` est RÉPÉTABLE sur les scripts : un seul sous-processus par
    source, quel que soit le nombre de noms. RIAA : headless d'abord, CDP en
    repli. Le magasin ayant changé sur disque, le matcher est reconstruit AVANT
    le bilan d'après (sinon il relirait l'état d'avant le run).
    """
    from src.utils.cert_artist import (
        bilan_de,
        certifications,
        evolution,
        nouveautes,
        recap,
        repartition,
    )

    bilan = BilanCertifs()
    if not noms:
        bilan.interrompu("aucun nom d'artiste")
        return bilan
    py = sys.executable
    etiquette = " + ".join(noms)
    args_noms = [a for nom in noms for a in ("--artist", nom)]
    certs_avant = certifications(noms)
    drapeaux = {"SNEP": "🇫🇷", "RIAA": "🇺🇸", "BPI": "🇬🇧"}

    for source in sources:
        if source not in SOURCES_PAR_ARTISTE:
            bilan.lignes.append(f"{source} : pas de recherche par artiste (corpus local)")
            continue
        progres(f"{drapeaux.get(source, '')} {source} : {etiquette}…")
        commande = [py, str(SCRIPTS / MISES_A_JOUR[source].script), *args_noms]
        try:
            code, sortie = lancer(commande, f"{source} {etiquette}", progres=progres)
            if code != 0 and MISES_A_JOUR[source].repli_cdp:
                progres(f"{source} : repli via Chrome…")
                url = preparer_cdp()
                if url:
                    code, sortie = lancer(
                        commande, f"{source} {etiquette} (CDP)", env=_env_cdp(url), progres=progres
                    )
            bilan.lignes.append(ligne_bilan(source, code, sortie))
        except Exception as e:
            # Boucle résiliente : une source qui tombe ne doit pas priver des
            # autres. Trace complète en dernier ressort.
            logger.exception(f"Récupération {source} par artiste")
            bilan.lignes.append(f"{source} : ❌ erreur ({e})")

    try:
        from src.utils.cert_matcher import reset_cert_matcher

        reset_cert_matcher()
    except Exception:
        logger.exception("Rafraîchissement du matcher")

    certs_apres = certifications(noms)
    bilan.lignes.append("BRMA : corpus local (Ultratop n'a pas de recherche par artiste)")
    # Le rapport dit CE QU'ON A, pas seulement combien — et l'ÉCART d'abord :
    # « 12 certifications » se lit pareil qu'on en ait rapporté douze ou zéro.
    neuves = nouveautes(certs_avant, certs_apres)
    parts = [
        "\n".join(bilan.lignes),
        f"Apport de ce run : {evolution(bilan_de(certs_avant), bilan_de(certs_apres))}",
    ]
    if ligne := repartition(certs_apres, noms):
        parts.append(ligne)
    parts.append(recap(certs_apres, neuves))
    bilan.rapport = "\n\n".join(parts)
    if bilan.echecs:
        bilan.interrompu(f"{len(bilan.echecs)} source(s) en échec")
    return bilan


def appliquer(runtime: Runtime, artist: Artist) -> BilanCertifs:
    """Rematche les certifs de l'artiste depuis les CSV clean puis persiste (E7h).

    Offline (matcher en mémoire) ; `reset_cert_matcher` pour repartir des CSV
    fraîchement mis à jour. `record_pending` et non `save_track` : seules les
    colonnes de certifs sont concernées, et le save complet ne savait pas
    RETIRER une certification devenue caduque.
    """
    from src.utils.cert_matcher import get_cert_matcher, reset_cert_matcher
    from src.utils.certification_enricher import apply_certifications

    bilan = BilanCertifs()
    if not artist.tracks:
        bilan.interrompu("aucun morceau chargé")
        return bilan
    reset_cert_matcher()
    bilan.certifies = apply_certifications(artist, artist.tracks, get_cert_matcher())
    for track in artist.tracks:
        runtime.data_manager.record_pending(track)
    oublies = runtime.data_manager.certifications_non_enregistrees(artist.tracks)
    if oublies:
        logger.error(
            f"Certifs recalculées mais NON enregistrées ({len(oublies)}): {', '.join(oublies[:8])}"
        )
        bilan.erreurs.extend(oublies)
    bilan.rapport = f"{bilan.certifies} morceau(x) certifié(s) pour {artist.name}."
    return bilan
