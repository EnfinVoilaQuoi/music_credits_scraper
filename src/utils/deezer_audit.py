"""Relecture des identifiants Deezer déjà en base, confrontés à `/track/{id}`.

Pendant de `spotify_audit`, pour la même raison : le balayage et le verdict
doivent être les MÊMES pour le rapport et pour la réparation
(`scripts/repair_deezer_ids.py`), sinon on retire ce que le rapport n'a jamais
montré. Aucune règle ici : le verdict vient de `deezer_identity.hit_concorde`.

Mesuré le 2026-09-22 sur 60 fiches tirées au sort : 3 ids (5 %) désignaient
« Rolling 200 Deep » de DJ Kay Slay (Kanye « I Got a Love », « The Joy », Kid
Cudi « Run ») — le repli par id d'artiste prenait le premier hit de l'artiste
sans regarder le titre.
"""

import time

from sqlalchemy import text

from src.utils.logger import get_logger

logger = get_logger(__name__)


def lignes_a_verifier(engine, artiste: str | None = None, limite: int | None = None) -> list[dict]:
    """Les fiches portant un `deezer_id`, avec ce qu'il faut pour juger.

    `duree_independante` = une durée arbitrée venue d'AILLEURS que Deezer ou
    `legacy` (songbpm, ytmusic, spotify_web, reccobeats, manual). C'est la
    seule qui puisse contredire l'id : une durée écrite PAR ce hit le
    validerait par sa propre conséquence.
    """
    filtre = "AND a.name = :artiste" if artiste else ""
    params = {"artiste": artiste} if artiste else {}
    requete = f"""
        SELECT t.id, t.title, t.deezer_id, t.is_featuring, t.primary_artist_name,
               t.artist_id, a.name AS artiste, a.deezer_id AS artist_deezer_id,
               (SELECT o.value FROM observations o
                 WHERE o.track_id = t.id AND o.field = 'duration'
                   AND o.source NOT IN ('deezer', 'legacy')
                 ORDER BY o.source LIMIT 1) AS duree_independante
          FROM tracks t JOIN artists a ON a.id = t.artist_id
         WHERE t.deezer_id IS NOT NULL {filtre}
         ORDER BY a.name, t.title
    """
    with engine.connect() as conn:
        lignes = [dict(r) for r in conn.execute(text(requete), params).mappings().all()]
    return lignes[:limite] if limite else lignes


def noms_acceptes_par_artiste(dm, lignes: list[dict]) -> dict[int, set[str]]:
    """`{artist_id: noms}` — formations et alias CONFIRMÉS, lus par le
    repository (`noms_des_formations` / `noms_de_lartiste`, les mêmes que la
    discographie réunie et les certifs) : pas de seconde requête maison."""
    out: dict[int, set[str]] = {}
    for ligne in lignes:
        aid = ligne.get("artist_id")
        if aid is None or aid in out:
            continue
        out[aid] = dm.noms_des_formations(aid) | dm.noms_de_lartiste(aid, ligne["artiste"])
    return out


def criteres_de_la_ligne(ligne: dict, noms_acceptes=()) -> dict:
    """Les arguments de `hit_concorde` pour cette fiche.

    Un FEATURING se juge sur l'artiste PRINCIPAL (c'est lui que Deezer crédite
    en `artist`), donc sans l'id de l'artiste de la ligne — même règle que le
    provider (`_artist_deezer_id`). La fiche `/track/{id}` porte les
    `contributors` : le gate y cherche aussi.
    """
    featuring = bool(ligne["is_featuring"]) and bool(ligne["primary_artist_name"])
    return {
        "artist_name": ligne["primary_artist_name"] if featuring else ligne["artiste"],
        "title": ligne["title"],
        "previous_duration": ligne["duree_independante"],
        "artist_deezer_id": None if featuring else ligne["artist_deezer_id"],
        "noms_acceptes": tuple(sorted(noms_acceptes)),
    }


def verifier_lignes(
    lignes: list[dict],
    lire_piste=None,
    *,
    noms_par_artiste: dict | None = None,
    pause: float = 0.2,
    progression=None,
    interrompu=None,
) -> dict:
    """Confronte chaque fiche à l'oracle et rend le rapport.

    `lire_piste` est INJECTÉ (`lire_piste_http` en production, un double en
    test). Une fiche illisible n'accuse personne : comptée à part, comme
    `absent`. `interrompu` est testé ENTRE deux requêtes.
    """
    from src.utils.deezer_identity import (
        artiste_etranger,
        hit_concorde,
        lire_piste_http,
        variante_etrangere,
    )

    lire_piste = lire_piste or lire_piste_http
    ecarts: list[dict] = []
    illisibles = faits = 0
    for ligne in lignes:
        if interrompu is not None and interrompu():
            break
        fiche = lire_piste(int(ligne["deezer_id"]))
        faits += 1
        if fiche is None:
            illisibles += 1
        else:
            criteres = criteres_de_la_ligne(
                ligne, (noms_par_artiste or {}).get(ligne.get("artist_id"), ())
            )
            ok, motif = hit_concorde(fiche, **criteres)
            if not ok:
                ecarts.append(
                    {
                        "track_id": ligne["id"],
                        "artiste": ligne["artiste"],
                        "titre": ligne["title"],
                        "deezer_id": int(ligne["deezer_id"]),
                        "motif": motif,
                        "artiste_etranger": artiste_etranger(
                            fiche,
                            artist_name=criteres["artist_name"],
                            artist_deezer_id=criteres["artist_deezer_id"],
                            noms_acceptes=criteres["noms_acceptes"],
                        ),
                        "variante_etrangere": variante_etrangere(fiche, title=ligne["title"]),
                        "deezer": {
                            "title": fiche.get("title"),
                            "artist": (fiche.get("artist") or {}).get("name"),
                            "duration": fiche.get("duration"),
                        },
                    }
                )
        if progression is not None:
            progression(faits, len(lignes))
        if pause:
            time.sleep(pause)
    return {"verifies": faits - illisibles, "illisibles": illisibles, "ecarts": ecarts}
