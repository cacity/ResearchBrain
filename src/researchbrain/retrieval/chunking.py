from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Chunk:
    id: str
    ordinal: int
    text: str
    section: str
    page_start: int | None
    page_end: int | None
    block_ids: list[str]
    content_hash: str


def chunk_document(
    artifact_hash: str,
    document: dict[str, Any],
    max_chars: int = 2400,
    overlap_chars: int = 240,
) -> list[Chunk]:
    units = []
    section = ""
    for block in document.get("blocks") or []:
        if not isinstance(block, dict):
            continue
        text = str(block.get("text") or "").strip()
        if not text:
            continue
        if block.get("type") in {"title", "heading"} or re.match(r"^#{1,6}\s+", text):
            section = re.sub(r"^#{1,6}\s+", "", text).strip()
        for part in _split_text(text, max_chars, overlap_chars):
            units.append(
                {
                    "text": part,
                    "section": section,
                    "page": _optional_int(block.get("page")),
                    "block_id": str(block.get("id") or ""),
                }
            )

    chunks = []
    current: list[dict[str, Any]] = []
    current_length = 0
    for unit in units:
        projected = current_length + len(unit["text"]) + (2 if current else 0)
        if current and projected > max_chars:
            chunks.append(_make_chunk(artifact_hash, len(chunks), current))
            overlap = _tail_overlap(current, overlap_chars)
            current = overlap
            current_length = sum(len(value["text"]) for value in current) + max(0, len(current) - 1) * 2
        current.append(unit)
        current_length += len(unit["text"]) + (2 if len(current) > 1 else 0)
    if current:
        chunks.append(_make_chunk(artifact_hash, len(chunks), current))
    return chunks


def _make_chunk(artifact_hash: str, ordinal: int, units: list[dict[str, Any]]) -> Chunk:
    text = "\n\n".join(unit["text"] for unit in units)
    pages = [unit["page"] for unit in units if unit["page"] is not None]
    block_ids = list(dict.fromkeys(unit["block_id"] for unit in units if unit["block_id"]))
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    chunk_id = hashlib.sha256(f"{artifact_hash}:{ordinal}:{content_hash}".encode()).hexdigest()
    return Chunk(
        id=chunk_id,
        ordinal=ordinal,
        text=text,
        section=next((unit["section"] for unit in reversed(units) if unit["section"]), ""),
        page_start=min(pages) if pages else None,
        page_end=max(pages) if pages else None,
        block_ids=block_ids,
        content_hash=content_hash,
    )


def _split_text(text: str, max_chars: int, overlap_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    parts = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        if end < len(text):
            boundary = max(
                text.rfind("。", start, end),
                text.rfind(". ", start, end),
                text.rfind("\n", start, end),
            )
            if boundary > start + max_chars // 2:
                end = boundary + 1
        parts.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(start + 1, end - overlap_chars)
    return [part for part in parts if part]


def _tail_overlap(units: list[dict[str, Any]], overlap_chars: int) -> list[dict[str, Any]]:
    selected = []
    length = 0
    for unit in reversed(units):
        if selected and length + len(unit["text"]) > overlap_chars:
            break
        selected.append(unit)
        length += len(unit["text"])
    return list(reversed(selected))


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
