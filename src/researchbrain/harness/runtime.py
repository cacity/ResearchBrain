from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import Lock
from typing import Any

import httpx

from researchbrain.runtime.manager import RuntimeInstallError, RuntimeManager

DSH_PACKAGE = "@deepseek-ai/dsh@0.1.1-rc.2"
MINIMUM_NODE = (22, 19, 0)
PORTABLE_NODE_MAJOR = 24
DEFAULT_PORT = 3080
MAX_NODE_DOWNLOAD_BYTES = 150 * 1024 * 1024

_BUNDLE_PACKAGE = {
    "name": "researchbrain-harness-bridge",
    "version": "0.1.0",
    "private": True,
    "type": "module",
    "files": ["cordis.patch.yml"],
    "dsh": {"bundle": {"patch": "./cordis.patch.yml"}},
}

_BUNDLE_PATCH = """- insert:
    - id: mcp-researchbrain
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: researchbrain
        transport: stdio
        command: !!js process.env.RESEARCHBRAIN_MCP_COMMAND
        args: !!js JSON.parse(process.env.RESEARCHBRAIN_MCP_ARGS || '[]')
        env:
          RESEARCHBRAIN_DATA_DIR: !!js process.env.RESEARCHBRAIN_DATA_DIR
          RESEARCHBRAIN_DEFAULT_LIBRARY_ID: !!js process.env.RESEARCHBRAIN_DEFAULT_LIBRARY_ID || ''
        cwd: !!js process.env.RESEARCHBRAIN_HARNESS_WORKSPACE
        toolCallTimeoutMs: 180000
        failOnStartupError: true
        reconnect:
          enabled: true
          initialDelayMs: 500
          maxDelayMs: 30000
          maxAttempts: 10
"""

_SKILL = """---
name: researchbrain-literature
description: >-
  Use ResearchBrain local libraries and academic discovery tools for
  evidence-grounded literature research.
whenToUse: >-
  Use for literature searches, research reviews, paper comparisons, DOI
  imports, full-text retrieval, and evidence-backed research planning.
user-invocable: true
disable-model-invocation: false
metadata:
  product: ResearchBrain
  evidence_policy: strict
---

# ResearchBrain Literature Research

Use the `mcp__researchbrain__*` tools as the source of truth for the user's literature library.

## Required workflow

1. Call `get_research_context` first. Use its default library unless the user explicitly names another.
2. Break broad questions into focused searches. Search the local library before searching online.
3. Use `search_library` for evidence chunks and `get_item` for complete bibliographic metadata.
4. Use `search_online` only when the user asks for current coverage, external
   literature, or the local evidence is insufficient.
5. Distinguish full-text evidence from title/abstract metadata. Never claim to
   have read figures, equations, pages, detailed methods, or results from
   metadata alone.
6. Cite local evidence IDs and online source identifiers immediately after
   factual claims. Include DOI when available.
7. Separate reported findings, cross-paper synthesis, inference, and proposed
   work. State evidence limitations.
8. Before `import_dois` or `queue_fulltext`, state exactly what will be added or
   queued and obtain approval when the active permission policy requires it.
9. After a write action, call `list_jobs` and report the queued job or batch
   identifiers. Do not claim that PDF parsing or embedding is complete until job
   status confirms it.

## Review output

For a substantial review, organize the answer by research question rather than
paper order. Compare data, methods, processing steps, findings, disagreements,
limitations, and actionable research gaps. Use compact Markdown tables where
they improve comparison.
"""


class HarnessInstallError(RuntimeError):
    pass


@dataclass(frozen=True)
class NodeRuntime:
    command: str
    version: str
    source: str
    supported: bool


@dataclass(frozen=True)
class HarnessStatus:
    available: bool
    configured: bool
    running: bool
    owned_process: bool
    port: int
    url: str
    dsh_package: str
    node: dict[str, Any]
    profile_path: str
    workspace_path: str
    log_path: str
    error: str = ""


