"""Garde STRUCTURELLE : un écrivain de `ArtistRepository` ouvre sa transaction
par `_ecrire_artiste()` et DÉCLARE ce qu'il invalide (`_invalider`).

Les écrivains d'artiste n'ont pas de colonne dérivée, mais certains changent la
façon dont les MORCEAUX sont jugés sans rien redéclencher (revue du
2026-10-19) : un alias confirmé change les noms sous lesquels on cherche ses
certifs, un profil Spotify ce que le gate d'identité accepte, un ID Deezer ou
Discogs ce que les oracles tranchent. Il fallait relancer « Appliquer les
certifs » ou un run à la main — sans que rien ne dise lesquels.

Ce crible interdit le MOTIF, comme `test_ecrivains_passent_par_ecrire` pour
`TrackRepository` : toute méthode qui ouvre elle-même une transaction passe par
`_ecrire_artiste` et y appelle `_invalider`, sauf une liste FERMÉE d'exceptions
dont aucun portier ne dépend.
"""

from pathlib import Path

import pytest

from src.models import ArtistRelation
from tests.test_ecrivains_passent_par_ecrire import _appels_de_transaction, _methodes

FICHIER = Path(__file__).resolve().parents[1] / "src" / "utils" / "artist_repository.py"

#: Écrivains HORS `_ecrire_artiste`. Liste FERMÉE : une entrée dit pourquoi
#: aucun jugement de morceau n'en dépend.
EXCEPTIONS: dict[str, str] = {
    "_ecrire_artiste": "le mécanisme lui-même",
    "set_artist_image_path": "photo de profil : affichage seul",
    "set_artist_ytm_channel": (
        "chaîne YTM : relue par CHAQUE run YTM, qui rejuge tout ce qu'il lit ; "
        "aucun verdict stocké ne dépend de l'ancienne"
    ),
    "clear_artist_ytm_channel": "idem `set_artist_ytm_channel` (ré-inférée au run suivant)",
    "update_artist_kworb_totals": "totaux du récapitulatif Kworb : affichage seul",
    "update_artist_monthly_listeners": "auditeurs mensuels et leur historique : affichage seul",
    "propose_artist_relations": (
        "lien PROPOSÉ ou `info` : seuls les liens confirmés jugent "
        "(`get_artist_relations`) — c'est leur confirmation qui invalide"
    ),
}


def _appelle(fonction, methode: str) -> bool:
    import ast

    return any(
        isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == methode
        and isinstance(n.func.value, ast.Name)
        and n.func.value.id == "self"
        for n in ast.walk(fonction)
    )


def _ecrivains():
    methodes = _methodes(FICHIER.read_text(encoding="utf-8"), "ArtistRepository")
    return {nom: f for nom, f in methodes.items() if _appels_de_transaction(f)} | {
        nom: f for nom, f in methodes.items() if _appelle(f, "_ecrire_artiste")
    }


def test_tout_ecrivain_d_artiste_passe_par_ecrire_artiste():
    fautifs = sorted(
        nom
        for nom, f in _ecrivains().items()
        if nom not in EXCEPTIONS and not _appelle(f, "_ecrire_artiste")
    )
    assert not fautifs, (
        f"Écrivain(s) d'artiste hors `_ecrire_artiste` : {fautifs}. Ouvrir la "
        "transaction par `with self._ecrire_artiste() as conn:` et déclarer ce "
        "qu'il invalide (`self._invalider(conn, artist_id, …)`) ; sinon, une "
        "exception JUSTIFIÉE dans `EXCEPTIONS`."
    )


def test_tout_ecrivain_d_artiste_declare_ce_qu_il_invalide():
    """Passer par `_ecrire_artiste` sans rien invalider, c'est n'avoir aucun
    dépendant : sa place est dans `EXCEPTIONS`, avec sa raison."""
    muets = sorted(
        nom
        for nom, f in _ecrivains().items()
        if nom not in EXCEPTIONS
        and _appelle(f, "_ecrire_artiste")
        and not _appelle(f, "_invalider")
    )
    assert not muets, f"Écrivain(s) qui n'invalident rien : {muets}"


def test_aucune_exception_perimee():
    ecrivains = _ecrivains()
    perimees = sorted(
        nom
        for nom in EXCEPTIONS
        if nom != "_ecrire_artiste"
        and (nom not in ecrivains or _appelle(ecrivains[nom], "_ecrire_artiste"))
    )
    assert not perimees, f"Exception(s) sans objet : {perimees}"


# ── Ce que chaque décision déclare ───────────────────────────────────────────


@pytest.fixture
def notes(data_manager, monkeypatch):
    """Les artistes notés à rejuger, par transaction COMMITÉE."""
    vues: list[dict] = []
    original = type(data_manager)._noter_a_rejuger

    def noter(self, conn):
        vues.append({a: set(p) for a, p in (conn.info.get(self._A_REJUGER) or {}).items()})
        original(self, conn)

    monkeypatch.setattr(type(data_manager), "_noter_a_rejuger", noter)
    return vues


def _artiste(dm, nom="Swing"):
    from src.models import Artist

    artiste = Artist(name=nom)
    artiste.id = dm.save_artist(artiste)
    return artiste


