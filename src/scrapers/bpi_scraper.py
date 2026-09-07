"""Certifications BPI (Royaume-Uni) — « BRIT Certified ».

**Le transport est trivial, et c'est une mesure, pas une hypothèse.** La page
`bpi.co.uk/page/certified-awards` n'est qu'une IFRAME ; la vraie application vit
sur `certified-awards.bpi.co.uk`, en **htmx rendu côté serveur**. Un `GET` nu
suffit : ni Playwright, ni patchright, ni route CDP — vérifié le 2026-09-07, un
client non-navigateur passe sans le moindre défi Cloudflare. BPI est donc la plus
légère des quatre sources de certifs ; ne pas lui coller par symétrie la
plomberie navigateur de BRMA.

**Le seul secret est un en-tête.** Sans `HX-Request: true`, l'endpoint rend la
coquille de l'application et **zéro ligne** — un scraper qui l'oublie « marche »
et ne ramène rien. C'est le premier piège, et il est silencieux.

Deux faits de structure dictent le reste :

1. **La fenêtre de dates ne filtre que la DERNIÈRE certification.** SIGALA
   « EASY LOVE » (Gold 04.12.2015, Platinum 24.06.2016, 2x Platinum 28.08.2026)
   ne ressort NI en décembre 2015, NI en juin 2016 : seulement en août 2026.
   Un balayage par dates ne reconstruit donc PAS l'historique, et une ligne
   QUITTE une vieille fenêtre dès qu'elle est réhaussée. Les fenêtres ne valent
   que pour l'incrémental (où elles sont exactes : tout nouvel événement entre
   dans la fenêtre courante) ; le corpus complet demande un balayage non filtré.
2. **Les paliers intermédiaires ne vivent que sur la page de détail.** Bonne
   nouvelle : le bouton « Show N more » n'est qu'un `hidden` CSS, tout
   l'historique est déjà dans le HTML — aucune requête supplémentaire.

Les parseurs sont PURS et exportés : les tests les rejouent sur fixtures, hors
ligne, sans toucher au réseau.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from bs4 import BeautifulSoup

from src.api.async_http import AsyncHttpSession
from src.config import BPI_MAX_PAGES
from src.observability import source_usage
from src.observability.issues import IssueKind
from src.utils.cert_normalize import bpi_level, bpi_level_connu
from src.utils.logger import get_logger
from src.utils.title_matching import contains_as_words, normalize_name

logger = get_logger(__name__)

_SOURCE = "bpi"

BASE_URL = "https://certified-awards.bpi.co.uk"

#: L'application est servie telle quelle à un client nu ; l'UA n'est là que par
#: politesse (aucun blocage mesuré sans lui).
_UA = {"User-Agent": "Mozilla/5.0 (compatible; music-credits-scraper/1.0)"}

#: **L'en-tête qui décide de tout.** Sans lui : coquille vide, 0 ligne, 200 OK.
_HX = {"HX-Request": "true"}

#: Lignes rendues par page. Constante du site, sert au recoupement de complétude.
PAR_PAGE = 24

#: Titres traités entre deux vidages du balayage complet. Assez grand pour que
#: l'écriture reste marginale, assez petit pour qu'une coupure ne coûte que
#: quelques minutes de détails sur les quatre heures que dure la reprise.
_LOT_DE_VIDAGE = 250

#: En-têtes du tableau `view=list`, dans l'ordre où le site les sert. Ils sont le
#: garde-fou G1 : les colonnes sont lues PAR LEUR NOM et jamais par leur rang.
ENTETES_ATTENDUES = (
    "Artist",
    "Title",
    "Award",
    "Format",
    "Corporate Group/Label",
    "Latest Certification",
    "Released",
)

#: Une ligne de résultat porte son identité dans son `hx-get`.
_TRIPLET_RE = re.compile(r"/format/(\d+)/artist/(\d+)/title/(\d+)")

_DATE_POINTS_RE = re.compile(r"^(\d{2})\.(\d{2})\.(\d{4})$")
_DATE_LETTRES_RE = re.compile(r"^(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})$")

_MOIS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}  # fmt: skip

#: Message d'état vide du site (« No certified awards found matching your
#: criteria. »). Motif VOLONTAIREMENT tolérant : une reformulation ne doit pas
#: faire passer un résultat vide pour une refonte… mais si la reformulation est
#: telle que le motif ne matche plus, on tombe sur G1 et on crie au `parse`.
#: C'est la bonne direction d'erreur : une fausse alerte se voit, un `absent`
#: injustifié rend une panne muette — et `absent` est le seul verdict exclu du
#: numérateur des échecs.
_VIDE_RE = re.compile(r"\bno\b[^.<>]{0,60}\bfound\b", re.I)

__all__ = [
    "BASE_URL",
    "ENTETES_ATTENDUES",
    "PAR_PAGE",
    "BpiScraper",
    "parse_annuaire",
    "parse_historique",
    "parse_liste",
    "to_iso",
]


# ── Parseurs purs ─────────────────────────────────────────────────────────────
def to_iso(valeur: str) -> str:
    """« 31.01.2020 » ou « 04 December 2015 » → « 2020-01-31 » / « 2015-12-04 ».

    Les deux formats coexistent : le tableau écrit en pointé, la page de détail
    en toutes lettres. Une valeur non reconnue est rendue telle quelle plutôt que
    devinée — une date fausse est pire qu'une date absente.
    """
    valeur = (valeur or "").strip()
    if not valeur:
        return ""
    m = _DATE_POINTS_RE.match(valeur)
    if m:
        jour, mois, annee = m.groups()
        return f"{annee}-{mois}-{jour}"
    m = _DATE_LETTRES_RE.match(valeur)
    if m:
        jour, mois_mot, annee = m.groups()
        mois = _MOIS.get(mois_mot.lower())
        if mois:
            return f"{annee}-{mois:02d}-{int(jour):02d}"
    return valeur


def _soupe(html: str) -> BeautifulSoup:
    return BeautifulSoup(html or "", "html.parser")


def entetes(html: str) -> tuple[str, ...]:
    """En-têtes du tableau de résultats, dans l'ordre servi."""
    return tuple(th.get_text(strip=True) for th in _soupe(html).select("thead th"))


