"""La mesure à blanc des photos : ne dit « faux » que ce qu'elle peut montrer."""

import hashlib
import sqlite3
import zlib
from contextlib import closing

from scripts import mesurer_photos_personnes as mp
from src.utils.image_downloader import IMAGES_PAR_DEFAUT_SHA256

VRAI = mp.Profil(4510044, "Laylow", "http://cdn/vrai.jpg", 588185)
HOMONYME = mp.Profil(12755051, "Laylow", "http://cdn/homonyme.jpg", 22)
AUTRE = mp.Profil(7, "Laylow Junior", "http://cdn/autre.jpg", 3)


class TestClasser:
    def test_l_image_du_profil_prouve_est_conforme(self):
        v = mp.classer("a", {"Laylow"}, [(VRAI, "a")], [])
        assert v.categorie == mp.CONFORME

    def test_le_cas_laylow_est_un_homonyme(self):
        v = mp.classer("h", {"Laylow"}, [(VRAI, "a")], [(HOMONYME, "h"), (VRAI, "a")])
        assert v.categorie == mp.HOMONYME and v.resultat == HOMONYME and v.rang == 0
        assert v.meme_nom == 2

    def test_l_image_d_un_resultat_a_un_autre_nom_est_fausse_meme_sans_preuve(self):
        v = mp.classer("x", {"Laylow"}, [], [(AUTRE, "x")])
        assert v.categorie == mp.AUTRE_NOM

    def test_un_resultat_au_meme_nom_sans_preuve_n_est_pas_declare_faux(self):
        v = mp.classer("h", {"Laylow"}, [], [(HOMONYME, "h")])
        assert v.categorie == mp.RECHERCHE_SANS_PREUVE

    def test_le_nom_se_compare_a_la_graphie_pres(self):
        v = mp.classer("h", {"M.A.N"}, [], [(mp.Profil(1, "MAN"), "h")])
        assert v.categorie == mp.RECHERCHE_SANS_PREUVE

    def test_une_photo_d_origine_inconnue_n_est_jamais_declaree_homonyme(self):
        contre = mp.classer("depot", {"Laylow"}, [(VRAI, "a")], [(HOMONYME, "h")])
        sans = mp.classer("depot", {"Laylow"}, [], [(HOMONYME, "h")])
        assert contre.categorie == mp.INCONNUE_CONTRE_PREUVE
        assert sans.categorie == mp.INCONNUE_SANS_PREUVE

    def test_une_photo_illisible_ne_prouve_rien(self):
        v = mp.classer("depot", {"Laylow"}, [(VRAI, None)], [(HOMONYME, None)])
        assert v.categorie == mp.INCONNUE_CONTRE_PREUVE

    def test_la_silhouette_restee_se_signale(self):
        silhouette = next(iter(IMAGES_PAR_DEFAUT_SHA256))
        assert mp.classer(silhouette, {"X"}, [], []).categorie == mp.SILHOUETTE


