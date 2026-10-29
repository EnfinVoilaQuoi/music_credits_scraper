"""Morceaux qui en CONTIENNENT d'autres (chantier du 2026-10-29). Module PUR.

Un même modèle pour deux cas : un CONTENANT, des CONTENUS ; les vues et les
streams du contenant ne s'ajoutent jamais à ceux des contenus.

- Le MEDLEY : une fiche qui enchaîne plusieurs morceaux (« Vers le bas +
  Freestyle » de Flynt sur *Rétrographie*, les triples de Médine). Relation
  `contient` de la fiche medley vers CHAQUE fiche contenue (colonne
  `relationships`, forme unique `track_soeurs.serialiser_relations`, jamais de
  clé locale). Posée à la main (action `contient` des corrections de fiches,
  `scripts/appliquer_corrections_fiches.py`, exportée avec les décisions) ou
  proposée par la règle des titres joints (`proposer_contenus`). Une fiche
  contenue absente reste en attente : jamais créée. Un contenant SANS page
  Genius n'a ni crédits ni paroles à réclamer à Genius : ils sont sur les
  fiches contenues (`contenant_sans_page`, étape crédits et validation).
- La VIDÉO D'UN DISQUE ENTIER (« FULL EP », « Album entier », « FULL
  BEAT-TAPE »…) : comptée UNE fois au niveau de l'album
  (`streams_calculator.clips_doubles`), comme un clip double, jamais aux vues
  d'une fiche (`vues_comptees.vue_comptee`), jamais sa durée
  (`duree_youtube.NON_MORCEAU`).
"""

from __future__ import annotations

import re
import unicodedata

#: Type de la relation, du contenant vers chaque contenu.
TYPE = "contient"

#: Provenance d'une relation posée par la règle des titres joints.
ORIGINE_REGLE = "regle:titres_joints"


def relations_contient(track) -> list[dict]:
    return [
        r
        for r in (track.relationships or [])
        if isinstance(r, dict) and r.get("type") == TYPE and r.get("track_id")
    ]


def ids_contenus(track) -> list[int]:
    """Les fiches que `track` contient, dans l'ordre du titre."""
    return [int(r["track_id"]) for r in relations_contient(track)]


def est_contenant(track) -> bool:
    return bool(relations_contient(track))


def contenant_sans_page(track) -> bool:
    """Un contenant sans page Genius : crédits et paroles ne lui sont pas
    réclamés (13 fiches de *Rétrographie* échouaient aux crédits à chaque run)."""
    return est_contenant(track) and not track.genius_url


def contenants_par_contenu(tracks) -> dict[int, list]:
    """`{id de fiche contenue: [fiches qui la contiennent]}` — le lien dans
    l'autre sens, calculé et jamais stocké (une seule relation fait foi)."""
    index: dict[int, list] = {}
    for t in tracks:
        for tid in ids_contenus(t):
            index.setdefault(tid, []).append(t)
    return index


# ── Règle des titres joints (« A + B », « A / B ») ───────────────────────────

#: Jointure de deux titres, hors parenthèses (« LAST BREATH (First Pressing /
#: Single Version) » est UN morceau) : « + » ENTOURÉ d'espaces (« C+C » est un
#: titre) ; « / » avec ou sans espaces (« Chakras/We Free », les triples de
#: Médine), sauf entre deux chiffres (« 24/7 », « 11/09 », « 68 3/4 »).
_JOINTURE = re.compile(r"\s+\+\s+|(?<!\d)\s*/\s*|\s*/\s*(?!\d)")


def parties_du_titre(titre: str | None) -> list[str]:
    """« En froid + Relativise + Prouesses » → les trois titres ; [] si le
    titre n'en joint pas au moins deux."""
    from src.utils.title_matching import strip_featuring

    texte = strip_featuring(titre or "")
    profondeur, profondeurs = 0, []
    for car in texte:
        if car in ")]":
            profondeur = max(profondeur - 1, 0)
        profondeurs.append(profondeur)
        if car in "([":
            profondeur += 1
    parties, debut = [], 0
    for m in _JOINTURE.finditer(texte):
        if profondeurs[m.start()] == 0:
            parties.append(texte[debut : m.start()].strip())
            debut = m.end()
    parties.append(texte[debut:].strip())
    if len(parties) < 2 or not all(parties):
        return []
    return parties


