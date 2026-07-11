"""Matcher de certifications UNIFIÉ (multi-pays).

Une seule logique de raccordement morceau/album ↔ certification, partagée par
toutes les sources (SNEP 🇫🇷, BRMA 🇧🇪, RIAA 🇺🇸 à venir), au lieu d'un manager
par pays. On AGRÈGE les sources dans un magasin normalisé en mémoire, puis on
applique les **stratégies de matching éprouvées de la SNEP** (exact → featuring
→ préfixe tronqué) — en réutilisant son `normalize_text` pour garantir une
normalisation identique (zéro régression SNEP, vérifié par test comparatif).

Les résultats sont tagués par pays (`country`/`body`/`flag`) → l'affichage peut
grouper par territoire.

RIAA-ready : ajouter une source = une méthode `_load_riaa()` qui pousse des
lignes au même format ; le matcher ne change pas.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional

import pandas as pd

from src.config import DATA_PATH
from src.api.snep_certifications import get_snep_manager
from src.utils.logger import get_logger

logger = get_logger(__name__)

_FEAT_RE = re.compile(r"^(.+?)\s+(?:FEAT\.?|FT\.?|FEATURING)\s+(.+)$", re.IGNORECASE)

# Catégorie unifiée
_CAT_MAP = {
    "singles": "single",
    "single": "single",
    "albums": "album",
    "album": "album",
    "vidéos": "video",
    "videos": "video",
    "vidéo": "video",
    "video": "video",
}

_COUNTRY = {"SNEP": "FR", "BRMA": "BE", "RIAA": "US"}
_FLAG = {"SNEP": "🇫🇷", "BRMA": "🇧🇪", "RIAA": "🇺🇸"}

# Priorité d'affichage (plus petit = plus haut). Les multi-platine BE sont
# classés juste au-dessus de Platine selon le multiplicateur.
_RANK = {
    "quadruple diamant": 1,
    "triple diamant": 2,
    "double diamant": 3,
    "diamant": 4,
    "triple platine": 5,
    "double platine": 6,
    "platine": 7,
    "triple or": 8,
    "double or": 9,
    "or": 10,
    # RIAA (anglais)
    "diamond": 4,
    "platinum": 7,
    "gold": 10,
}


def _norm_cat(cat: str) -> str:
    return _CAT_MAP.get((cat or "").strip().lower(), (cat or "").strip().lower())


def _to_iso_date(s: str) -> str:
    """« October 17, 2017 » → « 2017-10-17 ». Laisse tel quel si déjà ISO."""
    s = (s or "").strip()
    if not s or s.lower() == "none":
        return ""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return s
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(s.title() if "," in s else s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return s


def _riaa_level(s: str) -> str:
    """Normalise un niveau RIAA : « 4x Multi-Platinum » → « 4x Platinum »."""
    s = (s or "").strip()
    m = re.match(r"(\d+)\s*x\s*multi-?platinum", s, re.I)
    if m:
        return f"{m.group(1)}x Platinum"
    if re.fullmatch(r"multi-?platinum", s, re.I):
        return "Platinum"
    return s  # Gold, Platinum, Diamond, etc.


class CertMatcher:
    """Magasin unifié + raccordement morceau/album ↔ certifs, multi-pays."""

    def __init__(self):
        # Réutilise EXACTEMENT le normalize_text de la SNEP (parité garantie)
        self._norm = get_snep_manager().normalize_text
        self.df = self._load_all()
        logger.info(
            f"✅ CertMatcher : {len(self.df)} certifs unifiées "
            f"({self.df['body'].value_counts().to_dict() if not self.df.empty else {}})"
        )

    # ------------------------------------------------------------------ chargement
    def _load_all(self) -> pd.DataFrame:
        rows: List[dict] = []
        rows += self._load_snep()
        rows += self._load_brma()
        rows += self._load_riaa()
        if not rows:
            return pd.DataFrame(
                columns=[
                    "artist_clean",
                    "title_clean",
                    "cat",
                    "level",
                    "date",
                    "country",
                    "body",
                    "flag",
                    "artist_name",
                    "title",
                    "release_date",
                    "publisher",
                    "detail_url",
                ]
            )
        df = pd.DataFrame(rows)
        df["title_len"] = df["title_clean"].str.len()
        return df

    def _load_snep(self) -> List[dict]:
        db = Path(DATA_PATH) / "certifications" / "snep" / "certifications.db"
        if not db.exists():
            return []
        try:
            conn = sqlite3.connect(str(db))
            cur = conn.execute(
                "SELECT artist_name, artist_clean, title, title_clean, publisher, "
                "category, certification, release_date, certification_date "
                "FROM certifications"
            )
            rows = []
            for an, ac, ti, tc, pub, cat, lvl, rel, cdate in cur.fetchall():
                rows.append(
                    {
                        "artist_clean": ac or "",
                        "title_clean": tc or "",
                        "cat": _norm_cat(cat),
                        "level": lvl or "",
                        "date": (str(cdate)[:10] if cdate else ""),
                        "country": "FR",
                        "body": "SNEP",
                        "flag": "🇫🇷",
                        "artist_name": an or "",
                        "title": ti or "",
                        "release_date": (str(rel)[:10] if rel else ""),
                        "publisher": pub or "",
                        "detail_url": "",
                    }
                )
            conn.close()
            return rows
        except Exception as e:
            logger.error(f"CertMatcher: chargement SNEP impossible : {e}")
            return []

    def _load_brma(self) -> List[dict]:
        csv = Path(DATA_PATH) / "certifications" / "brma" / "certif_brma.csv"
        if not csv.exists():
            return []
        try:
            df = pd.read_csv(csv, encoding="utf-8-sig", dtype=str).fillna("")
        except Exception as e:
            logger.error(f"CertMatcher: chargement BRMA impossible : {e}")
            return []
        rows = []
        for _, r in df.iterrows():
            artist = str(r.get("artist", "")).strip()
            title = str(r.get("title", "")).strip()
            rows.append(
                {
                    "artist_clean": self._norm(artist),
                    "title_clean": self._norm(title),
                    "cat": _norm_cat(r.get("category", "")),
                    "level": str(r.get("certification_level", "")).strip(),
                    "date": str(r.get("certification_date", "")).strip()[:10],
                    "country": "BE",
                    "body": "BRMA",
                    "flag": "🇧🇪",
                    "artist_name": artist,
                    "title": title,
                    "release_date": "",
                    "publisher": "",
                    "detail_url": str(r.get("detail_url", "")).strip(),
                }
            )
        return rows

    def _load_riaa(self) -> List[dict]:
        """Charge les certifs RIAA (US). Fichier historique : certif_riaa.csv.

        Schéma toléré (insensible à la casse) : Artist, Title, Certification_Date
        (« October 17, 2017 »), Format_Type (SINGLE/ALBUM), Certification_Type
        (« 4x Multi-Platinum »), Label. Compatible aussi avec un futur schéma
        minuscule (award_level, format).
        """
        csv = Path(DATA_PATH) / "certifications" / "riaa" / "certif_riaa.csv"
        if not csv.exists():
            return []
        try:
            df = pd.read_csv(csv, encoding="utf-8-sig", dtype=str).fillna("")
        except Exception as e:
            logger.error(f"CertMatcher: chargement RIAA impossible : {e}")
            return []

        cmap = {c.lower(): c for c in df.columns}

        def col(row, *names, default=""):
            for n in names:
                if n in cmap:
                    return str(row.get(cmap[n], "")).strip()
            return default

        rows = []
        for _, r in df.iterrows():
            artist = col(r, "artist")
            title = col(r, "title")
            if not artist or not title:
                continue
            level = _riaa_level(col(r, "certification_type", "award_level", "certification_level"))
            rows.append(
                {
                    "artist_clean": self._norm(artist),
                    "title_clean": self._norm(title),
                    "cat": _norm_cat(col(r, "format_type", "format")),
                    "level": level,
                    "date": _to_iso_date(col(r, "certification_date")),
                    "country": "US",
                    "body": "RIAA",
                    "flag": "🇺🇸",
                    "artist_name": artist,
                    "title": title,
                    "release_date": _to_iso_date(col(r, "release_date")),
                    "publisher": col(r, "label"),
                    "detail_url": "",
                }
            )
        return rows

    # ------------------------------------------------------------------ matching
    def _level_rank(self, level: str) -> float:
        l = (level or "").strip().lower()
        if l in _RANK:
            return _RANK[l]
        m = re.match(r"(\d+)\s*x\s+(platine|platinum|or|gold|diamant|diamond)", l)
        if m:
            base = {"platine": 7, "platinum": 7, "or": 10, "gold": 10, "diamant": 4, "diamond": 4}[
                m.group(2)
            ]
            n = int(m.group(1))
            return base - min(n - 1, 9) * 0.1  # un cran au-dessus du palier simple
        return 99.0

    def _track_match_indices(self, a: str, t: str) -> List[int]:
        """Stratégies SNEP portées (exact → feat → featuring → tronqué)."""
        df = self.df
        if df.empty or not a:
            return []
        seen: List[int] = []
        sset = set()

        def add(sub):
            for idx in sub.index:
                if idx not in sset:
                    sset.add(idx)
                    seen.append(idx)

        ac = df["artist_clean"]
        tc = df["title_clean"]

        # Titre tout-symbole (ex: Ed Sheeran « ÷ », « = ») → normalize_text = ''.
        # On fait UNIQUEMENT un match exact (artiste + titre vide) ; surtout pas
        # de substring « LIKE %% » qui ramènerait TOUTE la disco (bug historique).
        if not t:
            add(df[(ac == a) & (tc == "")])
            return seen

        # S1 : exact (artiste+titre), sinon fuzzy substring
        s1 = df[(ac == a) & (tc == t)]
        if s1.empty:
            s1 = df[
                ac.str.contains(a, regex=False, na=False)
                & tc.str.contains(t, regex=False, na=False)
            ]
        add(s1)

        # S2 : si le titre contient un featuring, retenter avec la partie principale
        m = _FEAT_RE.match(t)
        if m:
            main = m.group(1).strip()
            s2 = df[(ac == a) & (tc == main)]
            if s2.empty and main:
                s2 = df[
                    ac.str.contains(a, regex=False, na=False)
                    & tc.str.contains(main, regex=False, na=False)
                ]
            add(s2)

        # S3 : l'artiste apparaît en featuring (substring artiste + titre)
        s3 = df[
            ac.str.contains(a, regex=False, na=False) & tc.str.contains(t, regex=False, na=False)
        ]
        add(s3)

        # S4 : titre de certif TRONQUÉ (préfixe du morceau) — en dernier recours
        if not seen and len(t) >= 8:
            cand = df[(ac == a) & df["title_len"].between(8, len(t) - 1)]
            if not cand.empty:
                cand = cand[
                    cand["title_clean"].apply(lambda x: isinstance(x, str) and t.startswith(x))
                ]
                add(cand)
        return seen

    def get_track_certifications(
        self, artist: str, title: str, extra_artists=None
    ) -> List[Dict[str, Any]]:
        """Toutes les certifs (tous pays) raccordées à ce morceau, triées.

        `extra_artists` : noms d'artistes supplémentaires à tester (ex:
        `primary_artist_name`, featurings) → permet de rattacher une certif
        déposée sous l'ARTISTE PRINCIPAL à un morceau où notre artiste n'est
        que secondaire/feat (cas « morceau secondaire »).
        """
        t = self._norm(title)
        candidates = [self._norm(artist)]
        for x in extra_artists or []:
            nx = self._norm(x)
            if nx and nx not in candidates:
                candidates.append(nx)

        idx, seen = [], set()
        for a in candidates:
            for i in self._track_match_indices(a, t):
                if i not in seen:
                    seen.add(i)
                    idx.append(i)
        return self._format(self.df.loc[idx]) if idx else []

    def get_artist_certifications(self, artist: str) -> List[Dict[str, Any]]:
        """Toutes les certifs (tous pays) de l'artiste (match substring, comme SNEP)."""
        a = self._norm(artist)
        df = self.df
        if df.empty or not a:
            return []
        return self._format(df[df["artist_clean"].str.contains(a, regex=False, na=False)])

    def get_album_certifications(self, artist: str, album: str) -> List[Dict[str, Any]]:
        """Certifs d'ALBUM (catégorie album) raccordées à cet album, tous pays."""
        a = self._norm(artist)
        t = self._norm(album)
        df = self.df
        if df.empty or not a or not t:
            return []
        alb = df[df["cat"] == "album"]
        exact = alb[(alb["artist_clean"] == a) & (alb["title_clean"] == t)]
        if exact.empty:
            exact = alb[
                alb["artist_clean"].str.contains(a, regex=False, na=False)
                & alb["title_clean"].str.contains(t, regex=False, na=False)
            ]
        return self._format(exact)

    # ------------------------------------------------------------------ sortie
    def _format(self, rows: pd.DataFrame) -> List[Dict[str, Any]]:
        out = []
        for _, r in rows.iterrows():
            out.append(
                {
                    "certification": r["level"],
                    "title": r["title"],
                    "artist_name": r["artist_name"],
                    "category": r["cat"],
                    "certification_date": r["date"],
                    "release_date": r.get("release_date", ""),
                    "publisher": r.get("publisher", ""),
                    "detail_url": r.get("detail_url", ""),
                    "country": r["country"],
                    "body": r["body"],
                    "flag": r["flag"],
                }
            )
        # Tri : pays (FR, BE, US) puis niveau puis date décroissante
        order = {"FR": 0, "BE": 1, "US": 2}
        out.sort(
            key=lambda c: (
                order.get(c["country"], 9),
                self._level_rank(c["certification"]),
                c["certification_date"] or "",
            ),
        )
        return out


_instance: Optional[CertMatcher] = None


def get_cert_matcher() -> CertMatcher:
    global _instance
    if _instance is None:
        _instance = CertMatcher()
    return _instance


def reset_cert_matcher() -> None:
    """Force un rechargement (après une MàJ de certifs)."""
    global _instance
    _instance = None