class HarnessRuntimeManager:
    """Install and supervise an isolated DeepSeek Harness Web profile."""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir.resolve()
        self.root = self.data_dir / "harness"
        self.dsh_home = self.root / "dsh-home"
        self.workspace = self.root / "workspace"
        self.bundle = self.root / "researchbrain-harness-bridge"
        self.log_path = self.root / "harness.log"
        self._runtime = RuntimeManager(self.data_dir)
        self._process: subprocess.Popen | None = None
        self._log_handle = None
        self._lock = Lock()
        self._port = DEFAULT_PORT

    def status(self, port: int | None = None) -> dict[str, Any]:
        resolved_port = _validate_port(port or self._port)
        node = self._select_node()
        process_running = bool(self._process and self._process.poll() is None)
        endpoint_running = _harness_is_ready(resolved_port)
        port_occupied = _port_is_open(resolved_port)
        configured = self._profile_is_configured()
        error = ""
        if node and not node.supported:
            error = f"Node.js {node.version} is too old; Harness requires 22.19 or newer"
        if port_occupied and not endpoint_running and not process_running:
            error = f"Port {resolved_port} is occupied by a service that is not DeepSeek Harness"
        state = HarnessStatus(
            available=bool(node and node.supported),
            configured=configured,
            running=process_running or endpoint_running,
            owned_process=process_running,
            port=resolved_port,
            url=f"http://127.0.0.1:{resolved_port}",
            dsh_package=DSH_PACKAGE,
            node=asdict(node)
            if node
            else {"command": "", "version": "", "source": "missing", "supported": False},
            profile_path=str(self.dsh_home / "profiles" / "web"),
            workspace_path=str(self.workspace),
            log_path=str(self.log_path),
            error=error,
        )
        return asdict(state)

    def install(self, default_library_id: str = "") -> dict[str, Any]:
        with self._lock:
            if self._process and self._process.poll() is None:
                raise HarnessInstallError("Stop Harness before installing or updating its profile")
            self._write_integration_files()
            node = self._select_node()
            if not node or not node.supported:
                node = self._install_portable_node()
            env = self._environment(node, default_library_id)
            runner = self._package_runner(node)
            self._run(
                [*runner, "web", "--help"],
                env=env,
                timeout=600,
            )
            self._run(
                [
                    *runner,
                    "plugin",
                    "--profile",
                    "web",
                    "add",
                    str(self.bundle),
                ],
                env=env,
                timeout=600,
            )
            status = self.status()
            if not status["configured"]:
                raise HarnessInstallError(
                    "Harness profile was created but the ResearchBrain bundle is inactive"
                )
            return status

    def start(
        self,
        port: int = DEFAULT_PORT,
        default_library_id: str = "",
        deepseek_api_key: str = "",
        deepseek_base_url: str = "",
        deepseek_model: str = "",
    ) -> dict[str, Any]:
        resolved_port = _validate_port(port)
        with self._lock:
            if self._process and self._process.poll() is None:
                self._port = resolved_port
                return self.status(resolved_port)
            if _harness_is_ready(resolved_port):
                self._port = resolved_port
                return self.status(resolved_port)
            if _port_is_open(resolved_port):
                raise HarnessInstallError(f"Port {resolved_port} is already in use by another service")
            node = self._select_node()
            if not node or not node.supported:
                raise HarnessInstallError("Install the Harness runtime before starting it")
            if not self._profile_is_configured():
                raise HarnessInstallError("Install the ResearchBrain Harness profile before starting it")
            self._write_integration_files()
            env = self._environment(node, default_library_id)
            if deepseek_api_key:
                env["DEEPSEEK_API_KEY"] = deepseek_api_key
            if deepseek_base_url:
                env["DEEPSEEK_BASE_URL"] = deepseek_base_url
            if deepseek_model:
                env["DSH_MODEL"] = deepseek_model
            runner = self._package_runner(node)
            self.root.mkdir(parents=True, exist_ok=True)
            self._log_handle = self.log_path.open("a", encoding="utf-8")
            creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            self._process = subprocess.Popen(
                [
                    *runner,
                    "web",
                    "--port",
                    str(resolved_port),
                    "--no-open",
                ],
                cwd=self.workspace,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=self._log_handle,
                stderr=subprocess.STDOUT,
                creationflags=creationflags,
            )
            self._port = resolved_port

        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if self._process and self._process.poll() is not None:
                message = self._log_tail()
                self._close_log()
                raise HarnessInstallError(f"Harness exited during startup: {message}")
            if _harness_is_ready(resolved_port):
                return self.status(resolved_port)
            time.sleep(0.25)
        self.stop()
        raise HarnessInstallError(f"Harness did not become ready on port {resolved_port}")

    def stop(self) -> dict[str, Any]:
        with self._lock:
            process = self._process
            self._process = None
            if process and process.poll() is None:
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                        capture_output=True,
                        check=False,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                else:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
            self._close_log()
        return self.status(self._port)

    def _write_integration_files(self) -> None:
        skill_dir = self.workspace / ".agents" / "skills" / "researchbrain-literature"
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(_SKILL, encoding="utf-8")
        self.bundle.mkdir(parents=True, exist_ok=True)
        (self.bundle / "package.json").write_text(
            json.dumps(_BUNDLE_PACKAGE, ensure_ascii=True, indent=2) + "\n",
            encoding="utf-8",
        )
        (self.bundle / "cordis.patch.yml").write_text(_BUNDLE_PATCH, encoding="utf-8")

    def _profile_is_configured(self) -> bool:
        manifest = self.dsh_home / "profiles" / "web" / "package.json"
        if not manifest.is_file():
            return False
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        bundles = payload.get("dsh", {}).get("profile", {}).get("bundles", [])
        return _BUNDLE_PACKAGE["name"] in bundles

    def _select_node(self) -> NodeRuntime | None:
        candidates: list[tuple[str, str]] = []
        portable = self._portable_node_command()
        if portable:
            candidates.append((portable, "portable"))
        system = shutil.which("node")
        if system:
            candidates.append((system, "system"))
        unsupported: NodeRuntime | None = None
        for command, source in candidates:
            version = _command_version(command)
            if not version:
                continue
            runtime = NodeRuntime(command, version, source, _node_supported(version))
            if runtime.supported:
                return runtime
            unsupported = unsupported or runtime
        return unsupported

    def _portable_node_command(self) -> str:
        try:
            components = self._runtime.status().get("components", {})
        except RuntimeInstallError:
            return ""
        node = components.get("node") or {}
        root = Path(str(node.get("path") or ""))
        if not root.is_dir():
            return ""
        matches = (
            list(root.glob("node-v*/node.exe")) if os.name == "nt" else list(root.glob("node-v*/bin/node"))
        )
        return str(matches[0]) if matches else ""

    def _install_portable_node(self) -> NodeRuntime:
        if os.name != "nt" or platform.machine().lower() not in {"amd64", "x86_64"}:
            raise HarnessInstallError("Automatic Node installation currently supports Windows x64 only")
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            distribution_url = os.getenv("RESEARCHBRAIN_NODE_DIST_URL", "https://nodejs.org/dist").rstrip("/")
            releases = _download_json(f"{distribution_url}/index.json")
            release = next(
                value
                for value in releases
                if str(value.get("version", "")).startswith(f"v{PORTABLE_NODE_MAJOR}.")
                and "win-x64-zip" in (value.get("files") or [])
            )
        except (OSError, StopIteration, ValueError, TypeError) as exc:
            raise HarnessInstallError(
                f"Unable to resolve a Node {PORTABLE_NODE_MAJOR} release: {exc}"
            ) from exc
        version = str(release["version"])
        filename = f"node-{version}-win-x64.zip"
        base_url = f"{distribution_url}/{version}"
        checksums = _download_text(f"{base_url}/SHASUMS256.txt")
        expected = _checksum_for(checksums, filename)
        archive = self.root / "downloads" / filename
        archive.parent.mkdir(parents=True, exist_ok=True)
        if not archive.is_file() or _sha256_file(archive) != expected:
            _download_file(f"{base_url}/{filename}", archive)
        try:
            self._runtime.install_archive("node", version.removeprefix("v"), archive, expected)
        except (OSError, RuntimeInstallError) as exc:
            raise HarnessInstallError(str(exc)) from exc
        runtime = self._select_node()
        if not runtime or not runtime.supported:
            raise HarnessInstallError("Portable Node installation completed but node.exe is unavailable")
        return runtime

    def _package_runner(self, node: NodeRuntime) -> list[str]:
        node_dir = Path(node.command).parent
        local = node_dir / ("npx.cmd" if os.name == "nt" else "npx")
        if local.is_file():
            return [str(local), "--yes", DSH_PACKAGE]
        npx = shutil.which("npx")
        if npx and Path(npx).parent.resolve() == node_dir.resolve():
            return [npx, "--yes", DSH_PACKAGE]
        pnpm = shutil.which("pnpm")
        if pnpm:
            return [pnpm, "dlx", DSH_PACKAGE]
        raise HarnessInstallError("Neither npx nor pnpm is available for the selected Node.js runtime")

    def _environment(self, node: NodeRuntime, default_library_id: str) -> dict[str, str]:
        env = dict(os.environ)
        node_dir = str(Path(node.command).parent)
        env["PATH"] = node_dir + os.pathsep + env.get("PATH", "")
        env["DSH_HOME"] = str(self.dsh_home)
        env["RESEARCHBRAIN_DATA_DIR"] = str(self.data_dir)
        env["RESEARCHBRAIN_DEFAULT_LIBRARY_ID"] = default_library_id
        env["RESEARCHBRAIN_HARNESS_WORKSPACE"] = str(self.workspace)
        command, args = _mcp_command()
        env["RESEARCHBRAIN_MCP_COMMAND"] = command
        env["RESEARCHBRAIN_MCP_ARGS"] = json.dumps(args)
        return env

    def _run(self, command: list[str], env: dict[str, str], timeout: int) -> None:
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            result = subprocess.run(
                command,
                cwd=self.workspace,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                creationflags=creationflags,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise HarnessInstallError(f"Unable to run DeepSeek Harness: {exc}") from exc
        if result.returncode:
            detail = (result.stderr or result.stdout).strip()
            raise HarnessInstallError(detail[-4000:] or f"Harness command exited {result.returncode}")

    def _log_tail(self) -> str:
        try:
            return self.log_path.read_text(encoding="utf-8", errors="replace")[-4000:].strip()
        except OSError:
            return "no startup log was written"

    def _close_log(self) -> None:
        if self._log_handle:
            self._log_handle.close()
            self._log_handle = None


def _mcp_command() -> tuple[str, list[str]]:
    if getattr(sys, "frozen", False):
        return sys.executable, ["mcp"]
    return sys.executable, ["-m", "researchbrain.mcp_server"]


def _validate_port(port: int) -> int:
    if not 1024 <= int(port) <= 65535:
        raise HarnessInstallError("Harness port must be between 1024 and 65535")
    return int(port)


def _port_is_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.25):
            return True
    except OSError:
        return False


