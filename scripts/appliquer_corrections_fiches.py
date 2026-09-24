"""Applique `data/corrections/fiches.json` (étape 3 du plan streams, 2026-09-24).

Le fichier porte les décisions humaines par fiche ; il est aussi la MÉMOIRE que
relisent les producteurs (cf. `src/utils/corrections_fiches.py`). Ce script est
IDEMPOTENT : une action déjà faite est constatée, pas refaite.

Actions d'une entrée (toutes optionnelles) :
  supprimer: true                      fiche supprimée (et mémorisée comme telle)
  fusionner_dans: {désignation}        fiche absorbée par une autre (`merge_tracks`)
  retirer_id_spotify: [ids]            `clear_track_spotify_id` (+ mémoire du gate)
  departager_id_avec: {désignation}    ID partagé : l'identité Spotify décide ;
                                       le résultat est ÉCRIT dans le fichier (--apply)
  retirer_lien_youtube: url            `reject_youtube_link`
  lien_youtube: url                    `set_youtube_link` (source manual)
  inedit: true                         `record_unreleased`
  version_de / cover_de / remix_de / sample_de: {désignation}   relation
  tiers: {role, principal}             version d'un tiers (rôle secondaire)
  retirer_credits: [{nom, role}]       `forget_credit` (+ mémoire de save_track)

Désignation : {"genius_id": N}, sinon {"titre": …, "album": …} (fiche Deezer).

    python scripts/appliquer_corrections_fiches.py            # dry-run
    python scripts/appliquer_corrections_fiches.py --apply    # backup + écriture
"""

import argparse
import json
import sys

if sys.platform == "win32" and "pytest" not in sys.modules:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.utils import corrections_fiches as cf

_RELATIONS = {
    "version_de": "version_of",
    "cover_de": "cover_of",
    "remix_de": "remix_of",
    "sample_de": "samples",
}


class _Contexte:
    def __init__(self, dm, apply: bool):
        self.dm, self.apply = dm, apply
        self.fiches: dict[str, list] = {}

    def fiches_de(self, artiste: str):
        if artiste not in self.fiches:
            a = self.dm.get_artist_by_name(artiste)
            self.fiches[artiste] = self.dm.get_artist_tracks(a.id) if a else []
        return self.fiches[artiste]

    def trouver(self, artiste: str, designation: dict):
        """La fiche désignée, ou (None, motif)."""
        trouves = [
            t
            for t in self.fiches_de(artiste)
            if cf.designe(designation, genius_id=t.genius_id, titre=t.title, album=t.album)
        ]
        if len(trouves) == 1:
            return trouves[0], None
        return None, ("introuvable" if not trouves else f"{len(trouves)} fiches désignées")


def _ids_de(track) -> set[str]:
    return {track.spotify_id} - {None} | {e.spotify_id for e in track.spotify_id_entries or []}


def _principal(track) -> str:
    if track.is_featuring and track.primary_artist_name:
        return track.primary_artist_name
    return track.artist.name if track.artist else ""


def score_identite(track, identite: dict) -> tuple:
    """PUR. Ce qui rattache une fiche à l'identité d'un ID Spotify, du plus au
    moins fort. La DURÉE ne départage pas deux fiches qui partagent un ID : elle
    vient souvent de l'ID lui-même (ReccoBeats), les deux fiches ont la même.
      1. le contrôle d'identité ne la rejette pas (veto) ;
      2. son artiste PRINCIPAL est crédité par Spotify (« Pour de Vrai » d'Eech
         contre « Pour de vrai » d'A2H, Spotify ne crédite qu'A2H) ;
      3. il est le PREMIER crédité (« Selfish » : Spotify crédite Slum Village,
         John Legend, Kanye West — les deux fiches y ont leur principal) ;
      4. titre IDENTIQUE, casse comprise (Spotify sert « outside » : la fiche
         de 2016, pas « OUTSIDE » de 2025).
    """
    from src.utils.spotify_identity import identite_concorde
    from src.utils.title_matching import names_match_as_words

    concorde = identite_concorde(track, identite)[0]
    artistes = identite.get("artists") or []
    principal = any(names_match_as_words(a, _principal(track)) for a in artistes)
    premier = bool(artistes) and names_match_as_words(artistes[0], _principal(track))
    titre = (track.title or "").strip() == (identite.get("name") or "").strip()
    return (concorde, principal, premier, titre)


