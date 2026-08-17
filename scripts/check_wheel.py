from __future__ import annotations

import sys
import tomllib
import zipfile
from email.parser import BytesParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = {
    "researchbrain/library/repository.py",
    "researchbrain/migrations/env.py",
    "researchbrain/migrations/script.py.mako",
    "researchbrain/migrations/versions/20260816_0004_chat_sessions.py",
    "researchbrain/migrations/versions/20260817_0005_identifier_scope.py",
    "researchbrain/migrations/versions/20260817_0006_item_embeddings.py",
    "researchbrain/runtime/manager.py",
}
FORBIDDEN_SUFFIXES = {".db", ".exe", ".pdf", ".sqlite", ".sqlite3"}


def main() -> int:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version = str(project["project"]["version"])
    wheel = ROOT / "dist" / f"researchbrain-{version}-py3-none-any.whl"
    if not wheel.is_file():
        print(f"Expected ResearchBrain {version} wheel was not found under dist/.")
        return 1
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        missing = sorted(REQUIRED - names)
        forbidden = sorted(name for name in names if Path(name).suffix.lower() in FORBIDDEN_SUFFIXES)
        metadata_name = next((name for name in names if name.endswith(".dist-info/METADATA")), "")
        license_name = next((name for name in names if name.endswith(".dist-info/licenses/LICENSE")), "")
        if not metadata_name:
            missing.append("*.dist-info/METADATA")
            license_expression = ""
        else:
            metadata = BytesParser().parsebytes(archive.read(metadata_name))
            license_expression = str(metadata.get("License-Expression") or "")
        if not license_name:
            missing.append("*.dist-info/licenses/LICENSE")

    errors = [*(f"missing: {name}" for name in missing)]
    errors.extend(f"forbidden artifact: {name}" for name in forbidden)
    if license_expression != "AGPL-3.0-only":
        errors.append(f"unexpected license expression: {license_expression or '<missing>'}")
    if errors:
        print(f"Wheel audit failed for {wheel.name}:")
        for error in errors:
            print(f"- {error}")
        return 1
    print(f"Wheel audit passed: {wheel.name} ({len(names)} files).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
