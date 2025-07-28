# Aller dans le dossier du projet
cd "C:\Users\g78re\Documents\Python\GitHub\music_credits_scraper"

# Débloquer temporairement les scripts pour cette session
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope Process -Force

# Activer l’environnement virtuel
.\venv\Scripts\Activate.ps1

# Message de confirmation
Write-Host "venv active avec succes dans music_credits_scraper" -ForegroundColor Green
Write-Host "Tu peux maintenant executer tes scripts Python ici." -ForegroundColor Yellow
