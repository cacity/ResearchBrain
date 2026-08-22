# ResearchBrain

English | [简体中文](README.md)

ResearchBrain is a Windows-first, local-first research knowledge workspace covering literature discovery,
acquisition, organization, reading, parsing, retrieval, investigation, and citation. It provides one workflow
from source material to traceable research conclusions.

It can incrementally mirror Zotero metadata, collections, tags, and PDFs; import DOI batches; and discover
literature through Crossref, OpenAlex, arXiv, and PubMed before resolving lawful open full text. Imported works
are grouped by canonical identifiers, PDFs can be read in the app, and MinerU or PyMuPDF converts them into
page- and section-aware Markdown. Separately indexed metadata, abstracts, and full text support local search,
local-first online supplementation, and online research. Answers retain source excerpts, page locations, or
online provenance; discovered works can be imported back into the library; conversations remain available;
references export to CSL-JSON, BibTeX, RIS, DOI lists, or Markdown; and MCP exposes the corpus to Codex and
other compatible clients.

Metadata, PDFs, parsed artifacts, vector indexes, and conversations remain on the local machine by default.
Only the requests needed for enabled online discovery or configured MiniMax and DeepSeek calls leave the
device. The Windows 11 application runs without WSL, gbrain, Docker, or PostgreSQL.

> Status: `0.1.9 alpha`. The core research loop works, but this is not a full Zotero replacement.

![ResearchBrain desktop library](docs/images/researchbrain-library.png)

## What it does

- Incrementally mirrors Zotero metadata, collections, tags, and PDF attachments through the Local API,
  with per-item PDF, parsing, and embedding status.
- Imports DOI batches and canonical records without a DOI; discovers and merges records through Crossref,
  OpenAlex, arXiv, and PubMed.
- Resolves authorized/openly licensed PDFs through Unpaywall, OpenAlex, PMC, and licensed Crossref links;
  extracts PDF links from verified OA landing pages, falls back across failed candidates, and accepts manual PDFs.
- Parses PDFs with MinerU and falls back to PyMuPDF, preserving page and section provenance.
- Embeds titles, abstracts, and parsed PDF text with MiniMax, then combines LanceDB full-text, vector,
  and RRF hybrid retrieval. Empty libraries return an explicit readiness response.
- Supports local-only, local-first plus online, and online-research chat scopes; DeepSeek must cite supplied
  local or online evidence IDs.
- Persists every conversation, message, and citation per library. The current question has retrieval weight
  `1.0`, recent-question context has weight `0.25`, and prior model answers have evidence weight `0`.
- Hands searches off to Google Scholar in the browser instead of relying on unstable page scraping.
- Exports CSL-JSON, BibTeX, RIS, DOI lists, and Markdown.
- Exposes the same local corpus through the desktop app, FastAPI, CLI, and stdio MCP.

## Safety and scope

- Zotero integration is read-only and never writes to `zotero.sqlite`.
- Zotero Desktop's Local API does not expose deleted-item records. New and changed records and new or
  replaced PDFs synchronize incrementally, but deleting an item in Zotero does not currently remove it
  from the mirror automatically.
- Automated acquisition does not use shadow libraries or bypass paywalls.
- Documents remain local by default. Relevant text leaves the machine only when the user calls configured
  MiniMax or DeepSeek services.
- MinerU models are not bundled. PyMuPDF is the automatic fallback.
- The alpha does not include advanced metadata editing, interactive duplicate merging, PDF annotation, or
  team collaboration.

See [Architecture](docs/architecture.md), [Privacy and security](docs/privacy-and-security.md), and
[Licensing](docs/licensing.md).

## Source setup

Requirements: Windows 11 x64, Python 3.11, Node.js 20, Rust stable MSVC, Visual Studio C++ Build Tools,
WebView2, and preferably [uv](https://docs.astral.sh/uv/).

```powershell
git clone <repository-url>
cd <repository-directory>
uv venv --python 3.11
uv sync --all-extras --group dev
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m researchbrain.cli init
cd desktop
npm ci
npm run tauri dev
```

Configure provider credentials in the desktop settings so they are stored in Windows Credential Manager.
The default data directory is `%LOCALAPPDATA%\ResearchBrain`.

## Quality gate

```powershell
.\.venv\Scripts\python.exe -m ruff format --check --no-cache src tests scripts
.\.venv\Scripts\python.exe -m ruff check --no-cache src tests scripts
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider
cd desktop
npm run typecheck
npm run format:check
```

Read [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request. Do not attach private PDFs, Zotero
databases, credentials, browser cookies, or unsanitized logs to public issues.

Before creating the public repository, follow the [GitHub publishing checklist](docs/github-publishing-checklist.md).

## License

ResearchBrain is licensed under [GNU AGPL-3.0-only](LICENSE). Third-party components retain their own terms;
review [docs/licensing.md](docs/licensing.md) before redistributing binaries.
