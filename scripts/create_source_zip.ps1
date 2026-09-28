$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$version = (Select-String -LiteralPath (Join-Path $projectRoot 'pyproject.toml') -Pattern '^version\s*=\s*"([^"]+)"').Matches[0].Groups[1].Value
$staging = Join-Path ([System.IO.Path]::GetTempPath()) "Jarvix-source-$version"
$archive = Join-Path $projectRoot "dist\Jarvix-$version-source.zip"
Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $staging | Out-Null
$excluded = @('.git', '.venv', 'build', 'dist', 'artifacts', '.pytest_cache', '.ruff_cache')
Get-ChildItem -LiteralPath $projectRoot -Force | Where-Object { $excluded -notcontains $_.Name } |
    Copy-Item -Destination $staging -Recurse -Force
Remove-Item -LiteralPath $archive -Force -ErrorAction SilentlyContinue
Compress-Archive -Path (Join-Path $staging '*') -DestinationPath $archive -Force
Remove-Item -LiteralPath $staging -Recurse -Force
Write-Host "Source archive: $archive"

