# Build script for the standalone ChessReview executable.
# Uso:
#   .\build.ps1            # build incrementale
#   .\build.ps1 -Clean     # rimuove build/ e dist/ prima
#   .\build.ps1 -Zip       # alla fine crea anche dist\ChessReview-windows.zip

[CmdletBinding()]
Param(
    [switch]$Clean,
    [switch]$Zip
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

# Risolve il Python da usare: preferisce il venv se esiste, altrimenti il Python globale.
if (Test-Path ".\.venv\Scripts\python.exe") {
    $py = (Resolve-Path ".\.venv\Scripts\python.exe").Path
    Write-Host "Uso venv: $py" -ForegroundColor DarkGray
} else {
    $py = "python"
    Write-Host "Nessun venv trovato in .\.venv - userò il Python globale." -ForegroundColor Yellow
}

# Assicura che PyInstaller sia installato (idempotente: no-op se gia' presente).
Write-Host "Verifico PyInstaller..." -ForegroundColor Cyan
& $py -m pip install --quiet pyinstaller
if ($LASTEXITCODE -ne 0) {
    Write-Host "pip install pyinstaller fallito." -ForegroundColor Red
    exit 1
}

# Pulizia opzionale.
if ($Clean) {
    if (Test-Path build) { Remove-Item -Recurse -Force build }
    if (Test-Path dist)  { Remove-Item -Recurse -Force dist  }
}

# Build (usando `python -m PyInstaller` per non dipendere dal PATH).
Write-Host "Avvio PyInstaller..." -ForegroundColor Cyan
& $py -m PyInstaller --noconfirm chessreview.spec
if ($LASTEXITCODE -ne 0) {
    Write-Host "PyInstaller ha terminato con errore." -ForegroundColor Red
    exit 1
}

$outDir = "dist\ChessReview"
if (-not (Test-Path "$outDir\ChessReview.exe")) {
    Write-Host "Build fallita: ChessReview.exe non trovato in $outDir" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "Build completata." -ForegroundColor Green
Write-Host "Output: $outDir\ChessReview.exe"

# Avviso se Stockfish non e' stato bundlato.
# PyInstaller 6.x mette i dati bundled in _internal\, NON accanto al .exe.
$bundledStockfish = "$outDir\_internal\engine\stockfish.exe"
if (-not (Test-Path $bundledStockfish)) {
    Write-Host ""
    Write-Host "ATTENZIONE: engine\stockfish.exe non era presente al momento del build." -ForegroundColor Yellow
    Write-Host "L'utente finale dovra' copiarlo in $outDir\engine\" -ForegroundColor Yellow
} else {
    $sfSize = [Math]::Round(((Get-Item $bundledStockfish).Length / 1MB), 1)
    Write-Host "Stockfish bundlato in _internal\engine\ ($sfSize MB)." -ForegroundColor DarkGray
}

# Zip opzionale per distribuzione.
# Compress-Archive di PowerShell 5.1 e' lento e fragile su cartelle grandi
# (e Windows Defender talvolta tiene i file appena scritti aperti per scansione),
# quindi usiamo direttamente System.IO.Compression.ZipFile.
if ($Zip) {
    $zipPath = (Join-Path (Resolve-Path "dist").Path "ChessReview-windows.zip")
    if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
    $srcDir = (Resolve-Path $outDir).Path
    Write-Host "Creo $zipPath ..." -ForegroundColor Cyan
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    # Retry sull'apertura dei file (Defender puo' tenerli locked per qualche secondo).
    $attempts = 0
    while ($true) {
        try {
            [System.IO.Compression.ZipFile]::CreateFromDirectory(
                $srcDir, $zipPath, [System.IO.Compression.CompressionLevel]::Optimal, $false
            )
            break
        } catch [System.IO.IOException] {
            $attempts++
            if ($attempts -ge 3) { throw }
            Write-Host "  IO locked, riprovo tra 3s (attempt $attempts/3)..." -ForegroundColor Yellow
            Start-Sleep -Seconds 3
            if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
        }
    }
    $size = [Math]::Round(((Get-Item $zipPath).Length / 1MB), 1)
    Write-Host "Archivio pronto: $zipPath ($size MB)" -ForegroundColor Green
}
