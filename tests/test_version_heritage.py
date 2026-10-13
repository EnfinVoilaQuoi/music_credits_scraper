"""Héritage transversal : une version reçoit du socle ce qui ne change pas."""

from src.models.track import Credit, CreditRole, Track
from src.utils import version_heritage as vh


def _socle():
    s = Track(title="Suzy")
    s.id = 7
    s.credits = [
        Credit(name="Diam's", role=CreditRole.WRITER, source="genius"),
        Credit(name="Tefa", role=CreditRole.PRODUCER, source="genius"),
        Credit(name="Nk.F", role=CreditRole.MIXING_ENGINEER, source="genius"),
        Credit(name="Clip Inc", role=CreditRole.VIDEO_DIRECTOR, source="genius"),
        Credit(name="Vieux", role=CreditRole.WRITER, source="heritage"),
    ]
    s.lyrics.text = "Paroles de Suzy"
    s.lyrics.present = True
    s.lyrics.synced = "[00:01.00] Paroles"
    return s


def _version(titre):
    return Track(title=titre)


class TestFamille:
    def test_familles(self):
        assert vh.famille_de("Suzy - Live 2006") == "performance"
        assert vh.famille_de("Suzy (Bonus Track)") == "edition"
        # Édition de DIFFUSION (2026-09-24) : pas une version, une édition de
        # la même fiche — rien à hériter.
        assert vh.famille_de("Suzy (Version Radio)") is None
        assert vh.famille_de("Suzy - Live Version") == "performance"  # la prise l'emporte
        assert vh.famille_de("Suzy (Instrumental)") == "instrumental"
        assert vh.famille_de("MW2 - Chopped & $crewed") == "chopped"
        assert vh.famille_de("Dolce Camara - Snight B Remix") == "remix_named"
        assert vh.famille_de("Suzy") is None

    def test_versions_inedites(self):
        assert vh.famille_de("Suzy (Demo)") == "demo"
        assert vh.famille_de("Suzy [V3]") == "alternate"
        assert vh.famille_de("Suzy (OG)") == "alternate"
        assert vh.famille_de("Suzy [Snippet]") == "snippet"
        assert vh.famille_de("Suzy (Kendrick Lamar Reference)") == "reference"
        assert vh.famille_de("Suzy (Demo) [Mixed]") == "demo"  # la prise l'emporte
        # Versions ANTÉRIEURES (décision utilisateur 2026-09-28) : comme les démos.
        for titre in ("Suzy (Original)", "Suzy (Original Version)", "Suzy (First Edition Version)"):
            assert vh.famille_de(titre) == "alternate", titre
        assert vh.famille_de("Suzy (First Pressing Edition)") == "alternate"
        # « Original Mix » : le même enregistrement, pas une version.
        assert vh.famille_de("Suzy (Original Mix)") is None

    def test_ce_que_l_edition_nomme(self):
        """Règle revue le 2026-10-13 (paquet 3) : « Version », « Edit »,
        « Mixed » ne disent pas, seuls, le même enregistrement."""
        attendu = {
            # Le même enregistrement, sur un autre support (Booba « DKR »).
            "Suzy (Bonus Track)": "edition",
            "Suzy - Bonus": "edition",
            "Suzy (Physical Version)": "edition",
            "Suzy (Version physique)": "edition",
            "Suzy (Studio Version)": "edition",
            # La même prise, autre minutage.
            "Suzy (Edit)": "montage",
            "Suzy (Extended Version)": "montage",
            "Suzy (Version longue)": "montage",
            "Suzy (UnCut 9 Min Version)": "montage",
            "Suzy (Music Video Version)": "montage",
            "Suzy (Version clip)": "montage",
            "Suzy (Mixed)": "montage",
            # L'edit d'un producteur, une captation, un audio accéléré.
            "Suzy (Twinsmatic Edit)": "remix_named",
            "Suzy (Yeezus Tour Version)": "performance",
            "Suzy (Big Day Out in Melbourne Version)": "performance",
            "Suzy (Nightcore Version)": "vitesse",
            "Suzy (Version Instrumentale)": "instrumental",
            "Suzy (Instrumental - BTB Version)": "instrumental",
            # Tout autre nom : une autre prise.
            "Suzy (2 Chainz Version)": "alternate",
            "Suzy (Promo CD Version)": "alternate",
            "Suzy (Stem Player Version)": "alternate",
            "Suzy (Version 2)": "alternate",
            "Suzy (Spanish Version)": "alternate",
            "Suzy (Version 2013)": "alternate",
        }
        assert {t: vh.famille_de(t) for t in attendu} == attendu

    def test_un_montage_herite_tout(self):
        v = _version("Suzy (Mixed)")
        h = vh.heriter(v, _socle())
        assert h.famille == "montage" and h.paroles
        assert {c.name for c in v.credits} == {"Diam's", "Tefa", "Nk.F"}


