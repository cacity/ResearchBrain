from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
import subprocess
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select

from researchbrain.db.base import Database
from researchbrain.db.models import (
    Attachment,
    DocumentArtifact,
    DocumentChunk,
    Identifier,
    Item,
    Job,
    Library,
)
from researchbrain.domain import CreatorInput, JobStatus, JobType, ReferenceRecord, normalize_doi
from researchbrain.library.repository import LibraryRepository
from researchbrain.retrieval.index import LanceIndex

LITERATURE_TYPES = {
    "book",
    "bookSection",
    "conferencePaper",
    "dataset",
    "encyclopediaArticle",
    "journalArticle",
    "paper",
    "preprint",
    "standard",
    "thesis",
    "webpage",
}

PAGE_EXPORT_SQL = """
select jsonb_build_object(
  'id', id,
  'slug', slug,
  'type', type,
  'title', title,
  'compiled_truth', compiled_truth,
  'timeline', timeline,
  'frontmatter', frontmatter,
  'content_hash', content_hash,
  'created_at', created_at,
  'updated_at', updated_at
)::text
from pages
order by id
""".strip()

CHUNK_EXPORT_SQL = """
select jsonb_build_object(
  'id', id,
  'page_id', page_id,
  'chunk_index', chunk_index,
  'chunk_text', chunk_text,
  'chunk_source', chunk_source,
  'embedding', case when embedding is null then null else embedding::text end,
  'model', model,
  'token_count', token_count,
  'embedded_at', embedded_at
)::text
from content_chunks
order by page_id, chunk_index
""".strip()


@dataclass(frozen=True)
class SnapshotManifest:
    created_at: str
    source: str
    pages: int
    chunks: int
    pages_sha256: str
    chunks_sha256: str


@dataclass(frozen=True)
class MigrationPlan:
    source_pages: int
    source_chunks: int
    eligible_pages: int
    canonical_items: int
    duplicate_pages: int
    reusable_vector_items: int
    reusable_vectors: int
    reembed_items: int
    skipped_non_literature: int


@dataclass(frozen=True)
class MigrationResult:
    library_id: str
    library_name: str
    created_items: int
    updated_items: int
    created_artifacts: int
    reused_vector_items: int
    reused_vectors: int
    queued_reembed_items: int
    skipped_items: int
    backup_dir: str