def est_vide(html: str) -> bool:
    """La page ne porte-t-elle AUCUNE ligne ?

    L'état « aucun résultat » ne rend aucun tableau (ni `<thead>`, ni `<tr>`) :
    sans ce test, le garde-fou d'en-têtes prendrait une recherche légitimement
    vide pour une refonte du site.

    ⚠️ **Le critère est l'ABSENCE DE LIGNES, pas la présence d'un message** — et
    cette précision a coûté 22 titres, mesurés le 2026-09-07. Le site sert DEUX
    messages différents :

      · « No certified awards found matching your criteria. » — recherche vide ;
      · « **No more** certified awards found » — sentinelle de FIN DE LISTE,
        posée sur la dernière page… **qui contient encore des lignes**.

    Un motif tolérant les confond, si bien que la dernière page partielle de
    TOUTE requête multi-pages était jetée : 22 titres au balayage complet (la fin
    de l'alphabet), 6 sur une fenêtre d'un mois. Le message ne compte donc que
    lorsqu'il n'y a rien à lire — et c'est la bonne hiérarchie, puisque `absent`
    est le seul verdict exclu du numérateur des échecs, donc le seul capable de
    rendre une perte silencieuse.
    """
    if lignes_de_donnees(html):
        return False
    return bool(_VIDE_RE.search(html or ""))


def lignes_de_donnees(html: str) -> list:
    """Lignes PORTEUSES du tableau (≥ 7 cellules).

    `find_all("tr")` et NON `select("tbody tr")` : **la page 1 rend un `<table>`
    complet, les pages suivantes rendent des `<tr>` NUS** que htmx ajoute dans le
    tableau déjà affiché. Chercher un `<tbody>` ancêtre ne trouve donc rien dès la
    page 2 — mesuré le 2026-09-07, et le balayage complet s'arrêtait à 24 titres
    sur ~26 500 (le garde-fou d'en-têtes criait `parse`, ce qui l'a rendu visible
    au lieu de tronquer en silence).

    Le tableau porte aussi une ligne sentinelle d'une seule cellule : la compter
    ferait passer une page vide pour une page pleine. Le critère est le nombre de
    cellules et non la présence de `hx-get` — sur une refonte qui retirerait
    l'attribut, il faut que le compte reste > 0 pour que le verdict soit `parse`
    et non `absent`.
    """
    return [tr for tr in _soupe(html).find_all("tr") if len(tr.find_all("td")) >= 7]