def departager(track, autre, lire_identite) -> list:
    """Pour chaque ID partagé : (id, fiche qui le PERD, motif). Une fiche ne
    garde l'ID que si son score est STRICTEMENT meilleur ; sinon on ne tranche
    pas (motif lisible, rien n'est touché)."""
    resultats = []
    for sid in sorted(_ids_de(track) & _ids_de(autre)):
        identite = lire_identite(sid)
        if identite is None:
            resultats.append((sid, None, "identité Spotify illisible"))
            continue
        a, b = score_identite(track, identite), score_identite(autre, identite)
        sert = (
            f"Spotify sert « {identite.get('name')} » de {', '.join(identite.get('artists') or [])}"
        )
        if a > b:
            resultats.append((sid, autre, sert))
        elif b > a:
            resultats.append((sid, track, sert))
        else:
            resultats.append(
                (sid, None, f"{sert} — les deux fiches concordent autant : à trancher")
            )
    return resultats


def appliquer(ctx: _Contexte, artiste: str, entree: dict, lire_identite, journal: list) -> None:
    track, motif = ctx.trouver(artiste, entree.get("fiche") or {})
    nom = f"{artiste} — {entree.get('fiche')}"
    if track is None:
        if motif == "introuvable" and (entree.get("fusionner_dans") or entree.get("supprimer")):
            journal.append(f"   ✓ {nom} : déjà appliquée (fiche absorbée ou supprimée)")
        else:
            journal.append(f"   ⚠️ {nom} : {motif}")
        return
    nom = f"{artiste} — « {track.title} » ({track.id})"
    dm, apply = ctx.dm, ctx.apply

    def fait(texte):
        journal.append(f"   {'✔' if apply else '·'} {nom} : {texte}")

    if entree.get("supprimer"):
        if apply:
            from src.utils.deleted_tracks_manager import DeletedTracksManager

            DeletedTracksManager().add_deleted(artiste, track.genius_id, track.title)
            dm.delete_track(track.id)
            ctx.fiches.pop(artiste, None)
        fait("supprimée")
        return
    if entree.get("fusionner_dans"):
        garde, m = ctx.trouver(artiste, entree["fusionner_dans"])
        if garde is None:
            journal.append(f"   ⚠️ {nom} : fiche gardée {m}")
            return
        if garde.id == track.id:
            # Garde-fou : fusionner une fiche dans elle-même la SUPPRIME.
            journal.append(f"   ⚠️ {nom} : la fiche serait sa propre cible — rien fait")
            return
        if apply:
            dm.merge_tracks(garde.id, track.id)
            ctx.fiches.pop(artiste, None)
        fait(f"fusionnée dans « {garde.title} » ({garde.id})")
        return
    for sid in entree.get("retirer_id_spotify") or []:
        if sid in _ids_de(track):
            if apply:
                dm.clear_track_spotify_id(track.id, sid)
            fait(f"ID Spotify {sid} retiré")
    if entree.get("departager_id_avec"):
        autre, m = ctx.trouver(artiste, entree["departager_id_avec"])
        if autre is None:
            journal.append(f"   ⚠️ {nom} : fiche à départager {m}")
        for sid, perdant, raison in departager(track, autre, lire_identite) if autre else []:
            if perdant is None:
                journal.append(f"   ❓ {nom} : ID {sid} — {raison}")
                continue
            if apply:
                dm.clear_track_spotify_id(perdant.id, sid)
                _memoriser_refus(artiste, perdant, sid)
            fait(f"ID {sid} retiré de « {perdant.title} » ({perdant.id}) — {raison}")
    if entree.get("retirer_lien_youtube"):
        if apply:
            from src.utils.youtube_integration import reject_youtube_link

            reject_youtube_link(dm, track, entree["retirer_lien_youtube"], artiste)
        fait(f"lien YouTube rejeté : {entree['retirer_lien_youtube']}")
    if entree.get("lien_youtube"):
        if apply:
            from src.utils.youtube_integration import set_youtube_link

            set_youtube_link(dm, track, entree["lien_youtube"], source="manual")
        fait(f"lien YouTube : {entree['lien_youtube']}")
    if entree.get("inedit") and not track.unreleased:
        if apply:
            dm.record_unreleased(track.id, True)
        fait("marquée inédite")
    rels = list(track.relationships or [])
    for cle, type_rel in _RELATIONS.items():
        if not entree.get(cle):
            continue
        cible, m = ctx.trouver(artiste, entree[cle])
        if cible is None:
            journal.append(f"   ⚠️ {nom} : cible de « {cle} » {m}")
            continue
        if any(r.get("type") == type_rel and r.get("track_id") == cible.id for r in rels):
            continue
        rels.append(
            {
                "type": type_rel,
                "title": cible.title,
                "artist": cible.primary_artist_name or artiste,
                "url": cible.genius_url,
                "track_id": cible.id,
            }
        )
        fait(f"{type_rel} → « {cible.title} » ({cible.id})")
    if apply and rels != list(track.relationships or []):
        dm.record_relationships(track.id, rels)
    tiers = entree.get("tiers")
    if tiers and (track.secondary_role, track.primary_artist_name) != (
        tiers["role"],
        tiers["principal"],
    ):
        if apply:
            dm.record_relation_artiste(
                track.id,
                is_featuring=True,
                primary_artist_name=tiers["principal"],
                secondary_role=tiers["role"],
            )
        fait(f"version d'un tiers : {tiers['role']} de {tiers['principal']}")
    for c in entree.get("retirer_credits") or []:
        n = dm.forget_credit(track.id, c["nom"], c["role"]) if apply else "?"
        fait(f"crédit retiré : {c['nom']} — {c['role']} ({n})")


