param(
    [string]$Destination = "",
    [switch]$WhatIf
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$sourceRoot = Join-Path $root ".agents\skills"
$codexHome = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $HOME ".codex" }
$destinationRoot = if ($Destination) { $Destination } else { Join-Path $codexHome "skills" }
$skillNames = @(
    "researchbrain-vector-index",
    "researchbrain-zotero-sync",
    "researchbrain-doi-fulltext",
    "researchbrain-pdf-ingest",
    "researchbrain-evidence-research"
)

foreach ($name in $skillNames) {
    $source = Join-Path $sourceRoot $name
    $target = Join-Path $destinationRoot $name
    if (-not (Test-Path -LiteralPath (Join-Path $source "SKILL.md"))) {
        throw "Skill source is incomplete: $source"
    }
    if ($WhatIf) {
        Write-Output "would_install=$name target=$target"
        continue
    }
    New-Item -ItemType Directory -Force -Path $target | Out-Null
    Copy-Item -LiteralPath (Join-Path $source "SKILL.md") -Destination $target -Force
    $agents = Join-Path $source "agents"
    if (Test-Path -LiteralPath $agents) {
        Copy-Item -LiteralPath $agents -Destination $target -Recurse -Force
    }
    Write-Output "installed=$name target=$target"
}

Write-Output "Restart Codex so the installed Skills are discovered."
