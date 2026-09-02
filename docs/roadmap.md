# Roadmap

The roadmap describes intent, not a delivery promise. Issues and milestones become authoritative after the
repository is published.

## 0.1 alpha hardening

- Clean-room Windows installer test without Python, Node, Rust, WSL, or gbrain.
- Backup, restore, and index rebuild UI.
- Fixed public retrieval and citation evaluation set.
- Structured diagnostics that exclude private content and personal paths.
- Signed release manifests and reproducible dependency license inventory.

## 0.2 library workflows

- Item detail editing for standalone libraries, notes, reading state, and trash restore.
- Duplicate candidate review and deterministic merge audit.
- Collection and tag management in the desktop interface.
- More citation formats and CSL style snapshots.

## 0.3 reading and retrieval

- PDF.js reader with citation-to-page navigation and evidence highlighting.
- Rebuildable local embedding providers and configurable reranking.
- Retrieval evaluation dashboard with Recall@k, MRR, nDCG, and citation support.
- User-controlled local, online, and combined research modes.
- Iterative evidence research, verification, cancellation, and recovery as specified in the
  [research orchestrator implementation plan](research-orchestrator-plan.md).

## Later

- Signed updater and independently versioned document runtimes.
- macOS/Linux feasibility review.
- Optional plugin interfaces after storage and security contracts stabilize.

Out of scope: shadow-library integration, automatic paywall bypass, Zotero cloud replacement, and silent
bidirectional writes to a Zotero library.
