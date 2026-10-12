"""Mémoire des décisions de rapprochement Kworb ↔ base (par artiste).

Pour les titres Kworb qui ressemblent à un morceau en base sans correspondre
exactement (« Matrix » vs « Matrix (Intro) »), l'utilisateur confirme ou rejette
UNE FOIS ; la décision est mémorisée pour ne plus redemander aux runs suivants.

Vue rendue par `load` (inchangée pour les appelants) :
    {
      "confirmed": { "<titre kworb normalisé>": <track_id> },
      "rejected":  [ "<titre kworb normalisé>", ... ],
      "decisions": { "<titre kworb normalisé>": {"kind": "...", "track_id": <id|null>} }
    }
`decisions` (2026-09-21) porte la taxonomie des variantes — rendition / existant
/ collab / tiers / ignore — et l'emporte sur les deux autres.
Clé = titre Kworb normalisé (via title_matching.normalize_title) pour être
stable entre les runs malgré ponctuation/casse.

Stockage : EN BASE (table `kworb_decisions`, e50 — chantier C lot 3,
2026-10-12) dès qu'une base est ouverte (`moteur`, posé par `DataManager`).
Les fichiers `data/kworb_links/<artiste>.json` — ignorés par git, donc ni
versionnés ni sauvegardés avec la base — ne servent plus que sans base, et de
source à `migrer_vers_la_base`.
"""

import re
import shutil
from datetime import datetime
from pathlib import Path

from src.config import DATA_DIR
from src.utils.fichiers_json import MemoireIllisible, ecrire_atomique, lire_memoire
from src.utils.logger import get_logger
from src.utils.title_matching import normalize_title

logger = get_logger(__name__)

#: Moteur de la base, posé par `DataManager` à sa construction (même motif que
#: `corrections_fiches.moteur`).
moteur = None


def _vide() -> dict:
    return {"confirmed": {}, "rejected": [], "decisions": {}}


class KworbLinksManager:
    def __init__(self, base_dir: str | None = None, *, moteur_base=None):
        # Ancré sur DATA_DIR : relatif au dossier courant, un lancement depuis
        # ailleurs lisait et écrivait une mémoire FANTÔME (même piège que BRMA).
        self.base_dir = Path(base_dir) if base_dir else Path(DATA_DIR) / "kworb_links"
        # Un dossier DONNÉ explicitement vaut stockage fichier (tests, migration).
        self.moteur = moteur_base if moteur_base is not None else (None if base_dir else moteur)
        if self.moteur is None:
            self.base_dir.mkdir(parents=True, exist_ok=True)

    # ── Lecture / écriture ─────────────────────────────────────────────────────

    def load(self, artist_name: str) -> dict:
        if self.moteur is not None:
            return self._load_base(artist_name)
        return self._load_fichier(artist_name)

    def confirm(self, artist_name: str, kworb_title: str, track_id: int):
        norm = normalize_title(kworb_title)
        if self.moteur is not None:
            self._ecrire_base(artist_name, norm, "lien", "confirme", track_id)
        else:
            data = self._load_fichier(artist_name)
            data["confirmed"][norm] = track_id
            if norm in data["rejected"]:
                data["rejected"].remove(norm)
            self._save(artist_name, data)
        logger.info(f"🔗 Kworb décision mémorisée : '{kworb_title}' → track #{track_id}")

    def reject(self, artist_name: str, kworb_title: str):
        norm = normalize_title(kworb_title)
        if self.moteur is not None:
            self._ecrire_base(artist_name, norm, "lien", "rejete", None)
        else:
            data = self._load_fichier(artist_name)
            if norm not in data["rejected"]:
                data["rejected"].append(norm)
            data["confirmed"].pop(norm, None)
            self._save(artist_name, data)
        logger.info(f"🚫 Kworb décision mémorisée : '{kworb_title}' rejeté")

    def decide(self, artist_name: str, kworb_title: str, kind: str, track_id: int | None = None):
        """Décision de la taxonomie des variantes (2026-09-21), mémorisée par titre.

        `kind` ∈ rendition (rattachée au morceau `track_id`) · existant / collab /
        tiers (la ligne Kworb appartient au morceau `track_id`, existant ou créé)
        · ignore. Elle REMPLACE un rejet ou une confirmation antérieurs : ces
        deux-là répondaient à une autre question (« même morceau ? »).
        """
        norm = normalize_title(kworb_title)
        if self.moteur is not None:
            self._ecrire_base(artist_name, norm, "decision", kind, track_id, efface_lien=True)
        else:
            data = self._load_fichier(artist_name)
            data["decisions"][norm] = {"kind": kind, "track_id": track_id}
            if norm in data["rejected"]:
                data["rejected"].remove(norm)
            data["confirmed"].pop(norm, None)
            self._save(artist_name, data)
        logger.info(f"🔗 Kworb décision mémorisée : '{kworb_title}' → {kind} (#{track_id})")

    # ── En base ────────────────────────────────────────────────────────────────

    def _load_base(self, artist_name: str) -> dict:
        from sqlalchemy import text
        from sqlalchemy.exc import SQLAlchemyError

        data = _vide()
        try:
            with self.moteur.connect() as conn:
                lignes = conn.execute(
                    text(
                        "SELECT titre, nature, statut, track_id FROM kworb_decisions "
                        "WHERE artiste = :a ORDER BY id"
                    ),
                    {"a": artist_name},
                ).all()
        except SQLAlchemyError as e:
            # Lu comme vide pour décider ; les écritures, elles, échoueront fort.
            logger.error(f"Décisions Kworb illisibles en base ({artist_name}) : {e}")
            return data
        for titre, nature, statut, track_id in lignes:
            if nature == "decision":
                data["decisions"][titre] = {"kind": statut, "track_id": track_id}
            elif statut == "confirme":
                data["confirmed"][titre] = track_id
            else:
                data["rejected"].append(titre)
        return data

    def _ecrire_base(
        self, artiste, titre, nature, statut, track_id, *, efface_lien=False, conn=None
    ):
        from sqlalchemy import text

        params = {
            "a": artiste,
            "t": titre,
            "n": nature,
            "s": statut,
            "tid": track_id,
            "le": datetime.now().isoformat(timespec="seconds"),
        }
        upsert = text(
            "INSERT INTO kworb_decisions (artiste, titre, nature, statut, track_id, decide_le) "
            "VALUES (:a, :t, :n, :s, :tid, :le) ON CONFLICT (artiste, titre, nature) "
            "DO UPDATE SET statut = excluded.statut, track_id = excluded.track_id, "
            "decide_le = excluded.decide_le"
        )

        def ecrire(c):
            c.execute(upsert, params)
            if efface_lien:
                c.execute(
                    text(
                        "DELETE FROM kworb_decisions WHERE artiste = :a AND titre = :t "
                        "AND nature = 'lien'"
                    ),
                    params,
                )

        if conn is not None:
            ecrire(conn)
        else:
            with self.moteur.begin() as c:
                ecrire(c)

    # ── En fichier (sans base, et source de la migration) ─────────────────────

    def _path(self, artist_name: str) -> Path:
        safe = re.sub(r"[^\w\- ]", "_", artist_name or "artist")
        return self.base_dir / f"{safe}.json"

    def _load_fichier(self, artist_name: str) -> dict:
        try:
            data = lire_memoire(self._path(artist_name))
        except MemoireIllisible as e:
            # Lu comme vide pour décider, mais JAMAIS réécrit : la première
            # décision mémorisée effaçait toutes les autres (2026-10-11).
            logger.error(f"Décisions Kworb illisibles, elles ne seront pas réécrites : {e}")
            data = {"_illisible": True}
        data.setdefault("confirmed", {})
        data.setdefault("rejected", [])
        data.setdefault("decisions", {})
        return data

    def _save(self, artist_name: str, data: dict):
        if data.get("_illisible"):
            logger.error(f"Décisions Kworb de {artist_name} illisibles : rien n'est écrit")
            return
        try:
            ecrire_atomique(self._path(artist_name), data, indent=1)
        except (OSError, TypeError) as e:
            logger.error(f"Sauvegarde décisions Kworb échouée ({artist_name}): {e}")


