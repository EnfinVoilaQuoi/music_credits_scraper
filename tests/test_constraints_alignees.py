"""Les versions ÉPINGLÉES des requirements doivent être celles de `constraints.txt`.

`constraints.txt` est le `pip freeze` du venv (CI durcie, 2026-09-28) : c'est la
version réellement installée et testée. Une épingle divergente dans un
requirements rend l'installation IMPOSSIBLE en CI (`ResolutionImpossible`) —
cas réel du 2026-09-29, `ytmusicapi==1.8.0` contre `1.12.1`, vu seulement
à l'étape suivante (« playwright n'est pas reconnu »).
"""

import re
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
_EPINGLE = re.compile(r"^([A-Za-z0-9_.\-]+)(?:\[[^\]]*\])?\s*==\s*([^\s;#]+)")


def _epingles(fichier: str) -> dict[str, str]:
    versions = {}
    for ligne in (RACINE / fichier).read_text(encoding="utf-8").splitlines():
        m = _EPINGLE.match(ligne.strip())
        if m:
            versions[m[1].lower().replace("_", "-")] = m[2]
    return versions


def test_requirements_epingles_comme_les_contraintes():
    contraintes = _epingles("constraints.txt")
    ecarts = [
        f"{fichier}: {paquet}=={version} ≠ constraints {contraintes[paquet]}"
        for fichier in ("requirements.txt", "requirements-dev.txt")
        for paquet, version in _epingles(fichier).items()
        if paquet in contraintes and contraintes[paquet] != version
    ]
    assert not ecarts, "\n".join(ecarts)
