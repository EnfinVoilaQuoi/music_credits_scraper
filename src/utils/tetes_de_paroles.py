"""Le juge des têtes de section ajoutées à tort aux paroles Genius.

Jusqu'au 2026-11-07, le scraper ajoutait « : <artiste de la ligne> » à toute tête
sans attribution (« [Couplet 1 : Booba] » sur « Fenwick » de Niro). Ce module dit,
fiche par fiche, quelles têtes nommées ont été FABRIQUÉES et ce qu'elles
deviennent ; deux lecteurs :

- `scripts/corriger_tetes_de_paroles.py` (rattrapage, avec le brut des pages) ;
- l'audit des données (`services.audit.tetes_fabriquees`, invariant), sans le
  brut : le seul témoin `pas_interprete`.

PUR : aucune lecture de base ni de brut ici.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass

NOM_FAUX = "nom_faux"
HORS_SECTION = "hors_section"
TEMOIN_BRUT = "brut"
TEMOIN_PAS_INTERPRETE = "pas_interprete"
#: La séparation qu'écrit le scraper (et que les pages françaises écrivent aussi).
_SEPARATION = " : "


@dataclass
class Fiche:
    id: int
    title: str
    artiste: str
    principal: str | None
    est_feat: bool | None
    role: str | None
    url: str | None
    genius_id: int | None
    paroles: str


@dataclass(frozen=True)
class Changement:
    ancienne: str
    nouvelle: str
    classe: str
    temoin: str


def _couper(etiquette: str) -> tuple[str, str] | None:
    """`(base, nom)` d'une étiquette en « base : nom », sinon None. Une base qui a
    elle-même un « : » n'a pas pu être nommée par le scraper, qui la laissait."""
    if _SEPARATION not in etiquette:
        return None
    base, nom = etiquette.rsplit(_SEPARATION, 1)
    return None if ":" in base else (base.rstrip(), nom.strip())


def _meme_nom(a: str | None, b: str | None) -> bool:
    return bool(a) and bool(b) and a.strip().casefold() == b.strip().casefold()


def interprete_de_la_famille(lignes: list[Fiche]) -> tuple[str | None, bool]:
    """`(interprète, d'accord)` : ce que le scraper écrit depuis n'importe quelle
    ligne de l'enregistrement. Pas d'accord → la famille est laissée de côté."""
    from src.utils.paroles_genius import interprete_des_sections

    noms = {
        nom
        for f in lignes
        if (nom := interprete_des_sections(f.artiste, f.principal, f.est_feat, f.role))
    }
    if len({n.casefold() for n in noms}) > 1:
        return None, False
    return (min(noms) if noms else None), True


def _seule_la_ligne_est_nommee(fiche: Fiche) -> bool:
    """Aucune tête n'attribue à un autre : là où une page en attribue elle-même à
    d'autres, elle peut aussi nommer la ligne (Casseurs Flowters : « [Couplet 2 :
    Gringe] » et « [Refrain : Orelsan] », sur « Toc toc »)."""
    from src.utils.paroles_genius import _LIGNE_DE_TETE

    return all(
        _meme_nom(etiquette.rsplit(":", 1)[1], fiche.artiste)
        for etiquette in _LIGNE_DE_TETE.findall(fiche.paroles)
        if ":" in etiquette
    )


def temoin_de_la_tete(fiche: Fiche, base: str, nom: str, tetes_du_brut: set[str] | None) -> str:
    """Le témoin que cette tête nommée a été FABRIQUÉE, ou "" s'il n'y en a pas."""
    if tetes_du_brut is not None:
        complete = f"{base}{_SEPARATION}{nom}"
        return TEMOIN_BRUT if complete not in tetes_du_brut and base in tetes_du_brut else ""
    nomme_la_ligne_seule = (
        fiche.est_feat is False
        and fiche.principal
        and not _meme_nom(fiche.principal, fiche.artiste)
        and _meme_nom(nom, fiche.artiste)
        and _seule_la_ligne_est_nommee(fiche)
    )
    return TEMOIN_PAS_INTERPRETE if nomme_la_ligne_seule else ""


