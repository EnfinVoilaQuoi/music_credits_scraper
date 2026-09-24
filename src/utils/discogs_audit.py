"""Relecture des DISQUES Discogs déjà rattachés aux morceaux (2026-09-23).

Pendant de `deezer_audit` : le balayage et le verdict sont les MÊMES pour le
rapport et pour la réparation (`scripts/audit_discogs_releases.py`), sinon on
retire ce que le rapport n'a jamais montré. Aucune règle ici — le verdict vient
de `discogs_identity.release_concorde`, le prédicat que la recherche par
morceau applique désormais à chaque candidat.

Avant ce prédicat, la recherche Discogs ne vérifiait jamais l'artiste du
disque trouvé : Django « Nuages » portait le disque de Django Reinhardt, et un
crédit photo en avait été importé. `tracks.discogs_id` est l'id du DISQUE : un
disque lu une fois vaut pour tous ses morceaux (cache par id), à 1 req/s.
"""

from __future__ import annotations

import time

from sqlalchemy import text

from src.utils.discogs_identity import release_concorde, titre_de_piste
from src.utils.logger import get_logger

logger = get_logger(__name__)


def lignes_a_verifier(engine, artiste: str | None = None, limite: int | None = None) -> list[dict]:
    """Les fiches portant un `discogs_id` (disque), avec ce qu'il faut pour juger."""
    filtre = "AND a.name = :artiste" if artiste else ""
    params = {"artiste": artiste} if artiste else {}
    requete = f"""
        SELECT t.id, t.title, t.discogs_id, t.is_featuring, t.primary_artist_name,
               t.artist_id, a.name AS artiste, a.discogs_id AS artist_discogs_id
          FROM tracks t JOIN artists a ON a.id = t.artist_id
         WHERE t.discogs_id IS NOT NULL {filtre}
         ORDER BY a.name, t.title
    """
    with engine.connect() as conn:
        lignes = [dict(r) for r in conn.execute(text(requete), params).mappings().all()]
    return lignes[:limite] if limite else lignes


def noms_acceptes_par_artiste(dm, lignes: list[dict]) -> dict[int, set[str]]:
    """`{artist_id: noms}` — formations et alias CONFIRMÉS, lus par le
    repository : un morceau de Shurik'n sur un disque d'IAM est à sa place."""
    out: dict[int, set[str]] = {}
    for ligne in lignes:
        aid = ligne["artist_id"]
        if aid is None or aid in out:
            continue
        out[aid] = dm.noms_des_formations(aid) | dm.noms_de_lartiste(aid, ligne["artiste"])
    return out


def artistes_de_la_piste(disque: dict, titre: str) -> list[tuple]:
    """Artistes de la piste de la tracklist qui porte ce titre (même
    normalisation que la recherche), `[]` si aucune. PURE."""
    cible = titre_de_piste(titre or "")
    for titre_piste, artistes in disque.get("pistes") or []:
        if titre_de_piste(titre_piste or "") == cible:
            return list(artistes)
    return []


def verdict_ligne(ligne: dict, disque: dict, noms_acceptes=()) -> bool:
    """Le disque est-il celui de la fiche ? PURE — `release_concorde` avec les
    critères de la ligne. Un FEAT accepte l'artiste principal, sans l'identité
    Discogs de l'artiste de la ligne (ce n'est pas son disque)."""
    featuring = bool(ligne["is_featuring"])
    noms = {ligne["artiste"], *noms_acceptes}
    if ligne["primary_artist_name"]:
        noms.add(ligne["primary_artist_name"])
    return release_concorde(
        disque.get("artistes") or [],
        artistes_de_la_piste(disque, ligne["title"]),
        noms,
        None if featuring else ligne["artist_discogs_id"],
    )


def verifier_lignes(
    lignes: list[dict],
    lire_disque,
    *,
    noms_par_artiste: dict | None = None,
    pause: float = 1.0,
    progression=None,
) -> dict:
    """Confronte chaque fiche à son disque et rend le rapport.

    `lire_disque(release_id) -> dict | None` (`DiscogsClient.lire_disque`) ;
    un disque n'est lu qu'UNE fois, la pause ne s'applique qu'aux vraies
    lectures. Un disque illisible ne conclut rien (`illisibles`).
    """
    noms_par_artiste = noms_par_artiste or {}
    cache: dict[int, dict | None] = {}
    rapport = {"verifies": 0, "illisibles": 0, "disques_lus": 0, "ecarts": []}
    for i, ligne in enumerate(lignes, start=1):
        rid = int(ligne["discogs_id"])
        if rid not in cache:
            if cache and pause:
                time.sleep(pause)
            cache[rid] = lire_disque(rid)
            rapport["disques_lus"] += 1
        disque = cache[rid]
        if progression:
            progression(i, len(lignes))
        if disque is None:
            rapport["illisibles"] += 1
            continue
        rapport["verifies"] += 1
        if verdict_ligne(ligne, disque, noms_par_artiste.get(ligne["artist_id"], ())):
            continue
        rapport["ecarts"].append(
            {
                "track_id": ligne["id"],
                "artiste": ligne["artiste"],
                "titre": ligne["title"],
                "discogs_id": rid,
                "disque": disque.get("titre"),
                "credite_a": [nom for _id, nom in disque.get("artistes") or []],
            }
        )
    return rapport