def _harness_is_ready(port: int) -> bool:
    try:
        response = httpx.get(
            f"http://127.0.0.1:{port}/",
            timeout=0.75,
            follow_redirects=False,
        )
    except httpx.HTTPError:
        return False
    if response.status_code != 200:
        return False
    content_type = response.headers.get("content-type", "").lower()
    text = response.text[:16384].lower()
    return "text/html" in content_type and (
        "deepseek harness" in text or "deepseek-harness" in text or "dsh-client" in text
    )


def _command_version(command: str) -> str:
    try:
        result = subprocess.run(
            [command, "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip().removeprefix("v") if result.returncode == 0 else ""


def _node_supported(version: str) -> bool:
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)", version)
    if not match:
        return False
    return tuple(int(value) for value in match.groups()) >= MINIMUM_NODE


def _checksum_for(checksums: str, filename: str) -> str:
    for line in checksums.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("*") == filename:
            return parts[0].lower()
    raise HarnessInstallError(f"Node checksum is missing for {filename}")


def _download_json(url: str) -> list[dict[str, Any]]:
    return json.loads(_download_text(url))


def _download_text(url: str) -> str:
    try:
        response = httpx.get(
            url,
            headers={"User-Agent": "ResearchBrain/0.1"},
            follow_redirects=True,
            timeout=httpx.Timeout(45, connect=20),
        )
        response.raise_for_status()
        return response.text
    except httpx.HTTPError as exc:
        raise HarnessInstallError(f"Unable to download {url}: {exc}") from exc


def _download_file(url: str, destination: Path) -> None:
    temporary = destination.with_suffix(destination.suffix + ".partial")
    try:
        started = time.monotonic()
        downloaded = 0
        timeout = httpx.Timeout(connect=20, read=30, write=30, pool=20)
        with (
            httpx.stream(
                "GET",
                url,
                headers={"User-Agent": "ResearchBrain/0.1"},
                follow_redirects=True,
                timeout=timeout,
            ) as response,
            temporary.open("wb") as target,
        ):
            response.raise_for_status()
            declared_size = int(response.headers.get("content-length") or 0)
            if declared_size > MAX_NODE_DOWNLOAD_BYTES:
                raise HarnessInstallError("Node runtime archive exceeds the download size limit")
            for chunk in response.iter_bytes(1024 * 256):
                downloaded += len(chunk)
                if downloaded > MAX_NODE_DOWNLOAD_BYTES:
                    raise HarnessInstallError("Node runtime archive exceeds the download size limit")
                if time.monotonic() - started > 300:
                    raise HarnessInstallError("Node runtime download exceeded the five-minute limit")
                target.write(chunk)
        temporary.replace(destination)
    except httpx.HTTPError as exc:
        raise HarnessInstallError(f"Unable to download {url}: {exc}") from exc
    finally:
        temporary.unlink(missing_ok=True)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