def plan_de_la_fiche(
    fiche: Fiche, tetes_du_brut: set[str] | None, interprete: str | None
) -> tuple[str, list[Changement]]:
    """PUR. `(texte corrigé, changements)` d'une fiche. `tetes_du_brut` : les
    étiquettes de la page gardée, sans espaces autour ; None = pas de brut."""
    from src.utils.paroles_genius import _LIGNE_DE_TETE, attribuer_sections, est_tete_de_section

    changements: list[Changement] = []

    def corriger(tete) -> str:
        complete = tete.group(0)
        coupee = _couper(tete.group(1))
        if coupee is None:
            return complete
        base, nom = coupee
        temoin = temoin_de_la_tete(fiche, base.strip(), nom, tetes_du_brut)
        if not temoin:
            return complete
        nouvelle = attribuer_sections(f"[{base}]", interprete)
        if nouvelle.strip() == complete.strip():
            return complete
        classe = NOM_FAUX if est_tete_de_section(base) else HORS_SECTION
        changements.append(Changement(complete.strip(), nouvelle, classe, temoin))
        return nouvelle

    texte = _LIGNE_DE_TETE.sub(corriger, fiche.paroles)
    return texte, changements


def a_examiner(fiche: Fiche, interprete: str | None) -> bool:
    """Une tête nommée que ni son nom ni sa nature ne mettent hors de cause. Les
    autres (une section au nom de l'interprète) resteraient telles quelles : la
    page n'a pas à être relue pour elles."""
    from src.utils.paroles_genius import _LIGNE_DE_TETE, est_tete_de_section

    for etiquette in _LIGNE_DE_TETE.findall(fiche.paroles):
        coupee = _couper(etiquette)
        if coupee and not (_meme_nom(coupee[1], interprete) and est_tete_de_section(coupee[0])):
            return True
    return False


#: Les fiches que le juge regarde : paroles Genius qui ont au moins une tête.
#: Colonnes dans l'ordre de `Fiche` (après l'ID) ; `artiste` = nom de la ligne.
COLONNES_DES_FICHES = (
    "t.id, t.title, a.name, t.primary_artist_name, t.is_featuring, "
    "t.secondary_role, t.genius_url, t.genius_id, t.lyrics"
)
FICHES_A_TETES = "t.lyrics_source = 'genius' AND t.lyrics LIKE '%[%'"


def fiche_depuis(r) -> Fiche:
    """Une `Fiche` depuis une ligne aux `COLONNES_DES_FICHES`."""
    return Fiche(
        id=r[0],
        title=r[1],
        artiste=r[2],
        principal=r[3],
        est_feat=bool(r[4]),
        role=r[5],
        url=r[6],
        genius_id=r[7],
        paroles=r[8],
    )


@dataclass
class Bilan:
    plans: list[tuple[Fiche, str, list[Changement]]]
    sans_temoin: list[Fiche]
    discordantes: list[Fiche]
    #: Le témoin `pas_interprete` mesuré là où un brut le départage : têtes nommant
    #: la ligne chez des fiches « pas un feat, principal autre » ayant un brut.
    mesure_temoin: Counter


def planifier(fiches: list[Fiche], brut_des_pages: dict[str, set[str]]) -> Bilan:
    """PUR. Le plan de toutes les fiches, sachant ce que le brut a gardé."""
    familles: dict = defaultdict(list)
    for f in fiches:
        familles[f.genius_id or f"#{f.id}"].append(f)
    bilan = Bilan([], [], [], Counter())
    for lignes in familles.values():
        interprete, d_accord = interprete_de_la_famille(lignes)
        adresses = {f.url for f in lignes if f.url}
        vues = [brut_des_pages[u] for u in adresses if u in brut_des_pages]
        tetes = set().union(*vues) if vues else None
        for f in lignes:
            if not a_examiner(f, interprete):
                continue
            if not d_accord:
                bilan.discordantes.append(f)
                continue
            _mesurer_le_temoin(f, tetes, bilan.mesure_temoin)
            texte, changements = plan_de_la_fiche(f, tetes, interprete)
            if changements:
                bilan.plans.append((f, texte, changements))
            elif tetes is None:
                bilan.sans_temoin.append(f)
    return bilan


def _mesurer_le_temoin(f: Fiche, tetes: set[str] | None, compte: Counter) -> None:
    from src.utils.paroles_genius import _LIGNE_DE_TETE

    if tetes is None:
        return
    for etiquette in _LIGNE_DE_TETE.findall(f.paroles):
        coupee = _couper(etiquette)
        if not coupee or not temoin_de_la_tete(f, coupee[0].strip(), coupee[1], None):
            continue
        compte["naturelle" if etiquette.strip() in tetes else "fabriquee"] += 1