def export_gbrain_snapshot(
    snapshot_dir: Path,
    *,
    distro: str = "Ubuntu-20.04",
    psql: str = "/opt/pg16/bin/psql",
    database_url: str = "postgresql://postgres:postgres@127.0.0.1:5432/postgres",
) -> SnapshotManifest:
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    pages_path = snapshot_dir / "pages.jsonl"
    chunks_path = snapshot_dir / "chunks.jsonl"
    _run_psql_export(pages_path, PAGE_EXPORT_SQL, distro, psql, database_url)
    _run_psql_export(chunks_path, CHUNK_EXPORT_SQL, distro, psql, database_url)
    manifest = SnapshotManifest(
        created_at=datetime.now(UTC).isoformat(),
        source=f"wsl:{distro}:{database_url.rsplit('@', 1)[-1]}",
        pages=_line_count(pages_path),
        chunks=_line_count(chunks_path),
        pages_sha256=_file_sha256(pages_path),
        chunks_sha256=_file_sha256(chunks_path),
    )
    (snapshot_dir / "manifest.json").write_text(
        json.dumps(asdict(manifest), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def _run_psql_export(
    output_path: Path,
    sql: str,
    distro: str,
    psql: str,
    database_url: str,
) -> None:
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    command = [
        "wsl.exe",
        "-d",
        distro,
        "--",
        psql,
        database_url,
        "-v",
        "ON_ERROR_STOP=1",
        "-At",
        "-c",
        sql,
    ]
    with temporary.open("wb") as stream:
        result = subprocess.run(command, stdout=stream, stderr=subprocess.PIPE, check=False)
    if result.returncode:
        temporary.unlink(missing_ok=True)
        message = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"gbrain snapshot export failed: {message}")
    temporary.replace(output_path)


class GbrainSnapshot:
    def __init__(self, directory: Path):
        self.directory = directory
        self.pages = _read_jsonl(directory / "pages.jsonl")
        self.chunks = _read_jsonl(directory / "chunks.jsonl")
        self.chunks_by_page: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for chunk in self.chunks:
            self.chunks_by_page[int(chunk["page_id"])].append(chunk)

    def plan(self) -> MigrationPlan:
        groups, skipped = self._groups()
        reusable_items = reusable_vectors = reembed_items = 0
        for group in groups:
            page = self._choose_page(group)
            chunks = self._compiled_chunks(int(page["id"]))
            if chunks and all(_compatible_chunk(chunk) for chunk in chunks):
                reusable_items += 1
                reusable_vectors += len(chunks)
            elif str(page.get("compiled_truth") or "").strip():
                reembed_items += 1
        eligible = sum(len(group) for group in groups)
        return MigrationPlan(
            source_pages=len(self.pages),
            source_chunks=len(self.chunks),
            eligible_pages=eligible,
            canonical_items=len(groups),
            duplicate_pages=eligible - len(groups),
            reusable_vector_items=reusable_items,
            reusable_vectors=reusable_vectors,
            reembed_items=reembed_items,
            skipped_non_literature=skipped,
        )

    def canonical_records(self) -> list[tuple[dict[str, Any], list[dict[str, Any]]]]:
        groups, _ = self._groups()
        return [(self._choose_page(group), group) for group in groups]

    def _groups(self) -> tuple[list[list[dict[str, Any]]], int]:
        eligible = [page for page in self.pages if _is_literature(page)]
        skipped = len(self.pages) - len(eligible)
        parent = list(range(len(eligible)))
        identities: dict[str, int] = {}
        title_years: dict[str, set[int]] = defaultdict(set)
        for page in eligible:
            title = _normalized_title(page)
            frontmatter = page.get("frontmatter") or {}
            year = _year(frontmatter.get("year") or frontmatter.get("s2-year"))
            if title and year:
                title_years[title].add(year)

        def find(index: int) -> int:
            while parent[index] != index:
                parent[index] = parent[parent[index]]
                index = parent[index]
            return index

        def union(left: int, right: int) -> None:
            left_root, right_root = find(left), find(right)
            if left_root != right_root:
                parent[right_root] = left_root

        for index, page in enumerate(eligible):
            for identity in _page_identities(page, title_years):
                previous = identities.get(identity)
                if previous is None:
                    identities[identity] = index
                else:
                    union(index, previous)

        grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for index, page in enumerate(eligible):
            grouped[find(index)].append(page)
        return list(grouped.values()), skipped

    def _choose_page(self, group: list[dict[str, Any]]) -> dict[str, Any]:
        longest = max(len(str(page.get("compiled_truth") or "")) for page in group)
        near_longest = [
            page for page in group if len(str(page.get("compiled_truth") or "")) >= longest * 0.98
        ]
        reusable = [
            page
            for page in near_longest
            if (chunks := self._compiled_chunks(int(page["id"])))
            and all(_compatible_chunk(chunk) for chunk in chunks)
        ]
        candidates = reusable or near_longest
        return max(
            candidates,
            key=lambda page: (
                len(str(page.get("compiled_truth") or "")),
                str(page.get("updated_at") or ""),
            ),
        )

    def _compiled_chunks(self, page_id: int) -> list[dict[str, Any]]:
        return [
            chunk
            for chunk in self.chunks_by_page.get(page_id, [])
            if str(chunk.get("chunk_source") or "") == "compiled_truth"
        ]


class GbrainMigrator:
    def __init__(
        self,
        database: Database,
        data_dir: Path,
        index: LanceIndex,
        snapshot: GbrainSnapshot,
    ):
        self.database = database
        self.data_dir = data_dir
        self.index = index
        self.snapshot = snapshot

    def migrate(self, library_name: str, backup: bool = True) -> MigrationResult:
        backup_dir = backup_researchbrain(self.data_dir) if backup else None
        created = updated = artifacts = reused_items = reused_vectors = queued = skipped = 0
        index_records: list[dict[str, Any]] = []
        index_artifact_ids: list[str] = []
        with self.database.session() as session:
            library = session.scalar(select(Library).where(Library.name == library_name))
            if not library:
                raise ValueError(f"target library not found: {library_name}")
            repository = LibraryRepository(session)
            for page, group in self.snapshot.canonical_records():
                try:
                    item, was_created = self._upsert_item(session, repository, library, page, group)
                    created += int(was_created)
                    updated += int(not was_created)
                    artifact, was_artifact_created = self._upsert_artifact(session, item, page, group)
                    artifacts += int(was_artifact_created)
                    if not artifact:
                        continue
                    chunks = self.snapshot._compiled_chunks(int(page["id"]))
                    if chunks and all(_compatible_chunk(chunk) for chunk in chunks):
                        records = self._replace_chunks(session, item, artifact, chunks)
                        index_records.extend(records)
                        index_artifact_ids.append(artifact.id)
                        reused_items += 1
                        reused_vectors += len(records)
                    else:
                        session.execute(delete(DocumentChunk).where(DocumentChunk.artifact_id == artifact.id))
                        if self._queue_embedding(session, library.id, item.id, artifact.id):
                            queued += 1
                except (TypeError, ValueError, OSError, json.JSONDecodeError):
                    skipped += 1
            self._queue_metadata_embedding(session, library.id)

        if index_records:
            self.index.bulk_upsert(index_artifact_ids, index_records)
        return MigrationResult(
            library_id=library.id,
            library_name=library.name,
            created_items=created,
            updated_items=updated,
            created_artifacts=artifacts,
            reused_vector_items=reused_items,
            reused_vectors=reused_vectors,
            queued_reembed_items=queued,
            skipped_items=skipped,
            backup_dir=str(backup_dir or ""),
        )

    def _upsert_item(
        self,
        session,
        repository: LibraryRepository,
        library: Library,
        page: dict[str, Any],
        group: list[dict[str, Any]],
    ) -> tuple[Item, bool]:
        frontmatter = _merged_frontmatter(group, page)
        record = _reference_record(page, frontmatter)
        source_key = str(frontmatter.get("zotero-key") or "").strip() or None
        existing = None
        if source_key:
            existing = session.scalar(
                select(Item).where(Item.library_id == library.id).where(Item.source_key == source_key)
            )
        if not existing and record.identifiers.get("doi"):
            existing = repository.find_item_by_identifier(library.id, "doi", record.identifiers["doi"])
        if not existing:
            existing = repository.find_item_by_title_year(library.id, record.title, record.year)
        if existing:
            repository._merge_missing_fields(existing, record, "gbrain")
            item, created = existing, False
        else:
            item, created = repository.add_reference(library.id, record, "gbrain", deduplicate=False)
        if source_key and not item.source_key:
            conflict = session.scalar(
                select(Item.id)
                .where(Item.library_id == library.id)
                .where(Item.source_key == source_key)
                .where(Item.id != item.id)
            )
            if not conflict:
                item.source_key = source_key
        _ensure_identifier(session, library.id, item.id, "zotero", source_key)
        return item, created

    def _upsert_artifact(
        self,
        session,
        item: Item,
        page: dict[str, Any],
        group: list[dict[str, Any]],
    ) -> tuple[DocumentArtifact | None, bool]:
        markdown = str(page.get("compiled_truth") or "").strip()
        if not markdown:
            return None, False
        source_hash = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
        existing_artifact = session.scalar(
            select(DocumentArtifact)
            .join(Attachment, Attachment.id == DocumentArtifact.attachment_id)
            .where(Attachment.item_id == item.id)
            .where(DocumentArtifact.source_sha256 == source_hash)
            .where(DocumentArtifact.parser_name == "gbrain")
            .where(DocumentArtifact.parser_version == "0.14.2")
        )
        if existing_artifact:
            return existing_artifact, False
        source_key = f"gbrain:{int(page['id'])}"
        attachment = session.scalar(
            select(Attachment).where(Attachment.item_id == item.id).where(Attachment.source_key == source_key)
        )
        relative_dir = Path("artifacts") / "gbrain" / item.id / source_hash[:12]
        markdown_path = relative_dir / "document.md"
        document_path = relative_dir / "document.json"
        target_dir = self.data_dir / relative_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / markdown_path).write_text(markdown + "\n", encoding="utf-8")
        document = _markdown_document(markdown, page, group)
        (self.data_dir / document_path).write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        if not attachment:
            attachment = Attachment(item_id=item.id, source_key=source_key, logical_name="")
            session.add(attachment)
        attachment.sha256 = source_hash
        attachment.logical_name = f"{page.get('slug') or item.id}.md"
        attachment.object_path = markdown_path.as_posix()
        attachment.mime = "text/markdown"
        attachment.source_url = item.url
        attachment.status = "ready"
        attachment.bytes = len(markdown.encode("utf-8"))
        session.flush()
        artifact = session.scalar(
            select(DocumentArtifact)
            .where(DocumentArtifact.attachment_id == attachment.id)
            .where(DocumentArtifact.source_sha256 == source_hash)
            .where(DocumentArtifact.parser_name == "gbrain")
            .where(DocumentArtifact.parser_version == "0.14.2")
        )
        created = artifact is None
        if not artifact:
            artifact = DocumentArtifact(
                attachment_id=attachment.id,
                source_sha256=source_hash,
                parser_name="gbrain",
                parser_version="0.14.2",
                schema_version="1",
                markdown_path=markdown_path.as_posix(),
                document_json_path=document_path.as_posix(),
                content_hash=source_hash,
                page_count=0,
                status="ready",
            )
            session.add(artifact)
            session.flush()
        return artifact, created

    def _replace_chunks(
        self,
        session,
        item: Item,
        artifact: DocumentArtifact,
        chunks: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        attachment = session.get(Attachment, artifact.attachment_id)
        session.execute(delete(DocumentChunk).where(DocumentChunk.artifact_id == artifact.id))
        records = []
        for ordinal, source in enumerate(chunks):
            text = str(source.get("chunk_text") or "").strip()
            content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
            chunk_id = hashlib.sha256(f"gbrain:{artifact.id}:{ordinal}:{content_hash}".encode()).hexdigest()
            vector = _embedding(source)
            session.add(
                DocumentChunk(
                    id=chunk_id,
                    artifact_id=artifact.id,
                    item_id=item.id,
                    attachment_id=attachment.id,
                    ordinal=ordinal,
                    text=text,
                    section="gbrain compiled truth",
                    page_start=None,
                    page_end=None,
                    block_ids=[],
                    content_hash=content_hash,
                    embedding_provider="minimax",
                    embedding_model="embo-01",
                    embedding_dimensions=1536,
                    index_version="v1",
                    index_status="ready",
                )
            )
            records.append(
                {
                    "chunk_id": chunk_id,
                    "vector": vector,
                    "library_id": item.library_id,
                    "item_id": item.id,
                    "artifact_id": artifact.id,
                    "attachment_id": attachment.id,
                    "title": item.title,
                    "year": item.year,
                    "text": text,
                    "section": "gbrain compiled truth",
                    "page_start": None,
                    "page_end": None,
                    "content_hash": content_hash,
                    "embedding_provider": "minimax",
                    "embedding_model": "embo-01",
                    "index_version": "v1",
                }
            )
        return records

    @staticmethod
    def _queue_embedding(session, library_id: str, item_id: str, artifact_id: str) -> bool:
        digest = hashlib.sha256(f"embed:{artifact_id}".encode()).hexdigest()
        existing = session.scalar(select(Job).where(Job.idempotency_key == digest))
        if existing:
            if existing.status in {
                JobStatus.FAILED.value,
                JobStatus.REVIEW_REQUIRED.value,
                JobStatus.CANCELED.value,
            }:
                existing.status = JobStatus.QUEUED.value
                existing.error_code = ""
                existing.error_message = ""
            return False
        session.add(
            Job(
                job_type=JobType.EMBED_DOCUMENT.value,
                status=JobStatus.QUEUED.value,
                idempotency_key=digest,
                payload={"library_id": library_id, "item_id": item_id, "artifact_id": artifact_id},
            )
        )
        return True

    @staticmethod
    def _queue_metadata_embedding(session, library_id: str) -> None:
        active = session.scalar(
            select(Job.id)
            .where(Job.job_type == JobType.EMBED_METADATA.value)
            .where(Job.payload["library_id"].as_string() == library_id)
            .where(Job.status.in_([JobStatus.QUEUED.value, JobStatus.RUNNING.value]))
        )
        if not active:
            digest = hashlib.sha256(f"embed-metadata:{library_id}:gbrain".encode()).hexdigest()
            existing = session.scalar(select(Job).where(Job.idempotency_key == digest))
            if not existing:
                session.add(
                    Job(
                        job_type=JobType.EMBED_METADATA.value,
                        status=JobStatus.QUEUED.value,
                        idempotency_key=digest,
                        payload={"library_id": library_id},
                    )
                )


def backup_researchbrain(data_dir: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = data_dir / "backups" / f"before-gbrain-{stamp}"
    target.mkdir(parents=True, exist_ok=False)
    source_database = data_dir / "data" / "library.sqlite"
    if source_database.exists():
        with sqlite3.connect(source_database) as source, sqlite3.connect(target / "library.sqlite") as dest:
            source.backup(dest)
    source_index = data_dir / "data" / "lancedb"
    if source_index.exists():
        shutil.copytree(source_index, target / "lancedb")
    (target / "manifest.json").write_text(
        json.dumps(
            {"created_at": datetime.now(UTC).isoformat(), "reason": "before gbrain migration"},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return target


def _reference_record(page: dict[str, Any], frontmatter: dict[str, Any]) -> ReferenceRecord:
    text = str(page.get("compiled_truth") or "")
    identifiers = {}
    if doi := _normalized_doi(frontmatter.get("doi")):
        identifiers["doi"] = doi
    if value := str(frontmatter.get("s2-id") or "").strip():
        identifiers["semantic-scholar"] = value
    authors = frontmatter.get("authors") or []
    if isinstance(authors, str):
        authors = [value.strip() for value in re.split(r";|\band\b", authors) if value.strip()]
    creators = [CreatorInput(literal=str(value)) for value in authors if str(value).strip()]
    return ReferenceRecord(
        type=_item_type(str(page.get("type") or "")),
        title=str(page.get("title") or frontmatter.get("title") or "Untitled"),
        abstract=_extract_abstract(text),
        year=_year(frontmatter.get("year") or frontmatter.get("s2-year")),
        container_title=str(
            frontmatter.get("publication-title")
            or frontmatter.get("publication")
            or frontmatter.get("journal")
            or ""
        ),
        volume=str(frontmatter.get("volume") or ""),
        issue=str(frontmatter.get("issue") or frontmatter.get("number") or ""),
        pages=str(frontmatter.get("pages") or ""),
        publisher=str(frontmatter.get("publisher") or ""),
        language=str(frontmatter.get("language") or ""),
        url=str(frontmatter.get("url") or ""),
        identifiers=identifiers,
        creators=creators,
        raw={
            "gbrain": {
                "page_id": page.get("id"),
                "slug": page.get("slug"),
                "frontmatter": frontmatter,
            }
        },
    )


def _markdown_document(
    markdown: str,
    page: dict[str, Any],
    group: list[dict[str, Any]],
) -> dict[str, Any]:
    blocks = []
    for index, value in enumerate(re.split(r"\n\s*\n", markdown)):
        text = value.strip()
        if not text:
            continue
        block_type = "heading" if re.match(r"^#{1,6}\s+", text) else "paragraph"
        blocks.append({"id": f"gbrain-{index}", "type": block_type, "text": text, "page": None})
    return {
        "schema_version": "1",
        "source": "gbrain",
        "source_page_id": page.get("id"),
        "merged_page_ids": [value.get("id") for value in group],
        "blocks": blocks,
    }


def _merged_frontmatter(group: list[dict[str, Any]], preferred: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for page in sorted(group, key=lambda value: len(str(value.get("compiled_truth") or ""))):
        value = page.get("frontmatter") or {}
        if isinstance(value, dict):
            merged.update({key: item for key, item in value.items() if _present(item)})
    value = preferred.get("frontmatter") or {}
    if isinstance(value, dict):
        merged.update({key: item for key, item in value.items() if _present(item)})
    return merged


def _present(value: Any) -> bool:
    return value is not None and value != "" and value != []


def _page_identities(page: dict[str, Any], title_years: dict[str, set[int]]) -> list[str]:
    frontmatter = page.get("frontmatter") or {}
    identities = []
    if key := str(frontmatter.get("zotero-key") or "").strip().upper():
        identities.append(f"zotero:{key}")
    if doi := _normalized_doi(frontmatter.get("doi")):
        identities.append(f"doi:{doi}")
    title = _normalized_title(page)
    year = _year(frontmatter.get("year") or frontmatter.get("s2-year"))
    if title and year:
        identities.append(f"title-year:{title}:{year}")
    if title and len(title) >= 8 and len(title_years.get(title, set())) <= 1:
        identities.append(f"title:{title}")
    body = str(page.get("compiled_truth") or "").strip()
    if body:
        identities.append(f"content:{hashlib.sha256(body.encode()).hexdigest()}")
    return identities or [f"page:{page.get('id')}"]


def _normalized_title(page: dict[str, Any]) -> str:
    return re.sub(r"\W+", " ", str(page.get("title") or "").casefold()).strip()


def _is_literature(page: dict[str, Any]) -> bool:
    frontmatter = page.get("frontmatter") or {}
    return bool(frontmatter.get("zotero-key")) or str(page.get("type") or "") in LITERATURE_TYPES


def _compatible_chunk(chunk: dict[str, Any]) -> bool:
    if str(chunk.get("model") or "") != "embo-01":
        return False
    try:
        return len(_embedding(chunk)) == 1536
    except (TypeError, ValueError, json.JSONDecodeError):
        return False


def _embedding(chunk: dict[str, Any]) -> list[float]:
    value = chunk.get("embedding")
    if not isinstance(value, str):
        raise ValueError("embedding is missing")
    vector = json.loads(value)
    if not isinstance(vector, list) or len(vector) != 1536:
        raise ValueError("embedding dimension mismatch")
    return [float(item) for item in vector]


def _ensure_identifier(
    session,
    library_id: str,
    item_id: str,
    scheme: str,
    value: str | None,
) -> None:
    if not value:
        return
    exists = session.scalar(
        select(Identifier.id)
        .where(Identifier.item_id == item_id)
        .where(Identifier.scheme == scheme)
        .where(Identifier.normalized_value == value)
    )
    if not exists:
        session.add(
            Identifier(
                library_id=library_id,
                item_id=item_id,
                scheme=scheme,
                normalized_value=value,
                is_primary=False,
            )
        )


def _extract_abstract(markdown: str) -> str:
    match = re.search(r"(?ims)^##\s+Abstract\s*$\s*(.+?)(?=^##\s+|\Z)", markdown)
    if not match:
        return ""
    return re.sub(r"\s+", " ", match.group(1)).strip()[:20000]


def _normalized_doi(value: Any) -> str:
    try:
        return normalize_doi(str(value or ""))
    except ValueError:
        return ""


def _year(value: Any) -> int | None:
    match = re.search(r"(?:18|19|20|21)\d{2}", str(value or ""))
    return int(match.group()) if match else None


def _item_type(value: str) -> str:
    return {
        "journalArticle": "article-journal",
        "conferencePaper": "paper-conference",
        "bookSection": "chapter",
        "book": "book",
        "thesis": "thesis",
        "preprint": "article",
        "dataset": "dataset",
        "standard": "standard",
        "webpage": "webpage",
    }.get(value, "document")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    values = []
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path.name}:{line_number} is not a JSON object")
            values.append(value)
    return values


def _line_count(path: Path) -> int:
    with path.open("rb") as stream:
        return sum(1 for line in stream if line.strip())


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
