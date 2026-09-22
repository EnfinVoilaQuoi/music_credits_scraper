"""Extraction des crédits depuis Discogs (`discogs_api`).

141 statements non couverts (32 %). Discogs est la source la plus riche en
crédits d'ingénierie et d'instruments : sa table de correspondance
rôle Discogs → `CreditRole` décide de la façon dont chaque contribution est
classée en base. Une correspondance ratée ne perd pas la donnée, elle la range
en « OTHER » — invisible dans les regroupements par rôle.

Aucun réseau : `DiscogsClient.__new__` + de faux objets `Release` reproduisant
la forme de `discogs_client` (attributs + le dict `data` des crédits).
"""

import pytest

from src.api.discogs_api import DiscogsClient
from src.models import Artist, Track
from src.models.track import Credit, CreditRole


@pytest.fixture
def client():
    """Client sans `__init__` (qui construit un vrai client discogs_client)."""
    c = DiscogsClient.__new__(DiscogsClient)
    c.client = None
    c.rate_limit_remaining = 60
    c.rate_limit_used = 0
    return c


class _Piste:
    def __init__(self, title, position="A1", duration="3:34"):
        self.title = title
        self.position = position
        self.duration = duration


class _Credit:
    """Un crédit Discogs : un objet Artist portant un dict `data`."""

    def __init__(self, name, role="Producer", tracks=None):
        self.name = name
        self.data = {"role": role}
        if tracks:
            self.data["tracks"] = tracks


class _Label:
    def __init__(self, name):
        self.name = name


class _Release:
    def __init__(self, **kw):
        self.title = kw.get("title", "Mon Album")
        self.id = kw.get("id", 12345)
        self.url = kw.get("url", "https://discogs.com/release/12345")
        self.tracklist = kw.get("tracklist", [_Piste("Bande organisée")])
        for champ in ("genres", "styles", "year", "labels", "credits", "extraartists"):
            if champ in kw:
                setattr(self, champ, kw[champ])


class TestNormalisation:
    @pytest.mark.parametrize(
        ("entree", "attendu"),
        [
            ("Bande Organisée", "bande organisée"),
            ("  Titre  ", "titre"),
            ("Titre (Remix)", "titre"),
            ("Titre [Live]", "titre"),
            ("Titre feat. SCH", "titre"),
            ("Titre ft. SCH", "titre"),
            ("Titre (feat. SCH)", "titre"),
        ],
    )
    def test_formes_equivalentes(self, client, entree, attendu):
        assert client._normalize_string(entree) == attendu

    @pytest.mark.parametrize("apostrophe", ["’", "‘", "`", "´"])
    def test_apostrophes_unifiees(self, client, apostrophe):
        assert client._normalize_string(f"L{apostrophe}empire") == "l'empire"


