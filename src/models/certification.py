"""Modèles pour représenter les certifications musicales"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional


class CertificationLevel(Enum):
    """Niveaux de certification"""

    # Certifications françaises SNEP
    OR = "Or"
    DOUBLE_OR = "Double Or"
    TRIPLE_OR = "Triple Or"
    PLATINE = "Platine"
    DOUBLE_PLATINE = "Double Platine"
    TRIPLE_PLATINE = "Triple Platine"
    DIAMANT = "Diamant"
    DOUBLE_DIAMANT = "Double Diamant"
    TRIPLE_DIAMANT = "Triple Diamant"
    QUADRUPLE_DIAMANT = "Quadruple Diamant"

    # Certifications internationales (pour extension future)
    GOLD = "Gold"
    PLATINUM = "Platinum"
    DIAMOND = "Diamond"

    @classmethod
    def from_string(cls, value: str) -> Optional["CertificationLevel"]:
        """Convertit une string en CertificationLevel"""
        value_clean = value.strip().upper().replace(" ", "_")

        # Mapping des variantes
        mapping = {
            "OR": cls.OR,
            "DOUBLE_OR": cls.DOUBLE_OR,
            "TRIPLE_OR": cls.TRIPLE_OR,
            "PLATINE": cls.PLATINE,
            "DOUBLE_PLATINE": cls.DOUBLE_PLATINE,
            "TRIPLE_PLATINE": cls.TRIPLE_PLATINE,
            "DIAMANT": cls.DIAMANT,
            "DOUBLE_DIAMANT": cls.DOUBLE_DIAMANT,
            "TRIPLE_DIAMANT": cls.TRIPLE_DIAMANT,
            "QUADRUPLE_DIAMANT": cls.QUADRUPLE_DIAMANT,
            "GOLD": cls.GOLD,
            "PLATINUM": cls.PLATINUM,
            "DIAMOND": cls.DIAMOND,
        }

        return mapping.get(value_clean)

    def get_threshold(self, country: str = "FR", category: str = "Singles") -> int:
        """Retourne le seuil pour cette certification"""
        thresholds = {
            "FR": {
                "Singles": {
                    self.OR: 15_000_000,
                    self.PLATINE: 30_000_000,
                    self.DIAMANT: 50_000_000,
                },
                "Albums": {
                    self.OR: 50_000,
                    self.DOUBLE_OR: 100_000,
                    self.PLATINE: 100_000,
                    self.DOUBLE_PLATINE: 200_000,
                    self.TRIPLE_PLATINE: 300_000,
                    self.DIAMANT: 500_000,
                },
            }
        }

        try:
            return thresholds[country][category][self]
        except KeyError:
            return 0


class CertificationCategory(Enum):
    """Catégories de certification"""

    SINGLES = "Singles"
    ALBUMS = "Albums"
    VIDEOS = "Vidéos"
    DVD = "DVD"

    @classmethod
    def from_string(cls, value: str) -> "CertificationCategory":
        """Convertit une string en CertificationCategory"""
        if not value:
            return cls.SINGLES

        value_clean = value.strip().upper()

        # Normaliser "Single" -> "Singles"
        if value_clean == "SINGLE":
            value_clean = "SINGLES"

        mapping = {
            "SINGLES": cls.SINGLES,
            "ALBUMS": cls.ALBUMS,
            "VIDÉOS": cls.VIDEOS,
            "VIDEOS": cls.VIDEOS,
            "DVD": cls.DVD,
        }

        return mapping.get(value_clean, cls.SINGLES)


@dataclass
class Certification:
    """Représente une certification musicale"""

    id: int | None = None
    artist_name: str = ""
    title: str = ""
    category: CertificationCategory = CertificationCategory.SINGLES
    level: CertificationLevel = CertificationLevel.OR
    certification_date: datetime | None = None
    release_date: datetime | None = None
    publisher: str | None = None
    country: str = "FR"
    certifying_body: str = "SNEP"

    # Métadonnées
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    # Champs calculés
    threshold_units: int = field(init=False)

    # Frontière colonne JSON (E7g) : payload EXACT du dict `cert_matcher` capturé
    # à la matérialisation, restitué verbatim par `to_column_dict`. Non typé à
    # dessein — les valeurs matcher sont des strings arbitraires (niveaux non
    # bornés, catégories `single`/`album`, dates brutes) qu'un passage par les
    # enums/datetime perdrait. Le round-trip doit rester byte-compatible.
    _column: dict[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self):
        """Calcule les champs dérivés après initialisation"""
        self.threshold_units = self.level.get_threshold(self.country, self.category.value)

    # Clés du format colonne (contrat mapper/GUI = sortie de cert_matcher._format).
    _COLUMN_KEYS = (
        "certification",
        "title",
        "artist_name",
        "category",
        "certification_date",
        "release_date",
        "publisher",
        "detail_url",
        "country",
        "body",
        "flag",
    )

    @classmethod
    def from_match(cls, match: dict[str, Any]) -> "Certification":
        """Matérialise un dict de `cert_matcher` en objet typé (frontière E7g).

        Peuple les champs domaine en best-effort (accès typé côté enricher) ET
        capture le payload colonne EXACT pour un round-trip fidèle. Ne LÈVE
        jamais sur une valeur matcher inattendue (niveau hors enum → défaut Or).
        """
        cert = cls()
        cert.artist_name = match.get("artist_name", "") or ""
        cert.title = match.get("title", "") or ""
        cert.publisher = match.get("publisher") or None
        cert.country = match.get("country", "FR") or "FR"
        cert.certifying_body = match.get("body", "SNEP") or "SNEP"
        lvl = CertificationLevel.from_string(match.get("certification", "") or "")
        if lvl is not None:
            cert.level = lvl
        cert.category = CertificationCategory.from_string(match.get("category", "") or "")
        # Payload colonne verbatim (identité du round-trip, cf. golden test).
        cert._column = {k: match.get(k, "") for k in cls._COLUMN_KEYS}
        return cert

    def to_column_dict(self) -> dict[str, Any]:
        """Dict au format colonne JSON, byte-compatible avec `cert_matcher._format`.

        C'est le CONTRAT lu par le mapper et la GUI (`track_details.py`) : la
        sérialisation reproduit à l'identique le dict d'origine du matcher."""
        return dict(self._column)

    def to_dict(self) -> dict[str, Any]:
        """Convertit la certification en dictionnaire"""
        return {
            "id": self.id,
            "artist_name": self.artist_name,
            "title": self.title,
            "category": self.category.value,
            "level": self.level.value,
            "certification_date": (
                self.certification_date.isoformat() if self.certification_date else None
            ),
            "release_date": self.release_date.isoformat() if self.release_date else None,
            "publisher": self.publisher,
            "country": self.country,
            "certifying_body": self.certifying_body,
            "threshold_units": self.threshold_units,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    def __str__(self) -> str:
        """Représentation string"""
        return f"{self.artist_name} - {self.title} ({self.level.value} {self.country})"

    def __repr__(self) -> str:
        """Représentation pour debug"""
        return f"<Certification: {self.artist_name} - {self.title} - {self.level.value}>"
