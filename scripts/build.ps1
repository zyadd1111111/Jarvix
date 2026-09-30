param([string]$PythonPath)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not $PythonPath) { $PythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe' }
$versionLine = Select-String -LiteralPath (Join-Path $projectRoot 'pyproject.toml') -Pattern '^version\s*=\s*"([^"]+)"' | Select-Object -First 1
if (-not $versionLine) { throw 'Could not determine the project version.' }
$version = $versionLine.Matches[0].Groups[1].Value
$archive = "dist\Jarvix-$version-windows-x64.zip"
Push-Location -LiteralPath $projectRoot
try {
    $env:PYTHONPATH = Join-Path $projectRoot 'src'
    $sourceBefore = (Get-ChildItem -LiteralPath 'src' -Filter '*.py' -Recurse | Sort-Object FullName | Get-FileHash).Hash -join ','
    & $pythonPath -m pip check
    if ($LASTEXITCODE -ne 0) { throw 'Dependency validation failed; packaging stopped.' }
    & $pythonPath -m ruff check src tests scripts
    if ($LASTEXITCODE -ne 0) { throw 'Lint failed; packaging stopped.' }
    & $pythonPath -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw 'Tests failed; packaging stopped.' }
    & $pythonPath -m PyInstaller --noconfirm Jarvix.spec
    if ($LASTEXITCODE -ne 0) { throw 'Packaging failed.' }
    $sourceAfter = (Get-ChildItem -LiteralPath 'src' -Filter '*.py' -Recurse | Sort-Object FullName | Get-FileHash).Hash -join ','
    if ($sourceAfter -ne $sourceBefore) { throw 'Application source changed during verification/build. Rebuild from a stable checkpoint.' }
    & $pythonPath (Join-Path $PSScriptRoot 'verify-package.py') (Join-Path $projectRoot 'dist\Jarvix')
    if ($LASTEXITCODE -ne 0) { throw 'Frozen code or x64 verification failed.' }
    Copy-Item -LiteralPath 'assets\browser_extension' -Destination 'dist\Jarvix' -Recurse -Force
    & (Join-Path $PSScriptRoot 'smoke-package.ps1') -PythonPath $PythonPath
    & $pythonPath -m build --wheel
    if ($LASTEXITCODE -ne 0) { throw 'Wheel build failed.' }
    Copy-Item -LiteralPath 'README.md', 'SECURITY.md', 'LICENSE' -Destination 'dist\Jarvix'
    Copy-Item -LiteralPath 'docs' -Destination 'dist\Jarvix' -Recurse -Force
    Copy-Item -LiteralPath 'examples' -Destination 'dist\Jarvix' -Recurse -Force
    Compress-Archive -LiteralPath 'dist\Jarvix' -DestinationPath $archive -Force
    Write-Host 'Desktop executable: dist\Jarvix\Jarvix.exe'
    Write-Host 'Browser helper: dist\Jarvix\JarvixBrowserHost.exe; extension: dist\Jarvix\browser_extension'
}
finally { Pop-Location }
