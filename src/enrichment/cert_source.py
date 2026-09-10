"""Protocole des SOURCES de certification (phase E7f).

Distinct d'`EnrichmentProvider` (track-scoped) : une source de certif est
GLOBALE (un CSV « clean » par pays, lu par `cert_matcher`), pas rattachée à un
morceau. Le protocole n'expose que de quoi l'orchestrer uniformément :
disponibilité, fraîcheur (lue du `metadata.json` de la source), fermeture.

Les adaptateurs enveloppent l'existant (builders `update_snep`/`update_riaa`/
`update_brma`) SANS toucher aux CLI ni aux subprocess GUI, qui restent le
chemin de MÀJ. Ici, on ne lit que l'état des fichiers (fraîcheur/présence).

Fraîcheur normalisée (`freshness()`) : la MàJ NON-artiste la plus récente (pas
le mtime du fichier, qu'une simple recherche par artiste suffit à bumper — fix
JOURNAL 2026-06-25), la dernière récup par artiste, le nombre de lignes clean.
Sémantique partagée par les 3 sources (SNEP/RIAA/BRMA après uniformisation).
"""

import json
from pathlib import Path
from typing import Protocol, runtime_checkable

from src.config import DATA_PATH
from src.enrichment.base import Capability
from src.utils.logger import get_logger

logger = get_logger(__name__)

_CERT_DIR = Path(DATA_PATH) / "certifications"


def read_freshness(meta_path: Path, clean_path: Path) -> dict:
    """Fraîcheur normalisée d'une source depuis son sidecar + présence du clean.

    Renvoie `{available, last_global, last_artist, count, partial}` :
      - `available` : le CSV clean (lu par le matcher) existe ;
      - `last_global` : MàJ NON-artiste la plus récente (`updates` hors ARTIST
        et hors CLEAN), repli `last_update` si l'ancien format n'a pas de dict
        `updates` ;
      - `last_artist` : `updates['ARTIST']` (récup par artiste), sinon None ;
      - `count` : nb de lignes clean (`count` ou `total_records` legacy BRMA) ;
      - `partial` : motif d'INCOMPLÉTUDE du dernier run non-artiste, ou "".
        Un run partiel horodate comme un run complet — c'est délibéré, retenir
        l'horodatage ferait afficher « jamais mise à jour » à un balayage qui a
        ramené 300 vraies lignes. Ce qui manquait n'est donc pas une date
        retenue, c'est ce motif à côté d'elle : sans lui, la coche verte survit
        à la boîte d'erreur qui la contredit, et il ne reste plus, une semaine
        après, qu'une date du jour rassurante.
    """
    fresh = {
        "available": clean_path.exists(),
        "last_global": None,
        "last_artist": None,
        "count": None,
        "partial": "",
    }
    if not meta_path.exists():
        return fresh
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning(f"Lecture {meta_path.name} impossible : {e}")
        return fresh

    updates = data.get("updates") or {}
    fresh["last_artist"] = updates.get("ARTIST")
    # CLEAN est écarté au même titre qu'ARTIST : un nettoyage est une opération
    # 100 % LOCALE, il ne prouve pas qu'on a vérifié la source. Le compter
    # rajeunissait une source en panne depuis six semaines dès qu'on cliquait
    # « Nettoyer » — le seul verdict censé répondre à « quand a-t-on VÉRIFIÉ ? »
    # répondait « quand a-t-on TOUCHÉ le fichier ? ». BRMA, seul des quatre, ne
    # produisait pas ce faux signal parce que son dédup n'horodate pas.
    globales = [(t, s) for s, t in updates.items() if s not in ("ARTIST", "CLEAN")]
    if globales:
        horodatage, source_gagnante = max(globales)
        fresh["last_global"] = horodatage
        fresh["partial"] = (data.get("partial") or {}).get(source_gagnante, "")
    elif data.get("last_source", data.get("source")) not in ("ARTIST", "CLEAN"):
        # Ancien format sans `updates` : `last_update` vaut MàJ globale.
        fresh["last_global"] = data.get("last_update")
    fresh["count"] = data.get("count", data.get("total_records"))
    return fresh


@runtime_checkable
class CertificationSource(Protocol):
    """Interface minimale d'une source de certification (globale, non track-scoped)."""

    name: str
    capabilities: set[Capability]
    clean_path: Path

    def is_available(self) -> bool:
        """True si le CSV clean de la source est présent (matchable)."""
        ...

    def freshness(self) -> dict:
        """État de fraîcheur normalisé (cf. `read_freshness`)."""
        ...

    def close(self) -> None:
        """No-op : ces sources ne tiennent aucune ressource ouverte."""
        ...


class _FileCertificationSource:
    """Base des 3 adaptateurs : lecture d'état de fichiers, aucune ressource."""

    name: str = ""
    capabilities = {Capability.CERTS}
    _subdir: str = ""
    _clean: str = ""
    _meta: str = ""

    @property
    def _dir(self) -> Path:
        return _CERT_DIR / self._subdir

    @property
    def clean_path(self) -> Path:
        """CSV « clean » lu par le matcher (sert au repli mtime GUI)."""
        return self._dir / self._clean

    def is_available(self) -> bool:
        return self.clean_path.exists()

    def freshness(self) -> dict:
        return read_freshness(self._dir / self._meta, self.clean_path)

    def close(self) -> None:
        return None


class SnepCertificationSource(_FileCertificationSource):
    """SNEP 🇫🇷 — clean `certif_snep.csv`, sidecar `certif_snep.meta.json`."""

    name = "SNEP"
    _subdir = "snep"
    _clean = "certif_snep.csv"
    _meta = "certif_snep.meta.json"


class BrmaCertificationSource(_FileCertificationSource):
    """BRMA 🇧🇪 — clean `certif_brma.csv`, sidecar `metadata.json`."""

    name = "BRMA"
    _subdir = "brma"
    _clean = "certif_brma.csv"
    _meta = "metadata.json"


class RiaaCertificationSource(_FileCertificationSource):
    """RIAA 🇺🇸 — clean `certif_riaa.csv`, sidecar `metadata.json`."""

    name = "RIAA"
    _subdir = "riaa"
    _clean = "certif_riaa.csv"
    _meta = "metadata.json"


class BpiCertificationSource(_FileCertificationSource):
    """BPI 🇬🇧 — clean `certif_bpi.csv`, sidecar `metadata.json`."""

    name = "BPI"
    _subdir = "bpi"
    _clean = "certif_bpi.csv"
    _meta = "metadata.json"


def all_certification_sources() -> list[CertificationSource]:
    """Les 4 sources de certification, dans l'ordre d'affichage GUI."""
    return [
        SnepCertificationSource(),
        BrmaCertificationSource(),
        RiaaCertificationSource(),
        BpiCertificationSource(),
    ]
