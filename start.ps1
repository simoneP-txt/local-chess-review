# Avvio rapido del server Chess Review.
# Uso: tasto destro sul file -> "Esegui con PowerShell"
#      oppure da terminale:    .\start.ps1

$ErrorActionPreference = "Stop"

# Spostati nella cartella dello script (così funziona anche da doppio click).
Set-Location -Path $PSScriptRoot

# Sblocca gli script SOLO per questa sessione (non altera la policy globale).
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force

# Crea il venv se non esiste e installa le dipendenze.
if (-not (Test-Path ".\.venv\Scripts\Activate.ps1")) {
    Write-Host "Creo il venv..." -ForegroundColor Cyan
    python -m venv .venv
    .\.venv\Scripts\Activate.ps1
    pip install -r requirements.txt
} else {
    .\.venv\Scripts\Activate.ps1
}

# Mostra gli IP utili (LAN + eventuale Tailscale 100.x.y.z).
Write-Host "`nIndirizzi raggiungibili dal telefono:" -ForegroundColor Yellow
Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object { $_.IPAddress -like "192.168.*" -or $_.IPAddress -like "100.*" } |
    ForEach-Object { Write-Host ("  http://{0}:5000" -f $_.IPAddress) }
Write-Host ""

python app.py
