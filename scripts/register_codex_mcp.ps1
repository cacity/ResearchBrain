param(
    [string]$Name = "researchbrain",
    [string]$DataDir = "$env:LOCALAPPDATA\ResearchBrain"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "Python environment not found: $python"
}
$codex = Get-Command codex.cmd -ErrorAction Stop
& $codex.Source mcp remove $Name 2>$null
& $codex.Source mcp add $Name --env "RESEARCHBRAIN_DATA_DIR=$DataDir" -- $python -m researchbrain.mcp_server
if ($LASTEXITCODE -ne 0) { throw "Codex MCP registration failed" }
& $codex.Source mcp get $Name
