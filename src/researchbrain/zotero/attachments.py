from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select

from researchbrain.db.base import Database
from researchbrain.db.models import Attachment, Item
from researchbrain.fulltext.storage import DownloadError, ObjectStore
from researchbrain.jobs.service import JobService


@dataclass(frozen=True)
class ZoteroAttachmentImportResult:
    imported: int
    missing: int
    invalid: int


class ZoteroAttachmentImporter:
    def __init__(
        self,
        database: Database,
        data_dir: Path,
        zotero_data_dir: Path,
        max_download_mb: int = 200,
    ):
        self.database = database
        self.data_dir = data_dir
        self.zotero_data_dir = zotero_data_dir
        self.object_store = ObjectStore(data_dir, max_download_mb)

    async def import_pending(self, library_id: str) -> ZoteroAttachmentImportResult:
        with self.database.session() as session:
            rows = list(
                session.execute(
                    select(Attachment.id, Attachment.item_id, Attachment.source_key, Attachment.object_path)
                    .join(Item, Item.id == Attachment.item_id)
                    .where(Item.library_id == library_id)
                    .where(Attachment.status.in_(["linked", "missing"]))
                )
            )

        imported = missing = invalid = 0
        for attachment_id, item_id, source_key, raw_path in rows:
            source = self._resolve_path(str(raw_path), str(source_key or ""))
            if not source or not source.is_file():
                self._set_status(attachment_id, "missing")
                missing += 1
                continue

            try:
                stored = await self.object_store.store_pdf_stream(_file_chunks(source))
            except (DownloadError, OSError):
                self._set_status(attachment_id, "invalid")
                invalid += 1
                continue

            with self.database.session() as session:
                attachment = session.get(Attachment, attachment_id)
                item = session.get(Item, item_id)
                if not attachment or not item:
                    continue
                attachment.sha256 = stored.sha256
                attachment.object_path = str(stored.path.relative_to(self.data_dir))
                attachment.bytes = stored.bytes
                attachment.mime = "application/pdf"
                attachment.status = "stored"
                JobService(session).create_parse_job(
                    library_id,
                    item.id,
                    attachment.id,
                    stored.sha256,
                )
            imported += 1

        return ZoteroAttachmentImportResult(
            imported=imported,
            missing=missing,
            invalid=invalid,
        )

    def _resolve_path(self, raw_path: str, source_key: str) -> Path | None:
        root = self.zotero_data_dir.expanduser().resolve()
        if raw_path.startswith("storage:"):
            filename = raw_path.removeprefix("storage:")
            if not source_key or Path(source_key).name != source_key or Path(filename).name != filename:
                return None
            return root / "storage" / source_key / filename

        candidate = Path(raw_path).expanduser()
        if not candidate.is_absolute():
            return None
        try:
            resolved = candidate.resolve()
            resolved.relative_to(root)
        except (OSError, ValueError):
            return None
        return resolved

    def _set_status(self, attachment_id: str, status: str) -> None:
        with self.database.session() as session:
            attachment = session.get(Attachment, attachment_id)
            if attachment:
                attachment.status = status


async def _file_chunks(path: Path):
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 128):
            yield chunk
