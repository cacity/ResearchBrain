# Licensing and Third-Party Components

ResearchBrain source is distributed under `AGPL-3.0-only`. This choice is required for the current binary
distribution because the PyMuPDF fallback is linked into the packaged Python sidecar.

## Components that need explicit review

| Component              | Use                                 | License note                                                                                      |
| ---------------------- | ----------------------------------- | ------------------------------------------------------------------------------------------------- |
| PyMuPDF                | Built-in PDF text fallback          | GNU AGPL v3 or an Artifex commercial license.                                                     |
| MinerU                 | Optional external parser executable | MinerU Open Source License, based on Apache 2.0 with additional commercial and attribution terms. |
| Tauri                  | Desktop host                        | Apache-2.0 OR MIT.                                                                                |
| React                  | User interface                      | MIT.                                                                                              |
| LanceDB / Apache Arrow | Retrieval and columnar data         | Apache-2.0.                                                                                       |

PyMuPDF's publisher states that open-source use is under AGPL and commercial licensing is available. MinerU
is not bundled, but users and distributors still need to follow the license shipped by the MinerU version they
install. MinerU's terms changed in 2026, so do not rely on an old summary when making a release.

Authoritative references:

- [PyMuPDF licensing](https://pymupdf.io/pymupdf)
- [MinerU license](https://github.com/opendatalab/MinerU/blob/master/LICENSE.md)
- [Tauri licenses](https://github.com/tauri-apps/tauri)
- [React license](https://github.com/facebook/react/blob/main/LICENSE)
- [LanceDB license](https://github.com/lancedb/lancedb/blob/main/LICENSE)

Before each public binary release, generate a complete dependency license inventory from the locked Python,
npm, and Cargo dependency graphs. This document is a project-level guide, not legal advice or a substitute
for the license text included by each dependency.

## Permissive-license option

Do not relicense the current packaged application as MIT or Apache-2.0 while bundling PyMuPDF. A future
permissive edition would need a permissively licensed fallback parser or a separately distributed parser
boundary, followed by a fresh dependency and legal review.
