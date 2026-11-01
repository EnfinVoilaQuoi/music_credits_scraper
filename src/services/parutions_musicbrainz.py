"""Comble les parutions que MusicBrainz n'a jamais vues : albums LIVE (2026-11-01).

`releases.mb_live` (e64) ne se pose que si l'on cherche la parution par son
code-barres chez MusicBrainz — fait jusqu'ici par l'étape Identité
(`spotify_parutions`) pour les seuls albums de ce run : 378 parutions sur
5 019, 112 codes-barres en base. Ce passage reprend les autres, une par une :

  ① le code-barres : celui de la base, sinon la fiche album Deezer (`upc`),
    posé sur la parution (`poser_upc_parution`) ;
  ② la recherche MusicBrainz par code-barres (`parutions_par_code_barres`,
    1 req/s, User-Agent, brut gardé par la session du client) ;
  ③ `noter_parution_musicbrainz` : datée, `mb_live`, et l'album Spotify que
    la parution lie si elle lie EXACTEMENT notre album Deezer (même garde que
    l'étape Identité, qui ne refera pas la recherche avant expiration).

Une parution déjà jugée (`mb_checked_at` ou `mb_live`) est sautée : le passage
REPREND là où il s'est arrêté. Une source qui ne RÉPOND plus arrête le passage
(`SansReponse`) : rien n'est daté sur une panne. Une fiche Deezer sans
code-barres n'est pas datée non plus (MusicBrainz n'a pas été interrogé).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from src.observability import source_usage
from src.observability.issues import SansReponse
from src.services.spotify_parutions import album_spotify_garde
from src.utils.codes_barres import cle_upc
from src.utils.logger import get_logger

logger = get_logger(__name__)

#: Une ligne de bilan toutes les N parutions (le chien de garde des lots).
PROGRESSION_TOUTES = 10


@dataclass
class BilanMusicBrainz:
    vues: int = 0
    upc_deezer: int = 0
    sans_upc: int = 0
    connues_mb: int = 0
    live: list[dict] = field(default_factory=list)
    arret: str | None = None


def code_barres_deezer(deezer, album_id: int) -> str | None:
    """Le code-barres de la fiche album Deezer, ou None si Deezer a RÉPONDU
    sans (album retiré, champ vide). Lève `SansReponse` si Deezer n'a pas
    répondu : une panne n'est pas une absence."""
    with source_usage.observe("deezer", label=f"album {album_id}", hors_morceau=True) as obs:
        data = deezer.get_album(int(album_id))
        upc = (data or {}).get("upc")
        if cle_upc(upc):
            obs.ok()
            return str(upc)
        if source_usage.a_repondu(obs):
            obs.absent("pas de code-barres")
    source_usage.exiger_reponse(obs)
    return None


def completer(
    dm,
    parutions: list[dict],
    *,
    deezer,
    mb,
    ecrire: bool,
    dire: Callable[[str], None] = print,
) -> BilanMusicBrainz:
    """①②③ pour chaque parution de `parutions`
    (`dm.parutions_sans_musicbrainz`). À blanc (`ecrire=False`), les sources
    sont lues mais rien n'est écrit."""
    bilan = BilanMusicBrainz()
    total = len(parutions)
    for rang, p in enumerate(parutions, 1):
        libelle = f"{p['artiste']} — « {p['title']} » (parution {p['id']})"
        try:
            upc = p.get("upc") if cle_upc(p.get("upc")) else None
            if upc is None:
                upc = code_barres_deezer(deezer, p["deezer_album_id"])
                if upc is None:
                    bilan.sans_upc += 1
                    dire(f"[{rang}/{total}] {libelle} : Deezer sans code-barres")
                    continue
                bilan.upc_deezer += 1
                if ecrire:
                    dm.poser_upc_parution(p["id"], upc)
            trouvees = mb.parutions_par_code_barres(upc)
        except SansReponse as e:
            bilan.arret = f"{libelle} : {e}"
            break
        except OSError as e:  # requests.RequestException en hérite : réseau coupé
            bilan.arret = f"{libelle} : {type(e).__name__} {e}"
            break
        bilan.vues += 1
        live = any(t.get("live") for t in trouvees) if trouvees else None
        if trouvees:
            bilan.connues_mb += 1
        if live:
            bilan.live.append({**p, "upc": upc})
        if ecrire:
            dm.noter_parution_musicbrainz(
                p["id"], p["artist_id"], album_spotify_garde(trouvees, p["deezer_album_id"]), live
            )
        etat = "LIVE" if live else ("studio" if trouvees else "inconnu de MusicBrainz")
        dire(f"[{rang}/{total}] {libelle} UPC {upc} : {etat}")
        if rang % PROGRESSION_TOUTES == 0:
            dire(
                f"… {rang}/{total} : {bilan.vues} vue(s), {bilan.connues_mb} connue(s) "
                f"de MusicBrainz, {len(bilan.live)} live"
            )
    return bilan


def fiches_studio_sur_live(dm, noms: list[str] | None = None) -> list:
    """Les cas de la famille d'audit `studio_sur_live` (tous artistes si None)."""
    from contextlib import closing

    from src.services import audit

    with closing(audit._connexion(dm.db_path)) as c:
        artistes = None
        if noms:
            lignes = c.execute(
                f"SELECT id FROM artists WHERE name IN ({','.join('?' * len(noms))})", noms
            ).fetchall()
            artistes = [r["id"] for r in lignes]
        return audit.studio_sur_live(c, audit._pistes(c, artistes))
