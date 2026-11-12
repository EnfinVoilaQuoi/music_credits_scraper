"""ID Genius des formations et règle de nature (CORRECTIFS 2026-11-12,
« discographies des formations »)."""

import json

from src.models import Artist, ArtistRelation
from src.persistence import brut
from src.services import formations_genius as service
from src.utils import formations_genius as fg

MEMBRE = 100
GROUPE_ID = 200
HOMONYME_ID = 300


def _chanson(sid, titre, principal, nom, invites=()):
    return {
        "id": sid,
        "title": titre,
        "primary_artist": {"id": principal, "name": nom},
        "primary_artists": [{"id": principal, "name": nom}],
        "featured_artists": [{"id": i, "name": f"#{i}"} for i in invites],
    }


def _garder_liste(genius_id, chansons, page=1, suivante=None):
    corps = json.dumps({"response": {"songs": chansons, "next_page": suivante}}).encode()
    url = f"{fg.prefixe_de_liste(genius_id)}page={page}&per_page=50&sort=release_date"
    brut.consigner(fg.SOURCE, url, 200, corps)
    brut.fermer()


# ── L'ID, par les morceaux du membre ─────────────────────────────────────────


def test_l_id_se_lit_dans_les_morceaux_du_membre_suffixe_regional_compris():
    chansons = [
        _chanson(1, "A", GROUPE_ID, "Soleil Noir (FRA)"),
        _chanson(2, "B", GROUPE_ID, "Soleil Noir (FRA)"),
        _chanson(3, "C", MEMBRE, "Membre"),
    ]
    trouve = fg.id_par_les_morceaux(chansons, "Soleil Noir")
    assert (trouve.genius_id, trouve.pages) == (GROUPE_ID, 2)


def test_deux_id_au_meme_nom_rien_n_est_pose():
    chansons = [
        _chanson(1, "A", GROUPE_ID, "Soleil Noir (FRA)"),
        _chanson(2, "B", HOMONYME_ID, "Soleil Noir"),
    ]
    trouve = fg.id_par_les_morceaux(chansons, "Soleil Noir")
    assert trouve.genius_id is None and trouve.ambigu


def test_les_pages_du_brut_sont_reunies_la_plus_recente_gagne():
    _garder_liste(MEMBRE, [_chanson(1, "A", MEMBRE, "M")], page=1, suivante=2)
    _garder_liste(MEMBRE, [_chanson(2, "B", GROUPE_ID, "G")], page=2)
    assert {c["id"] for c in fg.chansons_du_brut(MEMBRE)} == {1, 2}
    # Un autre artiste dont l'ID commence pareil n'est pas lu.
    assert fg.chansons_du_brut(10) == []


# ── La règle de nature ───────────────────────────────────────────────────────


def _part(nom, credites, total=20):
    return fg.Part(nom, credites, total)


def test_tous_au_dessus_de_75_pour_cent_groupe():
    assert fg.nature_par_les_parts([_part("A", 19), _part("B", 16)]).nature == fg.GROUPE


def test_un_membre_sous_la_moitie_collectif():
    assert fg.nature_par_les_parts([_part("A", 19), _part("B", 6)]).nature == fg.COLLECTIF


def test_entre_les_deux_seuils_l_humain():
    verdict = fg.nature_par_les_parts([_part("A", 19), _part("B", 13)])
    assert verdict.nature is None and not verdict.signale


def test_peu_de_morceaux_l_humain():
    assert fg.nature_par_les_parts([_part("A", 7, total=7)]).nature is None


def test_deux_membres_groupe():
    assert fg.nature_par_les_parts([_part("A", 19), _part("B", 6)], nb_membres=2).nature == (
        fg.GROUPE
    )


def test_une_part_nulle_est_signalee_jamais_tranchee():
    verdict = fg.nature_par_les_parts([_part("A", 19), _part("B", 0)], nb_membres=2)
    assert verdict.nature is None and verdict.signale