def parse_liste(html: str, entetes_connus: Sequence[str] = ()) -> list[dict]:
    """Lignes du tableau `view=list` → dicts.

    Les colonnes sont repérées par le NOM de leur `<th>`, jamais par leur rang :
    la refonte RIAA de 2026 avait réordonné ses cellules, et une lecture
    positionnelle y rendait un label en guise de date sans que rien ne proteste.

    `entetes_connus` sert les pages de CONTINUATION, qui n'ont pas de `<thead>` :
    l'appelant y passe l'ordre relevé sur la page 1 de la MÊME requête. C'est
    plus solide qu'un ordre en dur — si le site réordonne ses colonnes, la
    page 1 le dit et les suivantes suivent.
    """
    soup = _soupe(html)
    noms = [th.get_text(strip=True) for th in soup.select("thead th")] or list(entetes_connus)
    rang = {nom: i for i, nom in enumerate(noms)}
    sorties: list[dict] = []

    for tr in soup.find_all("tr"):
        cellules = tr.find_all("td")
        if len(cellules) < 7:
            continue  # ligne sentinelle

        def cellule(nom: str, _c=cellules) -> str:
            i = rang.get(nom)
            return _c[i].get_text(" ", strip=True) if i is not None and i < len(_c) else ""

        artiste = cellule("Artist")
        titre = cellule("Title")
        if not (artiste and titre):
            continue

        # Le niveau est écrit DEUX fois (texte + `alt` du badge). On garde le
        # texte et on croise : une divergence trahit un gabarit qui bouge.
        niveau_texte = cellule("Award")
        i_award = rang.get("Award")
        img = cellules[i_award].find("img") if i_award is not None else None
        niveau_alt = (img.get("alt") or "").strip() if img else ""
        if niveau_alt and niveau_texte and niveau_alt != niveau_texte:
            logger.warning(
                f"BPI : niveau divergent pour « {artiste} — {titre} » : "
                f"texte « {niveau_texte} » vs badge « {niveau_alt} »"
            )

        triplet = _TRIPLET_RE.search(tr.get("hx-get") or "")
        format_id, artist_id, title_id = (
            tuple(int(g) for g in triplet.groups()) if triplet else (0, 0, 0)
        )

        sorties.append(
            {
                "artist": artiste,
                "title": titre,
                "certification_level": bpi_level(niveau_texte or niveau_alt),
                "category": cellule("Format"),
                "label": cellule("Corporate Group/Label"),
                "certification_date": to_iso(cellule("Latest Certification")),
                "release_date": to_iso(cellule("Released")),
                "format_id": format_id,
                "artist_id": artist_id,
                "title_id": title_id,
                "detail_url": f"{BASE_URL}{triplet.group(0)}" if triplet else "",
            }
        )
    return sorties


def parse_historique(html: str) -> list[dict]:
    """Paliers datés d'une page de détail, du plus récent au plus ancien.

    Les entrées repliées derrière « Show N more » sont dans un `div.hidden` :
    elles sont DANS le HTML et doivent être lues comme les autres. Ne jamais se
    fier au texte visible.

    Les `<p>` du bloc arrivent en alternance date / niveau dans l'ordre du
    document ; on avance sur cette alternance plutôt que sur des classes
    Tailwind, qui changent au moindre coup de peinture.
    """
    soup = _soupe(html)
    etiquette = soup.find("p", string=re.compile(r"Certification history", re.I))
    if etiquette is None:
        return []
    carte = etiquette.parent
    if carte is None:
        return []

    textes = [p.get_text(" ", strip=True) for p in carte.find_all("p")]
    paliers: list[dict] = []
    i = 0
    while i < len(textes) - 1:
        date = to_iso(textes[i])
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            paliers.append(
                {"certification_date": date, "certification_level": bpi_level(textes[i + 1])}
            )
            i += 2
        else:
            i += 1
    return paliers


