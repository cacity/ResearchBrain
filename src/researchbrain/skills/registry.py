from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote, urlparse

import httpx

MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
MAX_EXTRACTED_BYTES = 200 * 1024 * 1024
MAX_FILES = 2000
NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
BUILTIN_NAME = "researchbrain-literature"


class SkillError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(UTC).isoformat()


class SkillRegistry:
    """Validate, store and deploy Skills without executing their contents."""

    def __init__(self, data_dir: Path, client: httpx.Client | None = None):
        self.data_dir = data_dir.resolve()
        self.root = self.data_dir / "skills"
        self.packages = self.root / "packages"
        self.registry_path = self.root / "registry.json"
        self.cache = self.data_dir / "cache" / "skill-installs"
        self._client = client
        self.builtin_root = self._find_builtin_root()

    def list(self) -> list[dict[str, Any]]:
        records = list(self._load().values())
        records.extend(self._builtin_records().values())
        return sorted(
            records,
            key=lambda value: (
                value["name"] != BUILTIN_NAME,
                not value["builtin"],
                value["name"],
            ),
        )

    def get(self, name: str) -> dict[str, Any]:
        if record := self._builtin_records().get(name):
            return record
        record = self._load().get(name)
        if not record:
            raise SkillError(f"Skill not found: {name}")
        return record

    def install(
        self,
        source_kind: str,
        source: str,
        *,
        ref: str = "",
        subpath: str = "",
        enabled: bool = False,
    ) -> dict[str, Any]:
        source_kind = source_kind.strip().lower()
        if source_kind not in {"local", "archive", "github"}:
            raise SkillError("source_kind must be local, archive, or github")
        if not source.strip():
            raise SkillError("Skill source is required")

        self.cache.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix="skill-", dir=self.cache))
        try:
            source_root = self._stage_source(staging, source_kind, source, ref, subpath)
            package = staging / "package"
            self._copy_checked_tree(source_root, package)
            inspected = self._inspect(package)
            name = inspected["name"]
            if name in self._builtin_records():
                raise SkillError(f"{name} is built in and cannot be replaced")

            digest = self._tree_digest(package)
            target = self.packages / name / digest
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() and self._tree_digest(target) != digest:
                shutil.rmtree(target)
            if not target.exists():
                os.replace(package, target)

            records = self._load()
            previous = records.get(name, {})
            timestamp = _now()
            record = {
                **inspected,
                "source_kind": source_kind,
                "source": source,
                "source_ref": ref,
                "source_subpath": subpath,
                "sha256": digest,
                "enabled": bool(enabled),
                "builtin": False,
                "managed_path": str(target),
                "installed_at": previous.get("installed_at", timestamp),
                "updated_at": timestamp,
            }
            records[name] = record
            self._save(records)
            self._remove_unused_versions(name, digest)
            return record
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def update(self, name: str) -> dict[str, Any]:
        record = self.get(name)
        if record["builtin"]:
            raise SkillError("Built-in Skills are updated with ResearchBrain")
        return self.install(
            record["source_kind"],
            record["source"],
            ref=record.get("source_ref", ""),
            subpath=record.get("source_subpath", ""),
            enabled=record["enabled"],
        )

    def set_enabled(self, name: str, enabled: bool) -> dict[str, Any]:
        builtins = self._builtin_records()
        if name in builtins:
            if not enabled:
                raise SkillError("The built-in ResearchBrain Skill cannot be disabled")
            return builtins[name]
        records = self._load()
        record = records.get(name)
        if not record:
            raise SkillError(f"Skill not found: {name}")
        if enabled and record["compatibility"] == "incompatible":
            raise SkillError("This Skill is incompatible with the Harness runtime")
        record["enabled"] = bool(enabled)
        record["updated_at"] = _now()
        records[name] = record
        self._save(records)
        return record

    def uninstall(self, name: str) -> None:
        if name in self._builtin_records():
            raise SkillError("The built-in ResearchBrain Skill cannot be uninstalled")
        records = self._load()
        if name not in records:
            raise SkillError(f"Skill not found: {name}")
        records.pop(name)
        self._save(records)
        shutil.rmtree(self.packages / name, ignore_errors=True)

    def reveal(self, name: str) -> str:
        record = self.get(name)
        if record["builtin"]:
            raise SkillError("The built-in Skill is generated inside the Harness workspace")
        target = Path(record["managed_path"])
        if not target.is_dir():
            raise SkillError("The managed Skill directory is missing")
        try:
            if os.name == "nt":
                os.startfile(target)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(target)])
            else:
                subprocess.Popen(["xdg-open", str(target)])
        except OSError as exc:
            raise SkillError(f"Unable to open the Skill directory: {exc}") from exc
        return str(target)

    def materialize(self, destination: Path, builtins: dict[str, str]) -> list[str]:
        destination.mkdir(parents=True, exist_ok=True)
        marker = destination / ".researchbrain-managed.json"
        previous: list[str] = []
        if marker.is_file():
            try:
                payload = json.loads(marker.read_text(encoding="utf-8"))
                previous = [str(value) for value in payload.get("skills", [])]
            except (OSError, ValueError, TypeError):
                previous = []
        for name in previous:
            if NAME_PATTERN.fullmatch(name):
                shutil.rmtree(destination / name, ignore_errors=True)

        deployed: list[str] = []
        for name, content in builtins.items():
            target = destination / name
            target.mkdir(parents=True, exist_ok=True)
            (target / "SKILL.md").write_text(content, encoding="utf-8")
            deployed.append(name)
        for name, record in self._builtin_records().items():
            if name in builtins:
                continue
            source = Path(record["managed_path"])
            target = destination / name
            self._copy_checked_tree(source, target)
            deployed.append(name)
        for record in self._load().values():
            if not record.get("enabled") or record.get("compatibility") == "incompatible":
                continue
            source = Path(record["managed_path"])
            if not source.is_dir():
                raise SkillError(f"Managed Skill files are missing: {record['name']}")
            if self._tree_digest(source) != record["sha256"]:
                raise SkillError(f"Managed Skill integrity check failed: {record['name']}")
            target = destination / record["name"]
            if target.exists():
                raise SkillError(f"Harness workspace already contains an unmanaged Skill: {record['name']}")
            self._copy_checked_tree(source, target)
            deployed.append(record["name"])
        self._atomic_json(marker, {"skills": sorted(deployed), "updated_at": _now()})
        return sorted(deployed)

    def summary(self) -> dict[str, int]:
        values = self.list()
        return {
            "installed": len(values),
            "enabled": sum(bool(value["enabled"]) for value in values),
            "issues": sum(value["compatibility"] != "compatible" for value in values),
        }

    def launch_prompt(self, name: str, library_name: str) -> str:
        record = self.get(name)
        if not record["enabled"]:
            raise SkillError("Enable this Skill before using it")
        default = str(record.get("default_prompt") or "").strip()
        if default:
            return default
        return f"Use ${name} with the ResearchBrain library '{library_name}' for this task."

    def _stage_source(self, staging: Path, source_kind: str, source: str, ref: str, subpath: str) -> Path:
        if source_kind == "local":
            root = Path(source).expanduser().resolve()
            if subpath:
                root = self._safe_child(root, subpath)
            return self._locate_skill(root)
        if source_kind == "archive":
            archive = Path(source).expanduser().resolve()
            if not archive.is_file():
                raise SkillError(f"ZIP archive not found: {archive}")
            extracted = staging / "extracted"
            self._extract_zip(archive, extracted)
            root = self._archive_root(extracted)
            if subpath:
                root = self._safe_child(root, subpath)
            return self._locate_skill(root)

        owner, repo, url_ref, url_subpath = self._parse_github(source)
        resolved_ref = ref or url_ref or "HEAD"
        resolved_subpath = subpath or url_subpath
        archive = staging / "github.zip"
        url = f"https://github.com/{owner}/{repo}/archive/{quote(resolved_ref, safe='')}.zip"
        self._download(url, archive)
        extracted = staging / "extracted"
        self._extract_zip(archive, extracted)
        root = self._archive_root(extracted)
        if resolved_subpath:
            root = self._safe_child(root, resolved_subpath)
        return self._locate_skill(root)

    def _inspect(self, root: Path) -> dict[str, Any]:
        skill_file = root / "SKILL.md"
        text = skill_file.read_text(encoding="utf-8")
        metadata = self._frontmatter(text)
        name = metadata.get("name", "").strip()
        description = metadata.get("description", "").strip()
        if not NAME_PATTERN.fullmatch(name):
            raise SkillError("SKILL.md name must use 1-63 lowercase letters, digits, or hyphens")
        if not description:
            raise SkillError("SKILL.md frontmatter must include a description")
        if len(description) > 1200:
            raise SkillError("Skill description is too long")

        dependencies, default_prompt = self._agent_metadata(root / "agents" / "openai.yaml")
        permissions: list[str] = []
        compatibility = "compatible"
        unsupported = [value for value in dependencies if value["type"] != "mcp"]
        unknown_mcp = [
            value for value in dependencies if value["type"] == "mcp" and value["value"] != "researchbrain"
        ]
        script_files = [
            value
            for value in root.rglob("*")
            if value.is_file() and "scripts" in value.relative_to(root).parts
        ]
        if dependencies:
            permissions.append("调用声明的 MCP 工具")
        if script_files:
            permissions.append("执行 Skill 附带的本地脚本")
            compatibility = "review_required"
        if unknown_mcp:
            permissions.append("连接额外的 MCP 服务")
            compatibility = "needs_configuration"
        if unsupported:
            compatibility = "incompatible"
        return {
            "name": name,
            "description": description,
            "default_prompt": default_prompt,
            "compatibility": compatibility,
            "dependencies": dependencies,
            "permissions": permissions,
            "file_count": sum(1 for value in root.rglob("*") if value.is_file()),
        }

    @staticmethod
    def _frontmatter(text: str) -> dict[str, str]:
        lines = text.lstrip("\ufeff").splitlines()
        if not lines or lines[0].strip() != "---":
            raise SkillError("SKILL.md must start with YAML frontmatter")
        try:
            end = next(index for index in range(1, len(lines)) if lines[index].strip() == "---")
        except StopIteration as exc:
            raise SkillError("SKILL.md frontmatter is not closed") from exc
        result: dict[str, str] = {}
        index = 1
        while index < end:
            line = lines[index]
            if line and not line[0].isspace() and ":" in line:
                key, raw = line.split(":", 1)
                value = raw.strip().strip("\"'")
                if value in {">", ">-", "|", "|-"}:
                    values: list[str] = []
                    index += 1
                    while index < end and (not lines[index] or lines[index][0].isspace()):
                        values.append(lines[index].strip())
                        index += 1
                    result[key.strip()] = " ".join(filter(None, values))
                    continue
                result[key.strip()] = value
            index += 1
        return result

    @staticmethod
    def _agent_metadata(path: Path) -> tuple[list[dict[str, str]], str]:
        if not path.is_file():
            return [], ""
        text = path.read_text(encoding="utf-8")
        dependencies: list[dict[str, str]] = []
        for block in re.split(r"(?m)^\s*-\s+type:\s*", text)[1:]:
            tool_type = block.splitlines()[0].strip().strip("\"'")
            value_match = re.search(r"(?m)^\s+value:\s*[\"']?([^\"'\r\n]+)", block)
            description_match = re.search(r"(?m)^\s+description:\s*[\"']?([^\"'\r\n]+)", block)
            dependencies.append(
                {
                    "type": tool_type,
                    "value": value_match.group(1).strip() if value_match else "",
                    "description": description_match.group(1).strip() if description_match else "",
                }
            )
        prompt_match = re.search(r"(?m)^\s*default_prompt:\s*[\"']?([^\"'\r\n]+)", text)
        return dependencies, prompt_match.group(1).strip() if prompt_match else ""

    def _copy_checked_tree(self, source: Path, target: Path) -> None:
        if not source.is_dir():
            raise SkillError(f"Skill directory not found: {source}")
        files = 0
        size = 0
        for value in source.rglob("*"):
            if value.is_symlink():
                raise SkillError(f"Symbolic links are not allowed in Skills: {value.name}")
            if value.is_file():
                files += 1
                size += value.stat().st_size
                if files > MAX_FILES or size > MAX_EXTRACTED_BYTES:
                    raise SkillError("Skill exceeds the file count or size limit")
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source, target)

    @staticmethod
    def _locate_skill(root: Path) -> Path:
        if (root / "SKILL.md").is_file():
            return root
        matches = list(root.glob("*/SKILL.md")) + list(root.glob(".agents/skills/*/SKILL.md"))
        unique = list(dict.fromkeys(value.parent.resolve() for value in matches))
        if len(unique) == 1:
            return unique[0]
        if not unique:
            raise SkillError("No SKILL.md was found at the selected source")
        names = ", ".join(value.name for value in unique[:8])
        raise SkillError(f"This source contains multiple Skills ({names}); specify a subpath")

    @staticmethod
    def _safe_child(root: Path, relative: str) -> Path:
        clean = PurePosixPath(relative.replace("\\", "/"))
        if clean.is_absolute() or ".." in clean.parts:
            raise SkillError("Skill subpath must stay inside the source")
        candidate = (root / Path(*clean.parts)).resolve()
        try:
            candidate.relative_to(root.resolve())
        except ValueError as exc:
            raise SkillError("Skill subpath escapes the source") from exc
        return candidate

    @staticmethod
    def _archive_root(extracted: Path) -> Path:
        children = [value for value in extracted.iterdir()]
        return children[0] if len(children) == 1 and children[0].is_dir() else extracted

    @staticmethod
    def _extract_zip(archive: Path, target: Path) -> None:
        if archive.stat().st_size > MAX_ARCHIVE_BYTES:
            raise SkillError("Skill ZIP exceeds 100 MB")
        target.mkdir(parents=True, exist_ok=True)
        count = 0
        declared_size = 0
        extracted_size = 0
        try:
            with zipfile.ZipFile(archive) as package:
                for info in package.infolist():
                    path = PurePosixPath(info.filename.replace("\\", "/"))
                    mode = info.external_attr >> 16
                    if (
                        path.is_absolute()
                        or ".." in path.parts
                        or any(":" in part for part in path.parts)
                        or stat.S_ISLNK(mode)
                    ):
                        raise SkillError("Skill ZIP contains an unsafe path or symbolic link")
                    if info.is_dir():
                        continue
                    count += 1
                    declared_size += info.file_size
                    if count > MAX_FILES or declared_size > MAX_EXTRACTED_BYTES:
                        raise SkillError("Skill ZIP exceeds the extraction limits")
                    destination = target.joinpath(*path.parts)
                    try:
                        destination.resolve().relative_to(target.resolve())
                    except ValueError as exc:
                        raise SkillError("Skill ZIP contains an unsafe path") from exc
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with package.open(info) as source, destination.open("wb") as output:
                        while chunk := source.read(1024 * 1024):
                            extracted_size += len(chunk)
                            if extracted_size > MAX_EXTRACTED_BYTES:
                                raise SkillError("Skill ZIP exceeds the extraction limits")
                            output.write(chunk)
        except zipfile.BadZipFile as exc:
            raise SkillError("The selected file is not a valid ZIP archive") from exc

    def _download(self, url: str, destination: Path) -> None:
        client = self._client or httpx.Client(follow_redirects=True, timeout=60)
        owns_client = self._client is None
        try:
            with client.stream("GET", url) as response:
                response.raise_for_status()
                size = 0
                with destination.open("wb") as output:
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > MAX_ARCHIVE_BYTES:
                            raise SkillError("Downloaded Skill ZIP exceeds 100 MB")
                        output.write(chunk)
        except httpx.HTTPError as exc:
            raise SkillError(f"Unable to download GitHub Skill: {exc}") from exc
        finally:
            if owns_client:
                client.close()

    @staticmethod
    def _parse_github(source: str) -> tuple[str, str, str, str]:
        parsed = urlparse(source.strip())
        if parsed.scheme != "https" or parsed.hostname not in {"github.com", "www.github.com"}:
            raise SkillError("GitHub source must be an https://github.com URL")
        parts = [value for value in parsed.path.split("/") if value]
        if len(parts) < 2:
            raise SkillError("GitHub URL must identify an owner and repository")
        owner, repo = parts[0], parts[1].removesuffix(".git")
        ref = parts[3] if len(parts) >= 4 and parts[2] == "tree" else ""
        subpath = "/".join(parts[4:]) if ref else ""
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", owner) or not re.fullmatch(r"[A-Za-z0-9_.-]+", repo):
            raise SkillError("GitHub owner or repository is invalid")
        return owner, repo, ref, subpath

    @staticmethod
    def _tree_digest(root: Path) -> str:
        digest = hashlib.sha256()
        for path in sorted(value for value in root.rglob("*") if value.is_file()):
            relative = path.relative_to(root).as_posix()
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0")
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
        return digest.hexdigest()

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self.registry_path.is_file():
            return {}
        try:
            payload = json.loads(self.registry_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return {}
        skills = payload.get("skills", {}) if isinstance(payload, dict) else {}
        return skills if isinstance(skills, dict) else {}

    def _save(self, records: dict[str, dict[str, Any]]) -> None:
        self._atomic_json(self.registry_path, {"version": 1, "skills": records})

    @staticmethod
    def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def _remove_unused_versions(self, name: str, current: str) -> None:
        root = self.packages / name
        if not root.is_dir():
            return
        for value in root.iterdir():
            if value.name != current:
                shutil.rmtree(value, ignore_errors=True)

    def _builtin_records(self) -> dict[str, dict[str, Any]]:
        records = {BUILTIN_NAME: self._primary_builtin_record()}
        if not self.builtin_root.is_dir():
            return records
        for source in self.builtin_root.iterdir():
            if not source.is_dir() or source.name == BUILTIN_NAME or not (source / "SKILL.md").is_file():
                continue
            inspected = self._inspect(source)
            records[inspected["name"]] = {
                **inspected,
                "source_kind": "builtin",
                "source": "ResearchBrain",
                "source_ref": "",
                "source_subpath": "",
                "sha256": self._tree_digest(source),
                "enabled": True,
                "builtin": True,
                "managed_path": str(source),
                "installed_at": "",
                "updated_at": "",
            }
        return records

    @staticmethod
    def _find_builtin_root() -> Path:
        override = os.getenv("RESEARCHBRAIN_BUILTIN_SKILLS_DIR", "").strip()
        if override:
            return Path(override).expanduser().resolve()
        bundle_root = getattr(sys, "_MEIPASS", "")
        if bundle_root:
            bundled = Path(bundle_root) / "researchbrain" / "builtin_skills"
            if bundled.is_dir():
                return bundled
        packaged = Path(__file__).resolve().parents[1] / "builtin_skills"
        if packaged.is_dir():
            return packaged
        return Path(__file__).resolve().parents[3] / ".agents" / "skills"

    @staticmethod
    def _primary_builtin_record() -> dict[str, Any]:
        return {
            "name": BUILTIN_NAME,
            "description": "ResearchBrain local-library and online evidence research workflow.",
            "default_prompt": (
                "Use $researchbrain-literature to research this question with local evidence first."
            ),
            "compatibility": "compatible",
            "dependencies": [
                {
                    "type": "mcp",
                    "value": "researchbrain",
                    "description": "Local ResearchBrain literature tools",
                }
            ],
            "permissions": ["调用 ResearchBrain MCP 工具"],
            "file_count": 1,
            "source_kind": "builtin",
            "source": "ResearchBrain",
            "source_ref": "",
            "source_subpath": "",
            "sha256": "",
            "enabled": True,
            "builtin": True,
            "managed_path": "",
            "installed_at": "",
            "updated_at": "",
        }