def migrer_vers_la_base(base_dir: str | None = None, moteur_base=None) -> dict[str, int]:
    """Verse les fichiers `kworb_links/*.json` dans `kworb_decisions`, puis les
    DÉPLACE dans `kworb_links.avant_base_<horodatage>/` (décision utilisateur
    2026-10-12 : comme les refus — retirés, copie gardée). Le nom d'artiste est
    celui du fichier, retrouvé parmi `artistes` connus de la base. Un fichier
    illisible ou d'artiste inconnu reste en place. IDEMPOTENT. Rend les comptes."""
    m = moteur_base if moteur_base is not None else moteur
    if m is None:
        raise RuntimeError("aucune base ouverte (construire un DataManager d'abord)")
    source = KworbLinksManager(base_dir or str(Path(DATA_DIR) / "kworb_links"))
    fichiers = sorted(source.base_dir.glob("*.json"))
    if not fichiers:
        return {}
    from sqlalchemy import text

    with m.connect() as conn:
        noms = [r[0] for r in conn.execute(text("SELECT name FROM artists"))]
    par_fichier = {source._path(n).name: n for n in noms}
    comptes = {"confirme": 0, "rejete": 0, "decision": 0, "fichiers": 0}
    cible = KworbLinksManager(moteur_base=m)
    a_deplacer = []
    with m.begin() as conn:
        for f in fichiers:
            artiste = par_fichier.get(f.name)
            if artiste is None:
                logger.error(f"Décisions Kworb : artiste introuvable pour {f.name} — laissé")
                continue
            data = source._load_fichier(artiste)
            if data.get("_illisible"):
                continue
            for titre, tid in data["confirmed"].items():
                cible._ecrire_base(artiste, titre, "lien", "confirme", tid, conn=conn)
                comptes["confirme"] += 1
            for titre in data["rejected"]:
                cible._ecrire_base(artiste, titre, "lien", "rejete", None, conn=conn)
                comptes["rejete"] += 1
            for titre, d in data["decisions"].items():
                cible._ecrire_base(
                    artiste, titre, "decision", d.get("kind"), d.get("track_id"), conn=conn
                )
                comptes["decision"] += 1
            comptes["fichiers"] += 1
            a_deplacer.append(f)
    if a_deplacer:
        copie = source.base_dir.with_name(
            f"{source.base_dir.name}.avant_base_{datetime.now():%Y%m%d_%H%M%S}"
        )
        copie.mkdir(parents=True)
        for f in a_deplacer:
            shutil.move(str(f), copie / f.name)
        logger.info(f"🗂️ Décisions Kworb versées en base : {comptes} (copie : {copie.name})")
    return comptes
