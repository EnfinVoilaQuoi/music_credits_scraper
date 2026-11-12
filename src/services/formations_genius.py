"""Fin d'étape discographie : l'ID Genius des formations de l'artiste, et la
liste Genius de leurs morceaux (gardée dans le brut) qui mesure leur nature
(CORRECTIFS 2026-11-12, « discographies des formations »).

1. L'ID se lit dans les morceaux du membre (`formations_genius.id_par_les_morceaux`,
   hors ligne, depuis le brut de sa discographie) : prouvé par construction.
2. Repli pour une formation absente de ses morceaux : recherche Genius par nom,
   un candidat n'est gardé que si un morceau dont il est principal crédite le
   membre ; sinon (aucun, plusieurs), le lien est SIGNALÉ, jamais deviné
   (« Soleil Noir » : la recherche prend le rappeur solo).
3. La liste de la formation est lue une fois quand sa nature reste à décider
   (lien proposé, ou nature posée par règle) : la règle de nature la lit
   ensuite dans le brut, hors ligne (`politiques_revue`).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.utils import formations_genius as fg
from src.utils.logger import get_logger

logger = get_logger(__name__)

STATUTS_LUS = ("confirmed", "proposed")


@dataclass
class BilanFormations:
    poses: list[str] = field(default_factory=list)
    signales: list[str] = field(default_factory=list)
    listes_lues: list[str] = field(default_factory=list)
    #: Natures posées par règle alignées sur la formation (`aligner_natures`).
    alignees: list[str] = field(default_factory=list)


def nature_a_decider(rel) -> bool:
    """Une règle peut encore choisir la nature : lien proposé sans nature
    humaine, ou nature posée par une règle. Une nature humaine ne bouge jamais."""
    if rel.formation is None:
        return True
    return (rel.formation_origine or "").startswith("regle:")


def par_la_recherche(genius, nom: str, membre_id: int, pages_du_membre: set[int]):
    """L'ID d'une formation trouvé par la recherche Genius, PROUVÉ : un morceau
    dont le candidat est principal crédite le membre. None si aucun ou
    plusieurs candidats prouvés."""
    cle = fg.cle_de_nom(nom)
    prouves = []
    for candidat in genius.search_artist_candidates(nom):
        if not candidat.genius_id or fg.cle_de_nom(candidat.name) != cle:
            continue
        chansons = genius.chansons_brutes(candidat.genius_id)
        if not chansons:
            continue
        pages = [c for c in chansons if int(candidat.genius_id) in fg.ids_principaux(c)]
        if any(fg.credite(c, membre_id, pages_du_membre) for c in pages):
            prouves.append(int(candidat.genius_id))
    return prouves[0] if len(prouves) == 1 else None


def mesurer_nature(dm, nom: str, *, chemin_brut=None, index_membres=None):
    """La nature de la formation `nom` selon la règle validée
    (`formations_genius.nature_par_les_parts`), HORS LIGNE : sa liste Genius et
    celles de nos membres sont lues dans le brut, son nombre de membres aussi
    (`formations_membres`, `index_membres` lu une fois par l'appelant qui
    mesure plusieurs formations). None si la formation n'a pas d'ID ou que sa
    liste n'a jamais été lue (rien à mesurer)."""
    from src.utils import formations_membres as fm

    formation_id, membres = dm.membres_de_formation(nom)
    if not formation_id:
        return None
    pages = fg.pages_de_la_formation(fg.chansons_du_brut(formation_id, chemin_brut), formation_id)
    if not pages:
        return None
    parts = [
        fg.part_du_membre(
            membre,
            pages,
            membre_id,
            {int(c["id"]) for c in fg.chansons_du_brut(membre_id, chemin_brut)},
        )
        for membre, membre_id in membres
    ]
    index = index_membres if index_membres is not None else fm.index_du_brut(chemin_brut)
    return fg.nature_par_les_parts(parts, fm.nombre_de_membres(*index.compte(nom)))