def test_un_morceau_credite_par_la_liste_du_membre_ou_comme_invite():
    pages = [
        _chanson(1, "A", GROUPE_ID, "G"),
        _chanson(2, "B", GROUPE_ID, "G", invites=(MEMBRE,)),
        _chanson(3, "C", GROUPE_ID, "G"),
        _chanson(4, "Intro", GROUPE_ID, "G"),
    ]
    pages = fg.pages_de_la_formation(pages, GROUPE_ID)
    part = fg.part_du_membre("M", pages, MEMBRE, {1})
    assert (part.credites, part.total) == (2, 3)  # l'intro n'est pas comptée


# ── Le service : pose, repli, signalement ────────────────────────────────────


class _GeniusFactice:
    def __init__(self, candidats, listes):
        self.candidats, self.listes, self.lues = candidats, listes, []

    def search_artist_candidates(self, nom):
        return [Artist(name=n, genius_id=i) for n, i in self.candidats]

    def chansons_brutes(self, genius_id):
        self.lues.append(genius_id)
        return self.listes.get(genius_id)


def _membre(data_manager, *formations):
    artiste = Artist(name="Membre", genius_id=MEMBRE)
    artiste.id = data_manager.save_artist(artiste)
    data_manager.propose_artist_relations(
        artiste.id, [ArtistRelation(related_name=f, kind="member_of") for f in formations]
    )
    return artiste


def _ids(data_manager, artiste):
    return {
        r.related_name: r.genius_id
        for r in data_manager.get_artist_relations(artiste.id, status=None)
    }


def test_la_fin_de_discographie_pose_l_id_prouve(data_manager):
    _garder_liste(MEMBRE, [_chanson(1, "A", GROUPE_ID, "Le Groupe")])
    artiste = _membre(data_manager, "Le Groupe")
    bilan = service.poser_ids_genius(data_manager, artiste)
    assert _ids(data_manager, artiste) == {"Le Groupe": GROUPE_ID}
    assert bilan.poses == [f"Le Groupe → {GROUPE_ID}"]


def test_a_blanc_rien_n_est_ecrit(data_manager):
    _garder_liste(MEMBRE, [_chanson(1, "A", GROUPE_ID, "Le Groupe")])
    artiste = _membre(data_manager, "Le Groupe")
    bilan = service.poser_ids_genius(data_manager, artiste, appliquer=False)
    assert bilan.poses and _ids(data_manager, artiste) == {"Le Groupe": None}


def test_le_repli_par_le_nom_ne_garde_que_le_candidat_prouve(data_manager):
    """« Soleil Noir » : la recherche rend le rappeur solo ET le groupe ; seul
    le groupe a un morceau qui crédite le membre."""
    _garder_liste(MEMBRE, [_chanson(1, "Solo", MEMBRE, "Membre")])
    artiste = _membre(data_manager, "Soleil Noir")
    genius = _GeniusFactice(
        [("Soleil Noir", HOMONYME_ID), ("Soleil Noir (FRA)", GROUPE_ID)],
        {
            HOMONYME_ID: [_chanson(9, "X", HOMONYME_ID, "Soleil Noir")],
            GROUPE_ID: [_chanson(8, "Y", GROUPE_ID, "Soleil Noir (FRA)", invites=(MEMBRE,))],
        },
    )
    service.poser_ids_genius(data_manager, artiste, genius)
    assert _ids(data_manager, artiste) == {"Soleil Noir": GROUPE_ID}


def test_sans_preuve_la_formation_est_signalee(data_manager):
    _garder_liste(MEMBRE, [_chanson(1, "Solo", MEMBRE, "Membre")])
    artiste = _membre(data_manager, "Soleil Noir")
    genius = _GeniusFactice(
        [("Soleil Noir", HOMONYME_ID)], {HOMONYME_ID: [_chanson(9, "X", HOMONYME_ID, "S")]}
    )
    bilan = service.poser_ids_genius(data_manager, artiste, genius)
    assert _ids(data_manager, artiste) == {"Soleil Noir": None}
    assert bilan.signales and "Soleil Noir" in bilan.signales[0]


