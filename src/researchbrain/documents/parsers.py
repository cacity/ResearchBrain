from __future__ import annotations

import asyncio
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


class ParserError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ParsedDocument:
    markdown: str
    document: dict[str, Any]
    parser_name: str
    parser_version: str


class Parser(Protocol):
    name: str
    version: str

    async def parse(self, input_path: Path, output_dir: Path) -> ParsedDocument: ...


class MinerUParser:
    name = "mineru"

    def __init__(
        self,
        executable: str = "mineru",
        backend: str = "pipeline",
        version: str = "3.x",
        timeout_seconds: int = 3600,
    ):
        self.executable = executable
        self.backend = backend
        self.version = version
        self.timeout_seconds = timeout_seconds

    async def parse(self, input_path: Path, output_dir: Path) -> ParsedDocument:
        executable = shutil.which(self.executable)
        if not executable:
            raise ParserError("mineru_unavailable", f"MinerU executable not found: {self.executable}")
        output_dir.mkdir(parents=True, exist_ok=True)
        process = await asyncio.create_subprocess_exec(
            executable,
            "-p",
            str(input_path),
            "-o",
            str(output_dir),
            "-b",
            self.backend,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=self.timeout_seconds)
        except TimeoutError as exc:
            process.kill()
            await process.communicate()
            raise ParserError("mineru_timeout", "MinerU parsing timed out") from exc
        if process.returncode != 0:
            detail = (stderr or stdout).decode("utf-8", errors="replace")[-4000:]
            raise ParserError("mineru_failed", detail or f"MinerU exited with {process.returncode}")
        markdown_path = _largest_file(output_dir, "*.md")
        if not markdown_path:
            raise ParserError("mineru_no_markdown", "MinerU produced no Markdown file")
        markdown = markdown_path.read_text(encoding="utf-8", errors="replace")
        content_path = _largest_file(output_dir, "*content_list.json")
        content = _read_json(content_path) if content_path else []
        document = normalize_document(markdown, content, self.name, self.version)
        document["source_artifacts"] = {
            "mineru_markdown": str(markdown_path.relative_to(output_dir)),
            "content_list": str(content_path.relative_to(output_dir)) if content_path else None,
        }
        return ParsedDocument(markdown, document, self.name, self.version)


class PyMuPDFParser:
    name = "pymupdf"

    def __init__(self, version: str = "1.x"):
        self.version = version

    async def parse(self, input_path: Path, output_dir: Path) -> ParsedDocument:
        try:
            import pymupdf
        except ImportError as exc:
            raise ParserError("pymupdf_unavailable", "PyMuPDF is not installed") from exc

        def extract() -> tuple[str, list[dict[str, Any]], int]:
            pages = []
            chunks = []
            with pymupdf.open(input_path) as pdf:
                for page_index, page in enumerate(pdf):
                    text = page.get_text("text").strip()
                    chunks.append(f"<!-- page:{page_index + 1} -->\n\n{text}")
                    pages.append({"type": "text", "text": text, "page_idx": page_index})
                return "\n\n".join(chunks), pages, len(pdf)

        markdown, content, page_count = await asyncio.to_thread(extract)
        document = normalize_document(markdown, content, self.name, self.version)
        document["page_count"] = page_count
        return ParsedDocument(markdown, document, self.name, self.version)


class FallbackParser:
    name = "fallback"
    version = "1"

    def __init__(self, primary: Parser, fallback: Parser):
        self.primary = primary
        self.fallback = fallback

    async def parse(self, input_path: Path, output_dir: Path) -> ParsedDocument:
        try:
            return await self.primary.parse(input_path, output_dir / self.primary.name)
        except ParserError as primary_error:
            parsed = await self.fallback.parse(input_path, output_dir / self.fallback.name)
            parsed.document["fallback_reason"] = {
                "code": primary_error.code,
                "message": str(primary_error),
            }
            return parsed


def normalize_document(
    markdown: str,
    content: Any,
    parser_name: str,
    parser_version: str,
) -> dict[str, Any]:
    raw_blocks = content if isinstance(content, list) else []
    blocks = []
    figures = []
    tables = []
    max_page = -1
    for position, raw_block in enumerate(raw_blocks):
        if not isinstance(raw_block, dict):
            continue
        block_type = str(raw_block.get("type") or raw_block.get("block_type") or "text")
        page_index = _page_index(raw_block)
        max_page = max(max_page, page_index)
        text = str(raw_block.get("text") or raw_block.get("content") or "").strip()
        block = {
            "id": f"b{position + 1}",
            "type": block_type,
            "page": page_index + 1 if page_index >= 0 else None,
            "text": text,
        }
        image_path = raw_block.get("img_path") or raw_block.get("image_path")
        if image_path:
            block["asset_path"] = str(image_path)
        blocks.append(block)
        if block_type in {"image", "figure"}:
            figures.append(block)
        if block_type == "table":
            tables.append(block)
    if not blocks:
        blocks = _markdown_blocks(markdown)
    headings = [
        {"level": len(match.group(1)), "title": match.group(2).strip()}
        for match in re.finditer(r"^(#{1,6})\s+(.+)$", markdown, flags=re.MULTILINE)
    ]
    return {
        "schema_version": "1",
        "parser": {"name": parser_name, "version": parser_version},
        "page_count": max_page + 1 if max_page >= 0 else _markdown_page_count(markdown),
        "headings": headings,
        "blocks": blocks,
        "figures": figures,
        "tables": tables,
    }


def _markdown_blocks(markdown: str) -> list[dict[str, Any]]:
    blocks = []
    page = None
    for position, chunk in enumerate(re.split(r"\n\s*\n", markdown)):
        chunk = chunk.strip()
        if not chunk:
            continue
        page_match = re.fullmatch(r"<!--\s*page:(\d+)\s*-->", chunk)
        if page_match:
            page = int(page_match.group(1))
            continue
        blocks.append({"id": f"b{position + 1}", "type": "text", "page": page, "text": chunk})
    return blocks


def _markdown_page_count(markdown: str) -> int:
    pages = [int(value) for value in re.findall(r"<!--\s*page:(\d+)\s*-->", markdown)]
    return max(pages, default=0)


def _page_index(block: dict[str, Any]) -> int:
    value = block.get("page_idx", block.get("page_index", -1))
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1


def _largest_file(root: Path, pattern: str) -> Path | None:
    files = [path for path in root.rglob(pattern) if path.is_file()]
    return max(files, key=lambda path: path.stat().st_size, default=None)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ParserError("mineru_invalid_json", f"Cannot read MinerU JSON: {exc}") from exc
