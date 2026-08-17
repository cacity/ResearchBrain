from __future__ import annotations

import hashlib
import json
import re
import shutil
import uuid
import zipfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class RuntimeInstallError(RuntimeError):
    pass


@dataclass(frozen=True)
class ComponentState:
    name: str
    version: str
    path: str
    installed_at: str
    sha256: str


class RuntimeManager:
    def __init__(self, data_dir: Path):
        self.root = data_dir / "runtime"
        self.state_path = self.root / "current.json"

    def status(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"components": {}, "state_file": str(self.state_path)}
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeInstallError(f"runtime state is invalid: {exc}") from exc
        return {"components": payload.get("components") or {}, "state_file": str(self.state_path)}

    def install_archive(
        self,
        name: str,
        version: str,
        archive_path: Path,
        expected_sha256: str,
    ) -> ComponentState:
        safe_name = _safe_component_value(name)
        safe_version = _safe_component_value(version)
        actual_sha256 = _sha256_file(archive_path)
        if actual_sha256.lower() != expected_sha256.lower():
            raise RuntimeInstallError(
                f"component SHA-256 mismatch: expected {expected_sha256}, got {actual_sha256}"
            )
        component_root = self.root / safe_name
        destination = component_root / safe_version
        staging = component_root / f".{safe_version}-{uuid.uuid4().hex}.staging"
        staging.mkdir(parents=True, exist_ok=False)
        try:
            _safe_extract_zip(archive_path, staging)
            if destination.exists():
                shutil.rmtree(staging)
            else:
                staging.replace(destination)
            state = ComponentState(
                name=safe_name,
                version=safe_version,
                path=str(destination),
                installed_at=datetime.now(UTC).isoformat(),
                sha256=actual_sha256,
            )
            self._activate(state)
            return state
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    def rollback(self, name: str, version: str) -> ComponentState:
        safe_name = _safe_component_value(name)
        safe_version = _safe_component_value(version)
        destination = self.root / safe_name / safe_version
        if not destination.is_dir():
            raise RuntimeInstallError("requested runtime version is not installed")
        state = ComponentState(
            name=safe_name,
            version=safe_version,
            path=str(destination),
            installed_at=datetime.now(UTC).isoformat(),
            sha256="existing-installation",
        )
        self._activate(state)
        return state

    def _activate(self, state: ComponentState) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        current = self.status()["components"] if self.state_path.exists() else {}
        previous = current.get(state.name)
        current[state.name] = {**asdict(state), "previous": previous}
        temporary = self.state_path.with_suffix(".json.partial")
        temporary.write_text(
            json.dumps({"components": current}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.state_path)


def _safe_extract_zip(archive_path: Path, destination: Path) -> None:
    resolved_destination = destination.resolve()
    try:
        with zipfile.ZipFile(archive_path) as archive:
            for member in archive.infolist():
                member_path = (destination / member.filename).resolve()
                if member_path != resolved_destination and resolved_destination not in member_path.parents:
                    raise RuntimeInstallError(f"unsafe path in component archive: {member.filename}")
                if member.is_dir():
                    member_path.mkdir(parents=True, exist_ok=True)
                    continue
                member_path.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source, member_path.open("xb") as target:
                    shutil.copyfileobj(source, target)
    except zipfile.BadZipFile as exc:
        raise RuntimeInstallError("component archive is not a valid ZIP file") from exc


def _safe_component_value(value: str) -> str:
    value = value.strip()
    if not re.fullmatch(r"[A-Za-z0-9._-]+", value):
        raise RuntimeInstallError(
            "component name and version may only contain letters, numbers, dot, dash, underscore"
        )
    return value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