def test_la_liste_d_une_formation_a_la_nature_humaine_n_est_pas_lue(data_manager):
    _garder_liste(MEMBRE, [_chanson(1, "A", GROUPE_ID, "Le Groupe")])
    artiste = Artist(name="Membre", genius_id=MEMBRE)
    artiste.id = data_manager.save_artist(artiste)
    data_manager.record_artist_relations(
        artiste.id, [ArtistRelation(related_name="Le Groupe", formation="groupe")]
    )
    genius = _GeniusFactice([], {GROUPE_ID: []})
    service.poser_ids_genius(data_manager, artiste, genius)
    assert genius.lues == []


def test_la_nature_posee_par_une_regle_garde_son_origine(data_manager):
    artiste = _membre(data_manager, "Le Groupe")
    data_manager.set_relation_status(
        artiste.id, "Le Groupe", "member_of", "confirmed", "groupe", origine="regle:x"
    )
    (rel,) = data_manager.get_artist_relations(artiste.id)
    assert (rel.formation, rel.formation_origine) == ("groupe", "regle:x")
    # Un geste humain (sans origine) sur la nature la rend humaine.
    data_manager.set_relation_status(artiste.id, "Le Groupe", "member_of", "confirmed", "collectif")
    (rel,) = data_manager.get_artist_relations(artiste.id)
    assert (rel.formation, rel.formation_origine) == ("collectif", None)


# ── Le correcteur du panneau : la nature mesurée, notée posée par règle ──────


def test_le_correcteur_pose_la_nature_mesuree_avec_son_origine(data_manager):
    from src.services import revue, revue_auto

    groupe = [_chanson(i, f"T{i}", GROUPE_ID, "Le Groupe") for i in range(1, 11)]
    _garder_liste(GROUPE_ID, groupe)
    _garder_liste(MEMBRE, groupe[:9])  # 9 / 10 : groupe
    artiste = _membre(data_manager, "Le Groupe")
    data_manager.poser_genius_id_formation(artiste.id, "Le Groupe", "member_of", GROUPE_ID)
    cas = revue.Cas(
        "liens_proposes", None, "M", "", 0, {"nom": "Le Groupe", "kind": "member_of"}, "k"
    )
    compte_rendu, annulation = revue_auto._confirmer_formation(
        data_manager, None, cas, artiste=artiste
    )
    (rel,) = data_manager.get_artist_relations(artiste.id)
    assert (rel.formation, rel.formation_origine) == ("groupe", "regle:nature_genius")
    assert annulation["nature_posee"] and "(groupe)" in compte_rendu


def test_le_correcteur_laisse_une_nature_indecise(data_manager):
    import pytest

    from src.services import revue, revue_auto

    groupe = [_chanson(i, f"T{i}", GROUPE_ID, "Le Groupe") for i in range(1, 11)]
    _garder_liste(GROUPE_ID, groupe)
    _garder_liste(MEMBRE, groupe[:6])  # 60 % : entre les deux seuils
    artiste = _membre(data_manager, "Le Groupe")
    data_manager.poser_genius_id_formation(artiste.id, "Le Groupe", "member_of", GROUPE_ID)
    cas = revue.Cas(
        "liens_proposes", None, "M", "", 0, {"nom": "Le Groupe", "kind": "member_of"}, "k"
    )
    with pytest.raises(LookupError):
        revue_auto._confirmer_formation(data_manager, None, cas, artiste=artiste)
    (rel,) = data_manager.get_artist_relations(artiste.id, status=None)
    assert rel.status == "proposed" and rel.formation is None


# ── Verrou : une règle ne touche jamais une nature choisie à la main ─────────


def _lien_humain(data_manager, nature="collectif"):
    artiste = _membre(data_manager, "Le Groupe")
    data_manager.set_relation_status(artiste.id, "Le Groupe", "member_of", "confirmed", nature)
    return artiste


def _nature(data_manager, artiste):
    (rel,) = data_manager.get_artist_relations(artiste.id, status=None)
    return rel.formation, rel.formation_origine


def test_une_regle_ne_change_pas_une_nature_humaine(data_manager):
    artiste = _lien_humain(data_manager)
    data_manager.set_relation_status(
        artiste.id, "Le Groupe", "member_of", "confirmed", "groupe", origine="regle:x"
    )
    assert _nature(data_manager, artiste) == ("collectif", None)


