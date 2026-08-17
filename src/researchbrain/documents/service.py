from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import and_, or_, select

from researchbrain.db.base import Database
from researchbrain.db.models import Attachment, DocumentArtifact
from researchbrain.documents.parsers import Parser


@dataclass(frozen=True)
class DocumentResult:
    artifact_id: str
    parser_name: str
    parser_version: str
    page_count: int
    reused: bool


class DocumentPipeline:
    def __init__(self, database: Database, data_dir: Path, parser: Parser):
        self.database = database
        self.data_dir = data_dir
        self.parser = parser

    async def process(self, attachment_id: str) -> DocumentResult:
        with self.database.session() as session:
            attachment = session.get(Attachment, attachment_id)
            if not attachment:
                raise ValueError("attachment not found")
            source_sha256 = attachment.sha256 or _hash_file(self._attachment_path(attachment))
            identities = _parser_identities(self.parser)
            identity_filter = or_(
                *[
                    and_(
                        DocumentArtifact.parser_name == name,
                        DocumentArtifact.parser_version == version,
                    )
                    for name, version in identities
                ]
            )
            existing = session.scalar(
                select(DocumentArtifact)
                .where(DocumentArtifact.attachment_id == attachment_id)
                .where(DocumentArtifact.source_sha256 == source_sha256)
                .where(identity_filter)
                .order_by(DocumentArtifact.created_at.desc())
            )
            if existing:
                return DocumentResult(
                    existing.id,
                    existing.parser_name,
                    existing.parser_version,
                    existing.page_count,
                    True,
                )
            input_path = self._attachment_path(attachment)

        parser_directory = f"{self.parser.name}-{self.parser.version}"
        artifact_dir = self.data_dir / "artifacts" / source_sha256 / parser_directory
        parser_output = artifact_dir / "parser-output"
        parsed = await self.parser.parse(input_path, parser_output)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        markdown_path = artifact_dir / "document.md"
        document_path = artifact_dir / "document.json"
        _atomic_write_text(markdown_path, parsed.markdown)
        normalized_json = json.dumps(parsed.document, ensure_ascii=False, indent=2, sort_keys=True)
        _atomic_write_text(document_path, normalized_json)
        content_hash = hashlib.sha256((parsed.markdown + normalized_json).encode("utf-8")).hexdigest()
        page_count = int(parsed.document.get("page_count") or 0)

        with self.database.session() as session:
            artifact = DocumentArtifact(
                attachment_id=attachment_id,
                source_sha256=source_sha256,
                parser_name=parsed.parser_name,
                parser_version=parsed.parser_version,
                schema_version=str(parsed.document.get("schema_version") or "1"),
                markdown_path=str(markdown_path.relative_to(self.data_dir)),
                document_json_path=str(document_path.relative_to(self.data_dir)),
                content_hash=content_hash,
                page_count=page_count,
                status="ready",
            )
            session.add(artifact)
            session.flush()
            return DocumentResult(artifact.id, parsed.parser_name, parsed.parser_version, page_count, False)

    def _attachment_path(self, attachment: Attachment) -> Path:
        path = Path(attachment.object_path)
        return path if path.is_absolute() else self.data_dir / path


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write_text(path: Path, value: str) -> None:
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)


def _parser_identities(parser: Parser) -> list[tuple[str, str]]:
    primary = getattr(parser, "primary", None)
    fallback = getattr(parser, "fallback", None)
    if primary and fallback:
        return [(primary.name, primary.version), (fallback.name, fallback.version)]
    return [(parser.name, parser.version)]
