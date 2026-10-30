"""Garde STRUCTURELLE du chantier A : un écrivain de `TrackRepository` ouvre sa
transaction par `_ecrire()`.

`_ecrire` recalcule, avant le commit, les colonnes dérivées (durée, date, ISRC,
streams, total YTM, `has_lyrics`, LRC, `is_primary`) de chaque fiche touchée,
de ses sœurs et des autres porteurs de ses vidéos. La bascule s'est faite en
opt-in, écrivain par écrivain : la revue du 2026-10-19 a compté 30 écrivains
restés sur `self.engine.begin()` sans que rien ne l'interdise — et deux
invariants d'audit avaient régressé entre-temps.

Ce crible interdit le MOTIF : toute méthode qui ouvre elle-même une transaction
(`.begin()`, `.commit()`) passe par `_ecrire`, sauf une liste FERMÉE
d'exceptions, chacune avec sa raison. Une méthode qui reçoit `conn` écrit dans
la transaction de son appelant : elle n'est pas concernée.

Ce que `_ecrire` ne fait PAS : propager une colonne partagée aux sœurs (c'est
`synchroniser_soeurs`, dans `save_track`) ni la retirer chez elles
(`effacer_chez_les_soeurs`, explicite).
"""

import ast
from pathlib import Path

FICHIER = Path(__file__).resolve().parents[1] / "src" / "utils" / "track_repository.py"

_PARUTIONS = (
    "tables de parutions (`releases*`, `release_tracks`) : aucune colonne de fiche, "
    "aucun verdict n'en dépend"
)
_ALBUMS = "table `albums` (streams et nature du disque) : aucune colonne de fiche"
_CREDITS = "table `credits` : aucune colonne dérivée n'en dépend"
_LIGNE = "donnée PROPRE à la ligne d'un artiste (hors `COLONNES_PARTAGEES`), sans verdict dérivé"
_RECALCUL = "c'est lui-même un RECALCUL (écrit des verdicts depuis les faits, n'en déplace aucun)"

#: Méthodes qui ouvrent leur propre transaction HORS `_ecrire`. Liste FERMÉE :
#: une entrée nouvelle dit pourquoi le recalcul n'a rien à faire, pas seulement
#: qu'il « n'est pas utile ».
EXCEPTIONS: dict[str, str] = {
    "_ecrire": "le mécanisme lui-même",
    # ── Parutions ──
    "confirm_release_suggestion": _PARUTIONS,
    "ensure_release": _PARUTIONS,
    "fusionner_parutions_meme_upc": _PARUTIONS + " (réparation par lots, passage à blanc)",
    "link_track_to_release": _PARUTIONS,
    "noter_parution_apple": _PARUTIONS,
    "noter_parution_musicbrainz": _PARUTIONS,
    "record_release_observations": _PARUTIONS,
    # ── Albums ──
    "set_album_record_type": _ALBUMS,
    "update_album_ytm_streams": _ALBUMS,
    "upsert_album": _ALBUMS,
    # ── Crédits ──
    "forget_credit": _CREDITS,
    "forget_discogs_credits": _CREDITS,
    "purger_credits_relations_deguisees": _CREDITS + " (purge par lots)",
    "reclasser_credits": _CREDITS + " (reclassement par lots)",
    # ── Données propres à la ligne ──
    # (Titre, album repère et interprète principal n'en sont PLUS, 2026-10-29 :
    # la durée lit le titre, le total YTM et les streams lisent titre, album et
    # principal — `rename_track`, `clear_track_album`, `renommer_album`,
    # `set_track_reference_release`, `unlink_track_from_release` et
    # `record_relation_artiste` passent par `_ecrire`.)
    "record_certifications": _LIGNE + " (certifs : refus et rattachement à la ligne)",
    "record_secondary_role": _LIGNE + " (rôle de CET artiste ; aucun verdict ne le lit)",
    # ── Recalculs ──
    # (`rearbitrer_duree_et_date` et `combler_isrc_vides` passent par `_ecrire`
    # depuis le 2026-10-29 : un recalcul partiel laissait les sœurs en retard.)
    "rematerialiser": _RECALCUL + " (appelle `_rematerialiser_fiche`, passage à blanc)",
    "realigner_soeurs": (
        _RECALCUL + " (pilote le mécanisme à la main, une transaction par famille, passage à blanc)"
    ),
    "recalculer_vues_ytm": (
        _RECALCUL + " — avec les exclusions de l'APPELANT (`vues_comptees`), que le "
        "recalcul générique (exclusions relues en base) défairait"
    ),
    # ── Décisions documentées (CORRECTIFS, chantier A lot 2) ──
    "declarer_provenance_legacy": (
        "par lots ; déclare `legacy` la valeur DÉJÀ en colonne, et `legacy` ne sert que "
        "seul — aucun verdict ne change. Le convertir recalculerait des milliers de "
        "fiches (6 201 datées après e24) en UNE transaction, verrou tenu d'autant"
    ),
}