class TestCorrespondanceDesRoles:
    @pytest.mark.parametrize(
        ("role", "attendu"),
        [
            ("Producer", CreditRole.PRODUCER),
            ("Co-producer", CreditRole.CO_PRODUCER),
            ("Executive Producer", CreditRole.EXECUTIVE_PRODUCER),
            ("Mixed By", CreditRole.MIXING_ENGINEER),
            ("Mastered By", CreditRole.MASTERING_ENGINEER),
            ("Recorded By", CreditRole.RECORDING_ENGINEER),
            ("Written By", CreditRole.WRITER),
            ("Composed By", CreditRole.COMPOSER),
            ("Lyrics By", CreditRole.LYRICIST),
            ("Guitar", CreditRole.GUITAR),
            ("Bass", CreditRole.BASS),
            ("Drums", CreditRole.DRUMS),
            ("Artwork", CreditRole.ARTWORK),
            ("Photography", CreditRole.PHOTOGRAPHY),
        ],
    )
    def test_roles_connus(self, client, role, attendu):
        assert client._map_discogs_role_to_enum(role) == attendu

    def test_casse_indifferente(self, client):
        assert client._map_discogs_role_to_enum("PRODUCER") == CreditRole.PRODUCER
        assert client._map_discogs_role_to_enum("  mixed by  ") == CreditRole.MIXING_ENGINEER

    def test_correspondance_par_inclusion(self, client):
        """Discogs qualifie ses rôles (« Producer [Additional] ») : le mot-clé
        suffit à classer."""
        assert client._map_discogs_role_to_enum("Producer [Additional]") == CreditRole.PRODUCER

    @pytest.mark.parametrize(
        ("role", "attendu"),
        [
            ("Co-producer", CreditRole.CO_PRODUCER),
            ("Executive Producer", CreditRole.EXECUTIVE_PRODUCER),
            ("Vocal Producer", CreditRole.VOCAL_PRODUCER),
            ("Assistant Engineer", CreditRole.ASSISTANT_ENGINEER),
            ("Lead Vocals", CreditRole.LEAD_VOCALS),
            ("Backing Vocals", CreditRole.BACKGROUND_VOCALS),
        ],
    )
    def test_role_specifique_prime_sur_le_general(self, client, role, attendu):
        """Ces SIX rôles étaient INATTEIGNABLES jusqu'au 2026-09-03 : la table
        était parcourue dans son ordre d'insertion et « producer », placé avant
        « co-producer », captait tout. Ils figuraient pourtant dans la table et
        dans l'enum — l'intention était là, l'exécution ne suivait pas."""
        assert client._map_discogs_role_to_enum(role) == attendu

    def test_aucun_role_de_la_table_n_est_masque(self, client):
        """Garde-fou GÉNÉRAL : toute clé ajoutée plus tard doit rester
        atteignable, quelle que soit sa place dans la table."""
        import inspect
        import re

        source = inspect.getsource(DiscogsClient._map_discogs_role_to_enum)
        cles = re.findall(r'"([^"]+)":\s*CreditRole\.', source)
        assert len(cles) > 20, "table de rôles introuvable"
        for cle in cles:
            assert client._map_discogs_role_to_enum(cle) is not CreditRole.OTHER, cle

    @pytest.mark.parametrize(
        ("libelle", "attendu"),
        [
            ("Songwriter", CreditRole.WRITER),
            ("Graphics", CreditRole.GRAPHIC_DESIGN),
            ("Logo", CreditRole.GRAPHIC_DESIGN),
            ("Cover", CreditRole.ARTWORK),
            ("DJ Mix, Scratches", CreditRole.SCRATCHES),
            ("Direct Metal Mastering By", CreditRole.MASTERING_ENGINEER),
            ("Lacquer Cut By", CreditRole.MASTERING_ENGINEER),
            ("Editor", CreditRole.VIDEO_EDITOR),
        ],
    )
    def test_alias_releves_sur_la_base_reelle(self, client, libelle, attendu):
        """Libellés Discogs rencontrés en base et ajoutés le 2026-09-03. Chacun
        est soit lexicalement non ambigu, soit confirmé par l'oracle Genius
        (« Direct Metal Mastering By » : 6/6 Mastering Engineer)."""
        assert client._map_discogs_role_to_enum(libelle) == attendu

    @pytest.mark.parametrize(
        "libelle",
        [
            "Music By",
            "Realization",
            "Project Manager",
            "Management",
            "Production Manager",
            "Stylist",
        ],
    )
    def test_libelles_laisses_en_other_deliberement(self, client, libelle):
        """DÉCISION, pas un oubli : l'oracle Genius est partagé sur « Music By »
        (Producer x4, Mixing Engineer x3) et « Realization » (Mixing x10,
        Recording x9, Producer x2) ; les quatre autres n'ont aucun équivalent
        dans l'enum. Les mapper inventerait une précision que la donnée n'a pas.
        Ce test existe pour qu'un futur « complétons la table » voie que ces
        libellés ont été écartés SCIEMMENT."""
        assert client._map_discogs_role_to_enum(libelle) == CreditRole.OTHER

    @pytest.mark.parametrize(
        ("libelle", "attendu"),
        [
            ("Written-By", CreditRole.WRITER),
            ("Written-By, Performer", CreditRole.WRITER),
            ("Written-By [Gimme The Loot]", CreditRole.WRITER),
            ("Co-Producer", CreditRole.CO_PRODUCER),
        ],
    )
    def test_le_TIRET_de_Discogs_ne_fait_plus_perdre_un_role(self, client, libelle, attendu):
        """Discogs panache « Written-By » et « Mixed By » dans le même
        formulaire ; la table ne portait que la graphie à espace et le
        rapprochement se fait par sous-chaîne. Mesuré le 2026-09-22 : **504
        crédits d'écriture** rangés en `Other` pour un caractère — et autant de
        morceaux déclarés sans auteur alors qu'ils en avaient un."""
        assert client._map_discogs_role_to_enum(libelle) is attendu

    @pytest.mark.parametrize(
        ("libelle", "attendu"),
        [
            ("Art Direction", CreditRole.ART_DIRECTION),
            ("Featuring", CreditRole.FEATURED),
            ("Illustration", CreditRole.ILLUSTRATION),
            ("A&R", CreditRole.A_AND_R),
        ],
    )
    def test_les_libelles_que_seule_la_table_GENIUS_connaissait(self, client, libelle, attendu):
        """Un crédit ne doit pas dépendre de la source qui l'a lu : ces quatre
        libellés étaient traduits par `credit_roles.map_role` et ignorés ici."""
        assert client._map_discogs_role_to_enum(libelle) is attendu

    def test_role_inconnu(self, client):
        """Rangé en OTHER — la donnée n'est pas perdue, mais elle disparaît des
        regroupements par rôle : c'est là qu'un rôle manquant se voit."""
        assert client._map_discogs_role_to_enum("Tape Op") == CreditRole.OTHER

    def test_role_vide(self, client):
        assert client._map_discogs_role_to_enum("") == CreditRole.OTHER


