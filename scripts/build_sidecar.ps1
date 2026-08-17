param(
    [string]$TargetTriple = "x86_64-pc-windows-msvc"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$destinationDir = Join-Path $root "desktop\src-tauri\binaries"
$destination = Join-Path $destinationDir "researchbrain-sidecar-$TargetTriple.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Python environment not found: $python"
}

Push-Location $root
try {
    & $python -m PyInstaller --noconfirm --clean "researchbrain-sidecar.spec"
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }
    New-Item -ItemType Directory -Force -Path $destinationDir | Out-Null
    Copy-Item -LiteralPath (Join-Path $root "dist\researchbrain-sidecar.exe") -Destination $destination -Force
    $hash = (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash.ToLowerInvariant()
    Write-Output "sidecar=$destination"
    Write-Output "sha256=$hash"
}
finally {
    Pop-Location
}
