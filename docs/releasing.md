# Releasing

## Preconditions

- The repository name, Python package name, Tauri product name, Windows identifier, and MCP registration name
  have been finalized.
- `CHANGELOG.md`, `CITATION.cff`, `versions.lock`, and all package versions match.
- Dependencies and third-party licenses have been reviewed from the current lock files.
- CI passes on `main`; the clean-room Windows installer test passes.
- No PDF, database, API key, personal path, model weight, or generated executable is tracked by Git.

## Build

```powershell
git status --short
.\scripts\build_release.ps1
```

Record SHA-256 for the sidecar, NSIS installer, and MSI. Test installation, startup, Zotero detection, manual
PDF import, MCP handshake, graceful shutdown, uninstall, and user-data retention.

## Publish

Create an annotated `vX.Y.Z` tag only after local and CI artifacts agree. GitHub Release notes should contain
the changelog section, checksums, unsigned/signed status, supported Windows versions, data migration notes,
known limitations, and third-party runtime notices.

Do not commit generated binaries. Attach them to the GitHub Release.