class TestExtractionDesCredits:
    def test_credits_simples(self, client):
        release = _Release(credits=[_Credit("Kore", "Producer")])
        credits = client._extract_credits_from_release(release)
        assert credits == [{"name": "Kore", "role": "Producer", "tracks": None}]

    def test_pistes_concernees_conservees(self, client):
        """Discogs précise les pistes d'un crédit (« A1, B4 ») : l'information
        distingue un producteur d'album d'un producteur d'un seul morceau.

        La clé s'appelait `role_detail` (e18) alors qu'elle portait les PISTES :
        c'est cette confusion de nom qui a fait cohabiter deux données dans une
        seule colonne."""
        release = _Release(credits=[_Credit("Kore", "Producer", tracks="A1, B4")])
        assert client._extract_credits_from_release(release)[0]["tracks"] == "A1, B4"

    def test_deux_sources_fusionnees(self, client):
        """Les crédits arrivent dans `credits` ET `extraartists` selon les
        releases : les deux sont lus."""
        release = _Release(
            credits=[_Credit("Kore", "Producer")],
            extraartists=[_Credit("DJ Bellek", "Mixed By")],
        )
        noms = [c["name"] for c in client._extract_credits_from_release(release)]
        assert noms == ["Kore", "DJ Bellek"]

    def test_credit_sans_data(self, client):
        credit = _Credit("Kore")
        del credit.data
        release = _Release(credits=[credit])
        assert client._extract_credits_from_release(release)[0]["role"] == "Unknown"

    def test_release_sans_credit(self, client):
        assert client._extract_credits_from_release(_Release()) == []

    def test_objet_inexploitable(self, client):
        assert client._extract_credits_from_release(object()) == []


class TestExtractionDuMorceau:
    def test_morceau_trouve(self, client):
        release = _Release(
            tracklist=[_Piste("Autre"), _Piste("Bande organisée", position="B2")],
            year=2020,
            genres=["Hip Hop"],
            styles=["Rap"],
            labels=[_Label("D'or et de platine")],
            credits=[_Credit("Kore", "Producer")],
        )
        data = client._extract_track_from_release(release, "Bande organisée", "Jul")
        assert data["album"] == "Mon Album"
        assert data["position"] == "B2"
        assert data["year"] == 2020
        assert data["genres"] == ["Hip Hop"]
        assert data["labels"] == ["D'or et de platine"]
        assert data["credits"][0]["name"] == "Kore"

    def test_titre_normalise_avant_comparaison(self, client):
        """La tracklist Discogs écrit « Bande Organisée (feat. SCH) » là où notre
        base a « Bande organisée » : sans normalisation, aucun crédit ne
        remonterait."""
        release = _Release(tracklist=[_Piste("Bande Organisée (feat. SCH)")])
        assert client._extract_track_from_release(release, "Bande organisée", "Jul") is not None

    def test_morceau_absent_de_la_tracklist(self, client):
        release = _Release(tracklist=[_Piste("Un autre morceau")])
        assert client._extract_track_from_release(release, "Bande organisée", "Jul") is None

    def test_tracklist_vide(self, client):
        assert client._extract_track_from_release(_Release(tracklist=[]), "T", "A") is None

    def test_release_sans_tracklist(self, client):
        release = _Release()
        del release.tracklist
        assert client._extract_track_from_release(release, "T", "A") is None

    def test_champs_optionnels_absents(self, client):
        """Une release minimale ne doit pas produire de clés vides parasites."""
        data = client._extract_track_from_release(_Release(), "Bande organisée", "Jul")
        assert "year" not in data
        assert "genres" not in data
        assert "credits" not in data

    def test_objet_inexploitable(self, client):
        assert client._extract_track_from_release(object(), "T", "A") is None


