from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _default_data_dir() -> Path:
    override = os.getenv("RESEARCHBRAIN_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    local_app_data = os.getenv("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "ResearchBrain"
    return Path.home() / ".researchbrain"


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    database_url: str
    contact_email: str
    crossref_base_url: str = "https://api.crossref.org"
    unpaywall_base_url: str = "https://api.unpaywall.org/v2"
    max_download_mb: int = 200
    mineru_executable: str = "mineru"
    mineru_backend: str = "pipeline"
    mineru_version: str = "3.x"
    minimax_embedding_url: str = "https://api.minimax.chat/v1/embeddings"
    minimax_group_id: str = ""
    minimax_embedding_model: str = "embo-01"
    minimax_embedding_dimensions: int = 1536
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"
    worker_enabled: bool = True
    worker_poll_seconds: float = 1.0
    zotero_data_dir: Path = Path.home() / "Zotero"

    @classmethod
    def load(cls) -> Settings:
        data_dir = _default_data_dir()
        saved = UserConfigStore(data_dir).load()
        database_url = os.getenv(
            "RESEARCHBRAIN_DATABASE_URL",
            f"sqlite:///{(data_dir / 'data' / 'library.sqlite').as_posix()}",
        )
        return cls(
            data_dir=data_dir,
            database_url=database_url,
            contact_email=os.getenv(
                "RESEARCHBRAIN_CONTACT_EMAIL",
                str(saved.get("contact_email") or ""),
            ),
            crossref_base_url=os.getenv("RESEARCHBRAIN_CROSSREF_URL", "https://api.crossref.org").rstrip("/"),
            unpaywall_base_url=os.getenv(
                "RESEARCHBRAIN_UNPAYWALL_URL", "https://api.unpaywall.org/v2"
            ).rstrip("/"),
            max_download_mb=int(os.getenv("RESEARCHBRAIN_MAX_DOWNLOAD_MB", "200")),
            mineru_executable=os.getenv(
                "RESEARCHBRAIN_MINERU_EXECUTABLE",
                str(saved.get("mineru_executable") or "mineru"),
            ),
            mineru_backend=os.getenv("RESEARCHBRAIN_MINERU_BACKEND", "pipeline"),
            mineru_version=os.getenv("RESEARCHBRAIN_MINERU_VERSION", "3.x"),
            minimax_embedding_url=os.getenv(
                "RESEARCHBRAIN_MINIMAX_EMBEDDING_URL",
                "https://api.minimax.chat/v1/embeddings",
            ),
            minimax_group_id=os.getenv(
                "MINIMAX_GROUP_ID",
                str(saved.get("minimax_group_id") or ""),
            ),
            minimax_embedding_model=os.getenv("RESEARCHBRAIN_MINIMAX_EMBEDDING_MODEL", "embo-01"),
            minimax_embedding_dimensions=int(os.getenv("RESEARCHBRAIN_MINIMAX_EMBEDDING_DIMENSIONS", "1536")),
            deepseek_base_url=os.getenv("RESEARCHBRAIN_DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip(
                "/"
            ),
            deepseek_model=os.getenv("RESEARCHBRAIN_DEEPSEEK_MODEL", "deepseek-chat"),
            worker_enabled=os.getenv("RESEARCHBRAIN_WORKER_ENABLED", "1").strip().lower()
            not in {"0", "false", "no", "off"},
            worker_poll_seconds=max(
                0.1,
                float(os.getenv("RESEARCHBRAIN_WORKER_POLL_SECONDS", "1.0")),
            ),
            zotero_data_dir=Path(
                os.getenv(
                    "RESEARCHBRAIN_ZOTERO_DATA_DIR",
                    str(saved.get("zotero_data_dir") or Path.home() / "Zotero"),
                )
            ).expanduser(),
        )

    def ensure_directories(self) -> None:
        for relative in (
            "config",
            "data",
            "data/lancedb",
            "library/objects",
            "artifacts",
            "cache/http",
            "backups",
            "logs",
            "runtime",
        ):
            (self.data_dir / relative).mkdir(parents=True, exist_ok=True)


class UserConfigStore:
    def __init__(self, data_dir: Path):
        self.path = data_dir / "config" / "settings.json"

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def update(self, values: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "contact_email",
            "minimax_group_id",
            "mineru_executable",
            "zotero_data_dir",
        }
        unexpected = set(values) - allowed
        if unexpected:
            raise ValueError(f"unsupported settings: {', '.join(sorted(unexpected))}")
        payload = self.load()
        payload.update({key: value for key, value in values.items() if key in allowed})
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)
        return payload