def _base() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE artists (id INTEGER PRIMARY KEY, name TEXT, deezer_id INTEGER);
        CREATE TABLE tracks (id INTEGER PRIMARY KEY, artist_id INTEGER, deezer_id INTEGER,
                             featured_artists TEXT, is_featuring INTEGER);
        CREATE TABLE credits (id INTEGER PRIMARY KEY, track_id INTEGER, name TEXT, role TEXT);
        INSERT INTO artists VALUES (1, 'Josman', 111);
        INSERT INTO tracks VALUES (1, 1, 77, 'Laylow, M.A.N', 0);
        INSERT INTO tracks VALUES (2, 1, NULL, 'LAYLOW', 0);
        INSERT INTO credits VALUES (1, 1, 'Kore', 'Producer');
        INSERT INTO credits VALUES (2, 1, 'Mixeur', 'Mixing Engineer');
        """)
    return conn


class TestPersonnes:
    def test_les_personnes_de_la_base(self):
        with closing(_base()) as conn:
            personnes = mp.personnes_de_la_base(conn)
        laylow = personnes["laylow"]
        assert laylow.noms == {"Laylow", "LAYLOW"} and list(laylow.pistes) == [77]
        assert laylow.roles == {"invite"}
        assert personnes["kore"].roles == {"producteur"}
        assert personnes["mixeur"].roles == {"autre_credit"}
        josman = personnes["josman"]
        assert josman.ids_connus == {111} and josman.roles == {"artiste"}
        assert list(josman.pistes) == [77]


class _Deezer:
    def __init__(self, pistes=None, profils=None, recherches=None):
        self.pistes = pistes or {}
        self.profils = profils or {}
        self.recherches = recherches or {}
        self.appels = []

    def get_track_by_id(self, piste):
        self.appels.append(("track", piste))
        return self.pistes.get(piste)

    def get_artist(self, deezer_id):
        self.appels.append(("artist", deezer_id))
        return self.profils.get(deezer_id)

    def _make_request(self, endpoint, params):
        self.appels.append((endpoint, params["q"]))
        return {"data": self.recherches.get(params["q"], [])}


def _hit(deezer_id, nom, url, fans=None):
    return {"id": deezer_id, "name": nom, "picture_xl": url, "nb_fan": fans}


def _sha(contenu: bytes) -> str:
    return hashlib.sha256(contenu).hexdigest()


def _mesure(tmp_path, deezer, photos, connu=None):
    connu = connu or {"track": {}, "artist": {}, "search": {}}
    return mp.Mesure(connu, tmp_path / "cache.json", deezer=deezer, telecharger=photos.get)


class TestMesure:
    def test_de_bout_en_bout_le_fichier_de_l_homonyme(self, tmp_path):
        fichier = tmp_path / "Laylow.jpg"
        fichier.write_bytes(b"homonyme")
        deezer = _Deezer(
            pistes={77: {"contributors": [_hit(4510044, "Laylow", "u/vrai")]}},
            recherches={"Laylow": [_hit(12755051, "Laylow", "u/h", 22)]},
            profils={4510044: _hit(4510044, "Laylow", "u/vrai", 588185)},
        )
        photos = {"u/vrai": _sha(b"vrai"), "u/h": _sha(b"homonyme")}
        personne = mp.Personne(noms={"Laylow"}, pistes={77: None})
        v = _mesure(tmp_path, deezer, photos).juger(fichier, personne)
        assert v.categorie == mp.HOMONYME and v.resultat.id == 12755051
        assert v.preuves[0].id == 4510044 and v.preuves[0].nb_fan == 588185

    def test_conforme_sans_lancer_de_recherche(self, tmp_path):
        fichier = tmp_path / "Laylow.jpg"
        fichier.write_bytes(b"vrai")
        deezer = _Deezer(pistes={77: {"contributors": [_hit(4510044, "Laylow", "u/vrai")]}})
        personne = mp.Personne(noms={"Laylow"}, pistes={77: None})
        v = _mesure(tmp_path, deezer, {"u/vrai": _sha(b"vrai")}).juger(fichier, personne)
        assert v.categorie == mp.CONFORME and deezer.appels == [("track", 77)]

    def test_le_brut_evite_le_reseau_et_le_cache_survit(self, tmp_path):
        fichier = tmp_path / "X.jpg"
        fichier.write_bytes(b"x")
        connu = {
            "track": {"5": [{"id": 9, "nom": "X", "photo": "u/x", "nb_fan": None}]},
            "artist": {},
            "search": {},
        }
        deezer = _Deezer()
        mesure = _mesure(tmp_path, deezer, {"u/x": _sha(b"x")}, connu)
        personne = mp.Personne(noms={"X"}, pistes={5: None})
        assert mesure.juger(fichier, personne).categorie == mp.CONFORME
        assert deezer.appels == []
        mesure.sauver()
        reprise = _mesure(tmp_path, deezer, {})
        assert reprise.empreinte("u/x") == _sha(b"x")

    def test_les_fiches_lues_sur_deezer_sont_plafonnees(self, tmp_path):
        deezer = _Deezer()
        personne = mp.Personne(noms={"X"}, pistes={i: None for i in range(1, 20)})
        assert _mesure(tmp_path, deezer, {}).preuves(personne) == []
        assert len(deezer.appels) == mp.MAX_FICHES_RESEAU

    def test_une_lecture_sans_reponse_n_est_pas_gardee(self, tmp_path):
        """Une source muette n'est pas une absence : une reprise relit la piste."""
        deezer = _Deezer()
        mesure = _mesure(tmp_path, deezer, {})
        assert mesure.contributeurs(5) == [] and mesure.sans_reponse == 1
        assert not mesure.piste_deja_lue(5)
        deezer.pistes[5] = {"contributors": [_hit(9, "X", "u/x")]}
        assert mesure.contributeurs(5)[0]["id"] == 9

    def test_un_fichier_hors_base_n_a_que_la_recherche(self, tmp_path):
        fichier = tmp_path / "Depot.jpg"
        fichier.write_bytes(b"depot")
        deezer = _Deezer(recherches={"Depot": [_hit(1, "Depot", "u/d")]})
        v = _mesure(tmp_path, deezer, {"u/d": _sha(b"autre")}).juger(fichier, None)
        assert v.categorie == mp.INCONNUE_SANS_PREUVE


