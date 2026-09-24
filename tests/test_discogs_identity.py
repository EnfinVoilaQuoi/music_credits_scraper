"""Le disque trouvé pour un morceau est-il celui de NOTRE artiste ? (2026-09-23)

Django « Nuages » portait le disque de Django Reinhardt (recherche libre, même
titre de piste) et un crédit photo en était importé. `release_concorde` exige
un artiste du disque OU de la piste à l'id Discogs de l'artiste, ou au nom
EXACT (suffixe d'homonyme retiré) — jamais par mots entiers. Aucun réseau.
"""

import pytest

from src.models import Artist, Credit, CreditRole, Track
from src.utils.discogs_audit import artistes_de_la_piste, verdict_ligne, verifier_lignes
from src.utils.discogs_identity import cle_nom, nom_sans_suffixe, release_concorde


class TestReleaseConcorde:
    def test_django_reinhardt_nest_pas_django(self):
        """`names_match_as_words` l'accepterait : « Django » ⊂ « Django Reinhardt »."""
        assert not release_concorde([(9, "Django Reinhardt")], [], {"Django"})

    def test_nom_exact_suffixe_retire(self):
        assert release_concorde([(5, "Django (3)")], [], {"Django"})
        assert release_concorde([(5, "ISHA")], [], {"Isha"})

    def test_lid_discogs_suffit(self):
        assert release_concorde([(6244752, "Isha Pili Pili")], [], {"Isha"}, 6244752)
        assert not release_concorde([(1, "Autre")], [], {"Isha"}, 6244752)

    def test_feat_disque_de_lartiste_principal(self):
        assert release_concorde([(3, "Jul")], [], {"SCH", "Jul"})

    def test_compilation_cest_la_piste_qui_decide(self):
        assert release_concorde([(194, "Various")], [(5, "Django")], {"Django"})
        assert not release_concorde([(194, "Various")], [(9, "Autre")], {"Django"})
        assert not release_concorde([(194, "Various")], [], {"Django"})

    def test_noms_vides_ignores(self):
        assert not release_concorde([(None, "")], [], {"", None})

    @pytest.mark.parametrize(
        ("brut", "cle"), [("667 (4)", "667"), ("Moon Man (9)", "moon man"), ("CFR", "cfr")]
    )
    def test_cle_nom(self, brut, cle):
        assert cle_nom(brut) == cle

    def test_nom_sans_suffixe_reste_importable_du_client(self):
        from src.api.discogs_api import nom_sans_suffixe as depuis_client

        assert depuis_client is nom_sans_suffixe


def _ligne(**kw):
    base = {
        "id": 1,
        "title": "Nuages",
        "discogs_id": 100,
        "is_featuring": 0,
        "primary_artist_name": None,
        "artist_id": 1,
        "artiste": "Django",
        "artist_discogs_id": None,
    }
    base.update(kw)
    return base


class TestAudit:
    def test_piste_retrouvee_par_titre_normalise(self):
        disque = {"pistes": [("Nuages (feat. X)", [(5, "Django")]), ("Autre", [])]}
        assert artistes_de_la_piste(disque, "Nuages") == [(5, "Django")]
        assert artistes_de_la_piste(disque, "Inconnu") == []

    def test_verdict_accepte_une_formation_confirmee(self):
        disque = {"artistes": [(1, "IAM")], "pistes": []}
        assert not verdict_ligne(_ligne(artiste="Shurik'N"), disque)
        assert verdict_ligne(_ligne(artiste="Shurik'N"), disque, {"IAM"})

    def test_feat_sans_identite_de_la_ligne(self):
        """Pour un feat, l'id Discogs de l'artiste de la LIGNE ne vaut rien :
        ce n'est pas son disque."""
        disque = {"artistes": [(42, "Autre")], "pistes": []}
        ligne = _ligne(is_featuring=1, artist_discogs_id=42, primary_artist_name="Jul")
        assert not verdict_ligne(ligne, disque)
        assert verdict_ligne(_ligne(artist_discogs_id=42), disque)

    def test_verifier_lit_chaque_disque_une_fois(self):
        lus = []
        disques = {
            100: {"titre": "Nuages", "artistes": [(9, "Django Reinhardt")], "pistes": []},
            200: {"titre": "Le nôtre", "artistes": [(5, "Django")], "pistes": []},
        }

        def lire(rid):
            lus.append(rid)
            return disques.get(rid)

        lignes = [
            _ligne(id=1, discogs_id=100),
            _ligne(id=2, discogs_id=100, title="Minor Swing"),
            _ligne(id=3, discogs_id=200),
            _ligne(id=4, discogs_id=300),  # illisible
        ]
        rapport = verifier_lignes(lignes, lire, pause=0)
        assert lus == [100, 200, 300]
        assert rapport["verifies"] == 3 and rapport["illisibles"] == 1
        assert [e["track_id"] for e in rapport["ecarts"]] == [1, 2]
        assert rapport["ecarts"][0]["credite_a"] == ["Django Reinhardt"]


class TestClearTrackDiscogsRelease:
    def _morceau(self, dm, artiste, titre, genius_id=None, discogs_id=100):
        a = dm.get_artist_by_name(artiste)
        if a is None:
            a = Artist(name=artiste)
            a.id = dm.save_artist(a)
        t = Track(title=titre, artist=a)
        t.genius_id = genius_id
        t.discogs_id = discogs_id
        t.genre = "Jazz, Swing"
        t.add_credit(Credit(name="Photographe", role=CreditRole.PHOTOGRAPHY, source="discogs"))
        t.add_credit(Credit(name="Kore", role=CreditRole.PRODUCER, source="genius"))
        t.id = dm.save_track(t)
        return t

    def _etat(self, dm, tid):
        with dm.engine.connect() as conn:
            ligne = conn.exec_driver_sql(
                f"SELECT discogs_id, genre FROM tracks WHERE id = {int(tid)}"
            ).first()
            sources = sorted(
                r[0]
                for r in conn.exec_driver_sql(
                    f"SELECT source FROM credits WHERE track_id = {int(tid)}"
                )
            )
        return tuple(ligne), sources

    def test_trois_gestes_et_les_soeurs(self, data_manager):
        t = self._morceau(data_manager, "Django", "Nuages", genius_id=777)
        soeur = self._morceau(data_manager, "Invité", "Nuages", genius_id=777)
        autre = self._morceau(data_manager, "Tiers", "Nuages", genius_id=777, discogs_id=999)

        r = data_manager.clear_track_discogs_release(t.id)
        assert r["id_retire"] == 100 and sorted(r["lignes"]) == sorted([t.id, soeur.id])
        assert self._etat(data_manager, t.id) == ((None, None), ["genius"])
        assert self._etat(data_manager, soeur.id)[0] == (None, None)
        # Une sœur qui porte un AUTRE disque garde le sien.
        assert self._etat(data_manager, autre.id)[0] == (999, "Jazz, Swing")

    def test_rien_a_retirer(self, data_manager):
        t = self._morceau(data_manager, "Django", "Nuages", discogs_id=None)
        assert data_manager.clear_track_discogs_release(t.id)["id_retire"] is None
        assert data_manager.clear_track_discogs_release(999999)["lignes"] == []
