# Contributing

Thank you for helping improve ResearchBrain. The project is in alpha; small, testable changes are easier
to review than broad rewrites.

## Before opening a change

- Search existing issues before filing a duplicate.
- Use a discussion or feature request before changing storage schemas, public APIs, licensing, providers,
  or the Zotero synchronization contract.
- Never attach copyrighted PDFs, API keys, Zotero databases, or private research data to an issue.
- Automated full-text retrieval must remain limited to lawful, authorized sources.

## Development setup

Follow [docs/development.md](docs/development.md). The minimum quality gate is:

```powershell
.\.venv\Scripts\python.exe -m ruff format --check --no-cache src tests scripts
.\.venv\Scripts\python.exe -m ruff check --no-cache src tests scripts
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider
cd desktop
npm run typecheck
npm run format:check
cd src-tauri
cargo fmt --check
cargo clippy --all-targets -- -D warnings
```

## Pull requests

- Keep one behavioral purpose per pull request.
- Add or update tests for behavior changes.
- Document data migrations, privacy impact, network calls, and new dependencies.
- Update `CHANGELOG.md` under `Unreleased` for user-visible changes.
- Confirm that generated files, binaries, model weights, PDFs, databases, and secrets are not committed.

By contributing, you agree that your contribution is licensed under `AGPL-3.0-only`.