class TestRateLimit:
    def test_compteurs_relus(self, client):
        """Discogs renvoie son quota dans les en-têtes : on le suit pour ne pas
        se faire couper en plein enrichissement."""

        class _Fetcher:
            rate_limit_remaining = 42
            rate_limit_used = 18

        client.client = type("C", (), {"_fetcher": _Fetcher()})()
        client._check_rate_limit()
        assert client.rate_limit_remaining == 42
        assert client.rate_limit_used == 18

    def test_quota_faible_declenche_une_pause(self, client, monkeypatch):
        pauses = []
        monkeypatch.setattr("src.api.discogs_api.time.sleep", lambda s: pauses.append(s))

        class _Fetcher:
            rate_limit_remaining = 2
            rate_limit_used = 58

        client.client = type("C", (), {"_fetcher": _Fetcher()})()
        client._check_rate_limit()
        assert pauses == [60]

    def test_client_sans_compteur(self, client):
        """Toutes les versions de discogs_client n'exposent pas le quota :
        l'absence ne doit pas interrompre l'enrichissement."""
        client.client = object()
        client._check_rate_limit()  # ne lève pas


# ────────────────────────── enrichissement d'un Track (sans réseau)


def _track(titre="Titre", artiste="ISHA", **extra):
    t = Track(title=titre, artist=Artist(name=artiste), **extra)
    return t


def _donnees(**extra):
    """Le dict rendu par `search_track`, surchargeable."""
    base = {"discogs_id": 42, "genres": ["Hip Hop"], "styles": ["Boom Bap"], "credits": []}
    base.update(extra)
    return base


