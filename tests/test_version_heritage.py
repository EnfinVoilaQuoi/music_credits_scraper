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
        assert vh.famille_de("Suzy (Version Radio)") == "edition"
        assert vh.famille_de("Suzy - Live Version") == "performance"  # la prise l'emporte
        assert vh.famille_de("Suzy (Instrumental)") == "sans_voix"
        assert vh.famille_de("MW2 - Chopped & $crewed") == "chopped"
        assert vh.famille_de("Dolce Camara - Snight B Remix") == "remix_named"
        assert vh.famille_de("Suzy") is None


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
        v = _version("Suzy (Version Radio)")
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
