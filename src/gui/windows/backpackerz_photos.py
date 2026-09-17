"""Fenêtre « Photos BPZ » — chercher, voir et garder une photo The BACKPACKERZ.

Outil AUTONOME (décision utilisateur 2026-09-16) : il n'est branché sur aucun
export. On cherche un artiste, on voit les articles où il est tagué et leurs
photos, on en télécharge une — et la CITATION part avec elle (sidecar
`credits.json`, cf. `src/utils/backpackerz_photos.py`). L'usage des photos
est autorisé par le site à cette condition.

Ce que montre la fenêtre, et pourquoi dans cet ordre :

  · une section par ARTICLE (titre, date, lien) — c'est l'article qu'on cite ;
  · à l'ouverture, la photo de UNE de chaque article et les photos dont le
    titre nomme l'artiste (deux requêtes en tout) ;
  · « Toutes les photos » sur un article, ou « Tout charger » pour les
    parcourir tous (≈ 1 requête/s : 30 articles ≈ 30 s, d'où le bouton
    séparé et sa barre de progression) ;
  · un article où l'artiste n'est pas TAGUÉ n'apparaît pas — c'est dit
    dans le bandeau plutôt que découvert par l'absence.

Premier usage d'IMAGES dans la GUI du projet : `ctk.CTkImage` sur des `PIL`
(Pillow est une dépendance transitive de customtkinter). Les vignettes sont
chargées en fond, UNE À LA FOIS (un seul thread : c'est le CDN du site, on
reste courtois), et gardées en cache par URL pour la durée de la fenêtre.

Threads : `start_worker` (contrat `lifecycle.py`) pour tout ce qui touche
le réseau ; les retours passent par `root.after`. Piège connu : un `CTkFrame`
vide garde 200 × 200 → `height=1` sur les cadres remplis plus tard.
"""

from __future__ import annotations

import io
import os
import threading
import webbrowser
from collections import deque

import customtkinter as ctk
import requests
from PIL import Image

from src.api.backpackerz_api import Article, Photo, Tag, TagAmbigu, credit_line
from src.concurrency.lifecycle import start_worker, stop_requested
from src.utils import backpackerz_photos as store
from src.utils.logger import get_logger

logger = get_logger(__name__)

#: Taille d'affichage des vignettes (bord le plus long).
_VIGNETTE = 200
_COLONNES = 4
_UA = {
    "User-Agent": "MusicCreditsScraper/1.0 ( https://github.com/EnfinVoilaQuoi/music_credits_scraper )"
}


