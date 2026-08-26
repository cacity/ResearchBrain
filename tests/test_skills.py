import json
import zipfile
from io import BytesIO
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from researchbrain.api.app import create_app
from researchbrain.skills import SkillError, SkillRegistry


def make_skill(root: Path, name: str = "example-skill", *, script: bool = False) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                f"name: {name}",
                "description: A test Skill for ResearchBrain.",
                "---",
                "",
                "# Test Skill",
            ]
        ),
        encoding="utf-8",
    )
    agents = root / "agents"
    agents.mkdir()
    (agents / "openai.yaml").write_text(
        """interface:
  default_prompt: "Use $example-skill to inspect the current library."
dependencies:
  tools:
    - type: "mcp"
      value: "researchbrain"
      description: "ResearchBrain tools"
""",
        encoding="utf-8",
    )
    if script:
        scripts = root / "scripts"
        scripts.mkdir()
        (scripts / "run.py").write_text("print('test')\n", encoding="utf-8")
    return root


def test_registry_installs_enables_and_materializes_local_skill(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCHBRAIN_BUILTIN_SKILLS_DIR", str(tmp_path / "no-builtins"))
    source = make_skill(tmp_path / "source")
    registry = SkillRegistry(tmp_path / "data")

    installed = registry.install("local", str(source), enabled=True)
    deployed = registry.materialize(
        tmp_path / "workspace" / ".agents" / "skills",
        {"researchbrain-literature": "---\nname: researchbrain-literature\n---\n"},
    )

    assert installed["name"] == "example-skill"
    assert installed["compatibility"] == "compatible"
    assert installed["dependencies"][0]["value"] == "researchbrain"
    assert deployed == ["example-skill", "researchbrain-literature"]
    copied = tmp_path / "workspace" / ".agents" / "skills" / "example-skill" / "SKILL.md"
    assert copied.is_file()
    marker = json.loads(
        (tmp_path / "workspace" / ".agents" / "skills" / ".researchbrain-managed.json").read_text(
            encoding="utf-8"
        )
    )
    assert marker["skills"] == deployed

    registry.set_enabled("example-skill", False)
    registry.materialize(
        tmp_path / "workspace" / ".agents" / "skills",
        {"researchbrain-literature": "---\nname: researchbrain-literature\n---\n"},
    )
    assert not copied.exists()


def test_registry_marks_script_skill_for_review(tmp_path):
    source = make_skill(tmp_path / "source", script=True)
    record = SkillRegistry(tmp_path / "data").install("local", str(source))

    assert record["compatibility"] == "review_required"
    assert "执行 Skill 附带的本地脚本" in record["permissions"]


def test_registry_exposes_bundled_researchbrain_skills(tmp_path):
    names = {record["name"] for record in SkillRegistry(tmp_path / "data").list()}

    assert {
        "researchbrain-literature",
        "researchbrain-zotero-sync",
        "researchbrain-doi-fulltext",
        "researchbrain-pdf-ingest",
        "researchbrain-vector-index",
        "researchbrain-evidence-research",
    }.issubset(names)


def test_registry_blocks_tampered_managed_skill(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCHBRAIN_BUILTIN_SKILLS_DIR", str(tmp_path / "no-builtins"))
    source = make_skill(tmp_path / "source")
    registry = SkillRegistry(tmp_path / "data")
    record = registry.install("local", str(source), enabled=True)
    Path(record["managed_path"], "SKILL.md").write_text("tampered", encoding="utf-8")

    with pytest.raises(SkillError, match="integrity"):
        registry.materialize(
            tmp_path / "workspace" / ".agents" / "skills",
            {"researchbrain-literature": "---\nname: researchbrain-literature\n---\n"},
        )


def test_registry_rejects_invalid_skill_and_unsafe_zip(tmp_path):
    invalid = make_skill(tmp_path / "invalid", name="Invalid Name")
    registry = SkillRegistry(tmp_path / "data")
    with pytest.raises(SkillError, match="lowercase"):
        registry.install("local", str(invalid))

    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("../SKILL.md", "unsafe")
        package.writestr("..\\outside.txt", "unsafe")
    with pytest.raises(SkillError, match="unsafe"):
        registry.install("archive", str(archive))

    windows_archive = tmp_path / "unsafe-windows.zip"
    with zipfile.ZipFile(windows_archive, "w") as package:
        package.writestr("..\\outside.txt", "unsafe")
    with pytest.raises(SkillError, match="unsafe"):
        registry.install("archive", str(windows_archive))


def test_registry_installs_skill_from_github_archive(tmp_path):
    archive = BytesIO()
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr(
            "sample-main/SKILL.md",
            "---\nname: github-skill\ndescription: Downloaded test Skill.\n---\n",
        )

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://github.com/example/sample/archive/HEAD.zip"
        return httpx.Response(200, content=archive.getvalue())

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        record = SkillRegistry(tmp_path / "data", client=client).install(
            "github", "https://github.com/example/sample"
        )

    assert record["name"] == "github-skill"
    assert record["source_kind"] == "github"


def test_skill_api_lifecycle(settings, tmp_path):
    source = make_skill(tmp_path / "api-skill", name="api-skill")
    app = create_app(settings)
    with TestClient(app) as client:
        initial = client.get("/v1/skills")
        assert initial.status_code == 200
        assert initial.json()[0]["name"] == "researchbrain-literature"

        installed = client.post(
            "/v1/skills",
            json={"source_kind": "local", "source": str(source), "enabled": False},
        )
        assert installed.status_code == 201
        assert installed.json()["enabled"] is False

        enabled = client.put("/v1/skills/api-skill/enabled", json={"enabled": True})
        assert enabled.status_code == 200
        assert enabled.json()["enabled"] is True

        removed = client.delete("/v1/skills/api-skill")
        assert removed.status_code == 204
        assert "api-skill" not in [value["name"] for value in client.get("/v1/skills").json()]