def _appels_de_transaction(fonction: ast.AST) -> list[str]:
    """Les `X.begin()` / `X.commit()` du corps (fonctions imbriquées comprises)."""
    return [
        ast.unparse(n.func)
        for n in ast.walk(fonction)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr in ("begin", "commit")
    ]


def _passe_par_ecrire(fonction: ast.AST) -> bool:
    return any(
        isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "_ecrire"
        and isinstance(n.func.value, ast.Name)
        and n.func.value.id == "self"
        for n in ast.walk(fonction)
    )


def _methodes(source: str, classe: str = "TrackRepository") -> dict[str, ast.AST]:
    arbre = ast.parse(source)
    cls = next(n for n in arbre.body if isinstance(n, ast.ClassDef) and n.name == classe)
    return {n.name: n for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _ecrivains_hors_ecrire(source: str) -> dict[str, list[str]]:
    """{méthode: appels de transaction} pour celles qui ouvrent une transaction
    sans passer par `_ecrire`."""
    return {
        nom: appels
        for nom, fonction in _methodes(source).items()
        if (appels := _appels_de_transaction(fonction)) and not _passe_par_ecrire(fonction)
    }


def test_tout_ecrivain_passe_par_ecrire():
    fautifs = {
        nom: appels
        for nom, appels in _ecrivains_hors_ecrire(FICHIER.read_text(encoding="utf-8")).items()
        if nom not in EXCEPTIONS
    }
    assert not fautifs, (
        "Écrivain(s) de TrackRepository hors du mécanisme du chantier A :\n"
        + "\n".join(f"  {nom}  →  {', '.join(appels)}" for nom, appels in sorted(fautifs.items()))
        + "\nOuvrir la transaction par `with self._ecrire() as conn:` et marquer la "
        "fiche (`self._toucher(conn, track_id)`) ; sinon, une exception JUSTIFIÉE "
        "dans `EXCEPTIONS`."
    )


def test_les_exceptions_existent_encore():
    """Une exception qui ne désigne plus rien (méthode renommée, supprimée ou
    convertie) se retire : sinon la liste fermée ne l'est plus."""
    hors = _ecrivains_hors_ecrire(FICHIER.read_text(encoding="utf-8"))
    perimees = sorted(nom for nom in EXCEPTIONS if nom not in hors)
    assert not perimees, f"Exceptions périmées à retirer : {perimees}"


def test_le_crible_reconnait_le_motif():
    """Un crible qui ne détecte plus rien passerait en silence."""
    source = """
class TrackRepository:
    def nu(self, tid):
        with self.engine.begin() as conn:
            conn.execute("UPDATE tracks SET x = 1")

    def converti(self, tid):
        with self._ecrire() as conn:
            self._toucher(conn, tid)

    def a_la_main(self):
        with self.engine.connect() as conn:
            conn.execute("DELETE FROM tracks")
            conn.commit()

    def dans_la_transaction_de_l_appelant(self, conn, tid):
        conn.execute("UPDATE tracks SET x = 1")
"""
    assert set(_ecrivains_hors_ecrire(source)) == {"nu", "a_la_main"}
