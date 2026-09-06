# Voice Denoiser - one-time setup script
# Run this in an ADMINISTRATOR PowerShell window.
# Right-click PowerShell -> "Run as administrator" first, then run this script.

Write-Host "=== Voice Denoiser Setup ===" -ForegroundColor Cyan

# 1. Check for Python 3.11
Write-Host "`nChecking for Python 3.11..."
$python311 = Get-ChildItem "C:\Users\$env:USERNAME\AppData\Local\Programs\Python\Python311\python.exe" -ErrorAction SilentlyContinue
if (-not $python311) {
    Write-Host "Python 3.11 not found. Installing via winget..." -ForegroundColor Yellow
    winget install -e --id Python.Python.3.11
    Write-Host "Python 3.11 installed. You may need to restart this terminal after setup." -ForegroundColor Yellow
} else {
    Write-Host "Python 3.11 already installed." -ForegroundColor Green
}
$pythonPath = "C:\Users\$env:USERNAME\AppData\Local\Programs\Python\Python311\python.exe"

# 2. Check for Chocolatey (needed to install VB-CABLE automatically)
Write-Host "`nChecking for Chocolatey..."
if (-not (Get-Command choco -ErrorAction SilentlyContinue)) {
    Write-Host "Chocolatey not found. Installing..." -ForegroundColor Yellow
    Set-ExecutionPolicy Bypass -Scope Process -Force
    [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.ServicePointManager]::SecurityProtocol -bor 3072
    Invoke-Expression ((New-Object System.Net.WebClient).DownloadString('https://community.chocolatey.org/install.ps1'))
} else {
    Write-Host "Chocolatey already installed." -ForegroundColor Green
}

# 3. Install VB-CABLE via Chocolatey
Write-Host "`nInstalling VB-CABLE (virtual audio device)..."
choco install vb-cable -y

# 3b. Install NirCmd (needed to auto-switch the default microphone)
Write-Host "`nInstalling NirCmd (used to switch default audio device)..."
choco install nircmd -y

# 4. Install Python dependencies
Write-Host "`nInstalling Python packages (this can take a few minutes)..."
& $pythonPath -m pip install --upgrade pip
& $pythonPath -m pip install -r requirements.txt --index-url https://download.pytorch.org/whl/cpu

Write-Host "`n=== Setup complete! ===" -ForegroundColor Cyan
Write-Host "IMPORTANT: Restart your computer now so VB-CABLE registers correctly." -ForegroundColor Yellow
Write-Host "After restarting, just double-click run.bat to start the denoiser." -ForegroundColor Yellow
Read-Host "`nPress Enter to exit"