def parse_annuaire(html: str) -> list[tuple[int, str]]:
    """Annuaire `/artists?q=` → [(id, libellé)].

    ⚠️ Le site répond en SOUS-CHAÎNE NUE : `q=iam` rend « ALYSON WILLIAMS »,
    « ANDY WILLIAMS », « DAFT PUNK FT PHARRELL WILLIAMS ». Ce parseur rend ce
    que le site dit ; c'est `BpiScraper.resoudre_artistes` qui ancre en mots
    entiers. Ne pas déplacer ce filtre ici : la fonction sert aussi à mesurer ce
    que la source renvoie réellement.
    """
    sorties: list[tuple[int, str]] = []
    for li in _soupe(html).select("li"):
        champ = li.find("input")
        libelle = li.find("span")
        if champ is None or libelle is None:
            continue
        valeur = (champ.get("value") or "").strip()
        if not valeur.isdigit():
            continue
        sorties.append((int(valeur), libelle.get_text(" ", strip=True)))
    return sorties


def verifier_fenetre(lignes: list[dict], debut: str, fin: str, obs) -> None:
    """La fenêtre demandée est-elle celle qu'on a obtenue ?

    Calque de `riaa_scraper_v2._verifier_fenetre`, et pour la même raison : là-bas
    l'interrupteur de date avait changé de nom sans que rien ne s'en aperçoive,
    parce qu'aucun invariant ne reliait la requête au résultat. Un débordement
    partiel se signale (bornes incluses ou non, on ne tranche pas) ; TOUTES les
    lignes hors fenêtre ne s'expliquent que par un filtre qui ne filtre plus.
    """
    if not (debut and fin):
        return
    dates = [ligne["certification_date"] for ligne in lignes if ligne.get("certification_date")]
    if not dates:
        return
    hors = [d for d in dates if d < debut or d > fin]
    if not hors:
        return
    exemples = ", ".join(sorted(hors)[:3])
    if len(hors) == len(dates):
        logger.error(
            f"BPI : les {len(hors)} certifications rendues sont HORS de la fenêtre "
            f"{debut}→{fin} (ex. {exemples}) — le filtre de date ne porte pas sur "
            "la date de certification."
        )
        obs.fail(IssueKind.PARSE, "fenêtre de dates non respectée")
    else:
        logger.warning(
            f"BPI : {len(hors)}/{len(dates)} certification(s) hors fenêtre "
            f"{debut}→{fin} (ex. {exemples})"
        )


