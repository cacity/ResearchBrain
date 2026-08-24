import time
from dataclasses import replace

from fastapi.testclient import TestClient
from sqlalchemy import select

from researchbrain.api.app import create_app
from researchbrain.db.models import Attachment, ChatMessage, ChatSession, Item, Job
from researchbrain.domain import ReferenceRecord
from researchbrain.fulltext.discovery import (
    OpenAlexFullTextProvider,
    PmcFullTextProvider,
    UnpaywallProvider,
)
from researchbrain.library.repository import LibraryRepository
from researchbrain.metadata.crossref import CrossrefProvider
from researchbrain.secrets import SecretStore


def test_health_and_doi_batch(settings):
    app = create_app(settings)
    with TestClient(app) as client:
        health = client.get("/v1/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"

        created = client.post("/v1/libraries", json={"name": "Test", "mode": "standalone"})
        assert created.status_code == 201
        library_id = created.json()["id"]

        batch = client.post(
            "/v1/imports/doi",
            json={
                "library_id": library_id,
                "dois": ["10.1000/test", "https://doi.org/10.1000/TEST", "invalid"],
                "include_si": False,
            },
        )
        assert batch.status_code == 202
        body = batch.json()
        assert body["total"] == 1
        assert len(body["input_errors"]) == 1

        jobs = client.get("/v1/jobs").json()
        assert len(jobs) == 1
        assert jobs[0]["payload"]["doi"] == "10.1000/test"

        repeated = client.post(
            "/v1/imports/doi",
            json={"library_id": library_id, "dois": ["10.1000/test"], "include_si": False},
        )
        assert repeated.status_code == 202
        assert len(client.get("/v1/jobs").json()) == 2

        mirror = client.post(
            "/v1/libraries",
            json={"name": "Zotero Mirror", "mode": "zotero_mirror"},
        )
        mirror_id = mirror.json()["id"]
        queued = client.post(f"/v1/libraries/{mirror_id}/zotero/sync")
        duplicate = client.post(f"/v1/libraries/{mirror_id}/zotero/sync")
        assert queued.status_code == 202
        assert duplicate.json()["id"] == queued.json()["id"]

        rejected = client.post(f"/v1/libraries/{library_id}/zotero/sync")
        assert rejected.status_code == 409


def test_harness_status_is_available_without_installing_runtime(settings):
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.get("/v1/harness/status")

        assert response.status_code == 200
        body = response.json()
        assert body["configured"] is False
        assert body["running"] is False
        assert body["port"] == 3080
        assert body["url"] == "http://127.0.0.1:3080"
        assert body["dsh_package"].startswith("@deepseek-ai/dsh@")


def test_local_session_token_is_enforced(settings, monkeypatch):
    monkeypatch.setenv("RESEARCHBRAIN_SESSION_TOKEN", "test-token")
    app = create_app(settings)
    with TestClient(app) as client:
        assert client.get("/v1/health").status_code == 401
        assert (
            client.get(
                "/v1/health",
                headers={"Authorization": "Bearer test-token"},
            ).status_code
            == 200
        )


def test_manual_pdf_upload_is_stored_and_queued(settings):
    app = create_app(settings)
    with TestClient(app) as client:
        library_id = client.post(
            "/v1/libraries",
            json={"name": "Manual PDFs", "mode": "standalone"},
        ).json()["id"]
        with app.state.researchbrain.database.session() as session:
            item = Item(library_id=library_id, title="Uploaded paper")
            session.add(item)
            session.flush()
            item_id = item.id

        response = client.post(
            f"/v1/items/{item_id}/attachments",
            files={"file": ("paper.pdf", b"%PDF-1.7\nmanual fixture", "application/pdf")},
        )

        assert response.status_code == 201
        assert response.json()["bytes"] == 23
        attachments = client.get(f"/v1/items/{item_id}/attachments")
        assert attachments.status_code == 200
        assert attachments.json()[0]["logical_name"] == "paper.pdf"
        assert len(attachments.json()[0]["sha256"]) == 64
        content = client.get(f"/v1/attachments/{attachments.json()[0]['id']}/content")
        assert content.status_code == 200
        assert content.content == b"%PDF-1.7\nmanual fixture"
        with app.state.researchbrain.database.session() as session:
            attachment = session.scalar(select(Attachment).where(Attachment.item_id == item_id))
            job = session.get(Job, response.json()["parse_job_id"])
            assert attachment is not None
            assert attachment.source_url == "manual-upload"
            assert job is not None
            assert job.job_type == "parse_document"
            assert job.status == "queued"

        listed = client.get(f"/v1/libraries/{library_id}/items").json()
        assert listed[0]["pdf_status"] == "ready"
        assert listed[0]["parse_status"] == "queued"
        assert listed[0]["embedding_status"] == "none"
        assert listed[0]["metadata_embedding_status"] == "none"
        assert listed[0]["fulltext_embedding_status"] == "none"
        assert listed[0]["knowledge_state"] == "pdf_stored"
        assert listed[0]["next_action"] == "parse_pdf"


def test_item_fulltext_endpoint_queues_and_retries_doi_download(settings):
    app = create_app(settings)
    with TestClient(app) as client:
        library_id = client.post(
            "/v1/libraries",
            json={"name": "DOI full text", "mode": "standalone"},
        ).json()["id"]
        with app.state.researchbrain.database.session() as session:
            repository = LibraryRepository(session)
            item, _ = repository.add_reference(
                library_id,
                ReferenceRecord(
                    title="Open paper",
                    identifiers={"doi": "10.1000/open-paper"},
                ),
                "fixture",
            )
            without_doi, _ = repository.add_reference(
                library_id,
                ReferenceRecord(title="Paper without DOI"),
                "fixture",
            )
            item_id = item.id
            without_doi_id = without_doi.id

        queued = client.post(f"/v1/items/{item_id}/fulltext")

        assert queued.status_code == 202
        assert queued.json()["status"] == "queued"
        assert queued.json()["job_type"] == "resolve_fulltext"
        assert queued.json()["doi"] == "10.1000/open-paper"
        assert queued.json()["requeued"] is False
        job_id = queued.json()["id"]
        listed = client.get(f"/v1/libraries/{library_id}/items").json()
        current = next(value for value in listed if value["id"] == item_id)
        assert current["pdf_status"] == "queued"

        repeated = client.post(f"/v1/items/{item_id}/fulltext")
        assert repeated.json()["id"] == job_id
        assert repeated.json()["requeued"] is False

        with app.state.researchbrain.database.session() as session:
            job = session.get(Job, job_id)
            assert job is not None
            job.status = "review_required"
            job.error_code = "no_oa_fulltext"
            job.error_message = "No downloadable open PDF"
        listed = client.get(f"/v1/libraries/{library_id}/items").json()
        current = next(value for value in listed if value["id"] == item_id)
        assert current["pdf_status"] == "review_required"

        retried = client.post(f"/v1/items/{item_id}/fulltext")
        assert retried.json()["id"] == job_id
        assert retried.json()["status"] == "queued"
        assert retried.json()["requeued"] is True

        missing = client.post(f"/v1/items/{without_doi_id}/fulltext")
        assert missing.status_code == 422
        assert missing.json()["detail"]["code"] == "doi_missing"


def test_doi_lookup_groups_zotero_duplicates_and_checks_exact_pdf(settings):
    app = create_app(settings)
    sha256 = "a" * 64
    with TestClient(app) as client:
        library_id = client.post(
            "/v1/libraries",
            json={"name": "DOI coverage", "mode": "zotero_mirror"},
        ).json()["id"]
        with app.state.researchbrain.database.session() as session:
            repository = LibraryRepository(session)
            first, _ = repository.add_reference(
                library_id,
                ReferenceRecord(title="First copy", identifiers={"doi": "10.1000/TEST"}),
                "fixture",
                deduplicate=False,
            )
            repository.add_reference(
                library_id,
                ReferenceRecord(title="Second copy", identifiers={"doi": "10.1000/test"}),
                "fixture",
                deduplicate=False,
            )
            session.add(
                Attachment(
                    item_id=first.id,
                    sha256=sha256,
                    logical_name="known.pdf",
                    object_path="objects/aa/known.pdf",
                    mime="application/pdf",
                    status="stored",
                )
            )

        response = client.post(
            f"/v1/libraries/{library_id}/items/lookup",
            json={"doi": "https://doi.org/10.1000/TEST", "pdf_sha256": sha256.upper()},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["found"] is True
        assert body["canonical_key"] == "doi:10.1000/test"
        assert body["exact_pdf_known"] is True
        assert body["recommended_action"] == "parse_pdf"
        assert len(body["matches"]) == 2
        assert {match["canonical_key"] for match in body["matches"]} == {"doi:10.1000/test"}

        missing = client.post(
            f"/v1/libraries/{library_id}/items/lookup",
            json={"doi": "10.2000/missing"},
        ).json()
        assert missing["found"] is False
        assert missing["recommended_action"] == "import_metadata_and_pdf"


def test_empty_library_chat_returns_a_visible_readiness_answer(settings):
    app = create_app(settings)
    with TestClient(app) as client:
        library_id = client.post(
            "/v1/libraries",
            json={"name": "Empty", "mode": "standalone"},
        ).json()["id"]
        chat_id = client.post(
            "/v1/chat/sessions",
            json={"library_id": library_id, "title": "New research"},
        ).json()["id"]

        response = client.post(
            f"/v1/chat/sessions/{chat_id}/messages",
            json={"content": "有什么研究成果？", "evidence_limit": 10},
        )

        assert response.status_code == 200
        assert "当前文库没有" in response.json()["content"]
        assert response.json()["model"] == "local-readiness-check"
        messages = client.get(f"/v1/chat/sessions/{chat_id}/messages").json()
        assert [message["role"] for message in messages] == ["user", "assistant"]


def test_chat_sessions_are_persisted_and_listed_with_latest_message(settings):
    app = create_app(settings)
    with TestClient(app) as client:
        library_id = client.post(
            "/v1/libraries",
            json={"name": "History", "mode": "standalone"},
        ).json()["id"]
        first_id = client.post(
            "/v1/chat/sessions",
            json={"library_id": library_id, "title": "First topic"},
        ).json()["id"]
        second_id = client.post(
            "/v1/chat/sessions",
            json={"library_id": library_id, "title": "Most recent topic"},
        ).json()["id"]
        with app.state.researchbrain.database.session() as session:
            session.add_all(
                [
                    ChatMessage(session_id=first_id, role="user", content="Older question"),
                    ChatMessage(session_id=second_id, role="user", content="Latest question"),
                    ChatMessage(
                        session_id=second_id,
                        role="assistant",
                        content="Latest evidence-grounded answer",
                        citations=[{"id": "E1"}],
                        model="fixture",
                    ),
                ]
            )
            first = session.get(ChatSession, first_id)
            second = session.get(ChatSession, second_id)
            assert first is not None and second is not None
            first.updated_at = first.created_at
            second.updated_at = second.created_at.replace(year=second.created_at.year + 1)

        response = client.get(f"/v1/chat/sessions?library_id={library_id}")

        assert response.status_code == 200
        sessions = response.json()
        assert [value["id"] for value in sessions] == [second_id, first_id]
        assert sessions[0]["message_count"] == 2
        assert sessions[0]["last_message_preview"] == "Latest evidence-grounded answer"
        restored = client.get(f"/v1/chat/sessions/{second_id}/messages").json()
        assert [message["content"] for message in restored] == [
            "Latest question",
            "Latest evidence-grounded answer",
        ]


def test_chat_reports_missing_embedding_key_instead_of_hanging(settings, monkeypatch):
    monkeypatch.setattr(SecretStore, "get", lambda _self, _name: "")
    app = create_app(settings)
    with TestClient(app) as client:
        library_id = client.post(
            "/v1/libraries",
            json={"name": "Metadata", "mode": "standalone"},
        ).json()["id"]
        with app.state.researchbrain.database.session() as session:
            session.add(Item(library_id=library_id, title="A paper", abstract="Evidence"))
        chat_id = client.post(
            "/v1/chat/sessions",
            json={"library_id": library_id, "title": "New research"},
        ).json()["id"]

        response = client.post(
            f"/v1/chat/sessions/{chat_id}/messages",
            json={"content": "总结一下", "evidence_limit": 10},
        )

        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "api_key_missing"


def test_bulk_retry_and_zotero_sync_status(settings):
    app = create_app(settings)
    with TestClient(app) as client:
        mirror_id = client.post(
            "/v1/libraries",
            json={"name": "Zotero", "mode": "zotero_mirror"},
        ).json()["id"]
        queued = client.post(f"/v1/libraries/{mirror_id}/zotero/sync").json()
        status = client.get(f"/v1/libraries/{mirror_id}/zotero/sync-status").json()
        assert status["last_version"] == 0
        assert status["counts"] == {
            "items": 0,
            "pdf_ready": 0,
            "parsed": 0,
            "embedded": 0,
        }
        assert status["job"]["id"] == queued["id"]
        assert status["job"]["status"] == "queued"

        with app.state.researchbrain.database.session() as session:
            job = session.get(Job, queued["id"])
            assert job is not None
            job.status = "failed"
            job.error_code = "zotero_unavailable"
        retried = client.post(
            "/v1/jobs/retry-failed",
            json={"library_id": mirror_id, "job_types": ["zotero_sync"]},
        )
        assert retried.json() == {"retried": 1}
        assert client.get("/v1/jobs").json()[0]["status"] == "queued"


def test_discovery_import_accepts_record_without_doi(settings):
    app = create_app(settings)
    with TestClient(app) as client:
        library_id = client.post(
            "/v1/libraries",
            json={"name": "Discovery", "mode": "standalone"},
        ).json()["id"]
        response = client.post(
            "/v1/discovery/import",
            json={
                "library_id": library_id,
                "records": [
                    {
                        "source": "arxiv",
                        "source_id": "2608.00001v1",
                        "title": "A preprint without DOI",
                        "authors": ["Ada Lovelace"],
                        "year": 2026,
                        "venue": "arXiv",
                        "abstract": "Testable abstract.",
                        "url": "https://arxiv.org/abs/2608.00001v1",
                        "sources": ["arxiv"],
                        "identifiers": {"arxiv": "2608.00001v1"},
                        "is_oa": False,
                    }
                ],
            },
        )

        assert response.status_code == 202
        assert response.json()["created"] == 1
        assert response.json()["fulltext_queued"] == 0
        jobs = client.get("/v1/jobs").json()
        assert jobs[0]["job_type"] == "embed_metadata"
        listed = client.get(f"/v1/libraries/{library_id}/items").json()
        assert listed[0]["title"] == "A preprint without DOI"


def test_embedded_worker_consumes_queued_jobs(settings, monkeypatch):
    async def resolve_doi(_self, doi: str) -> ReferenceRecord:
        return ReferenceRecord(title="Resolved in background", identifiers={"doi": doi})

    async def discover(_self, _doi: str):
        return []

    monkeypatch.setattr(CrossrefProvider, "resolve_doi", resolve_doi)
    monkeypatch.setattr(UnpaywallProvider, "discover", discover)
    monkeypatch.setattr(OpenAlexFullTextProvider, "discover", discover)
    monkeypatch.setattr(PmcFullTextProvider, "discover", discover)
    app = create_app(replace(settings, worker_enabled=True, worker_poll_seconds=0.01))
    with TestClient(app) as client:
        library_id = client.post(
            "/v1/libraries",
            json={"name": "Background worker", "mode": "standalone"},
        ).json()["id"]
        client.post(
            "/v1/imports/doi",
            json={"library_id": library_id, "dois": ["10.1000/background"], "include_si": False},
        )

        deadline = time.monotonic() + 2
        metadata_job = None
        while time.monotonic() < deadline:
            jobs = client.get("/v1/jobs").json()
            metadata_job = next(
                (job for job in jobs if job["job_type"] == "resolve_metadata"),
                None,
            )
            if metadata_job and metadata_job["status"] == "complete":
                break
            time.sleep(0.02)

        assert metadata_job is not None
        assert metadata_job["status"] == "complete"


def test_public_config_and_credentials_can_be_updated(settings, monkeypatch):
    saved_credentials: dict[str, str] = {}
    monkeypatch.setattr(
        SecretStore,
        "set",
        lambda _self, name, value: saved_credentials.__setitem__(name, value),
    )
    monkeypatch.setattr(
        SecretStore,
        "status",
        lambda _self: {
            "minimax_api_key": "minimax_api_key" in saved_credentials,
            "deepseek_api_key": "deepseek_api_key" in saved_credentials,
        },
    )
    app = create_app(settings)
    with TestClient(app) as client:
        updated = client.put(
            "/v1/config",
            json={"contact_email": " researcher@example.org ", "minimax_group_id": " group-1 "},
        )
        credential = client.put(
            "/v1/config/credential",
            json={"name": "deepseek_api_key", "value": " secret-value "},
        )
        status = client.get("/v1/config/status").json()

        assert updated.json() == {
            "contact_email": "researcher@example.org",
            "minimax_group_id": "group-1",
            "zotero_data_dir": str(settings.zotero_data_dir),
            "mineru_executable": "mineru",
            "retried_fulltext": 0,
        }
        assert credential.json()["configured"] is True
        assert credential.json()["retried"] == 0
        assert saved_credentials == {"deepseek_api_key": "secret-value"}
        assert status["contact_email"] == "researcher@example.org"
        assert status["secrets"]["deepseek_api_key"] is True


def test_saving_contact_email_retries_matching_fulltext_jobs(settings):
    app = create_app(settings)
    with TestClient(app) as client:
        with app.state.researchbrain.database.session() as session:
            job = Job(
                job_type="resolve_fulltext",
                status="failed",
                idempotency_key="contact-email-retry",
                payload={"library_id": "library", "item_id": "item", "doi": "10.1000/test"},
                error_code="contact_email_missing",
                error_message="Unpaywall requires a contact email",
            )
            session.add(job)
            session.flush()
            job_id = job.id

        response = client.put(
            "/v1/config",
            json={"contact_email": "researcher@example.org"},
        )

        assert response.json()["retried_fulltext"] == 1
        with app.state.researchbrain.database.session() as session:
            retried = session.get(Job, job_id)
            assert retried is not None
            assert retried.status == "queued"
            assert retried.error_code == ""
