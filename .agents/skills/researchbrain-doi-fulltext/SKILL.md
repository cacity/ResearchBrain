---
name: researchbrain-doi-fulltext
description: Normalize and batch-import DOIs into ResearchBrain, resolve bibliographic metadata, find lawful open-access PDFs, store them locally by content hash, and track parsing and embedding. Use when Codex receives one or more DOIs, needs to obtain available PDFs, must avoid duplicate literature, or must report whether each DOI has metadata, PDF, parsed Markdown, and vectors.
---

# ResearchBrain DOI Full Text

Use the `mcp__researchbrain__*` tools. Automated retrieval is limited to lawful open sources.

## Workflow

1. Call `get_research_context` and resolve the destination library.
2. Normalize obvious DOI URL prefixes and remove duplicate input lines. Preserve the original order for reporting.
3. State the destination and DOI count, then call `import_dois`.
4. Report rejected inputs from `input_errors`; do not silently discard them.
5. Call `list_jobs` and follow the batch through `resolve_metadata`, `resolve_fulltext`, `parse_document`, `embed_document`, and `embed_metadata`.
6. Read item IDs from completed metadata-job results and call `item_status` for final per-paper state.
7. Return a compact table with DOI, item ID, metadata, PDF, parsed text, full-text vectors, and error or limitation.

## Rules

- A completed metadata job does not mean a PDF was found.
- `no_oa_fulltext` means the record remains useful at title/abstract level; report it as no lawful open PDF found.
- Do not use Sci-Hub, bypass paywalls, reuse session cookies, or imply access rights.
- Do not download a duplicate when `item_status` or `queue_fulltext` reports an existing PDF.
- Never claim parsing or vectorization is complete from queue creation alone.

