# Privacy and Security

## Local data

SQLite metadata, source PDFs, parser artifacts, vector indexes, job history, and chats are stored under the
configured local data directory. The application does not include an account system, telemetry pipeline, or
cloud synchronization service.

## Data that can leave the device

- DOI and search queries go to configured scholarly metadata providers.
- PDF text chunks are sent to MiniMax when the user enables embedding.
- The question and retrieved evidence are sent to DeepSeek when the user requests an answer.
- Zotero Local API traffic stays on loopback.

Users are responsible for checking whether research documents may be sent to third-party model providers.
Do not process restricted, clinical, export-controlled, confidential, or personal data without authorization.

## Credentials

The desktop application stores provider keys in Windows Credential Manager. Environment variables are
supported for development and automation but can be exposed through process inspection or inherited shells.
Keys, session tokens, and credential values must never be logged or committed.

## Full-text policy

Automated retrieval is limited to explicit open-access or authorized candidates. The downloader rejects
private-network destinations, non-HTTP schemes, oversized responses, HTML masquerading as PDF, and files
without a PDF signature. This project does not provide a paywall bypass or shadow-library integration.

## Zotero policy

ResearchBrain uses the Zotero Local API for metadata and only copies managed PDF attachments from the
configured Zotero data root. It does not modify `zotero.sqlite` or write changes back to Zotero.

## Reporting

Follow [SECURITY.md](../SECURITY.md). Never publish a real private document or secret as a vulnerability
reproduction.
