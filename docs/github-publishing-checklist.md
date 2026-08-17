# GitHub Publishing Checklist

Do not publish until the project name and maintainer identity are finalized.

## Rename and identity

- Choose the public project name and repository slug.
- Decide the GitHub owner or organization and public maintainer contact.
- Rename the Python package/CLI, npm package, Rust crate, Tauri product/identifier, Windows data directory,
  Credential Manager service, MCP registration, environment prefix, icons, and documentation together.
- Add compatibility migration for existing `%LOCALAPPDATA%\ResearchBrain` data and registered MCP clients.
- Update `CITATION.cff`, `CHANGELOG.md`, package authors, repository URLs, and release artifact names.

## Repository creation

- Create an empty public repository without an auto-generated README, license, or `.gitignore`.
- Add the chosen remote only after reviewing `git status` and `scripts/public_repo_audit.py`.
- Make the first commit only after `scripts/public_repo_audit.py` passes; do not add ignored binaries or local
  data. File counts change as source and documentation evolve and must not be hard-coded.
- Push `main`, then verify README images, Mermaid diagrams, links, license detection, and community profile.

## Ignored content

The repository `.gitignore` intentionally excludes:

- API-key files, local environment files, certificates, credentials, and machine-specific settings;
- Zotero/ResearchBrain databases, PDFs, Office documents, parser artifacts, LanceDB data, and model weights;
- Python virtual environments and caches, Node dependencies, Rust/Tauri targets, Playwright output, and coverage;
- generated sidecars, Windows installers, release archives, logs, temporary files, and editor metadata.

`.env.example`, source-controlled screenshots under `docs/images`, and the empty Tauri binary-directory marker
remain publishable. Before the first commit, verify both the ignored and publishable sets:

```powershell
git status --short --ignored
uv run python scripts/public_repo_audit.py
```

## GitHub settings

- Enable Discussions if it will be the support channel.
- Enable Private Vulnerability Reporting, Dependabot alerts, secret scanning, and push protection.
- Restrict GitHub Actions permissions to read-only by default; allow write only in reviewed release workflows.
- Add branch protection/rulesets requiring `CI` and `CodeQL`, pull requests, and resolved conversations.
- Disable force pushes and branch deletion on `main`.
- Add a concise description, topics, social preview, and supported Windows version.

Suggested topics: `literature-management`, `zotero`, `rag`, `mcp`, `local-first`, `research-assistant`,
`tauri`, `fastapi`, `lancedb`, `mineru`.

## First release

- Run CI from a pull request and the manual unsigned Windows release workflow.
- Test the downloaded artifact on a clean Windows 11 account without Python, Node, Rust, WSL, or gbrain.
- Publish SHA-256 checksums, source archive, third-party notices, known limitations, and unsigned status.
- Do not advertise automatic updates or signed binaries until signing and updater infrastructure exists.
