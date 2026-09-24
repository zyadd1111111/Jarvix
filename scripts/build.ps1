$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
Push-Location -LiteralPath $projectRoot
try {
    & $pythonPath -m pip check
    if ($LASTEXITCODE -ne 0) { throw 'Dependency validation failed; packaging stopped.' }
    & $pythonPath -m ruff check src tests scripts
    if ($LASTEXITCODE -ne 0) { throw 'Lint failed; packaging stopped.' }
    & $pythonPath -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw 'Tests failed; packaging stopped.' }
    & $pythonPath -m PyInstaller --noconfirm Jarvix.spec
    if ($LASTEXITCODE -ne 0) { throw 'Packaging failed.' }
    & (Join-Path $PSScriptRoot 'smoke-package.ps1')
    & $pythonPath -m build --wheel
    if ($LASTEXITCODE -ne 0) { throw 'Wheel build failed.' }
    Copy-Item -LiteralPath 'README.md', 'SECURITY.md', 'LICENSE' -Destination 'dist\Jarvix'
    Copy-Item -LiteralPath 'docs' -Destination 'dist\Jarvix' -Recurse -Force
    Compress-Archive -LiteralPath 'dist\Jarvix' -DestinationPath 'dist\Jarvix-0.2.0-windows-x64.zip' -Force
    Write-Host 'Desktop executable: dist\Jarvix\Jarvix.exe'
}
finally { Pop-Location }
