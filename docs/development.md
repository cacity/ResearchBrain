# Development

## Toolchain

- Windows 11 x64
- Python 3.11 (3.12 is intentionally not supported in the alpha build)
- uv
- Node.js 20 and npm
- Rust stable with the `x86_64-pc-windows-msvc` target
- Visual Studio C++ Build Tools and WebView2

## Bootstrap

```powershell
uv venv --python 3.11
uv sync --all-extras --group dev
cd desktop
npm ci
cd ..
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m researchbrain.cli init
```

Do not put real credentials in `.env`. The desktop settings UI stores MiniMax and DeepSeek keys in Windows
Credential Manager. Tests use fixtures and mock transports.

## Running

Run the Tauri application:

```powershell
cd desktop
npm run tauri dev
```

Run the browser UI against the source API in two terminals:

```powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m researchbrain.cli serve
```

```powershell
cd desktop
npm run dev
```

## Tests and formatting

```powershell
.\.venv\Scripts\python.exe -m ruff format --no-cache src tests scripts
.\.venv\Scripts\python.exe -m ruff check --no-cache src tests scripts
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider

cd desktop
npm run typecheck
npm run format:check

cd src-tauri
cargo fmt --check
cargo clippy --all-targets -- -D warnings
```

Provider tests must use deterministic fixtures or mock transports. Do not make live paid API calls in CI.
Database changes require a forward Alembic migration and a migration test.

## Sidecar and installer

```powershell
.\scripts\build_sidecar.ps1
.\.venv\Scripts\python.exe .\scripts\smoke_mcp.py .\desktop\src-tauri\binaries\researchbrain-sidecar-x86_64-pc-windows-msvc.exe
.\scripts\build_release.ps1 -SkipPythonInstall -SkipNpmInstall
```

Generated executables, installer bundles, databases, PDFs, parser artifacts, and model weights are ignored.
Release binaries belong in GitHub Releases, not Git history.

For visual regression screenshots, install a Playwright browser with `npx playwright install chromium`, start
the source API and Vite, then run `npm run visual:check`. Set `RB_VISUAL_DIR` for the output directory or
`RB_BROWSER_PATH` to use an existing Chromium/Chrome executable.
