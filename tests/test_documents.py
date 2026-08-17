import json
from pathlib import Path

import pymupdf
import pytest

from researchbrain.db.base import Database
from researchbrain.db.models import Attachment, DocumentArtifact
from researchbrain.documents.parsers import (
    FallbackParser,
    ParsedDocument,
    ParserError,
    PyMuPDFParser,
    normalize_document,
)
from researchbrain.documents.service import DocumentPipeline
from researchbrain.domain import LibraryMode, ReferenceRecord
from researchbrain.library.repository import LibraryRepository


class FixtureParser:
    name = "fixture"
    version = "1.0"

    async def parse(self, input_path: Path, output_dir: Path) -> ParsedDocument:
        markdown = "# Introduction\n\n<!-- page:1 -->\n\nEvidence text."
        document = normalize_document(
            markdown,
            [{"type": "text", "text": "Evidence text.", "page_idx": 0}],
            self.name,
            self.version,
        )
        return ParsedDocument(markdown, document, self.name, self.version)


class FailingParser:
    name = "mineru"
    version = "3.x"

    async def parse(self, input_path: Path, output_dir: Path) -> ParsedDocument:
        raise ParserError("mineru_unavailable", "fixture")


def test_normalize_document_preserves_page_and_figure():
    document = normalize_document(
        "# Results",
        [
            {"type": "text", "text": "Finding", "page_idx": 1},
            {"type": "image", "text": "Figure 1", "page_idx": 2, "img_path": "figures/1.jpg"},
        ],
        "mineru",
        "3.x",
    )
    assert document["page_count"] == 3
    assert document["blocks"][0]["page"] == 2
    assert document["figures"][0]["asset_path"] == "figures/1.jpg"


@pytest.mark.asyncio
async def test_pymupdf_parser_extracts_pdf_text(tmp_path):
    pdf_path = tmp_path / "fixture.pdf"
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_text((72, 72), "ResearchBrain parser fixture")
        pdf.save(pdf_path)

    parsed = await PyMuPDFParser(version=pymupdf.version[0]).parse(pdf_path, tmp_path / "output")
    assert "ResearchBrain parser fixture" in parsed.markdown
    assert parsed.document["page_count"] == 1
    assert parsed.document["blocks"][0]["page"] == 1


@pytest.mark.asyncio
async def test_document_pipeline_writes_normalized_artifact(settings):
    settings.ensure_directories()
    database = Database(settings.database_url)
    database.create_schema()
    pdf_path = settings.data_dir / "library" / "objects" / "fixture.pdf"
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_path.write_bytes(b"%PDF-1.7\nfixture")
    with database.session() as session:
        library = LibraryRepository(session).create_library("Test", LibraryMode.STANDALONE)
        item, _ = LibraryRepository(session).add_reference(
            library.id,
            ReferenceRecord(title="Parsed Paper", identifiers={"doi": "10.1000/parsed"}),
            "fixture",
        )
        attachment = Attachment(
            item_id=item.id,
            sha256="a" * 64,
            logical_name="paper.pdf",
            object_path=str(pdf_path.relative_to(settings.data_dir)),
            mime="application/pdf",
            status="stored",
        )
        session.add(attachment)
        session.flush()
        attachment_id = attachment.id

    parser = FallbackParser(FailingParser(), FixtureParser())
    first = await DocumentPipeline(database, settings.data_dir, parser).process(attachment_id)
    second = await DocumentPipeline(database, settings.data_dir, parser).process(attachment_id)
    assert first.reused is False
    assert second.reused is True
    assert first.parser_name == "fixture"
    with database.session() as session:
        artifact = session.query(DocumentArtifact).filter_by(id=first.artifact_id).one()
        document = json.loads((settings.data_dir / artifact.document_json_path).read_text("utf-8"))
        assert document["blocks"][0]["page"] == 1
        assert artifact.page_count == 1
    database.engine.dispose()
