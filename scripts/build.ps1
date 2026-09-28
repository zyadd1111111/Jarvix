$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$versionLine = Select-String -LiteralPath (Join-Path $projectRoot 'pyproject.toml') -Pattern '^version\s*=\s*"([^"]+)"' | Select-Object -First 1
if (-not $versionLine) { throw 'Could not determine the project version.' }
$version = $versionLine.Matches[0].Groups[1].Value
$archive = "dist\Jarvix-$version-windows-x64.zip"
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
    Compress-Archive -LiteralPath 'dist\Jarvix' -DestinationPath $archive -Force
    Write-Host 'Desktop executable: dist\Jarvix\Jarvix.exe'
}
finally { Pop-Location }

