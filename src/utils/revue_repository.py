"""Persistance du panneau « À trancher » (e38, 2026-09-27).

Deux magasins, deux notions :
- les VERDICTS de l'utilisateur (« c'est normal ») — un cas tranché n'est plus
  reproposé tant que ses preuves ne changent pas (la clé du cas porte leur
  empreinte) ;
- les SIGNALEMENTS des runs (oracles réseau : Kworb, Deezer, Spotify), que
  chaque run REMPLACE pour l'artiste traité — il connaît la vérité du moment ;
- le JOURNAL des corrections automatiques (e39) : ce que les détecteurs
  FORMELS ont corrigé seuls, avec de quoi le défaire.

Requiert `self.engine` (moteur SQLAlchemy Core), comme les autres repositories.
"""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import delete, insert, select, update

from src.persistence.schema import revue_corrections, revue_signalements, revue_verdicts

VERDICT_NORMAL = "normal"


class RevueRepository:
    def verdicts_revue(self, artist_id: int) -> dict[tuple[str, str], dict]:
        """`{(détecteur, clé): {verdict, note, morceau, motif, decided_at}}`."""
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(revue_verdicts).where(revue_verdicts.c.artist_id == artist_id)
            ).mappings()
            return {(r["detecteur"], r["cle"]): dict(r) for r in rows}

    def trancher_cas(
        self,
        artist_id: int,
        detecteur: str,
        cle: str,
        *,
        verdict: str = VERDICT_NORMAL,
        morceau: str | None = None,
        motif: str | None = None,
        note: str | None = None,
    ) -> None:
        """Mémorise un verdict (le remplace s'il existait)."""
        with self.engine.begin() as conn:
            conn.execute(
                delete(revue_verdicts).where(
                    (revue_verdicts.c.artist_id == artist_id)
                    & (revue_verdicts.c.detecteur == detecteur)
                    & (revue_verdicts.c.cle == cle)
                )
            )
            conn.execute(
                insert(revue_verdicts).values(
                    artist_id=artist_id,
                    detecteur=detecteur,
                    cle=cle,
                    verdict=verdict,
                    morceau=morceau,
                    motif=motif,
                    note=note,
                    decided_at=datetime.now(),
                )
            )

    def annuler_verdict(self, artist_id: int, detecteur: str, cle: str) -> bool:
        """Retire un verdict : le cas redevient à trancher."""
        with self.engine.begin() as conn:
            return (
                conn.execute(
                    delete(revue_verdicts).where(
                        (revue_verdicts.c.artist_id == artist_id)
                        & (revue_verdicts.c.detecteur == detecteur)
                        & (revue_verdicts.c.cle == cle)
                    )
                ).rowcount
                > 0
            )

    def remplacer_signalements(self, artist_id: int, detecteur: str, cas) -> int:
        """Remplace TOUS les signalements d'un détecteur de run pour l'artiste
        (le run vient de tout revoir). `cas` : objets portant `cle`,
        `track_id`, `morceau`, `motif`, `preuves`, `impact`. Rend le nombre écrit."""
        maintenant = datetime.now()
        lignes = {}
        for c in cas:
            lignes[c.cle] = {
                "artist_id": artist_id,
                "detecteur": detecteur,
                "cle": c.cle,
                "track_id": c.track_id,
                "morceau": c.morceau,
                "motif": c.motif,
                "preuves": json.dumps(c.preuves, ensure_ascii=False, default=str),
                "impact": c.impact,
                "seen_at": maintenant,
            }
        with self.engine.begin() as conn:
            conn.execute(
                delete(revue_signalements).where(
                    (revue_signalements.c.artist_id == artist_id)
                    & (revue_signalements.c.detecteur == detecteur)
                )
            )
            if lignes:
                conn.execute(insert(revue_signalements), list(lignes.values()))
        return len(lignes)

    def signalements_revue(self, artist_id: int) -> list[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(revue_signalements).where(revue_signalements.c.artist_id == artist_id)
            ).mappings()
            sortie = []
            for r in rows:
                d = dict(r)
                try:
                    d["preuves"] = json.loads(d["preuves"] or "{}")
                except json.JSONDecodeError:
                    d["preuves"] = {}
                sortie.append(d)
            return sortie

    def retirer_signalement(self, artist_id: int, detecteur: str, cle: str) -> bool:
        """Un signalement TRAITÉ par une action du panneau quitte la liste sans
        attendre le prochain passage du run."""
        with self.engine.begin() as conn:
            return (
                conn.execute(
                    delete(revue_signalements).where(
                        (revue_signalements.c.artist_id == artist_id)
                        & (revue_signalements.c.detecteur == detecteur)
                        & (revue_signalements.c.cle == cle)
                    )
                ).rowcount
                > 0
            )

    # ── Journal des corrections automatiques (e39) ──────────────────────────

    def journaliser_correction(
        self,
        artist_id: int,
        cas,
        action: str,
        compte_rendu: str,
        annulation: dict,
    ) -> int:
        """Consigne une correction faite SANS l'utilisateur. Rend son id."""
        with self.engine.begin() as conn:
            return conn.execute(
                insert(revue_corrections).values(
                    artist_id=artist_id,
                    detecteur=cas.detecteur,
                    cle=cas.cle,
                    track_id=cas.track_id,
                    morceau=cas.morceau,
                    motif=cas.motif,
                    action=action,
                    compte_rendu=compte_rendu,
                    annulation=json.dumps(annulation, ensure_ascii=False, default=str),
                    applied_at=datetime.now(),
                )
            ).inserted_primary_key[0]

    def corrections_revue(self, artist_id: int, *, retablies: bool = False) -> list[dict]:
        """Le journal de l'artiste, le plus récent d'abord (sans les corrections
        déjà défaites, sauf `retablies=True`)."""
        requete = select(revue_corrections).where(revue_corrections.c.artist_id == artist_id)
        if not retablies:
            requete = requete.where(revue_corrections.c.retablie_at.is_(None))
        with self.engine.connect() as conn:
            sortie = []
            for r in conn.execute(requete.order_by(revue_corrections.c.id.desc())).mappings():
                d = dict(r)
                try:
                    d["annulation"] = json.loads(d["annulation"] or "{}")
                except json.JSONDecodeError:
                    d["annulation"] = {}
                sortie.append(d)
            return sortie

    def marquer_correction_retablie(self, correction_id: int) -> bool:
        with self.engine.begin() as conn:
            return (
                conn.execute(
                    update(revue_corrections)
                    .where(revue_corrections.c.id == correction_id)
                    .values(retablie_at=datetime.now())
                ).rowcount
                > 0
            )