def test_une_regle_n_efface_pas_une_nature_humaine(data_manager):
    artiste = _lien_humain(data_manager)
    data_manager.set_relation_status(
        artiste.id, "Le Groupe", "member_of", "proposed", effacer_nature=True, origine="regle:x"
    )
    assert _nature(data_manager, artiste) == ("collectif", None)


def test_une_regle_defait_sa_propre_nature(data_manager):
    artiste = _membre(data_manager, "Le Groupe")
    data_manager.set_relation_status(
        artiste.id, "Le Groupe", "member_of", "confirmed", "groupe", origine="regle:x"
    )
    data_manager.set_relation_status(
        artiste.id, "Le Groupe", "member_of", "proposed", effacer_nature=True, origine="regle:y"
    )
    assert _nature(data_manager, artiste) == (None, None)


def test_la_nature_choisie_chez_un_autre_membre_passe_avant_la_mesure(data_manager):
    """667 (2026-11-13) : collectif choisi à la main chez Freeze Corleone, la
    mesure dirait groupe (76 % à 75 %) ; un nouveau membre reçoit collectif."""
    from src.services import revue, revue_auto

    groupe = [_chanson(i, f"T{i}", GROUPE_ID, "Le Groupe") for i in range(1, 11)]
    _garder_liste(GROUPE_ID, groupe)
    _garder_liste(MEMBRE, groupe)  # 100 % : la mesure dirait groupe
    ancien = Artist(name="Ancien", genius_id=999)
    ancien.id = data_manager.save_artist(ancien)
    data_manager.record_artist_relations(
        ancien.id, [ArtistRelation(related_name="Le Groupe", formation="collectif")]
    )
    artiste = _membre(data_manager, "Le Groupe")
    data_manager.poser_genius_id_formation(artiste.id, "Le Groupe", "member_of", GROUPE_ID)
    natures = revue.natures_des_formations(
        data_manager, data_manager.get_artist_relations(artiste.id, status="proposed")
    )
    assert natures["Le Groupe"].nature == "collectif"
    cas = revue.Cas(
        "liens_proposes", None, "M", "", 0, {"nom": "Le Groupe", "kind": "member_of"}, "k"
    )
    revue_auto._confirmer_formation(data_manager, None, cas, artiste=artiste)
    assert _nature(data_manager, artiste) == ("collectif", "regle:nature_connue")


# ── Nombre de membres (MusicBrainz, Discogs) : « 2 membres ⇒ groupe » ────────


def test_le_nombre_de_membres_suit_musicbrainz_sauf_si_discogs_en_donne_plus():
    from src.utils.formations_membres import nombre_de_membres

    assert nombre_de_membres(2, None) == 2
    assert nombre_de_membres(2, 2) == 2
    assert nombre_de_membres(2, 3) == 3  # liste incomplète : pas un duo
    assert nombre_de_membres(7, 5) == 7
    assert nombre_de_membres(None, 2) is None  # Discogs seul ne suffit pas


def _mb_lookup(mbid, relations):
    from src.api.musicbrainz_api import MEMBRE_DE_GROUPE

    corps = {
        "relations": [
            {
                "type-id": MEMBRE_DE_GROUPE,
                "direction": sens,
                "artist": {"id": cible_id, "name": nom, "type": "Group"},
            }
            for sens, cible_id, nom in relations
        ]
    }
    url = f"https://musicbrainz.org/ws/2/artist/{mbid}?fmt=json&inc=artist-rels"
    brut.consigner("musicbrainz", url, 200, json.dumps(corps).encode())
    brut.fermer()


def _discogs_page(gid, groupes=(), membres=()):
    corps = {
        "id": gid,
        "groups": [{"id": i, "name": n} for i, n in groupes],
        "members": [{"id": 0, "name": n} for n in membres],
    }
    brut.consigner(
        "discogs", f"https://api.discogs.com/artists/{gid}", 200, json.dumps(corps).encode()
    )
    brut.fermer()


MB_MEMBRE = "00000000-0000-0000-0000-000000000001"
MB_DUO = "00000000-0000-0000-0000-000000000002"