def parse_verifie(html: str, obs, entetes_connus: Sequence[str] = ()) -> list[dict]:
    """Parse en distinguant « rien à dire » de « on ne sait plus lire ».

    Les verdicts sont ordonnés, et **l'ordre EST le garde-fou** :
      · G3 · le site dit « aucun résultat » → liste vide, l'appelant conclura
        `absent` ; c'est légitime et fréquent. Ce test vient EN PREMIER parce que
        l'état vide ne rend aucun tableau : le placer après G1 ferait crier à la
        refonte sur une recherche parfaitement normale.
      · G1 · un en-tête attendu manque → `parse`. Le détecteur de refonte le plus
        direct, et le moins cher.
      · G2 · des lignes porteuses existent mais aucune ne s'extrait → `parse`.

    `entetes_connus` distingue la page 1 des pages de CONTINUATION. Le site rend
    un `<table>` complet à la page 1 puis des `<tr>` NUS ensuite : sur ces
    pages-là il n'y a pas de `<thead>` à contrôler, et exiger G1 y transformait
    une pagination normale en fausse panne — le balayage complet s'arrêtait à
    24 titres sur ~26 500. Sur la PREMIÈRE page, en revanche, l'absence
    d'en-têtes reste une vraie anomalie.
    """
    if est_vide(html):
        return []

    presents = entetes(html)
    if presents:
        manquants = [nom for nom in ENTETES_ATTENDUES if nom not in presents]
        if manquants:
            logger.error(
                f"BPI : en-tête(s) manquant(s) {manquants} — servis : {list(presents)}. "
                "Le gabarit du site a changé (re-capturer les fixtures)."
            )
            obs.fail(IssueKind.PARSE, f"en-têtes manquants : {manquants}")
            return []
    elif not entetes_connus:
        logger.error("BPI : aucun en-tête de tableau dans la réponse (gabarit changé ?)")
        obs.fail(IssueKind.PARSE, "aucun en-tête de tableau")
        return []

    lignes = parse_liste(html, presents or entetes_connus)
    if not lignes:
        vues = len(lignes_de_donnees(html))
        if vues:
            logger.error(
                f"BPI : {vues} ligne(s) porteuse(s) dans la page, AUCUNE extraite — "
                "le gabarit du site a changé (re-capturer les fixtures)."
            )
            obs.fail(IssueKind.PARSE, f"{vues} lignes non parsées")
        return []

    # G5 · un libellé de niveau que le vocabulaire ne connaît pas est COMPTÉ et
    # remonté, jamais avalé : `absent` n'est le repli de rien.
    inconnus = sorted(
        {
            ligne["certification_level"]
            for ligne in lignes
            if not bpi_level_connu(ligne["certification_level"])
        }
    )
    if inconnus:
        logger.warning(
            f"BPI : {len(inconnus)} niveau(x) hors vocabulaire, conservés verbatim : "
            f"{inconnus[:5]}"
        )
    return lignes


