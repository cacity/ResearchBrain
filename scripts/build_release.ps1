param(
    [string]$TargetTriple = "x86_64-pc-windows-msvc",
    [switch]$SkipPythonInstall,
    [switch]$SkipNpmInstall
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$desktop = Join-Path $root "desktop"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Python environment not found: $python"
}
if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) {
    throw "Rust/Cargo is required. Install rustup and the stable MSVC toolchain first."
}

Push-Location $root
try {
    if (-not $SkipPythonInstall) {
        uv pip install --python $python -r "requirements\release.txt"
        if ($LASTEXITCODE -ne 0) { throw "Python release dependency installation failed" }
    }
    & (Join-Path $PSScriptRoot "build_sidecar.ps1") -TargetTriple $TargetTriple
    if ($LASTEXITCODE -ne 0) { throw "Sidecar build failed" }

    Push-Location $desktop
    try {
        if (-not $SkipNpmInstall) {
            npm install --no-audit --no-fund
            if ($LASTEXITCODE -ne 0) { throw "npm install failed" }
        }
        npm run tauri build
        if ($LASTEXITCODE -ne 0) { throw "Tauri build failed" }
    }
    finally {
        Pop-Location
    }

    $bundleRoot = Join-Path $desktop "src-tauri\target\release\bundle"
    Get-ChildItem -LiteralPath $bundleRoot -Recurse -File |
        Where-Object { $_.Extension -in ".exe", ".msi" } |
        ForEach-Object {
            $hash = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
            Write-Output "artifact=$($_.FullName)"
            Write-Output "sha256=$hash"
        }
}
finally {
    Pop-Location
}