class TestHeriter:
    def test_live_herite_paroles_et_ecriture_pas_la_production(self):
        v = _version("Suzy - Live 2006")
        h = vh.heriter(v, _socle())
        assert h.famille == "performance" and h.paroles
        assert {(c.name, c.role, c.source) for c in v.credits} == {
            ("Diam's", CreditRole.WRITER, "heritage")
        }
        assert v.lyrics.text == "Paroles de Suzy" and v.lyrics.source == "heritage:7"
        assert v.lyrics.synced is None  # jamais la synchro

    def test_radio_edit_herite_tout_sauf_la_video(self):
        v = _version("Suzy (Bonus Track)")
        vh.heriter(v, _socle())
        assert {c.role for c in v.credits} == {
            CreditRole.WRITER,
            CreditRole.PRODUCER,
            CreditRole.MIXING_ENGINEER,
        }

    def test_instrumental_sans_paroles_avec_constat(self):
        v = _version("Suzy (Instrumental)")
        h = vh.heriter(v, _socle())
        assert not h.paroles and v.lyrics.text is None and v.lyrics.instrumental is True
        assert {c.role for c in v.credits} == {
            CreditRole.WRITER,
            CreditRole.PRODUCER,
            CreditRole.MIXING_ENGINEER,
        }

    def test_remix_tiers_n_herite_que_l_ecriture(self):
        v = _version("Suzy - DJ X Remix")
        h = vh.heriter(v, _socle())
        assert not h.paroles and [c.role for c in v.credits] == [CreditRole.WRITER]

    def test_demo_alternate_snippet_n_heritent_de_rien(self):
        """Décisions utilisateur 2026-09-28 (paroles) puis 2026-10-09 (auteurs) :
        autre prise, souvent autre texte, et une équipe qui a pu s'agrandir
        après la démo (« Hurricane (Donda Demo) »)."""
        for titre in ("Suzy (Demo)", "Suzy [V3]", "Suzy [Snippet]"):
            v = _version(titre)
            h = vh.heriter(v, _socle())
            assert not h.paroles and v.lyrics.text is None, titre
            assert v.credits == [], titre

    def test_une_reference_n_herite_de_rien(self):
        v = _version("Suzy (Kendrick Lamar Reference)")
        h = vh.heriter(v, _socle())
        assert h.famille == "reference" and not h.paroles and v.credits == []

    def test_union_jamais_remplacement(self):
        v = _version("Suzy - Live 2006")
        v.credits = [Credit(name="Diam's", role=CreditRole.WRITER, source="discogs")]
        v.lyrics.text = "Paroles propres"
        h = vh.heriter(v, _socle())
        assert h.vide
        assert [c.source for c in v.credits] == ["discogs"]
        assert v.lyrics.text == "Paroles propres"

    def test_on_n_herite_pas_d_un_heritage(self):
        v = _version("Suzy - Live 2006")
        vh.heriter(v, _socle())
        assert "Vieux" not in {c.name for c in v.credits}

    def test_pas_de_socle_ou_pas_une_version(self):
        assert vh.heriter(_version("Suzy"), _socle()).vide
        assert vh.heriter(_version("Suzy - Live"), None).vide

    def test_est_herite(self):
        assert vh.est_herite("heritage:7") and vh.est_herite("heritage")
        assert not vh.est_herite("genius") and not vh.est_herite(None)


class TestHeritageCouvert:
    """Un crédit hérité s'efface dès qu'une source DIRECTE crédite sa famille
    (A2H « Le cœur des filles (Acoustic) », 2026-09-24)."""

    def _c(self, nom, role, source):
        return Credit(name=nom, role=role, source=source)

    def test_l_ecriture_directe_chasse_l_ecriture_heritee(self):
        credits = [
            self._c("Matthieu Cabaret", CreditRole.COMPOSER, "heritage"),
            self._c("A2H", CreditRole.WRITER, "heritage"),
            self._c("Clyde Bessi", CreditRole.COMPOSER, "youtube_topic"),
        ]
        assert [c.name for c in vh.sans_heritage_couvert(credits)] == ["Clyde Bessi"]

    def test_famille_par_famille(self):
        """Une production directe ne chasse pas l'écriture héritée."""
        credits = [
            self._c("A2H", CreditRole.WRITER, "heritage"),
            self._c("Clyde Bessi", CreditRole.PRODUCER, "youtube_topic"),
        ]
        assert len(vh.sans_heritage_couvert(credits)) == 2

    def test_sans_source_directe_l_heritage_reste(self):
        credits = [self._c("A2H", CreditRole.WRITER, "heritage")]
        assert vh.sans_heritage_couvert(credits) == credits