class TestEnrichissementDuTrack:
    """Trois retours DISTINCTS, et c'est la distinction qui compte : `True` =
    du neuf, `"not_needed"` = release matchée sans rien de nouveau (ni succès ni
    échec dans les compteurs), `False` = aucune release ne matche.

    Confondre les deux derniers faisait compter Discogs dans `all_failed` et
    afficher « ❌ discogs ÉCHEC » alors que tout allait bien.
    """

    def _stub(self, client, monkeypatch, donnees):
        monkeypatch.setattr(client, "search_track", lambda *a, **k: donnees)

    def test_aucune_release_ne_matche(self, client, monkeypatch):
        self._stub(client, monkeypatch, None)
        assert client.enrich_track_data(_track()) is False

    def test_rien_de_nouveau_a_poser(self, client, monkeypatch):
        """La release matche, mais l'ID et le genre sont déjà là."""
        self._stub(client, monkeypatch, _donnees())
        track = _track(discogs_id=42, genre="Rap")

        assert client.enrich_track_data(track) == "not_needed"

    def test_discogs_id_pose(self, client, monkeypatch):
        self._stub(client, monkeypatch, _donnees(genres=None))
        track = _track()

        assert client.enrich_track_data(track) is True
        assert track.discogs_id == 42

    def test_id_existant_non_ecrase(self, client, monkeypatch):
        self._stub(client, monkeypatch, _donnees(genres=None))
        track = _track(discogs_id=7)

        client.enrich_track_data(track)
        assert track.discogs_id == 7

    def test_force_update_ecrase(self, client, monkeypatch):
        self._stub(client, monkeypatch, _donnees(genres=None))
        track = _track(discogs_id=7)

        assert client.enrich_track_data(track, force_update=True) is True
        assert track.discogs_id == 42

    def test_genres_et_styles_fusionnes_et_plafonnes(self, client, monkeypatch):
        self._stub(
            client,
            monkeypatch,
            _donnees(genres=["Hip Hop", "Rap"], styles=["Boom Bap", "Trap", "Cloud"]),
        )
        track = _track()

        client.enrich_track_data(track)
        assert track.genre == "Hip Hop, Rap, Boom Bap"  # 3 maximum

    def test_credits_ajoutes(self, client, monkeypatch):
        credits = [
            {"name": "Producteur X", "role": "Producer"},
            {"name": "Ingé Y", "role": "Mixed By"},
        ]
        self._stub(client, monkeypatch, _donnees(discogs_id=None, genres=None, credits=credits))
        track = _track()

        assert client.enrich_track_data(track) is True
        assert {c.name for c in track.credits} == {"Producteur X", "Ingé Y"}
        assert all(c.source == "discogs" for c in track.credits)

    def test_libelle_brut_conserve_pour_un_role_inconnu(self, client, monkeypatch):
        """Un rôle sans équivalent dans l'enum tombe en OTHER, mais le libellé
        Discogs reste lisible dans `role_detail` — c'est ce qui a permis
        d'inventorier les rôles manquants en base."""
        credits = [{"name": "Quelqu'un", "role": "Tape Op"}]
        self._stub(client, monkeypatch, _donnees(discogs_id=None, genres=None, credits=credits))
        track = _track()

        client.enrich_track_data(track)
        assert track.credits[0].role == CreditRole.OTHER
        assert track.credits[0].role_detail == "Tape Op"

    def test_le_libelle_ET_les_pistes_survivent_ensemble(self, client, monkeypatch):
        """Deux corrections successives sur le même défaut, et seule la seconde
        le règle. Le code d'origine posait `pistes or (libellé si OTHER)` : quand
        Discogs fournissait les deux, le LIBELLÉ disparaissait. Inverser la
        priorité (2026-09-04) n'a fait que déplacer la perte sur les PISTES.

        Ce test portait alors l'ancienne assertion — il gelait la moitié perdue.
        Une colonne pour chacune (e18) le rend enfin vrai des deux côtés."""
        credits = [{"name": "Quelqu'un", "role": "Tape Op", "tracks": "A1, A2"}]
        self._stub(client, monkeypatch, _donnees(discogs_id=None, genres=None, credits=credits))
        track = _track()

        client.enrich_track_data(track)
        assert track.credits[0].role_detail == "Tape Op"
        assert track.credits[0].tracks == "A1, A2"

    def test_un_credit_video_range_en_other_reste_detectable(self, client, monkeypatch):
        """La conséquence concrète du correctif : `get_video_credits` retrouve le
        crédit vidéo là où le numéro de piste le lui cachait."""
        credits = [{"name": "Quelqu'un", "role": "Camera Operator", "tracks": "16"}]
        self._stub(client, monkeypatch, _donnees(discogs_id=None, genres=None, credits=credits))
        track = _track()

        client.enrich_track_data(track)
        assert track.credits[0].role == CreditRole.OTHER
        assert track.get_video_credits() == track.credits
        assert track.get_music_credits() == []

    def test_credit_malforme_ignore_sans_perdre_les_autres(self, client, monkeypatch):
        credits = [{"role": "Producer"}, {"name": "Bon", "role": "Producer"}]
        self._stub(client, monkeypatch, _donnees(discogs_id=None, genres=None, credits=credits))
        track = _track()

        client.enrich_track_data(track)
        assert [c.name for c in track.credits] == ["Bon"]

    def test_labels_journalises_sans_effet(self, client, monkeypatch):
        """Les labels sont lus mais n'ont pas de champ de destination."""
        self._stub(client, monkeypatch, _donnees(discogs_id=None, genres=None, labels=["Def Jam"]))
        assert client.enrich_track_data(_track()) == "not_needed"

    def test_erreur_inattendue_rend_false(self, client, monkeypatch):
        def _lever(*a, **k):
            raise RuntimeError("bug d'orchestration")

        monkeypatch.setattr(client, "search_track", _lever)
        assert client.enrich_track_data(_track()) is False


