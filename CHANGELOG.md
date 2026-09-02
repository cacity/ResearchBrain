# Changelog

All notable changes to this project are documented in this file. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Open-source repository metadata, community files, CI, security policy, and contributor documentation.

## [0.3.0] - 2026-09-02

### Added

- Persisted multi-round research runs with planning, iterative retrieval, evidence coverage assessment,
  synthesis, independent review, bounded revision, cancellation, steering, SSE progress, and retry.
- A complete evidence ledger that distinguishes metadata, abstracts, sections, and page-level full text while
  retaining both cited and inspected sources for audit.
- Explicit DOI acquisition approval, skip, background job tracking, and same-run re-retrieval when imported
  evidence finishes within the configured wait budget.
- Structured session memory with zero evidentiary weight for prior model answers, plus optional read-only
  parallel scouts isolated by subquestion.
- A fixed 24-case V1/V2 research quality set and deterministic citation and coverage scoring.
- Desktop progress, stop, mid-run constraint, approval, and failed or paused run recovery controls.

### Changed

- Evidence Chat uses the V2 research orchestrator by default while retaining the legacy synchronous API for
  rollback and comparison.
- Provider errors and empty evidence always produce a visible, diagnosable terminal state.

## [0.1.8] - 2026-08-17

### Added

- Canonical literature keys backed by normalized DOI, PMCID, PMID, arXiv, or internal item identifiers.
- DOI coverage lookup with optional exact-PDF SHA-256 matching and a recommended next processing action.
- Separate metadata and full-text embedding states plus five explicit literature coverage levels.

### Changed

- Completed DOI metadata batches now queue metadata embedding automatically.
- The library view displays DOI identity and separates abstract-vector from full-text-vector status.

## [0.1.7] - 2026-08-17

### Added

- Concurrent lawful full-text discovery through Unpaywall, OpenAlex, and the NCBI PMC DOI converter.
- PDF-link extraction from verified open-access landing pages, including citation metadata and download links.
- Candidate fallback so one invalid or unavailable PDF URL does not prevent another authorized source from succeeding.

### Changed

- DOI imports now combine dynamic OA providers with licensed Crossref links and discovery metadata before selecting a PDF.
- Redirect targets receive the same local/private-address safety validation as original download URLs.

## [0.1.6] - 2026-08-17

### Added

- Persistent per-library conversation history with session summaries, previews, message counts, and full restoration.
- Independent conversation-history navigation on desktop and a compact session selector on narrow screens.
- Follow-up retrieval that combines the current question at weight `1.0` with recent user context at weight `0.25`.

### Changed

- The most recent six messages provide conversational continuity, while prior assistant answers explicitly carry zero evidentiary weight.
- Session summaries are fetched in one database query and are no longer truncated to an arbitrary recent-session limit.

## [0.1.5] - 2026-08-17

### Added

- Online evidence now retains its canonical discovery record and can be imported directly from the right evidence pane.
- Evidence import reports newly created records, merged duplicates, and queued open-full-text processing.

### Fixed

- The evidence pane remains fixed while message history and long evidence content scroll independently.

## [0.1.4] - 2026-08-17

### Added

- Three evidence-chat scopes: local only, local-first with online supplementation, and online research.
- Source-aware academic discovery filters, per-provider status reporting, and a Google Scholar handoff.
- Direct import of canonical discovery records, including PubMed and arXiv records without a DOI.
- Optional NCBI and OpenAlex API-key storage in the native credential store.

### Changed

- PubMed discovery now fetches structured abstracts with EFetch, while OpenAlex inverted abstracts are restored.
- Search results merge identifiers and richer metadata across Crossref, OpenAlex, arXiv, and PubMed.
- Imported online records queue metadata embedding and lawful open-PDF processing when available.

### Fixed

- Provider failures are visible and no longer discard successful results from other sources.
- Duplicate imports now fill missing metadata without creating duplicate provenance rows.
- Evidence chat now keeps the right evidence pane fixed while messages and long evidence scroll independently.

## [0.1.3] - 2026-08-17

### Added

- A research-planning answer contract covering literature synthesis, local-library knowledge, feasible work, falsifiable hypotheses, and evidence boundaries.
- Explicit evidence-level labels for title/abstract records and full-text excerpts.
- Prompt regression tests preventing literature resources from being misrepresented as user-owned local capabilities.

### Changed

- Increased the default evidence set from 10 to 15 records and bounded each excerpt so every selected record reaches the model.
- Research directions must now state their required data, method, observable, decision criterion, and expected contribution.

### Fixed

- Unsupported claims that datasets, instruments, code, or model-running capabilities mentioned in papers are available locally to the user.

## [0.1.2] - 2026-08-17

### Added

- GitHub Flavored Markdown rendering for evidence-chat answers, including lists, tables, links, quotes, and code blocks.
- A browser regression check for assistant Markdown and citation-location interactions.

### Fixed

- Raw Markdown markers such as `**` appearing in assistant answers.
- Ambiguous `p.?` citation labels for title and abstract evidence; metadata now shows `题录/摘要`, while full-text evidence shows its available section and page range.

## [0.1.1] - 2026-08-17

### Added

- Incremental title and abstract embeddings so metadata-only imports can participate in evidence chat.
- Clickable PDF, parsed Markdown, and vector status controls with an in-app document viewer.
- Explicit assistant responses for empty libraries and visible provider or retrieval errors.

### Fixed

- Silent chat failures when the vector index was empty or model credentials were unavailable.
- Misaligned pipeline icons and labels in the literature table.
- Automatic retry of full-text and embedding jobs after required settings are saved.

## [0.1.0] - 2026-08-17

### Added

- Standalone and read-only Zotero mirror libraries.
- DOI metadata import and lawful open-access PDF acquisition.
- MinerU parsing with a PyMuPDF fallback.
- MiniMax embeddings and LanceDB hybrid retrieval.
- DeepSeek evidence-grounded answers with page-level citations.
- Crossref, OpenAlex, arXiv, and PubMed discovery.
- CSL-JSON, BibTeX, RIS, DOI, and Markdown exports.
- FastAPI, CLI, MCP, React, and Tauri interfaces sharing one local data store.
- Windows NSIS and MSI packaging.
