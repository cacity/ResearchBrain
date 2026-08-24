---
name: researchbrain-pdf-ingest
description: Attach user-selected local PDFs or process ResearchBrain-managed PDFs into deduplicated objects, structured Markdown, document artifacts, chunks, and vectors using MinerU with PyMuPDF fallback. Use when Codex is asked to import a PDF, convert PDF to Markdown, parse Zotero attachments, resume failed document processing, or verify page-aware full-text indexing.
---

# ResearchBrain PDF Ingest

Use the `mcp__researchbrain__*` tools. A PDF must belong to an existing literature item.

## Workflow

1. Resolve the target item with `get_item`, then call `item_status`.
2. If the user supplied a local PDF path and PDF status is missing, state the exact item and path, then call `attach_local_pdf`.
3. If the PDF should be obtained from its DOI, use `$researchbrain-doi-fulltext` instead.
4. Follow the returned parse job with `list_jobs`. Parsing automatically queues full-text embedding.
5. Inspect terminal `parse_document` and `embed_document` results, then call `item_status` again.
6. Report the parser (`mineru` or `pymupdf`), page count, reuse flag, chunk count, model, and any fallback or failure reason.

## Rules

- Obtain confirmation before attaching a local path that the user did not explicitly provide in the current request.
- Never match a PDF to an item by filename alone. Confirm item ID, DOI, title, or SHA-256 evidence.
- SHA-256 reuse is success, not a duplicate import failure.
- MinerU failure may legitimately fall back to PyMuPDF; report which parser produced the stored artifact.
- Do not claim Markdown or vectors exist until the corresponding job is complete.