class TestPistesEtLibelleNeSeDisputentPlusLaColonne:
    """`role_detail` qualifie le RÔLE ; `tracks` dit d'où vient le crédit.

    Les deux partageaient `role_detail` : le code écrivait `pistes or (libellé si
    OTHER)`, donc perdait le libellé ; l'inverse (2026-09-04) perdait les pistes.
    Mesuré avant correction : 209 lignes portaient une référence de piste dans
    `role_detail`, dont 21 en `OTHER` — les seules NUISIBLES, puisque
    `Track.get_video_credits` reclasse un OTHER en crédit vidéo d'après les MOTS
    de `role_detail`.
    """

    def _enrichir(self, client, monkeypatch, role, tracks):
        monkeypatch.setattr(
            client,
            "search_track",
            lambda *a, **k: _donnees(credits=[{"name": "X", "role": role, "tracks": tracks}]),
        )
        track = _track()
        client.enrich_track_data(track)
        return track.credits[0]

    def test_un_role_inconnu_garde_son_libelle_ET_ses_pistes(self, client, monkeypatch):
        """Le cas qui perdait de l'information dans les deux versions du code."""
        credit = self._enrichir(client, monkeypatch, "Stylist", "A1,B3")

        assert credit.role == CreditRole.OTHER
        assert credit.role_detail == "Stylist"  # le libellé, lisible
        assert credit.tracks == "A1,B3"  # et les pistes, à leur place

    def test_un_role_mappe_n_a_pas_de_libelle_redondant(self, client, monkeypatch):
        """Le rôle mappé dit déjà tout : répéter « Producer » dans `role_detail`
        n'ajouterait rien et rouvrirait la confusion."""
        credit = self._enrichir(client, monkeypatch, "Producer", "A1")

        assert credit.role == CreditRole.PRODUCER
        assert credit.role_detail is None
        assert credit.tracks == "A1"

    def test_sans_piste_le_libelle_reste(self, client, monkeypatch):
        credit = self._enrichir(client, monkeypatch, "Stylist", None)
        assert credit.role_detail == "Stylist" and credit.tracks is None

    def test_une_reference_de_piste_ne_declenche_plus_de_faux_credit_video(
        self, client, monkeypatch
    ):
        """La conséquence concrète du mélange : `get_video_credits` cherche des
        MOTS dans `role_detail`. Un numéro de piste n'a rien à y faire."""
        credit = self._enrichir(client, monkeypatch, "Video Editor", "A1")

        assert credit.role_detail != "A1"


class TestReimportIdempotent:
    """`Track.add_credit` dédoublonne sur (name, role, role_detail). Un ré-import
    avec des rôles CORRIGÉS ajoutait donc le nouveau crédit À CÔTÉ de l'ancien —
    la clé de dédup ayant justement changé, elle ne pouvait pas les rapprocher.
    """

    def _client_rendant(self, client, monkeypatch, role):
        monkeypatch.setattr(
            client,
            "search_track",
            lambda *a, **k: _donnees(credits=[{"name": "Kore", "role": role, "tracks": None}]),
        )

    def test_deux_imports_identiques_ne_doublent_pas(self, client, monkeypatch):
        self._client_rendant(client, monkeypatch, "Producer")
        track = _track()

        client.enrich_track_data(track)
        client.enrich_track_data(track)

        assert len(track.credits) == 1

    def test_un_role_corrige_REMPLACE_au_lieu_de_s_ajouter(self, client, monkeypatch):
        """Le cas qui motivait la purge : sans elle, on obtenait deux crédits
        pour la même personne, l'ancien rôle et le nouveau."""
        self._client_rendant(client, monkeypatch, "Stylist")
        track = _track()
        client.enrich_track_data(track)

        self._client_rendant(client, monkeypatch, "Producer")
        client.enrich_track_data(track)

        assert [(c.name, c.role) for c in track.credits] == [("Kore", CreditRole.PRODUCER)]

    def test_les_credits_des_AUTRES_sources_sont_epargnes(self, client, monkeypatch):
        """Discogs est ADDITIF, pas confirmatif (mesuré 2026-09-03 : 818 paires
        propres à Discogs contre 22 concordantes avec Genius). Purger ses propres
        lignes ne doit jamais toucher celles de Genius."""
        self._client_rendant(client, monkeypatch, "Producer")
        track = _track()
        track.add_credit(Credit(name="Genius Guy", role=CreditRole.WRITER, source="genius"))

        client.enrich_track_data(track)

        sources = sorted(c.source for c in track.credits)
        assert sources == ["discogs", "genius"]
