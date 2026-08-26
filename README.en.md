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

> Status: `0.2.0 alpha`. The core research loop works, but this is not a full Zotero replacement.

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
- On the experimental `feature/deepseek-harness` branch, launches an isolated DeepSeek Harness Web profile
  that uses ResearchBrain MCP tools and a strict literature-research Skill for multi-step investigations.
- Installs third-party filesystem Skills from a local folder, ZIP, or GitHub; validates dependencies and
  file safety; and manages enable, update, uninstall, and isolated Harness deployment.

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

## Codex Skills

ResearchBrain separates deterministic data pipelines from agent reasoning. The local service owns SQLite,
LanceDB, Zotero watermarks, PDF objects, MinerU/PyMuPDF parsing, MiniMax vectors, and job state. Codex Skills
select and compose tools, verify terminal state, and synthesize retrieved evidence directly.

| Skill                             | Capability                                                                  | Typical use                            |
| --------------------------------- | --------------------------------------------------------------------------- | -------------------------------------- |
| `researchbrain-zotero-sync`       | Incrementally mirrors Zotero metadata, deletions, and local PDF attachments | Synchronize newly added Zotero papers  |
| `researchbrain-doi-fulltext`      | Imports deduplicated DOIs and retrieves lawful open PDFs                    | Batch DOI and full-text acquisition    |
| `researchbrain-pdf-ingest`        | Deduplicates PDFs, parses to Markdown, and queues full-text vectors         | Local PDF or PDF-to-Markdown ingestion |
| `researchbrain-vector-index`      | Audits and fills missing metadata and full-text embeddings                  | Incremental vector-index maintenance   |
| `researchbrain-evidence-research` | Combines local evidence with Crossref, OpenAlex, arXiv, and PubMed          | Reviews, comparisons, and gap analysis |

### Install and enable

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\register_codex_mcp.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\install_codex_skills.ps1
codex.cmd mcp get researchbrain
```

Restart Codex after installation. Explicit `$skill-name` invocation is the most predictable form, although Codex
can also select a Skill from a natural-language request.

```text
Use $researchbrain-zotero-sync to incrementally sync Zotero into "My Library" and report missing PDFs.

Use $researchbrain-doi-fulltext to import these DOIs, retrieve lawful open PDFs, and report metadata,
PDF, Markdown, and vector status for each paper.

Use $researchbrain-pdf-ingest to attach F:\papers\example.pdf to DOI 10.xxxx/xxxxx, parse it to Markdown,
and verify full-text indexing.

Use $researchbrain-vector-index to audit "My Library" and process only missing metadata or full-text vectors.

Use $researchbrain-evidence-research to answer this research question from local full text first, then extend
coverage with verified academic sources and include DOI-backed limitations.
```

Skills can be composed as Zotero sync → PDF parsing → vector completion → evidence research. A `queued` or
`running` response is not completion; PDF, Markdown, or vectors are ready only after the corresponding job is
`complete`. See [ResearchBrain Codex Skills](docs/skills.md) for detailed responsibilities and the boundary
planned for a future standalone Skills repository.

## DeepSeek Harness branch

The `feature/deepseek-harness` branch manages the official Harness Web profile instead of reimplementing its
agent loop. The **Deep Research** view detects Node.js, installs a verified portable Node 24 runtime when the
system version is below `22.19`, installs a pinned DSH release, and links the ResearchBrain MCP bundle. Harness
runs in an isolated workspace and accesses the literature database only through MCP. Write operations such as
DOI import and lawful open-full-text resolution remain explicit queued tools.

The **Skills** view accepts local Skill directories, local ZIP archives, and `https://github.com/...`
repositories. A repository containing multiple Skills requires a subpath; a branch, tag, or commit can be
pinned. Third-party Skills are disabled by default. Their MCP dependencies, local-script permissions, and
compatibility state are shown before use, and Harness must restart after an enable, update, or uninstall.

ResearchBrain bundles its Zotero sync, DOI/full-text, PDF ingest, vector-index, evidence-research, and general
literature Skills, so they are available to Harness without copying them from a Codex installation.

Installation reads and copies files but does not execute third-party code. ResearchBrain rejects ZIP path
traversal, symbolic links, invalid `SKILL.md` frontmatter, built-in name conflicts, and oversized packages.
Skills containing scripts require review, while Skills declaring additional MCP servers require those services
to be configured. The play action starts Harness with the current library and copies an explicit
`$skill-name` prompt; actual capabilities remain bounded by configured Harness tools and user permissions.

See [DeepSeek Harness integration](docs/deepseek-harness.md) for setup, security boundaries, and rollback.

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
