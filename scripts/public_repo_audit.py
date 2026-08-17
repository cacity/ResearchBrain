from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = {
    ".editorconfig",
    ".env.example",
    ".gitattributes",
    ".gitignore",
    "CHANGELOG.md",
    "CITATION.cff",
    "CODE_OF_CONDUCT.md",
    "CONTRIBUTING.md",
    "LICENSE",
    "README.md",
    "README.en.md",
    "SECURITY.md",
    "SUPPORT.md",
}
FORBIDDEN_SUFFIXES = {
    ".7z",
    ".db",
    ".docx",
    ".exe",
    ".msi",
    ".onnx",
    ".pdf",
    ".pdb",
    ".pt",
    ".pth",
    ".pptx",
    ".safetensors",
    ".sqlite",
    ".sqlite3",
    ".xlsx",
    ".zip",
}
TEXT_SUFFIXES = {
    "",
    ".cff",
    ".css",
    ".env",
    ".ini",
    ".json",
    ".lock",
    ".md",
    ".py",
    ".ps1",
    ".rs",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}
MAX_TRACKED_BYTES = 5 * 1024 * 1024


def tracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        check=False,
        capture_output=True,
    )
    if result.returncode == 0 and result.stdout:
        return [ROOT / value.decode() for value in result.stdout.split(b"\0") if value]
    return [
        path
        for path in ROOT.rglob("*")
        if path.is_file()
        and not any(
            part
            in {
                ".git",
                ".venv",
                "__pycache__",
                "artifacts",
                "backups",
                "build",
                "cache",
                "data",
                "dist",
                "library",
                "logs",
                "node_modules",
                "runtime",
                "target",
            }
            for part in path.relative_to(ROOT).parts
        )
        and not (
            "desktop/src-tauri/binaries" in path.relative_to(ROOT).as_posix()
            and path.suffix.lower() == ".exe"
        )
    ]


def text_findings(path: Path, text: str) -> list[str]:
    if path.name == Path(__file__).name:
        return []
    patterns = {
        "personal Windows user path": re.compile(
            re.escape(":" + "\\" + "Users" + "\\") + r"(?!%USERPROFILE%|<)[^\\\s]+",
            re.IGNORECASE,
        ),
        "private workspace path": re.compile(r"[A-Za-z]:\\(?:work|01\.study)\\", re.IGNORECASE),
        "probable provider key": re.compile(
            r"(?i)(?:api[_-]?key|password|secret)\s*[:=]\s*[\"'][A-Za-z0-9_\-]{16,}[\"']"
        ),
    }
    return [label for label, pattern in patterns.items() if pattern.search(text)]


def main() -> int:
    errors: list[str] = []
    files = tracked_files()
    relative_names = {path.relative_to(ROOT).as_posix() for path in files}
    for missing in sorted(REQUIRED - relative_names):
        errors.append(f"missing required file: {missing}")

    for path in files:
        relative = path.relative_to(ROOT).as_posix()
        if path.suffix.lower() in FORBIDDEN_SUFFIXES:
            errors.append(f"forbidden tracked artifact: {relative}")
        if path.stat().st_size > MAX_TRACKED_BYTES:
            errors.append(f"tracked file exceeds 5 MiB: {relative}")
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for finding in text_findings(path, text):
            errors.append(f"{finding}: {relative}")

    if errors:
        print("Public repository audit failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print(f"Public repository audit passed for {len(files)} files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