def aligner_natures(dm, noms, *, index_membres=None, appliquer: bool = True) -> list[str]:
    """UNE nature par formation, chez tous nos membres (question utilisateur du
    2026-11-13 : un membre à 85 %, un autre à 60 %, la formation n'est pas un
    groupe pour l'un et un collectif pour l'autre). La règle mesure déjà tous
    nos membres ensemble ; ici, les natures POSÉES PAR RÈGLE suivent : la nature
    humaine de la formation s'il y en a une chez un membre, sinon la mesure
    d'aujourd'hui (un nouveau membre la change), un « groupe » que la mesure ne
    prouve plus redevenant « collectif » (le sens sans risque). Une nature
    humaine ne bouge jamais (verrou de `set_relation_status`). Rend les
    changements, « formation : avant → après »."""
    faits = []
    for nom in noms:
        regle = [
            lien
            for lien in dm.liens_de_formation(nom)
            if lien["formation"] and (lien["formation_origine"] or "").startswith("regle:")
        ]
        if not regle:
            continue
        humaine = dm.nature_connue_pour(nom, humaine=True)
        if humaine:
            cible, origine = humaine, "regle:nature_connue"
        else:
            verdict = mesurer_nature(dm, nom, index_membres=index_membres)
            if verdict is None:
                continue
            cible, origine = verdict.nature or fg.COLLECTIF, "regle:nature_genius"
        for lien in regle:
            if lien["formation"] == cible:
                continue
            if appliquer:
                dm.set_relation_status(
                    lien["artist_id"],
                    lien["related_name"],
                    "member_of",
                    "confirmed",
                    cible,
                    origine=origine,
                )
            faits.append(f"{nom} : {lien['formation']} → {cible}")
    if faits:
        logger.info(f"🎤 Natures de formation alignées : {', '.join(faits)}")
    return faits


def poser_ids_genius(
    dm, artist, genius=None, *, chemin_brut=None, appliquer: bool = True
) -> BilanFormations:
    """Pose l'ID Genius des formations de `artist` qui n'en ont pas, et lit la
    liste de celles dont la nature reste à décider. `genius` None = hors ligne
    (rattrapage depuis le brut : ni repli, ni lecture). `appliquer` False :
    à blanc, rien n'est écrit en base."""
    bilan = BilanFormations()
    if not artist.genius_id or artist.id is None:
        return bilan
    chansons = fg.chansons_du_brut(artist.genius_id, chemin_brut)
    pages_du_membre = {int(c["id"]) for c in chansons}
    for rel in dm.get_artist_relations(artist.id, status=None):
        if rel.kind != "member_of" or rel.status not in STATUTS_LUS:
            continue
        gid = rel.genius_id
        if gid is None:
            trouve = fg.id_par_les_morceaux(chansons, rel.related_name)
            gid = trouve.genius_id
            if gid is None and genius is not None and not trouve.ambigu:
                gid = par_la_recherche(genius, rel.related_name, artist.genius_id, pages_du_membre)
            if gid is None:
                motif = (
                    f"plusieurs ID au même nom {trouve.candidats}"
                    if trouve.ambigu
                    else "aucun morceau qui crédite le membre"
                )
                logger.warning(f"🎤 Formation « {rel.related_name} » sans ID Genius : {motif}")
                bilan.signales.append(f"{rel.related_name} ({motif})")
                continue
            if not appliquer or dm.poser_genius_id_formation(
                artist.id, rel.related_name, rel.kind, gid
            ):
                bilan.poses.append(f"{rel.related_name} → {gid}")
        if (
            genius is not None
            and nature_a_decider(rel)
            and not fg.chansons_du_brut(gid, chemin_brut)
            and genius.chansons_brutes(gid) is not None
        ):
            bilan.listes_lues.append(rel.related_name)
    if bilan.poses:
        logger.info(f"🎤 ID Genius de formation(s) : {', '.join(bilan.poses)}")
    if appliquer:
        bilan.alignees = aligner_natures(
            dm,
            [r.related_name for r in dm.get_artist_relations(artist.id) if r.kind == "member_of"],
        )
    return bilan
