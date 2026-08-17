# Security Policy

## Supported versions

Only the latest release and the current `main` branch receive security fixes during the alpha period.

## Reporting a vulnerability

Do not open a public issue for vulnerabilities involving local authentication, command execution, path
traversal, unsafe downloads, credential storage, or private-document disclosure. After the repository is
published, use GitHub Private Vulnerability Reporting. Until that channel is enabled, contact the repository
owner privately and share only the minimum reproducible information.

Do not include real API keys, private PDFs, Zotero databases, access cookies, or personal paths. A maintainer
should acknowledge a complete report within seven days and coordinate disclosure after a fix is available.

## Security boundaries

- The HTTP API binds to loopback and the desktop app generates a per-launch session token.
- Provider keys belong in Windows Credential Manager; environment variables are intended for development.
- Zotero synchronization uses the Local API and treats the Zotero library as read-only.
- Full-text downloads reject local/private addresses and accept only validated PDF content from authorized
  or openly licensed sources.