# ── Le scraper ────────────────────────────────────────────────────────────────
class BpiScraper:
    """Client BPI : httpx async partagé + BeautifulSoup. Aucun navigateur."""

    def __init__(self, http: AsyncHttpSession | None = None, *, base_url: str = BASE_URL) -> None:
        self._http = http
        self._proprietaire = http is None  # « qui crée ferme »
        self.base_url = base_url.rstrip("/")

    # -- transport -----------------------------------------------------------
    def _session(self) -> AsyncHttpSession:
        if self._http is None:
            self._http = AsyncHttpSession(headers=_UA)
        return self._http

    async def aclose(self) -> None:
        if self._proprietaire and self._http is not None:
            await self._http.aclose()
            self._http = None

    def url_liste(self, **filtres) -> str:
        """URL complète d'une recherche — pour les fixtures et le diagnostic.

        Les fixtures demandent leur URL AU SCRAPER plutôt que de la recopier
        (comme `capture_fixtures._riaa_artist_url`) : une URL recopiée diverge
        en silence du jour où le scraper change de paramètre.
        """
        from urllib.parse import urlencode

        return f"{self.base_url}/?{urlencode(self.params_liste(**filtres), doseq=True)}"

    @staticmethod
    def params_liste(
        *,
        artistes: tuple[int, ...] = (),
        debut: str = "",
        fin: str = "",
        page: int = 1,
        tri: str = "certificationDate desc",
    ) -> dict:
        """Paramètres de l'endpoint de résultats.

        `skin=bpi` et `view=list` sont constants : la vue liste est un vrai
        tableau nommé, deux fois plus légère que la vue carte.
        """
        params: dict = {"skin": "bpi", "view": "list", "sort": tri}
        if artistes:
            params["artists"] = list(artistes)
        if debut:
            params["certified_date_from"] = debut
        if fin:
            params["certified_date_to"] = fin
        if page > 1:
            params["page"] = page
        return params

    async def _get(self, chemin: str, *, params: dict | None = None, hx: bool = True) -> str:
        reponse = await self._session().get(
            f"{self.base_url}{chemin}",
            params=params,
            headers=dict(_HX) if hx else None,
            timeout=30.0,
        )
        reponse.raise_for_status()
        return reponse.text

    # -- collecte ------------------------------------------------------------
    async def _collecter(self, obs, *, max_pages: int | None = None, **filtres) -> list[dict]:
        """Pagine jusqu'à la première page VIDE.

        **Jamais d'arrêt sur une page courte.** L'API Genius sert des pages
        courtes en plein milieu de la liste, et en déduire la fin y amputait
        silencieusement la moitié d'une discographie. Le seul signal d'arrêt sûr
        est une page qui ne rend RIEN.
        """
        plafond = max_pages or BPI_MAX_PAGES
        lignes: list[dict] = []
        # Ordre des colonnes relevé sur la PAGE 1 et transmis aux suivantes, qui
        # ne rendent que des `<tr>` nus. On le relève au lieu de le figer : si le
        # site réordonne ses colonnes, la page 1 le dit et les suivantes suivent.
        entetes_connus: tuple[str, ...] = ()
        page = 1
        while page <= plafond:
            html = await self._get("/", params=self.params_liste(page=page, **filtres))
            lot = parse_verifie(html, obs, entetes_connus)
            if not entetes_connus:
                entetes_connus = entetes(html)
            if not lot:
                break
            lignes.extend(lot)
            page += 1
        else:
            logger.error(
                f"BPI : plafond de {plafond} pages atteint sans page vide — "
                "collecte possiblement TRONQUÉE (relever `bpi_max_pages`)."
            )
            obs.fail(IssueKind.PARSE, f"plafond de pagination atteint ({plafond})")
        return lignes

    async def _paliers(self, ligne: dict, obs, deja_connu=None) -> list[dict]:
        """Une ligne PAR PALIER daté, via la page de détail.

        Deux économies, et elles sont ce qui rend le balayage complet praticable :

        · un titre dont le dernier palier est **Silver** est rendu tel quel, sans
          requête — Silver est le plancher du barème, il n'a par construction rien
          à raconter de plus ;
        · `deja_connu(ligne)` permet à l'appelant de dire « j'ai déjà l'historique
          de ce titre À CE PALIER-LÀ ». C'est ce qui rend un balayage interrompu
          REPRENABLE : le re-balayage des listes coûte ~20 minutes, mais les
          milliers de pages de détail déjà vues ne sont pas redemandées.
        """
        if not ligne.get("detail_url") or ligne["certification_level"] == "Silver":
            return [ligne]
        if deja_connu is not None and deja_connu(ligne):
            return [ligne]

        chemin = ligne["detail_url"][len(self.base_url) :]
        html = await self._get(chemin, hx=False)
        historique = parse_historique(html)
        if not historique:
            logger.warning(
                f"BPI : historique illisible pour « {ligne['artist']} — {ligne['title']} » "
                f"({ligne['detail_url']}) ; palier courant conservé seul."
            )
            obs.note_attempt(IssueKind.PARSE, detail="historique de paliers illisible")
            return [ligne]

        return [{**ligne, **palier} for palier in historique]

    async def _avec_paliers(
        self, lignes: list[dict], obs, deja_connu=None, vidage=None
    ) -> list[dict]:
        """Déplie les paliers, en vidant périodiquement si l'appelant le demande.

        `vidage(lot)` est appelé tous les `_LOT_DE_VIDAGE` titres. Sans lui, un
        balayage complet garderait ses ~26 500 titres en mémoire et n'écrirait
        qu'à la toute fin : une coupure à la troisième heure perdrait les trois
        heures. Ce qui est vidé est retiré du retour — l'appelant l'a déjà.
        """
        sorties: list[dict] = []
        depuis_vidage = 0
        for ligne in lignes:
            sorties.extend(await self._paliers(ligne, obs, deja_connu))
            depuis_vidage += 1
            if vidage is not None and depuis_vidage >= _LOT_DE_VIDAGE:
                vidage(sorties)
                sorties = []
                depuis_vidage = 0
        return sorties

    # -- entrées publiques ---------------------------------------------------
    async def resoudre_artistes(self, nom: str) -> list[tuple[int, str]]:
        """Entités BPI dont le libellé contient `nom` en MOTS ENTIERS.

        Deux raisons d'exister, et la seconde est la plus utile :

        · l'annuaire du site match en SOUS-CHAÎNE NUE (`q=iam` → « WILLIAMS »),
          exactement le piège que `title_matching` existe pour fermer ;
        · une entité BPI est la CHAÎNE DE CRÉDIT facturée, pas un artiste :
          « SIGALA » rend 18 entités (*SIGALA*, *SIGALA & BECKY HILL*,
          *KATO/SIGALA/HAILEE STEINFELD*…). Ne retenir que la graphie exacte
          amputerait la moitié de la discographie certifiée — c'est le même
          problème que `cert_artist.noms_de_recherche`, mais résolu ici PAR LA
          SOURCE, sans heuristique de notre part.
        """
        with source_usage.observe(_SOURCE, label=f"annuaire:{nom}") as obs:
            aiguille = normalize_name(nom)
            if not aiguille:
                obs.skipped("nom vide")
                return []

            trouves: list[tuple[int, str]] = []
            page = 1
            while page <= BPI_MAX_PAGES:
                html = await self._get("/artists", params={"q": nom, "page": page}, hx=False)
                lot = parse_annuaire(html)
                if not lot:
                    break
                trouves.extend(lot)
                page += 1

            retenus = [
                (ident, libelle)
                for ident, libelle in trouves
                if contains_as_words(aiguille, normalize_name(libelle))
            ]
            if trouves and not retenus:
                logger.info(
                    f"BPI : {len(trouves)} entité(s) proposée(s) pour « {nom} », "
                    "aucune ne le contient en mots entiers (sous-chaîne nue du site)."
                )
            logger.info(
                f"BPI : « {nom} » → {len(retenus)} entité(s) sur {len(trouves)} proposée(s)"
            )
            return retenus

    async def scrape_by_artist(self, artist: str, get_details: bool = True) -> list[dict]:
        """Toutes les certifications des entités correspondant à `artist`."""
        with source_usage.observe(_SOURCE, label=f"artiste:{artist}") as obs:
            entites = await self.resoudre_artistes(artist)
            if not entites:
                obs.absent(f"aucune entité BPI pour « {artist} »")
                return []

            lignes = await self._collecter(obs, artistes=tuple(i for i, _ in entites))
            if not lignes:
                obs.absent(f"aucune certification pour « {artist} »")
                return []
            if get_details:
                lignes = await self._avec_paliers(lignes, obs)
            logger.info(f"BPI : {len(lignes)} certification(s) pour « {artist} »")
            return lignes

    async def scrape_by_date_range(
        self, debut: str, fin: str, get_details: bool = False
    ) -> list[dict]:
        """Certifications dont la DERNIÈRE date tombe dans [debut, fin] (ISO).

        ⚠️ « dernière » n'est pas une approximation : une certification réhaussée
        depuis ne ressort PAS dans la fenêtre de son palier d'origine. Cette
        méthode sert l'incrémental, pas la reconstitution d'historique.
        """
        with source_usage.observe(_SOURCE, label=f"{debut}→{fin}") as obs:
            lignes = await self._collecter(obs, debut=debut, fin=fin)
            if not lignes:
                obs.absent(f"aucune certification entre {debut} et {fin}")
                return []
            verifier_fenetre(lignes, debut, fin, obs)
            if get_details:
                lignes = await self._avec_paliers(lignes, obs)
            logger.info(f"BPI : {len(lignes)} certification(s) entre {debut} et {fin}")
            return lignes

    async def scrape_all(
        self, get_details: bool = True, deja_connu=None, vidage=None
    ) -> list[dict]:
        """Corpus COMPLET (~26 500 lignes, ~1 105 pages au 2026-09-07).

        Trié par nom d'artiste et non par date : une re-certification déplace une
        ligne vers la page 1 en cours de balayage, ce qui en ferait manquer une
        autre. Le tri par nom n'est pas parfaitement stable non plus (une
        insertion décale ce qui suit), mais il ne bouge pas à chaque certification
        hebdomadaire — et la dédup par identité absorbe les doublons.
        """
        with source_usage.observe(_SOURCE, label="corpus complet") as obs:
            lignes = await self._collecter(obs, tri="artist.name asc")
            if not lignes:
                obs.absent("corpus vide")
                return []
            logger.info(f"BPI : {len(lignes)} titre(s) au balayage complet")
            if get_details:
                lignes = await self._avec_paliers(lignes, obs, deja_connu, vidage)
            return lignes