class BackpackerzPhotosWindow:
    """Fenêtre CTkToplevel « Photos BPZ » (une instance à la fois)."""

    def __init__(self, app):
        self.app = app
        self.artist = app.current_artist
        self.artiste_nom = self.artist.name if self.artist else ""
        self.artist_id = self.artist.id if self.artist else None
        self.resultat: store.Resultat | None = None
        self._images: dict[str, ctk.CTkImage] = {}
        self._labels_par_url: dict[str, list[ctk.CTkLabel]] = {}
        self._file_vignettes: deque[str] = deque()
        self._vignettes_en_cours = False
        self._verrou = threading.Lock()
        self._occupe = False
        self._ferme = False

        self.window = ctk.CTkToplevel(app.root)
        self.window.title(f"Photos The BACKPACKERZ — {self.artiste_nom or 'artiste'}")
        self.window.geometry("1040x760")
        self.window.transient(app.root)
        self.window.protocol("WM_DELETE_WINDOW", self._fermer)

        self._build_entete()
        self._build_corps()
        self._build_pied()
        if self.artiste_nom:
            self.chercher()

    # ── Construction ─────────────────────────────────────────────────────────

    def _build_entete(self):
        haut = ctk.CTkFrame(self.window)
        haut.pack(fill="x", padx=10, pady=(10, 4))

        ctk.CTkLabel(haut, text="Artiste :").pack(side="left", padx=(8, 4))
        self.champ = ctk.CTkEntry(haut, width=240)
        self.champ.insert(0, self.artiste_nom)
        self.champ.pack(side="left", padx=4)
        self.champ.bind("<Return>", lambda _e: self.chercher())

        self.bouton_chercher = ctk.CTkButton(
            haut, text="🔍 Rechercher", width=130, command=self.chercher
        )
        self.bouton_chercher.pack(side="left", padx=4)
        self.bouton_tout = ctk.CTkButton(
            haut,
            text="📚 Tout charger",
            width=130,
            state="disabled",
            command=self.tout_charger,
            fg_color="#5e35b1",
            hover_color="#4527a0",
        )
        self.bouton_tout.pack(side="left", padx=4)
        self.bouton_dossier = ctk.CTkButton(
            haut, text="📂 Ouvrir le dossier", width=150, command=self._ouvrir_dossier
        )
        self.bouton_dossier.pack(side="right", padx=8)

        self.bandeau = ctk.CTkLabel(
            self.window,
            text=(
                "Photos publiées par The BACKPACKERZ — usage autorisé AVEC citation : "
                "chaque téléchargement écrit la citation (photographe, article) dans credits.json.\n"
                "Ne sont vus que les articles où l'artiste est TAGUÉ sur le site."
            ),
            justify="left",
            text_color="gray",
            anchor="w",
        )
        self.bandeau.pack(fill="x", padx=18, pady=(0, 4))

    def _build_corps(self):
        self.corps = ctk.CTkScrollableFrame(self.window)
        self.corps.pack(fill="both", expand=True, padx=10, pady=4)

    def _build_pied(self):
        pied = ctk.CTkFrame(self.window, fg_color="transparent")
        pied.pack(fill="x", padx=10, pady=(4, 10))
        self.progress = ctk.CTkProgressBar(pied, width=220)
        self.progress.set(0)
        self.statut = ctk.CTkLabel(pied, text="", anchor="w", justify="left")
        self.statut.pack(side="left", fill="x", expand=True, padx=8)

    # ── Helpers thread → GUI ─────────────────────────────────────────────────

    def _after(self, fn):
        if self._ferme:
            return
        try:
            self.app.root.after(0, fn)
        except RuntimeError:  # boucle Tk arrêtée
            pass

    def _dire(self, texte: str, couleur: str = "gray"):
        self.statut.configure(text=texte, text_color=couleur)

    def _occuper(self, oui: bool):
        self._occupe = oui
        etat = "disabled" if oui else "normal"
        self.bouton_chercher.configure(state=etat)
        self.bouton_tout.configure(
            state="disabled" if oui or not (self.resultat and self.resultat.articles) else "normal"
        )

    def _fermer(self):
        self._ferme = True
        self.window.destroy()

    # ── Recherche ────────────────────────────────────────────────────────────

    def chercher(self):
        nom = self.champ.get().strip()
        if not nom or self._occupe:
            return
        self.artiste_nom = nom
        self._occuper(True)
        self._dire(f"Recherche de « {nom} » sur The BACKPACKERZ…")

        def worker():
            try:
                res = store.rechercher(nom, artist_id=self.artist_id)
            except TagAmbigu as e:
                self._after(lambda cands=e.candidats: self._choisir_tag(cands))
                return
            except Exception as e:  # noqa: BLE001 — la fenêtre doit rester utilisable
                logger.exception("Recherche BACKPACKERZ échouée")
                self._after(lambda message=str(e): self._echec(message))
                return
            self._after(lambda: self._afficher_resultat(res))

        start_worker(worker, name="backpackerz:recherche")

    def _rechercher_tag(self, tag: Tag):
        self._occuper(True)
        self._dire(f"Articles du tag « {tag.name} » (#{tag.id})…")

        def worker():
            try:
                res = store.rechercher_tag(tag, self.artiste_nom, artist_id=self.artist_id)
            except Exception as e:  # noqa: BLE001 — la fenêtre doit rester utilisable
                logger.exception("Recherche BACKPACKERZ échouée")
                self._after(lambda message=str(e): self._echec(message))
                return
            self._after(lambda: self._afficher_resultat(res))

        start_worker(worker, name="backpackerz:recherche")

    def _choisir_tag(self, candidats: list[Tag]):
        """Plusieurs tags exacts : on demande, on ne choisit jamais à la place."""
        self._occuper(False)
        self._vider()
        self._dire(f"{len(candidats)} tags portent ce nom : lequel ?", "#e6a700")
        cadre = ctk.CTkFrame(self.corps)
        cadre.pack(fill="x", pady=8)
        for t in candidats:
            ctk.CTkButton(
                cadre,
                text=f"{t.name} — {t.count} article(s) (#{t.id}, /{t.slug}/)",
                anchor="w",
                command=lambda tag=t: self._rechercher_tag(tag),
            ).pack(fill="x", padx=10, pady=3)

    def _echec(self, message: str):
        self._occuper(False)
        self._dire(f"❌ {message}", "#c62828")

    # ── Affichage ────────────────────────────────────────────────────────────

    def _vider(self):
        for enfant in self.corps.winfo_children():
            enfant.destroy()
        self._labels_par_url.clear()
        with self._verrou:
            self._file_vignettes.clear()

    def _afficher_resultat(self, res: store.Resultat):
        self.resultat = res
        self._vider()
        self._occuper(False)
        if res.tag is None:
            self._dire(
                f"Aucun tag « {self.artiste_nom} » sur le site (les noms se comparent à l'exact).",
                "#e6a700",
            )
            return
        self._dire(
            f"Tag « {res.tag.name} » : {len(res.articles)} article(s), {res.nb_photos} photo(s) "
            "connue(s) — « Toutes les photos » sur un article, ou « Tout charger »."
        )
        for article in res.articles:
            self._section_article(
                article, res.photos.get(article.id, []), article.id in res.complets
            )
        if res.orphelines:
            self._section(
                None,
                "Photos nommées d'après l'artiste, sans article tagué",
                "",
                res.orphelines,
                complet=True,
            )
        self._lancer_vignettes()

    def _section_article(self, article: Article, photos: list[Photo], complet: bool):
        self._section(article, article.title, article.jour, photos, complet=complet)

    def _section(self, article, titre: str, date: str, photos: list[Photo], *, complet: bool):
        cadre = ctk.CTkFrame(self.corps, height=1)
        cadre.pack(fill="x", pady=(4, 8))

        entete = ctk.CTkFrame(cadre, fg_color="transparent")
        entete.pack(fill="x", padx=8, pady=(6, 2))
        libelle = f"{date}  ·  {titre}" if date else titre
        ctk.CTkLabel(entete, text=libelle, font=ctk.CTkFont(weight="bold"), anchor="w").pack(
            side="left", fill="x", expand=True
        )
        if article:
            ctk.CTkButton(
                entete,
                text="↗ Article",
                width=90,
                fg_color="transparent",
                border_width=1,
                command=lambda u=article.url: webbrowser.open(u),
            ).pack(side="right", padx=4)
            if not complet:
                ctk.CTkButton(
                    entete,
                    text="🖼 Toutes les photos",
                    width=140,
                    command=lambda a=article: self.charger_article(a),
                ).pack(side="right", padx=4)

        grille = ctk.CTkFrame(cadre, fg_color="transparent", height=1)
        grille.pack(fill="x", padx=8, pady=(0, 6))
        if not photos:
            ctk.CTkLabel(grille, text="(aucune photo connue)", text_color="gray").pack(anchor="w")
        for i, photo in enumerate(photos):
            self._carte(grille, photo, article).grid(
                row=i // _COLONNES, column=i % _COLONNES, padx=6, pady=6, sticky="n"
            )

    def _carte(self, parent, photo: Photo, article: Article | None) -> ctk.CTkFrame:
        carte = ctk.CTkFrame(parent, width=_VIGNETTE + 16)
        image = ctk.CTkLabel(carte, text="…", width=_VIGNETTE, height=int(_VIGNETTE * 0.66))
        image.pack(padx=8, pady=(8, 4))
        self._poser_vignette(image, photo.thumb_url)

        credit = photo.photographer or "photographe non indiqué"
        ctk.CTkLabel(
            carte,
            text=f"{photo.dimensions}  ·  {credit}",
            text_color="gray" if photo.photographer else "#e6a700",
            font=ctk.CTkFont(size=11),
        ).pack()
        titre = photo.title if len(photo.title) <= 34 else photo.title[:33] + "…"
        ctk.CTkLabel(carte, text=titre, font=ctk.CTkFont(size=11), wraplength=_VIGNETTE).pack()

        local = store.photo_locale(self.artiste_nom, photo)
        bouton = ctk.CTkButton(carte, width=_VIGNETTE - 20)
        if local is not None:
            bouton.configure(text="✅ Téléchargée", fg_color="#2e7d32", hover_color="#1b5e20")
        else:
            bouton.configure(text="⬇ Télécharger")
        bouton.configure(command=lambda p=photo, a=article, b=bouton: self.telecharger(p, a, b))
        bouton.pack(padx=8, pady=(4, 8))
        return carte

    # ── Vignettes (fond, une à la fois) ──────────────────────────────────────

    def _poser_vignette(self, label: ctk.CTkLabel, url: str):
        img = self._images.get(url)
        if img is not None:
            label.configure(image=img, text="")
            return
        self._labels_par_url.setdefault(url, []).append(label)
        with self._verrou:
            if url not in self._file_vignettes:
                self._file_vignettes.append(url)

    def _lancer_vignettes(self):
        with self._verrou:
            if self._vignettes_en_cours or not self._file_vignettes:
                return
            self._vignettes_en_cours = True

        def worker():
            while not self._ferme and not stop_requested():
                with self._verrou:
                    if not self._file_vignettes:
                        self._vignettes_en_cours = False
                        return
                    url = self._file_vignettes.popleft()
                if url in self._images:
                    continue
                pil = _charger_vignette(url)
                self._after(lambda u=url, p=pil: self._vignette_prete(u, p))
            with self._verrou:
                self._vignettes_en_cours = False

        start_worker(worker, name="backpackerz:vignettes")

    def _vignette_prete(self, url: str, pil: Image.Image | None):
        labels = self._labels_par_url.pop(url, [])
        if pil is None:
            for label in labels:
                self._safe_configure(label, text="(vignette indisponible)")
            return
        img = ctk.CTkImage(light_image=pil, dark_image=pil, size=pil.size)
        self._images[url] = img
        for label in labels:
            self._safe_configure(label, image=img, text="")

    @staticmethod
    def _safe_configure(widget, **kw):
        try:
            widget.configure(**kw)
        except Exception:  # noqa: BLE001 — widget détruit (section rafraîchie)
            pass

    # ── Chargement complet ───────────────────────────────────────────────────

    def charger_article(self, article: Article):
        if self._occupe or self.resultat is None:
            return
        self._occuper(True)
        self._dire(f"Photos de « {article.title} »…")

        def worker():
            try:
                photos = store.photos_article(
                    article.id, artiste=self.artiste_nom, artist_id=self.artist_id
                )
            except Exception as e:  # noqa: BLE001 — la fenêtre doit rester utilisable
                logger.exception("Photos d'article BACKPACKERZ échouées")
                self._after(lambda message=str(e): self._echec(message))
                return
            self._after(lambda: self._article_complet(article, photos))

        start_worker(worker, name="backpackerz:article")

    def _article_complet(self, article: Article, photos: list[Photo]):
        res = self.resultat
        if res is None:
            return
        store.completer(res, article.id, photos)
        self._occuper(False)
        self._dire(f"« {article.title} » : {len(res.photos.get(article.id, []))} photo(s).")
        self._afficher_resultat(res)

    def tout_charger(self):
        """Parcourt TOUS les articles (≈ 1 req/s) — barre de progression, arrêtable."""
        res = self.resultat
        if self._occupe or res is None or not res.articles:
            return
        restants = [a for a in res.articles if a.id not in res.complets]
        if not restants:
            self._dire("Tous les articles sont déjà chargés.")
            return
        self._occuper(True)
        self.progress.set(0)
        self.progress.pack(side="right", padx=8)

        def worker():
            total = len(restants)
            for i, article in enumerate(restants, 1):
                if self._ferme or stop_requested():
                    break
                try:
                    photos = store.photos_article(
                        article.id, artiste=self.artiste_nom, artist_id=self.artist_id
                    )
                except Exception as e:  # noqa: BLE001 — un article en panne n'arrête pas le lot
                    logger.warning(f"BACKPACKERZ : article {article.id} : {e}")
                    continue
                store.completer(res, article.id, photos)
                self._after(lambda i=i, t=total, a=article: self._progression(i, t, a))
            self._after(self._tout_charge)

        start_worker(worker, name="backpackerz:tout")

    def _progression(self, i: int, total: int, article: Article):
        self.progress.set(i / total)
        self._dire(f"{i}/{total} — {article.title}")

    def _tout_charge(self):
        self.progress.pack_forget()
        self._occuper(False)
        if self.resultat is not None:
            self._afficher_resultat(self.resultat)
            self._dire(
                f"{len(self.resultat.articles)} article(s) parcourus, "
                f"{self.resultat.nb_photos} photo(s) au total."
            )

    # ── Téléchargement ───────────────────────────────────────────────────────

    def telecharger(self, photo: Photo, article: Article | None, bouton: ctk.CTkButton):
        bouton.configure(state="disabled", text="⏳…")

        def worker():
            fichier = store.telecharger(self.artiste_nom, photo, article)
            self._after(lambda: self._telechargee(photo, fichier, bouton))

        start_worker(worker, name="backpackerz:download")

    def _telechargee(self, photo: Photo, fichier, bouton: ctk.CTkButton):
        if fichier is None:
            self._safe_configure(bouton, state="normal", text="⬇ Télécharger (échec, réessayer)")
            self._dire(f"❌ Le site n'a pas servi {photo.source_url}", "#c62828")
            return
        self._safe_configure(
            bouton, state="normal", text="✅ Téléchargée", fg_color="#2e7d32", hover_color="#1b5e20"
        )
        self._dire(f"Enregistrée : {fichier}\n{credit_line(photo)}", "#1DB954")

    def _ouvrir_dossier(self):
        dossier = store.dossier_artiste(self.artiste_nom)
        dossier.mkdir(parents=True, exist_ok=True)
        os.startfile(dossier)  # Windows : ouvre l'Explorateur sur le dossier local


def _charger_vignette(url: str) -> Image.Image | None:
    """Télécharge et réduit une vignette (bord ≤ `_VIGNETTE`). `None` si le CDN se tait."""
    try:
        reponse = requests.get(url, headers=_UA, timeout=20)
        reponse.raise_for_status()
        pil = Image.open(io.BytesIO(reponse.content))
        pil.load()
    except (requests.RequestException, OSError) as exc:
        logger.warning(f"BACKPACKERZ : vignette illisible ({url}) : {exc}")
        return None
    pil = pil.convert("RGB")
    pil.thumbnail((_VIGNETTE, _VIGNETTE))
    return pil


def show_backpackerz_photos(app):
    """Ouvre (ou refocus) la fenêtre Photos BPZ."""
    existing = getattr(app, "backpackerz_window", None)
    if existing is not None and not existing._ferme:
        try:
            existing.window.deiconify()
            existing.window.lift()
            existing.window.focus_force()
            return existing
        except Exception:  # noqa: BLE001 — fenêtre détruite entre-temps
            pass
    app.backpackerz_window = BackpackerzPhotosWindow(app)
    return app.backpackerz_window
