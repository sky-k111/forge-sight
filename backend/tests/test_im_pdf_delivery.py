"""Weekly-report PDF delivery to bound Feishu and DingTalk accounts."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import (
    Base,
    DataScopePolicy,
    DingTalkBinding,
    DomainEventOutbox,
    FeishuBinding,
    Message,
    MessageRecipient,
    PlatformUser,
    Report,
    ReportDelivery,
    ReportFile,
    ReportSubscription,
    ReportVersion,
)
from app.delivery.im_provider import (
    CapturePdfProvider,
    DingTalkPdfProvider,
    FeishuPdfProvider,
    OutgoingPdf,
    ProviderFailure,
)
from app.delivery.im_service import run_pdf_delivery_worker_once
from app.delivery.service import consume_message_published_event, enqueue_report_pdf_delivery
from app.reports.contracts import SubscriptionCreate


def _session_factory():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _seed(factory, storage_root, *, file_status: str = "ready") -> bytes:
    payload = b"%PDF-1.4 weekly-report-pdf"
    relative = "version-im/weekly.pdf"
    path = storage_root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    now = datetime(2026, 8, 23, 9, 0)
    with factory() as session:
        session.add_all(
            [
                PlatformUser(
                    id="user-im",
                    username="user-im",
                    password_hash="test-only",
                    display_name="IM User",
                    role="business",
                    enabled=True,
                ),
                ReportSubscription(
                    id="subscription-im",
                    name="经营周报",
                    template_id="tpl-weekly",
                    source_id=1,
                    owner_id="user-im",
                    cadence="weekly",
                    weekday=1,
                    channel="feishu_dingtalk",
                ),
                FeishuBinding(
                    id="feishu-binding",
                    user_id="user-im",
                    tenant_key="tenant-a",
                    open_id="ou_feishu",
                    bind_status="bound",
                ),
                DingTalkBinding(
                    id="dingtalk-binding",
                    user_id="user-im",
                    corp_id="corp-a",
                    union_id="union-dingtalk",
                    open_id="open-dingtalk",
                    bind_status="bound",
                ),
                DataScopePolicy(
                    id="scope-user-im",
                    subject_type="user",
                    subject_id="user-im",
                    business_key="weekly_report",
                    data_source_ids_json=[1],
                    dimensions_json={},
                ),
            ]
        )
        report = Report(
            id="report-im",
            title="生产经营周报",
            template_id="tpl-weekly",
            source_id=1,
            report_type="weekly",
            subscription_id="subscription-im",
            status="published",
            owner_id="user-im",
            period_start=datetime(2026, 8, 17),
            period_end=datetime(2026, 8, 23),
            current_version_id="version-im",
            scope_json={"audience": {"userIds": ["user-im"]}},
        )
        version = ReportVersion(
            id="version-im",
            report_id=report.id,
            version=1,
            status="published",
            summary="本周生产保持稳定。",
            content_json={"schemaVersion": "weekly-report/v1"},
            content_checksum="content-checksum",
            published_at=now,
        )
        message = Message(
            id="message-im",
            message_type="weekly_report",
            source="report-agent",
            title=report.title,
            summary=version.summary,
            related_type="report",
            related_id=report.id,
            report_version_id=version.id,
            idempotency_key="inbox:report-im",
            published_at=now,
        )
        session.add_all(
            [
                report,
                version,
                message,
                MessageRecipient(
                    id="recipient-im",
                    message_id=message.id,
                    user_id="user-im",
                ),
                ReportFile(
                    id="pdf-im",
                    report_id=report.id,
                    version_id=version.id,
                    format="pdf",
                    status=file_status,
                    storage_uri=relative if file_status == "ready" else None,
                    mime_type="application/pdf" if file_status == "ready" else None,
                    file_size=len(payload) if file_status == "ready" else None,
                    checksum=hashlib.sha256(payload).hexdigest()
                    if file_status == "ready"
                    else None,
                    source_content_checksum="content-checksum",
                ),
                DomainEventOutbox(
                    id="event-im",
                    event_type="message.published",
                    aggregate_id=message.id,
                    idempotency_key="message.published:message-im",
                    payload_json={"messageId": message.id},
                    status="processing",
                    lease_owner="event-worker",
                    attempt_count=1,
                ),
            ]
        )
        session.commit()
    return payload


def test_subscription_contract_supports_both_im_channels():
    value = SubscriptionCreate(
        name="经营周报",
        templateId="tpl-weekly",
        sourceId=1,
        channel="feishu_dingtalk",
    )

    assert value.channel == "feishu_dingtalk"


def test_message_event_creates_idempotent_delivery_for_each_bound_channel(tmp_path):
    factory = _session_factory()
    _seed(factory, tmp_path)

    with factory() as session:
        consume_message_published_event(session, "event-im", "event-worker")
        session.commit()
        rows = session.query(ReportDelivery).order_by(ReportDelivery.channel).all()

        assert [(row.channel, row.recipient_address_snapshot) for row in rows] == [
            ("dingtalk", "union-dingtalk"),
            ("feishu", "ou_feishu"),
        ]
        assert all(row.status == "queued" for row in rows)

        event = session.get(DomainEventOutbox, "event-im")
        event.status = "processing"
        event.lease_owner = "event-worker-2"
        consume_message_published_event(session, event.id, "event-worker-2")
        session.commit()
        assert session.query(ReportDelivery).count() == 2


def test_manual_pdf_delivery_uses_the_same_durable_audited_job(tmp_path):
    factory = _session_factory()
    _seed(factory, tmp_path)
    with factory() as session:
        session.query(DomainEventOutbox).delete()
        session.query(MessageRecipient).delete()
        session.query(Message).delete()
        session.commit()

        first = enqueue_report_pdf_delivery(
            session,
            "report-im",
            "user-im",
            "feishu",
            now=datetime(2026, 8, 23, 9, 0),
        )
        second = enqueue_report_pdf_delivery(
            session,
            "report-im",
            "user-im",
            "feishu",
            now=datetime(2026, 8, 23, 9, 1),
        )
        session.commit()

        assert first.id == second.id
        assert first.status == "queued"
        assert first.recipient_address_snapshot == "ou_feishu"
        assert session.query(Message).count() == 1
        assert session.query(MessageRecipient).count() == 1
        assert session.query(ReportDelivery).count() == 1

    provider = CapturePdfProvider("feishu")
    assert run_pdf_delivery_worker_once(
        factory,
        "im-worker",
        {"feishu": provider},
        storage_root=tmp_path,
    )
    assert provider.messages[0].content.startswith(b"%PDF")


def test_workers_send_the_same_verified_pdf_to_feishu_and_dingtalk(tmp_path):
    factory = _session_factory()
    payload = _seed(factory, tmp_path)
    with factory() as session:
        consume_message_published_event(session, "event-im", "event-worker")
        session.commit()

    feishu = CapturePdfProvider("feishu")
    dingtalk = CapturePdfProvider("dingtalk")
    providers = {"feishu": feishu, "dingtalk": dingtalk}
    assert run_pdf_delivery_worker_once(
        factory, "im-worker", providers, storage_root=tmp_path
    )
    assert run_pdf_delivery_worker_once(
        factory, "im-worker", providers, storage_root=tmp_path
    )

    assert feishu.messages[0].content == payload
    assert feishu.messages[0].recipient_id == "ou_feishu"
    assert feishu.messages[0].filename.endswith(".pdf")
    assert dingtalk.messages[0].content == payload
    assert dingtalk.messages[0].recipient_id == "union-dingtalk"
    with factory() as session:
        rows = session.query(ReportDelivery).order_by(ReportDelivery.channel).all()
        assert all(row.status == "sent" for row in rows)
        assert all(row.attachment_file_id == "pdf-im" for row in rows)
        assert all(row.provider_message_id for row in rows)


def test_pdf_delivery_waits_until_export_is_ready(tmp_path):
    factory = _session_factory()
    payload = _seed(factory, tmp_path, file_status="queued")
    with factory() as session:
        consume_message_published_event(session, "event-im", "event-worker")
        session.commit()

    provider = CapturePdfProvider("feishu")
    assert run_pdf_delivery_worker_once(
        factory,
        "im-worker",
        {"feishu": provider},
        storage_root=tmp_path,
    )
    assert provider.messages == []
    with factory() as session:
        waiting = session.query(ReportDelivery).filter_by(channel="feishu").one()
        assert waiting.status == "waiting_attachment"
        pdf = session.get(ReportFile, "pdf-im")
        pdf.status = "ready"
        pdf.storage_uri = "version-im/weekly.pdf"
        pdf.mime_type = "application/pdf"
        pdf.file_size = len(payload)
        pdf.checksum = hashlib.sha256(payload).hexdigest()
        waiting.next_retry_at = datetime(2026, 8, 23, 9, 1)
        session.commit()

    assert run_pdf_delivery_worker_once(
        factory,
        "im-worker",
        {"feishu": provider},
        storage_root=tmp_path,
        now=datetime(2026, 8, 23, 9, 1),
    )
    assert provider.messages[0].content == payload


def test_platform_failure_is_retried_without_persisting_provider_detail(tmp_path):
    factory = _session_factory()
    _seed(factory, tmp_path)
    with factory() as session:
        consume_message_published_event(session, "event-im", "event-worker")
        dingtalk = session.query(ReportDelivery).filter_by(channel="dingtalk").one()
        dingtalk.status = "suppressed"
        session.commit()

    class FailingProvider:
        channel = "feishu"

        def send(self, _message):
            raise ProviderFailure(
                "network",
                "secret upstream response",
                retryable=True,
            )

    run_pdf_delivery_worker_once(
        factory,
        "im-worker",
        {"feishu": FailingProvider()},
        storage_root=tmp_path,
        now=datetime(2026, 8, 23, 9, 0),
    )

    with factory() as session:
        row = session.query(ReportDelivery).filter_by(channel="feishu").one()
        assert row.status == "retry_wait"
        assert row.attempt_count == 1
        assert row.next_retry_at > datetime(2026, 8, 23, 9, 0)
        assert "secret upstream response" not in str(row.error_json)


def test_feishu_provider_uploads_and_sends_native_pdf_file():
    requests = []

    def handler(request: httpx.Request):
        requests.append(request)
        if request.url.path.endswith("tenant_access_token/internal"):
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "token"})
        if request.url.path.endswith("/im/v1/files"):
            assert b"application/pdf" in request.read()
            return httpx.Response(200, json={"code": 0, "data": {"file_key": "file-key"}})
        body = json.loads(request.read())
        assert request.url.params["receive_id_type"] == "open_id"
        assert body["receive_id"] == "ou_test"
        assert body["msg_type"] == "file"
        assert json.loads(body["content"]) == {"file_key": "file-key"}
        assert body["uuid"] == "delivery-key"
        return httpx.Response(200, json={"code": 0, "data": {"message_id": "om_1"}})

    provider = FeishuPdfProvider(
        "app-id",
        "app-secret",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    result = provider.send(
        OutgoingPdf(
            channel="feishu",
            recipient_id="ou_test",
            filename="周报.pdf",
            content=b"%PDF-test",
            idempotency_key="delivery-key",
        )
    )

    assert result.provider_message_id == "om_1"
    assert len(requests) == 3


def test_dingtalk_provider_resolves_union_id_and_sends_native_pdf_file():
    final_payload = {}

    def handler(request: httpx.Request):
        nonlocal final_payload
        path = request.url.path
        if path.endswith("/v1.0/oauth2/accessToken"):
            return httpx.Response(200, json={"accessToken": "token"})
        if path.endswith("/topapi/user/getbyunionid"):
            assert json.loads(request.read()) == {"unionid": "union_test"}
            return httpx.Response(200, json={"errcode": 0, "result": {"userid": "staff_1"}})
        if path.endswith("/media/upload"):
            assert request.url.params["access_token"] == "token"
            assert b"application/pdf" in request.read()
            return httpx.Response(200, json={"errcode": 0, "media_id": "media_1"})
        final_payload = json.loads(request.read())
        return httpx.Response(200, json={"errcode": 0, "task_id": 987})

    provider = DingTalkPdfProvider(
        "client-id",
        "client-secret",
        "4888064142",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    result = provider.send(
        OutgoingPdf(
            channel="dingtalk",
            recipient_id="union_test",
            filename="周报.pdf",
            content=b"%PDF-test",
            idempotency_key="delivery-key",
        )
    )

    assert result.provider_message_id == "987"
    assert final_payload["agent_id"] == 4888064142
    assert final_payload["userid_list"] == "staff_1"
    assert final_payload["msg"] == {
        "msgtype": "file",
        "file": {"media_id": "media_1"},
    }