def proposer_contenus(track, fiches) -> list | None:
    """Les fiches que la fiche `track` contient, d'après son titre — chaque
    partie a UNE fiche chez l'artiste (`fiches`), même titre à la graphie près
    (`titres_confondus` : même version, jamais une sous-chaîne) ; une partie
    au titre générique (« Intro ») exige le même disque. Une partie sans fiche,
    ou que deux pages portent, et rien n'est proposé : None."""
    from src.utils.title_matching import meme_album
    from src.utils.version_descriptors import titre_generique, titres_confondus

    parties = parties_du_titre(track.title)
    if not parties:
        return None
    candidates = [
        f for f in fiches if f.id is not None and f.id != track.id and not parties_du_titre(f.title)
    ]
    choisies = []
    for partie in parties:
        trouvees = [
            f
            for f in candidates
            if titres_confondus(partie, f.title)
            and (not titre_generique(partie) or meme_album(f.album, track.album))
        ]
        if len({f.genius_id or ("fiche", f.id) for f in trouvees}) != 1:
            return None
        choisies.append(trouvees[0])
    if len({f.id for f in choisies}) != len(choisies):
        return None
    return choisies


def relation_vers(contenu, artiste: str, origine: str | None = None) -> dict:
    """La relation `contient` vers la fiche `contenu`, sous LA forme stockée."""
    relation = {
        "type": TYPE,
        "title": contenu.title,
        "artist": contenu.primary_artist_name or artiste,
        "url": contenu.genius_url,
        "track_id": contenu.id,
    }
    if origine:
        relation["origine"] = origine
    return relation


# ── Vidéo d'un disque entier ─────────────────────────────────────────────────

_DISQUE = r"(?:e\.?p|album|lp|mix-?tape|beat-?tape|street-?album|projet)"

#: Le titre ANNONCE un disque entier : « (FULL EP) », « [FULL ALBUM] »,
#: « FULL-LENGTH MIXTAPE », « (Album entier) », « Mixtape complète ». Pas
#: « Full Version », « FULL SONG », « Full Video » (un morceau), ni « FULL
#: SET » / « FULL CONCERT » (une captation, pas un disque).
VIDEO_DE_DISQUE = re.compile(
    rf"\bfull[\s-]+(?:length[\s-]+)?{_DISQUE}\b"
    rf"|\b{_DISQUE}[\s-]+(?:en[\s-]+)?(?:entier|enti[eè]re|complet|compl[eè]te|int[ée]grale?)\b"
    rf"|\bint[ée]gralit[ée]\s+(?:de\s+)?(?:l['’]\s*|la\s+)?{_DISQUE}\b",
    re.IGNORECASE,
)

_GROUPES = re.compile(r"\s*[\(\[][^\(\)\[\]]*[\)\]]")


def _mots(texte: str) -> str:
    """Minuscules, sans accents ni ponctuation (« Vol.1 » → « vol 1 »)."""
    from src.utils.title_matching import developper_ligatures

    texte = developper_ligatures(texte)
    texte = unicodedata.normalize("NFKD", texte).encode("ascii", "ignore").decode("ascii")
    texte = re.sub(r"['’]", "", texte)
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", texte)).strip().lower()


def video_de_disque(titre_video: str | None, album: str | None) -> bool:
    """La vidéo est-elle le disque `album` EN ENTIER ? Le titre l'annonce
    (`VIDEO_DE_DISQUE`) ET nomme le disque, par mots entiers (sans ses
    parenthèses : « Rétrographie (Mixtape) »). Sans le nom du disque, on ne
    conclut pas : « 15: Number One (ft Kanye West) .. FULL ALBUM » est une
    piste d'un album mis en ligne en morceaux."""
    from src.utils.title_matching import contains_as_words

    if not titre_video or not album or not VIDEO_DE_DISQUE.search(titre_video):
        return False
    disque = _mots(_GROUPES.sub("", album))
    return bool(disque) and contains_as_words(disque, _mots(titre_video))
