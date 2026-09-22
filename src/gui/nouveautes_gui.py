"""Badge « nouveaux titres » : la vérification, et ce qu'elle affiche.

Adaptateur GUI pur (widgets, `root.after`, dialogues) — la règle vit dans
`src/services/nouveautes.py`, la mémoire dans `src/utils/nouveautes_cache.py`.

Le badge est le TEXTE du bouton « Discographie » (`Discographie · 🆕 3`), pas un
widget superposé : un `place()` par-dessus se désaligne au redimensionnement et
survit aux changements de thème, alors qu'un libellé suit le bouton partout.
"""

from tkinter import messagebox

from src.concurrency.lifecycle import run_worker
from src.gui.workers.lifecycle import stop_requested
from src.services import nouveautes
from src.utils.logger import get_logger

logger = get_logger(__name__)

_LIBELLE = "Discographie"


def verifier_en_fond(app, *, force: bool = False) -> None:
    """Lance la vérification hors du thread Tk et pose le badge au retour.

    Appelée au chargement d'un artiste (`force=False` : le cache journalier
    évite l'appel) et par le bouton « Nouveautés » (`force=True`).
    """
    artist = app.current_artist
    if artist is None:
        return
    # Le badge de l'artiste précédent ne vaut plus rien : l'effacer AVANT de
    # partir, sinon il resterait affiché pendant toute la vérification.
    oublier(app)

    def _travail():
        verification = nouveautes.verifier(app.runtime, artist, force=force)
        if stop_requested():
            return
        try:
            app.root.after(0, lambda: _appliquer(app, artist, verification, annoncer=force))
        except Exception:  # noqa: BLE001 - application déjà fermée
            logger.debug("Retour GUI des nouveautés abandonné : application fermée")

    run_worker(_travail, name="nouveautes")


def verifier_maintenant(app) -> None:
    """Bouton « Nouveautés » : ignore le cache et DIT le résultat."""
    if app.current_artist is None:
        messagebox.showinfo("Nouveautés", "Chargez d'abord un artiste.")
        return
    verifier_en_fond(app, force=True)


def _appliquer(app, artist, verification, *, annoncer: bool) -> None:
    """Pose le badge (thread Tk). L'artiste a pu changer entre-temps : on ne
    peint que si la vérification porte bien sur celui qui est à l'écran."""
    if app.current_artist is not artist:
        return
    app.nouveautes = verification
    rafraichir_badge(app)
    if not annoncer:
        return
    if verification.motif:
        messagebox.showwarning("Nouveautés", nouveautes.resume(verification, artist.name))
    elif verification.nouveautes:
        messagebox.showinfo("Nouveautés", nouveautes.resume(verification, artist.name))
    else:
        messagebox.showinfo("Nouveautés", f"Rien de neuf pour {artist.name}.")


def rafraichir_badge(app) -> None:
    """Le bouton Discographie porte le compte, ou son libellé nu."""
    bouton = getattr(app, "get_tracks_button", None)
    if bouton is None:
        return
    verification = getattr(app, "nouveautes", None)
    nombre = verification.nombre if verification and verification.concluante else 0
    try:
        bouton.configure(text=f"{_LIBELLE} · 🆕 {nombre}" if nombre else _LIBELLE)
    except Exception:  # noqa: BLE001 - widget détruit pendant la fermeture
        logger.debug("Badge non posé : bouton absent")


def oublier(app) -> None:
    """Changement d'artiste : le badge du précédent ne vaut plus rien."""
    app.nouveautes = None
    rafraichir_badge(app)


def bandeau(app) -> str | None:
    """Texte à afficher en tête du dialogue Discographie, ou None.

    Un compte ÉNORME ne veut pas dire « il y a eu des sorties » : il veut dire
    que la discographie n'a jamais été récupérée. Le bandeau le dit, plutôt
    que de faire passer un rattrapage pour une actualité.
    """
    verification = getattr(app, "nouveautes", None)
    if not verification or not verification.concluante or not verification.nouveautes:
        return None
    titres = ", ".join(f"« {n.titre} »" for n in verification.nouveautes[:5])
    reste = verification.nombre - min(5, verification.nombre)
    suite = f" … et {reste} autre(s)" if reste else ""
    if verification.nombre >= nouveautes.PAR_PAGE:
        return (
            f"🆕 Au moins {verification.nombre} titres Genius manquent en base — "
            "la discographie n'est pas à jour. Lancer une mise à jour."
        )
    return f"🆕 {verification.nombre} nouveau(x) titre(s) détecté(s) : {titres}{suite}"
