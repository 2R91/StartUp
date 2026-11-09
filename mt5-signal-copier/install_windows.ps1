# Installation du copieur de signaux MT5 sur un PC / VPS Windows.
# À coller dans PowerShell (une seule ligne, voir README) :
#   irm https://raw.githubusercontent.com/2R91/StartUp/claude/copy-trading-signals-mt5-0cicqj/mt5-signal-copier/install_windows.ps1 | iex

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # téléchargements beaucoup plus rapides
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Branch    = "claude/copy-trading-signals-mt5-0cicqj"
$InstallTo = "C:\mt5-signal-copier"
$PyVersion = "3.12.8"
$PyExe     = "C:\Program Files\Python312\python.exe"
$Tmp       = Join-Path $env:TEMP "mt5-copier-setup"
New-Item -ItemType Directory -Force -Path $Tmp | Out-Null

Write-Host "`n[1/4] Python $PyVersion..." -ForegroundColor Cyan
if (Test-Path $PyExe) {
    Write-Host "  déjà installé"
} else {
    $pyInstaller = Join-Path $Tmp "python-setup.exe"
    Invoke-WebRequest "https://www.python.org/ftp/python/$PyVersion/python-$PyVersion-amd64.exe" -OutFile $pyInstaller
    Start-Process $pyInstaller -Wait -ArgumentList "/quiet", "InstallAllUsers=1", "PrependPath=1", "Include_test=0"
    if (-not (Test-Path $PyExe)) { throw "L'installation de Python a échoué." }
}

Write-Host "`n[2/4] Téléchargement du copieur..." -ForegroundColor Cyan
$zip = Join-Path $Tmp "copier.zip"
Invoke-WebRequest "https://github.com/2R91/StartUp/archive/refs/heads/$Branch.zip" -OutFile $zip
$extract = Join-Path $Tmp "src"
if (Test-Path $extract) { Remove-Item $extract -Recurse -Force }
Expand-Archive $zip -DestinationPath $extract
$src = Get-ChildItem $extract -Directory | Select-Object -First 1
New-Item -ItemType Directory -Force -Path $InstallTo | Out-Null
# Mise à jour sans écraser config.toml, la session Telegram ni trades.json
Copy-Item (Join-Path $src.FullName "mt5-signal-copier\*") $InstallTo -Recurse -Force
if (-not (Test-Path "$InstallTo\config.toml")) {
    Copy-Item "$InstallTo\config.example.toml" "$InstallTo\config.toml"
}

Write-Host "`n[3/4] Dépendances Python (MetaTrader5, Telethon)..." -ForegroundColor Cyan
& $PyExe -m pip install --upgrade pip --quiet
& $PyExe -m pip install -r "$InstallTo\requirements.txt" --quiet
if ($LASTEXITCODE -ne 0) { throw "pip install a échoué." }

# Raccourcis sur le bureau
$desktop = [Environment]::GetFolderPath("Desktop")
@"
@echo off
cd /d "$InstallTo"
"$PyExe" copier.py
pause
"@ | Set-Content "$desktop\Lancer le copieur.bat" -Encoding ASCII
@"
@echo off
cd /d "$InstallTo"
"$PyExe" copier.py --list-chats
pause
"@ | Set-Content "$desktop\Lister mes canaux Telegram.bat" -Encoding ASCII
@"
@echo off
cd /d "$InstallTo"
"$PyExe" copier.py --source console
pause
"@ | Set-Content "$desktop\Tester un signal.bat" -Encoding ASCII
@"
@echo off
notepad "$InstallTo\config.toml"
"@ | Set-Content "$desktop\Modifier la config.bat" -Encoding ASCII

Write-Host "`n[4/4] MetaTrader 5..." -ForegroundColor Cyan
$mt5Paths = @("C:\Program Files\MetaTrader 5\terminal64.exe") +
            (Get-ChildItem "C:\Program Files" -Filter terminal64.exe -Recurse -Depth 2 -ErrorAction SilentlyContinue |
             ForEach-Object FullName)
if ($mt5Paths | Where-Object { $_ -and (Test-Path $_) }) {
    Write-Host "  déjà installé"
} else {
    $mt5Installer = Join-Path $Tmp "mt5setup.exe"
    Invoke-WebRequest "https://download.mql5.com/cdn/web/metaquotes.software.corp/mt5/mt5setup.exe" -OutFile $mt5Installer
    Write-Host "  L'installateur MT5 s'ouvre : clique sur Suivant jusqu'au bout."
    Start-Process $mt5Installer -Wait
}

Write-Host "`nTerminé !" -ForegroundColor Green
Write-Host "Copieur installé dans $InstallTo. Raccourcis créés sur le bureau :"
Write-Host "  - Modifier la config      : remplis Telegram et vérifie le risque"
Write-Host "  - Tester un signal        : colle un signal pour voir s'il est compris"
Write-Host "  - Lister mes canaux Telegram"
Write-Host "  - Lancer le copieur"
Write-Host "Dans MT5 : connecte-toi à ton compte (Fichier > Connexion) et active « Algo Trading »."