class TestBrut:
    def test_lire_le_brut_en_lecture_seule(self, tmp_path):
        chemin = tmp_path / "brut.db"
        conn = sqlite3.connect(chemin)
        conn.execute(
            "CREATE TABLE brut (id INTEGER PRIMARY KEY, source TEXT, requete TEXT, corps BLOB, "
            "premiere_lecture TEXT, derniere_lecture TEXT)"
        )
        lignes = {
            "https://api.deezer.com/track/77": (
                b'{"contributors": [{"id": 4, "name": "Laylow", "picture_xl": "u"}]}'
            ),
            "https://api.deezer.com/artist/4": b'{"id": 4, "name": "Laylow", "nb_fan": 9}',
            "https://api.deezer.com/search/artist?limit=5&q=%24ITYLO": (
                b'{"data": [{"id": 1, "name": "$ITYLO", "picture_xl": "v"}]}'
            ),
            "https://api.deezer.com/track/78": b'{"error": {"code": 800}}',
        }
        for requete, corps in lignes.items():
            conn.execute(
                "INSERT INTO brut (source, requete, corps) VALUES ('deezer', ?, ?)",
                (requete, zlib.compress(corps)),
            )
        conn.commit()
        conn.close()
        connu = mp.lire_le_brut(chemin)
        assert connu["track"] == {"77": [{"id": 4, "nom": "Laylow", "photo": "u", "nb_fan": None}]}
        assert connu["artist"]["4"]["nb_fan"] == 9
        assert connu["search"]["$ITYLO"][0]["nom"] == "$ITYLO"


class TestRapport:
    def test_le_rapport_montre_l_homonyme_et_range_les_inconnues_a_part(self, tmp_path):
        fichier = tmp_path / "Laylow.jpg"
        fichier.write_bytes(b"h")
        personne = mp.Personne(noms={"Laylow"}, roles={"invite"})
        lignes = [
            mp.ligne_de_resultat(
                fichier,
                personne,
                mp.Verdict(mp.HOMONYME, preuves=[VRAI], resultat=HOMONYME, rang=0, meme_nom=2),
            ),
            mp.ligne_de_resultat(fichier, None, mp.Verdict(mp.INCONNUE_SANS_PREUVE)),
        ]
        texte = mp.rapport_markdown(lignes, "2026-11-03")
        assert "Laylow (12755051, 22 fans)" in texte and "Laylow (4510044, 588185 fans)" in texte
        assert "| `homonyme` | 1 |" in texte and "| `inconnue_sans_preuve` | 1 |" in texte
        assert "Rien n'a été remplacé" in texte

    def test_le_doublon_deezer_se_range_a_part_de_l_homonyme(self, tmp_path):
        fichier = tmp_path / "Ab-Soul.jpg"
        fichier.write_bytes(b"h")
        doublon = mp.Profil(6110500, "Ab Soul", "u", 8)
        vrai = mp.Profil(1155002, "Ab-Soul", "v", 74290)
        ligne = mp.ligne_de_resultat(
            fichier, None, mp.Verdict(mp.HOMONYME, preuves=[doublon], resultat=vrai)
        )
        assert not mp.fichier_moins_connu(ligne)
        texte = mp.rapport_markdown([ligne], "2026-11-04")
        assert "le moins de fans : 0" in texte and "le plus de fans : 1" in texte

    def test_un_nom_proche_se_range_a_part_d_un_autre_nom(self, tmp_path):
        fichier = tmp_path / "Jehkhyl.jpg"
        fichier.write_bytes(b"h")
        personne = mp.Personne(noms={"Jehkhyl"})

        def ligne(nom):
            verdict = mp.Verdict(mp.AUTRE_NOM, resultat=mp.Profil(1, nom))
            return mp.ligne_de_resultat(fichier, personne, verdict)

        assert mp.nom_proche(ligne("Jehkyl")) and not mp.nom_proche(ligne("Ariana Grande"))