def test_le_brut_compte_les_membres_par_la_fiche_du_membre():
    from src.utils.formations_membres import index_du_brut

    _mb_lookup(MB_MEMBRE, [("forward", MB_DUO, "Le Duo")])
    _mb_lookup(MB_DUO, [("backward", "a", "Membre"), ("backward", "b", "Autre")])
    _discogs_page(10, groupes=[(20, "Le Duo (3)")])
    _discogs_page(20, membres=["Membre", "Autre", "Troisième"])
    assert index_du_brut().compte("Le Duo") == (2, 3)


def test_deux_membres_font_un_groupe_meme_a_60_pour_cent(data_manager):
    from src.services.formations_genius import mesurer_nature

    groupe = [_chanson(i, f"T{i}", GROUPE_ID, "Le Duo") for i in range(1, 11)]
    _garder_liste(GROUPE_ID, groupe)
    _garder_liste(MEMBRE, groupe[:6])
    artiste = _membre(data_manager, "Le Duo")
    data_manager.poser_genius_id_formation(artiste.id, "Le Duo", "member_of", GROUPE_ID)
    assert mesurer_nature(data_manager, "Le Duo").nature is None  # nombre inconnu
    _mb_lookup(MB_MEMBRE, [("forward", MB_DUO, "Le Duo")])
    _mb_lookup(MB_DUO, [("backward", "a", "Membre"), ("backward", "b", "Autre")])
    assert mesurer_nature(data_manager, "Le Duo").nature == "groupe"


# ── Une nature par formation, chez tous nos membres ──────────────────────────


def _deux_membres(data_manager, nature_a, origine_a):
    a = Artist(name="A", genius_id=MEMBRE)
    a.id = data_manager.save_artist(a)
    b = Artist(name="B", genius_id=MEMBRE + 1)
    b.id = data_manager.save_artist(b)
    for x in (a, b):
        data_manager.propose_artist_relations(
            x.id, [ArtistRelation(related_name="Le Groupe", kind="member_of")]
        )
        data_manager.poser_genius_id_formation(x.id, "Le Groupe", "member_of", GROUPE_ID)
    data_manager.set_relation_status(
        a.id, "Le Groupe", "member_of", "confirmed", nature_a, origine=origine_a
    )
    return a, b


def test_un_nouveau_membre_peu_credite_ramene_le_groupe_en_collectif(data_manager):
    """A seul à 90 % : groupe par règle ; B arrive à 30 % : collectif chez A."""
    groupe = [_chanson(i, f"T{i}", GROUPE_ID, "Le Groupe") for i in range(1, 11)]
    _garder_liste(GROUPE_ID, groupe)
    _garder_liste(MEMBRE, groupe[:9])
    _garder_liste(MEMBRE + 1, groupe[:3])
    a, _b = _deux_membres(data_manager, "groupe", "regle:nature_genius")
    faits = service.aligner_natures(data_manager, ["Le Groupe"])
    assert faits == ["Le Groupe : groupe → collectif"]
    assert _nature(data_manager, a) == ("collectif", "regle:nature_genius")


def test_une_nature_humaine_n_est_pas_alignee(data_manager):
    groupe = [_chanson(i, f"T{i}", GROUPE_ID, "Le Groupe") for i in range(1, 11)]
    _garder_liste(GROUPE_ID, groupe)
    _garder_liste(MEMBRE, groupe[:9])
    _garder_liste(MEMBRE + 1, groupe[:3])
    a, _b = _deux_membres(data_manager, "groupe", None)
    assert service.aligner_natures(data_manager, ["Le Groupe"]) == []
    assert _nature(data_manager, a) == ("groupe", None)


def test_une_nature_de_regle_suit_la_nature_humaine_d_un_autre_membre(data_manager):
    a, b = _deux_membres(data_manager, "groupe", "regle:nature_genius")
    data_manager.set_relation_status(b.id, "Le Groupe", "member_of", "confirmed", "collectif")
    service.aligner_natures(data_manager, ["Le Groupe"])
    assert _nature(data_manager, a) == ("collectif", "regle:nature_connue")