def _memoriser_refus(artiste: str, perdant, sid: str) -> None:
    """Le résultat d'un départage devient une décision EXPLICITE du fichier."""
    donnees = json.loads(cf.FICHIER.read_text(encoding="utf-8"))
    designation = (
        {"genius_id": perdant.genius_id}
        if perdant.genius_id
        else {"titre": perdant.title, "album": perdant.album}
    )
    entrees = donnees.setdefault(artiste, [])
    cible = next((e for e in entrees if e.get("fiche") == designation), None)
    if cible is None:
        cible = {"fiche": designation}
        entrees.append(cible)
    refus = cible.setdefault("retirer_id_spotify", [])
    if sid not in refus:
        refus.append(sid)
    cf.FICHIER.write_text(json.dumps(donnees, ensure_ascii=False, indent=2) + "\n", "utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="écrire (défaut : dry-run)")
    args = parser.parse_args()

    from src.utils.data_manager import DataManager
    from src.utils.spotify_identity import lire_identite_http

    dm = DataManager()
    if args.apply:
        from src.utils.database_backup import get_backup_manager

        print(f"💾 Backup : {get_backup_manager().create_backup('before_corrections_fiches')}")
    ctx = _Contexte(dm, args.apply)
    for artiste, entrees in cf.charger().items():
        journal: list[str] = []
        for entree in entrees:
            appliquer(ctx, artiste, entree, lire_identite_http, journal)
        if journal:
            print(f"== {artiste}")
            print("\n".join(journal))
    if not args.apply:
        print("\n(dry-run — relancer avec --apply pour écrire)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
