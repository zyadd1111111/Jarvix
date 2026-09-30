$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'Install Jarvix first using the README setup commands.'
}
Start-Process -FilePath $pythonPath -ArgumentList '-m', 'jarvix' -WorkingDirectory $projectRoot -WindowStyle Hidden
