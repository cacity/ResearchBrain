from __future__ import annotations

import json

from sqlalchemy import select

from researchbrain.db.base import Database
from researchbrain.db.migrations import upgrade_schema
from researchbrain.db.models import DocumentArtifact, DocumentChunk, Item, Job, Library
from researchbrain.legacy.gbrain import GbrainMigrator, GbrainSnapshot


class RecordingIndex:
    def __init__(self):
        self.artifact_ids = []
        self.records = []

    def bulk_upsert(self, artifact_ids, records):
        self.artifact_ids.extend(artifact_ids)
        self.records.extend(records)


def _write_snapshot(directory):
    directory.mkdir()
    vector = json.dumps([0.001] * 1536)
    pages = [
        {
            "id": 1,
            "slug": "old",
            "type": "journalArticle",
            "title": "Shared paper",
            "compiled_truth": "# Shared paper\n\n## Abstract\n\nShort.",
            "frontmatter": {"doi": "10.1000/shared", "year": "2024", "zotero-key": "KEY1"},
            "updated_at": "2026-01-01T00:00:00Z",
        },
        {
            "id": 2,
            "slug": "current",
            "type": "journalArticle",
            "title": "Shared paper",
            "compiled_truth": "# Shared paper\n\n## Abstract\n\nA longer abstract and body.",
            "frontmatter": {
                "doi": "https://doi.org/10.1000/shared",
                "year": "2024",
                "zotero-key": "KEY1",
                "publication-title": "Example Journal",
            },
            "updated_at": "2026-02-01T00:00:00Z",
        },
        {
            "id": 3,
            "slug": "needs-embedding",
            "type": "thesis",
            "title": "Second work",
            "compiled_truth": "# Second work\n\nBody without a compatible vector.",
            "frontmatter": {"year": "2023", "zotero-key": "KEY2"},
            "updated_at": "2026-02-01T00:00:00Z",
        },
        {
            "id": 4,
            "slug": "concept",
            "type": "concept",
            "title": "Not literature",
            "compiled_truth": "Concept",
            "frontmatter": {},
        },
        {
            "id": 5,
            "slug": "no-year-copy-1",
            "type": "journalArticle",
            "title": "No year duplicate",
            "compiled_truth": "# No year duplicate\n\nSame body.",
            "frontmatter": {},
        },
        {
            "id": 6,
            "slug": "no-year-copy-2",
            "type": "journalArticle",
            "title": "No year duplicate",
            "compiled_truth": "# No year duplicate\n\nSame body.",
            "frontmatter": {},
        },
        {
            "id": 7,
            "slug": "missing-year-version",
            "type": "journalArticle",
            "title": "Same title with one known year",
            "compiled_truth": "# Older body without year.",
            "frontmatter": {},
        },
        {
            "id": 8,
            "slug": "known-year-version",
            "type": "journalArticle",
            "title": "Same title with one known year",
            "compiled_truth": "# Newer body with year.",
            "frontmatter": {"year": "2025"},
        },
    ]
    chunks = [
        {
            "id": 20,
            "page_id": 2,
            "chunk_index": 0,
            "chunk_text": pages[1]["compiled_truth"],
            "chunk_source": "compiled_truth",
            "embedding": vector,
            "model": "embo-01",
        },
        {
            "id": 30,
            "page_id": 3,
            "chunk_index": 0,
            "chunk_text": pages[2]["compiled_truth"],
            "chunk_source": "compiled_truth",
            "embedding": vector,
            "model": "text-embedding-3-large",
        },
    ]
    (directory / "pages.jsonl").write_text(
        "".join(json.dumps(value) + "\n" for value in pages),
        encoding="utf-8",
    )
    (directory / "chunks.jsonl").write_text(
        "".join(json.dumps(value) + "\n" for value in chunks),
        encoding="utf-8",
    )


def test_gbrain_snapshot_plan_and_migration(settings, tmp_path):
    settings.ensure_directories()
    snapshot_dir = tmp_path / "snapshot"
    _write_snapshot(snapshot_dir)
    snapshot = GbrainSnapshot(snapshot_dir)
    plan = snapshot.plan()
    assert plan.canonical_items == 4
    assert plan.duplicate_pages == 3
    assert plan.reusable_vector_items == 1
    assert plan.reembed_items == 3
    assert plan.skipped_non_literature == 1

    database = Database(settings.database_url)
    upgrade_schema(settings)
    with database.session() as session:
        session.add(Library(name="我的文献库", mode="standalone"))
    index = RecordingIndex()
    result = GbrainMigrator(database, settings.data_dir, index, snapshot).migrate(
        "我的文献库",
        backup=False,
    )

    assert result.created_items == 4
    assert result.created_artifacts == 4
    assert result.reused_vectors == 1
    assert result.queued_reembed_items == 3
    assert len(index.records) == 1
    with database.session() as session:
        items = list(session.scalars(select(Item).order_by(Item.title)))
        assert len(items) == 4
        assert {item.source_key for item in items} == {"KEY1", "KEY2", None}
        assert session.query(DocumentArtifact).count() == 4
        assert session.query(DocumentChunk).count() == 1
        jobs = list(session.scalars(select(Job)))
        assert {job.job_type for job in jobs} == {"embed_document", "embed_metadata"}
