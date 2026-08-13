"""V07: authenticated report-file download authorization and response metadata."""

from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import (
    Base,
    DataScopePolicy,
    Message,
    MessageRecipient,
    PlatformUser,
    Report,
    ReportFile,
    ReportVersion,
)
from app.api import reports as reports_api
from app.auth.deps import require_user
from app.main import app
from app.reports.files import (
    FileAccessDenied,
    FileNotReady,
    resolve_download,
)
from app.reports import service as report_service


@pytest.fixture()
def file_session(tmp_path):
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    storage_root = tmp_path / "report-files"
    stored = storage_root / "version-1" / "weekly.pdf"
    stored.parent.mkdir(parents=True)
    stored.write_bytes(b"%PDF-safe")
    with session_factory() as session:
        session.add_all(
            [
                Report(
                    id="report-1",
                    title="生产经营周报 / 第32周",
                    template_id="tpl-weekly",
                    source_id=1,
                    owner_id="owner-name",
                    status="published",
                    current_version_id="version-1",
                    period_start=datetime(2026, 8, 3),
                    period_end=datetime(2026, 8, 10),
                ),
                ReportVersion(
                    id="version-1",
                    report_id="report-1",
                    version=1,
                    status="published",
                ),
                ReportFile(
                    id="file-ready",
                    report_id="report-1",
                    version_id="version-1",
                    format="pdf",
                    status="ready",
                    storage_uri="version-1/weekly.pdf",
                    mime_type="application/pdf",
                    file_size=9,
                    checksum="checksum",
                ),
                ReportFile(
                    id="file-queued",
                    report_id="report-1",
                    version_id="version-1",
                    format="xlsx",
                    status="queued",
                ),
                Message(
                    id="message-1",
                    message_type="weekly_report",
                    source="report-agent",
                    title="第32周周报",
                    report_version_id="version-1",
                    idempotency_key="message:version-1",
                ),
                MessageRecipient(
                    id="recipient-1",
                    message_id="message-1",
                    user_id="recipient-id",
                ),
                DataScopePolicy(
                    id="policy-owner",
                    subject_type="user",
                    subject_id="owner-name",
                    business_key="weekly_report",
                    effect="allow",
                    data_source_ids_json=[1],
                ),
                DataScopePolicy(
                    id="policy-recipient",
                    subject_type="user",
                    subject_id="recipient-id",
                    business_key="weekly_report",
                    effect="allow",
                    data_source_ids_json=[1],
                ),
            ]
        )
        session.commit()
    return session_factory, storage_root


def test_owner_admin_and_message_recipient_can_resolve_download(file_session):
    session_factory, storage_root = file_session
    identities = [
        {"id": "owner-id", "username": "owner-name", "role": "business"},
        {"id": "admin-id", "username": "admin", "role": "admin"},
        {"id": "recipient-id", "username": "recipient", "role": "business"},
    ]

    with session_factory() as session:
        for user in identities:
            download = resolve_download(session, "file-ready", user, storage_root)
            assert download.path.read_bytes() == b"%PDF-safe"
            assert download.mime_type == "application/pdf"
            assert download.filename == "生产经营周报-第32周-v1.pdf"


def test_unrelated_user_cannot_download_and_storage_path_never_escapes_root(file_session):
    session_factory, storage_root = file_session

    with session_factory() as session:
        with pytest.raises(FileAccessDenied):
            resolve_download(
                session,
                "file-ready",
                {"id": "stranger-id", "username": "stranger", "role": "business"},
                storage_root,
            )
        session.get(ReportFile, "file-ready").storage_uri = "../secret.txt"
        session.flush()
        with pytest.raises(FileAccessDenied):
            resolve_download(
                session,
                "file-ready",
                {"id": "admin-id", "username": "admin", "role": "admin"},
                storage_root,
            )


def test_not_ready_file_returns_explicit_status(file_session):
    session_factory, storage_root = file_session

    with session_factory() as session, pytest.raises(FileNotReady) as caught:
        resolve_download(
            session,
            "file-queued",
            {"id": "owner-id", "username": "owner-name", "role": "business"},
            storage_root,
        )

    assert caught.value.status == "queued"