def _alias(nom="Swingo"):
    return ArtistRelation(related_name=nom, kind="alias", source="manual")


def test_confirmer_un_alias_invalide_certifs_et_identites(data_manager, notes):
    artiste = _artiste(data_manager)
    notes.clear()

    data_manager.record_artist_relations(artiste.id, [_alias()])

    assert notes == [{artiste.id: {"certifs", "identite_spotify", "identite_deezer"}}]


def test_refuser_un_lien_seulement_propose_n_invalide_rien(data_manager, notes):
    artiste = _artiste(data_manager)
    data_manager.propose_artist_relations(artiste.id, [_alias()])
    notes.clear()

    assert data_manager.set_relation_status(artiste.id, "Swingo", "alias", "refused")

    assert notes == [{}]


def test_retirer_un_lien_confirme_invalide(data_manager, notes):
    artiste = _artiste(data_manager)
    data_manager.record_artist_relations(artiste.id, [_alias()])
    notes.clear()

    assert data_manager.forget_artist_relation(artiste.id, "Swingo", "alias")

    assert notes == [{artiste.id: {"certifs", "identite_spotify", "identite_deezer"}}]


def test_un_id_deezer_inchange_n_invalide_rien(data_manager, notes):
    artiste = _artiste(data_manager)
    data_manager.update_artist_deezer_id(artiste.id, 42)
    notes.clear()

    data_manager.update_artist_deezer_id(artiste.id, 42)
    data_manager.update_artist_deezer_id(artiste.id, 43)

    assert notes == [{}, {artiste.id: {"identite_deezer"}}]


def test_un_profil_spotify_confirme_invalide_le_gate(data_manager, notes):
    artiste = _artiste(data_manager)
    notes.clear()

    data_manager.confirmer_profil_spotify(artiste.id, "7ctD")
    data_manager.record_artist_spotify_ids(artiste.id, ["A", "B"], "musicbrainz")
    data_manager.record_artist_spotify_ids(artiste.id, ["B", "A"], "musicbrainz")

    assert notes == [
        {artiste.id: {"identite_spotify"}},
        {artiste.id: {"identite_spotify"}},
        {},
    ]


def test_un_save_sans_changement_n_invalide_rien(data_manager, notes):
    artiste = _artiste(data_manager)
    notes.clear()

    data_manager.save_artist(artiste)
    artiste.name = "Swing (FR)"
    data_manager.save_artist(artiste)

    assert notes == [{}, {artiste.id: set(_portiers())}]


def test_un_portier_inconnu_est_refuse(data_manager):
    with pytest.raises(ValueError), data_manager._ecrire_artiste() as conn:
        data_manager._invalider(conn, 1, "certfis")


def _portiers():
    from src.utils.artist_repository import PORTIERS_DE_L_ARTISTE

    return PORTIERS_DE_L_ARTISTE


# ── Le rejugement (validé par l'utilisateur le 2026-10-20) ───────────────────


def _fiche(dm, artiste, titre, **colonnes):
    from sqlalchemy import text

    from src.models import Track

    track = Track(title=titre, artist=artiste)
    dm.save_track(track)
    if colonnes:
        champs = ", ".join(f"{c} = :{c}" for c in colonnes)
        with dm.engine.begin() as conn:
            conn.execute(
                text(f"UPDATE tracks SET {champs} WHERE id = :id"), {**colonnes, "id": track.id}
            )
    return track


def _constats(dm, *ids):
    with dm.engine.connect() as conn:
        return {
            r[0]: (r[1], r[2])
            for r in conn.exec_driver_sql(
                "SELECT id, spotify_id_checked_at, deezer_checked_at FROM tracks "
                f"WHERE id IN ({', '.join(str(i) for i in ids)})"
            )
        }


def test_un_alias_confirme_rejuge_certifs_et_constats(data_manager):
    from src.services import regles

    artiste = _artiste(data_manager)
    absent = _fiche(
        data_manager,
        artiste,
        "Absent",
        spotify_id_checked_at="2026-10-01T00:00:00",
        deezer_checked_at="2026-10-01T00:00:00",
    )
    trouve = _fiche(
        data_manager,
        artiste,
        "Trouvé",
        spotify_id="SP",
        spotify_id_checked_at="2026-10-01T00:00:00",
    )
    regles.marquer(data_manager, artiste.name, "certifs")
    assert regles.perimes(data_manager, artiste.name) == []

    data_manager.record_artist_relations(artiste.id, [_alias()])

    assert regles.perimes(data_manager, artiste.name) == ["certifs"]
    constats = _constats(data_manager, absent.id, trouve.id)
    assert constats[absent.id] == (None, None)  # redemandée au prochain run
    assert constats[trouve.id][0] == "2026-10-01T00:00:00"  # elle a son ID : rien à rejuger


def test_un_id_deezer_change_n_expire_que_les_constats_deezer(data_manager):
    artiste = _artiste(data_manager)
    absent = _fiche(
        data_manager,
        artiste,
        "Absent",
        spotify_id_checked_at="2026-10-01T00:00:00",
        deezer_checked_at="2026-10-01T00:00:00",
    )

    data_manager.update_artist_deezer_id(artiste.id, 42)

    assert _constats(data_manager, absent.id)[absent.id] == ("2026-10-01T00:00:00", None)
