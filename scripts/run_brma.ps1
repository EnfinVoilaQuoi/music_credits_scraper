<#
  Mise à jour des certifications BRMA (Ultratop) via la route CDP.

  Ultratop est derrière un Cloudflare « managed » qui boucle tout navigateur
  LANCÉ par de l'automation (même le vrai Chrome via channel="chrome"). La seule
  voie fiable : démarrer NOTRE Chrome normalement (port de debug), résoudre le
  challenge une fois à la main, et laisser patchright s'y ATTACHER via CDP.

  Ce script :
    1. démarre un Chrome dédié (profil séparé) avec le port de debug s'il ne
       tourne pas déjà ;
    2. attend que le port réponde ;
    3. pose GENIUS_CDP_URL et lance la MàJ.

  Usage (depuis la racine du projet, venv activé) :
    .\scripts\run_brma.ps1            # années récentes (years-back 1)
    .\scripts\run_brma.ps1 3          # years-back 3

  La 1re fois, résous le Cloudflare dans la fenêtre Chrome qui s'ouvre ; le
  cookie est mémorisé dans le profil debug, ensuite c'est transparent.
#>
param([int]$YearsBack = 1)

$ErrorActionPreference = "Stop"

# Se placer à la racine du projet (parent du dossier scripts/)
Set-Location (Split-Path $PSScriptRoot -Parent)

$chrome  = "C:\Program Files\Google\Chrome\Application\chrome.exe"
$port    = 9222
$profile = Join-Path $env:USERPROFILE "chrome-debug"
$verUrl  = "http://127.0.0.1:$port/json/version"

function Test-CdpPort {
    try { Invoke-WebRequest -UseBasicParsing $verUrl -TimeoutSec 2 | Out-Null; return $true }
    catch { return $false }
}

if (Test-CdpPort) {
    Write-Host "[run_brma] Chrome-debug deja en ecoute sur $port." -ForegroundColor Green
} else {
    if (-not (Test-Path $chrome)) {
        Write-Error "Google Chrome introuvable : $chrome (installe-le ou corrige le chemin)."
        exit 1
    }
    Write-Host "[run_brma] Lancement de Chrome-debug (profil $profile, port $port)..." -ForegroundColor Cyan
    Start-Process $chrome -ArgumentList @(
        "--remote-debugging-port=$port",
        "--user-data-dir=`"$profile`""
    )
    $ok = $false
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Milliseconds 500
        if (Test-CdpPort) { $ok = $true; break }
    }
    if (-not $ok) {
        Write-Error "Le port de debug $port ne repond pas. Ferme tout Chrome et reessaie."
        exit 1
    }
    Write-Host "[run_brma] Chrome-debug pret." -ForegroundColor Green
}

Write-Host "[run_brma] Si ultratop affiche un Cloudflare, resous-le UNE fois dans la fenetre Chrome." -ForegroundColor Yellow

# CDP prioritaire ; on neutralise le mode channel="chrome" (inefficace sur ultratop)
$env:GENIUS_CDP_URL = "http://127.0.0.1:$port"
Remove-Item Env:\SCRAPER_BROWSER_CHANNEL -ErrorAction SilentlyContinue

python src/utils/update_brma.py --mode once --years-back $YearsBack
