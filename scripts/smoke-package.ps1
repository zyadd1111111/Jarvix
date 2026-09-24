$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$binaryPath = Join-Path $projectRoot 'dist\Jarvix\Jarvix.exe'
$captureName = 'artifacts\package-check-' + [Guid]::NewGuid().ToString('N') + '.png'
$profileName = 'artifacts\package-check-profile'
$process = Start-Process -FilePath $binaryPath -ArgumentList '--screenshot', $captureName, '--data-dir', $profileName -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru
if (-not $process.WaitForExit(30000)) {
    $process.Kill()
    throw 'Packaged startup did not finish within 30 seconds.'
}
if ($process.ExitCode -ne 0 -or -not (Test-Path -LiteralPath (Join-Path $projectRoot $captureName))) {
    throw "Packaged startup failed (exit $($process.ExitCode))."
}
Write-Host "Packaged startup verified: $captureName"
