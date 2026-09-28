"""Contrat avec le dépôt privé `therapie_studio` (visuels, extraits le 2026-09-20).

Le studio CONSOMME des briques publiques ; renommer, déplacer ou changer la
signature de l'une d'elles le casse sans que la CI publique ne le voie. Ce test
fige la surface qu'il importe, relevée dans ses sources le 2026-09-28 :

- chaque nom doit exister dans son module ;
- chaque fonction garde ses paramètres (ajouter un paramètre FACULTATIF est
  libre, en retirer ou en renommer un, ou en ajouter un OBLIGATOIRE, ne l'est pas).

Un échec ici n'interdit rien : il oblige à mettre le studio à jour dans la même
foulée (puis à ajuster ce contrat). Le contrôle profond — champs des modèles,
comportements — est celui de la CI du studio, qui rejoue ses tests contre le
`main` public.
"""

import importlib
import inspect

import pytest

# module -> {nom: paramètres attendus (None = existence seule : classe, constante, module)}
CONTRAT: dict[str, dict[str, list[str] | None]] = {
    "src.config": {"IMAGES_DIR": None, "EXPORTS_DIR": None},
    "src.api.deezer_api": {"DeezerAPI": None},
    "src.concurrency.lifecycle": {
        "start_worker": ["target", "name"],
        "stop_requested": [],
    },
    "src.enrichment.album_types": {"libelle_record_type": ["record_type"]},
    "src.gui.dialogs": {"report": None},
    "src.gui.windows.export_window": {"ExportWindow": None},
    "src.models.artist": {"Artist": None},
    "src.models.track": {
        "Credit": None,
        "CreditRole": None,
        "Track": None,
        "TrackSpotifyId": None,
    },
    "src.utils.backpackerz_photos": {
        "BACKPACKERZ_IMAGES_DIR": None,
        "dossier_artiste": ["artiste"],
        "lire_credits": ["artiste"],
    },
    "src.utils.cert_normalize": {
        "ORGANISMES": None,
        "RANG_PALIERS": None,
        "decouper_multiplicateur": ["niveau"],
    },
    "src.utils.credit_normalize": {"display_name": ["name"], "identity_key": ["name"]},
    "src.utils.data_manager": {"DataManager": None},
    "src.utils.dates": {"parse_flexible": ["value"]},
    "src.utils.disabled_tracks_manager": {"DisabledTracksManager": None},
    "src.utils.image_downloader": {"find_artist_image": ["name"]},
    "src.utils.logger": {"get_logger": ["name"]},
    "src.utils.lyrics_sync": {
        "extract_sections": ["structured", "lrc"],
        "parse_lrc": ["lrc"],
    },
    "src.utils.media_enricher": {
        "apply_images": ["artist", "tracks", "deezer", "genius", "force", "should_stop", "progress"]
    },
    "src.utils.participation": {
        "compte_comme_sien": ["p", "prods"],
        "participation": ["track", "noms", "formations"],
    },
    "src.utils.streams_calculator": {
        "SPOTIFY_SHARE": None,
        "calculate_total_streams": ["spotify_streams", "ytm_streams"],
        "calculate_total_monthly_listeners": ["spotify_listeners", "ytm_listeners"],
        "streams_variantes": ["track"],
    },
    "src.utils.title_matching": {
        "clean_display_title": ["title"],
        "normalize_title": ["s"],
        "split_title_paren": ["title"],
    },
}

# Méthodes de DataManager appelées par le studio.
METHODES_DATA_MANAGER: dict[str, list[str]] = {
    "get_albums_for_artist": ["artist_id"],
    "save_track": ["track"],
    "set_artist_image_path": ["artist_id", "path"],
}


def _verifier_signature(fonction, attendus: list[str], libelle: str) -> None:
    params = {
        nom: p
        for nom, p in inspect.signature(fonction).parameters.items()
        if nom != "self" and p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD)
    }
    manquants = [n for n in attendus if n not in params]
    assert not manquants, f"{libelle} : paramètres retirés ou renommés {manquants}"
    nouveaux_obligatoires = [
        n for n, p in params.items() if n not in attendus and p.default is p.empty
    ]
    assert not nouveaux_obligatoires, (
        f"{libelle} : nouveaux paramètres OBLIGATOIRES {nouveaux_obligatoires} "
        "— le studio ne les passe pas"
    )


@pytest.mark.parametrize(
    ("module", "nom", "attendus"),
    [(m, n, a) for m, noms in CONTRAT.items() for n, a in noms.items()],
    ids=lambda v: v if isinstance(v, str) else "",
)
def test_surface_importee_par_le_studio(module, nom, attendus):
    mod = importlib.import_module(module)
    assert hasattr(mod, nom), f"{module}.{nom} a disparu : le studio l'importe"
    if attendus is not None:
        _verifier_signature(getattr(mod, nom), attendus, f"{module}.{nom}")


@pytest.mark.parametrize(("methode", "attendus"), METHODES_DATA_MANAGER.items())
def test_methodes_data_manager_appelees_par_le_studio(methode, attendus):
    from src.utils.data_manager import DataManager

    assert hasattr(DataManager, methode), f"DataManager.{methode} a disparu"
    _verifier_signature(getattr(DataManager, methode), attendus, f"DataManager.{methode}")