def test_revoked_data_scope_blocks_owner_and_historical_recipient(file_session):
    session_factory, storage_root = file_session

    with session_factory() as session:
        session.get(DataScopePolicy, "policy-owner").enabled = False
        session.get(DataScopePolicy, "policy-recipient").enabled = False
        session.flush()

        for user in (
            {"id": "owner-id", "username": "owner-name", "role": "business"},
            {"id": "recipient-id", "username": "recipient", "role": "business"},
        ):
            with pytest.raises(FileAccessDenied):
                resolve_download(session, "file-ready", user, storage_root)


def test_download_api_requires_login_and_returns_safe_attachment(file_session, monkeypatch):
    session_factory, storage_root = file_session
    monkeypatch.setattr(reports_api, "PlatformSession", session_factory)
    monkeypatch.setattr(reports_api.settings, "report_storage_dir", str(storage_root))
    audit_calls = []
    monkeypatch.setattr(reports_api, "write_audit", lambda **values: audit_calls.append(values))
    client = TestClient(app)

    anonymous = client.get("/api/v1/report-files/file-ready/download")
    assert anonymous.status_code == 401

    app.dependency_overrides[require_user] = lambda: {
        "id": "owner-id",
        "username": "owner-name",
        "displayName": "周报负责人",
        "role": "business",
    }
    try:
        response = client.get("/api/v1/report-files/file-ready/download")
    finally:
        app.dependency_overrides.pop(require_user, None)

    assert response.status_code == 200
    assert response.content == b"%PDF-safe"
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"].startswith("attachment;")
    assert "storage_uri" not in response.text
    assert audit_calls[0]["resource"] == "report:report-1/file:file-ready"


def test_report_detail_exposes_file_state_and_opaque_download_url(file_session):
    session_factory, _ = file_session

    report = report_service.get_report("report-1", session_factory=session_factory)

    ready = next(item for item in report["files"] if item["id"] == "file-ready")
    assert ready == {
        "id": "file-ready",
        "format": "pdf",
        "status": "ready",
        "mimeType": "application/pdf",
        "fileSize": 9,
        "checksum": "checksum",
        "generatedAt": None,
        "error": None,
        "downloadUrl": "/api/v1/report-files/file-ready/download",
    }
    assert "storageUri" not in ready


def test_report_detail_api_rejects_forwarded_link_for_unrelated_user(
    file_session, monkeypatch
):
    session_factory, _ = file_session
    monkeypatch.setattr(reports_api, "PlatformSession", session_factory)
    monkeypatch.setattr(report_service, "PlatformSession", session_factory)
    client = TestClient(app)
    app.dependency_overrides[require_user] = lambda: {
        "id": "stranger-id",
        "username": "stranger",
        "displayName": "无权限用户",
        "role": "business",
    }
    try:
        denied = client.get("/api/v1/reports/report-1")
        denied_export = client.get("/api/v1/reports/report-1/export")
        app.dependency_overrides[require_user] = lambda: {
            "id": "recipient-id",
            "username": "recipient",
            "displayName": "接收人",
            "role": "business",
        }
        allowed = client.get("/api/v1/reports/report-1")
    finally:
        app.dependency_overrides.pop(require_user, None)

    assert denied.status_code == 404
    assert denied_export.status_code == 404
    assert allowed.status_code == 200
    assert allowed.json()["version"]["id"] == "version-1"


def test_report_owner_stable_id_cannot_be_claimed_by_colliding_username(file_session):
    session_factory, storage_root = file_session
    with session_factory() as session:
        report = session.get(Report, "report-1")
        report.owner_id = "stable-owner-id"
        session.add_all(
            [
                PlatformUser(
                    id="stable-owner-id",
                    username="real-owner",
                    password_hash="test-only",
                ),
                PlatformUser(
                    id="attacker-id",
                    username="stable-owner-id",
                    password_hash="test-only",
                ),
                DataScopePolicy(
                    id="policy-attacker",
                    subject_type="user",
                    subject_id="attacker-id",
                    business_key="weekly_report",
                    effect="allow",
                    data_source_ids_json=[1],
                ),
            ]
        )
        session.commit()

        with pytest.raises(FileAccessDenied):
            resolve_download(
                session,
                "file-ready",
                {
                    "id": "attacker-id",
                    "username": "stable-owner-id",
                    "role": "business",
                },
                storage_root,
            )
