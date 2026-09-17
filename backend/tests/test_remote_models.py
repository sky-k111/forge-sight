"""Remote model lifecycle through the real MCP JSON-RPC client."""

import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import remote_models as api
from app.auth.deps import require_user
from app.config import settings
from app.db.models import Base, DataSource, RemoteModelCall
from app.modeling import remote, mcp_client
from app.tenancy.service import TenantContext, require_tenant_admin, tenant_context
from scripts.mcp_model_demo import TOOL

ADMIN = TenantContext("tenant-default", "Test", "test", "owner", "user1")
CONFIG = {"name": "产量预测", "sourceId": 1, "endpoint": "http://model.test/mcp", "toolName": "predict_output",
          "version": "1", "description": "预测产量", "datasetSql": "SELECT hours FROM features",
          "fieldMapping": {"hours": "hours"}}


@pytest.fixture()
def env(monkeypatch):
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(remote, "PlatformSession", factory)
    monkeypatch.setattr(settings, "mcp_model_allowed_origins", ["http://model.test"])
    with factory() as session:
        session.add(DataSource(id=1, name="Test", type="mysql", db_name="test", username="reader"))
        session.commit()
    seen = []
    def handle(request):
        body = json.loads(request.content); seen.append(body)
        method = body["method"]
        if method == "notifications/initialized":
            return httpx.Response(202)
        if method == "initialize":
            result = {"protocolVersion": "2025-11-25", "capabilities": {"tools": {}}}
        elif method == "tools/list":
            result = {"tools": [TOOL]}
        else:
            result = {"structuredContent": {"predictions": [row["hours"]*10 for row in body["params"]["arguments"]["records"]]}}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})
    original_client = httpx.Client
    monkeypatch.setattr(mcp_client.httpx, "Client", lambda **kwargs: original_client(transport=httpx.MockTransport(handle), **kwargs))
    yield factory, seen
    engine.dispose()


def test_full_lifecycle_and_data_projection(env, monkeypatch):
    factory, seen = env
    asset = remote.save_asset(remote.RemoteConfig(**CONFIG), ADMIN)
    with pytest.raises(remote.RemoteModelError):
        remote.transition(asset["id"], ADMIN, "published")
    response = remote.invoke(asset["id"], ADMIN, [{"hours": 8, "secret": "not-sent"}], validate=True)
    assert response["predictions"] == [80]
    assert seen[-1]["params"]["arguments"] == {"records": [{"hours": 8}]}
    remote.transition(asset["id"], ADMIN, "published")
    monkeypatch.setattr(remote, "execute_readonly", lambda *args, **kwargs: SimpleNamespace(ok=True, columns=["hours"], rows=[[6]]))
    assert remote.invoke(asset["id"], ADMIN)["stats"]["mean"] == 60
    with pytest.raises(remote.RemoteModelError):
        remote.save_asset(remote.RemoteConfig(**CONFIG), ADMIN, asset["id"])
    remote.transition(asset["id"], ADMIN, "retired")
    with pytest.raises(remote.RemoteModelError):
        remote.invoke(asset["id"], ADMIN, [{"hours": 1}])
    updated = remote.save_asset(remote.RemoteConfig(**CONFIG), ADMIN, asset["id"])
    assert updated["status"] == "draft" and not updated["validation"]
    with factory() as session:
        assert session.query(RemoteModelCall).count() == 2


def test_failed_schema_and_audit(env):
    factory, _ = env
    asset = remote.save_asset(remote.RemoteConfig(**CONFIG), ADMIN)
    with pytest.raises(remote.RemoteModelError, match="Schema"):
        remote.invoke(asset["id"], ADMIN, [{"hours": "bad"}], validate=True)
    with factory() as session:
        assert session.query(RemoteModelCall).one().status == "failed"
    assert remote.list_assets(ADMIN, 1)[0]["status"] == "draft"


def test_tenant_isolation_and_http_lifecycle(env):
    app = FastAPI(); app.include_router(api.router)
    app.dependency_overrides[require_user] = lambda: {"id": "user1"}
    app.dependency_overrides[tenant_context] = lambda: ADMIN
    app.dependency_overrides[require_tenant_admin] = lambda: ADMIN
    client = TestClient(app)
    base = "/api/v1/modeling/remote-models"
    asset = client.post(base, json=CONFIG).json()
    assert client.post(f"{base}/{asset['id']}/validate", json={"records": [{"hours": 2}]}).status_code == 200
    assert client.post(f"{base}/{asset['id']}/publish").status_code == 200
    assert client.post(f"{base}/{asset['id']}/predict", json={"records": [{"hours": 3}]}).json()["predictions"] == [30]
    assert len(client.get(f"{base}/{asset['id']}/calls").json()["items"]) == 2
    app.dependency_overrides[tenant_context] = lambda: TenantContext("other", "Other", "other", "member", "user2")
    assert client.post(f"{base}/{asset['id']}/predict", json={"records": [{"hours": 3}]}).status_code == 404


@pytest.mark.parametrize("url", ["https://evil.test/mcp", "file:///tmp/model", "http://user:pass@model.test/mcp", "http://model.test/mcp?secret=x"])
def test_endpoint_denied(env, url):
    with pytest.raises(remote.RemoteModelError):
        mcp_client.check_endpoint(url)


@pytest.mark.parametrize("values", [[True], [float("nan")], [], ["hello"]])
def test_invalid_regression_output(values):
    with pytest.raises(remote.RemoteModelError):
        remote.check_predictions({"predictions": values}, {"outputKey": "predictions", "taskType": "regression"}, 1)


def test_external_schema_reference_never_fetched():
    with pytest.raises(remote.RemoteModelError):
        remote.validate_schema({"$ref": "http://untrusted.test/schema"}, {})


def test_agent_discovers_and_executes_published_remote_model(env, monkeypatch):
    from app.modeling import service
    from app.agent.capabilities.prediction import _run
    monkeypatch.setattr(service, "PlatformSession", env[0])
    monkeypatch.setattr(remote, "context_for_user", lambda *args: ADMIN)
    monkeypatch.setattr(remote, "execute_readonly", lambda *args, **kwargs: SimpleNamespace(ok=True, columns=["hours"], rows=[[7]]))
    asset = remote.save_asset(remote.RemoteConfig(**CONFIG), ADMIN)
    assert service.find_reusable_models("预测产量", 1, viewer_id="user1") == []
    remote.invoke(asset["id"], ADMIN, [{"hours": 1}], validate=True)
    remote.transition(asset["id"], ADMIN, "published")
    context = SimpleNamespace(source_id=1, user_id="user1", state={})
    result = _run(context, {"question": "预测产量"})
    assert result.status == "succeeded"
    assert result.payload["resultCard"]["predictions"] == [70]
    assert result.payload["resultCard"]["executionType"] == "mcp"


def test_validation_failure_revokes_old_validation(env):
    asset = remote.save_asset(remote.RemoteConfig(**CONFIG), ADMIN)
    remote.invoke(asset["id"], ADMIN, [{"hours": 1}], validate=True)
    with pytest.raises(remote.RemoteModelError):
        remote.invoke(asset["id"], ADMIN, [{"hours": "bad"}], validate=True)
    with pytest.raises(remote.RemoteModelError):
        remote.transition(asset["id"], ADMIN, "published")


def test_input_limit_and_schema_drift(env, monkeypatch):
    with pytest.raises(remote.RemoteModelError):
        remote.project_records([{"hours": "x"*1_000_001}], {"hours": "hours"})
    asset = remote.save_asset(remote.RemoteConfig(**CONFIG), ADMIN)
    remote.invoke(asset["id"], ADMIN, [{"hours": 1}], validate=True)
    remote.transition(asset["id"], ADMIN, "published")
    monkeypatch.setitem(TOOL, "inputSchema", {"type": "object", "properties": {"records": {"type": "array"}}})
    with pytest.raises(remote.RemoteModelError, match="契约已变化"):
        remote.invoke(asset["id"], ADMIN, [{"hours": 1}])


def test_non_readonly_tool_rejected(env, monkeypatch):
    monkeypatch.setitem(TOOL, "annotations", {})
    asset = remote.save_asset(remote.RemoteConfig(**CONFIG), ADMIN)
    with pytest.raises(remote.RemoteModelError, match="readOnlyHint"):
        remote.invoke(asset["id"], ADMIN, [{"hours": 1}], validate=True)


def test_business_can_configure_owned_model_but_cannot_publish(env):
    app = FastAPI(); app.include_router(api.router)
    app.dependency_overrides[require_user] = lambda: {"id": "user1"}
    member = TenantContext("tenant-default", "Test", "test", "member", "user1")
    app.dependency_overrides[tenant_context] = lambda: member
    client = TestClient(app)
    base = "/api/v1/modeling/remote-models"
    discovery = client.post(f"{base}/discover", json={"endpoint": CONFIG["endpoint"], "credentialRef": ""})
    assert discovery.status_code == 200
    assert discovery.json()["items"][0]["name"] == "predict_output"
    asset = client.post(base, json=CONFIG)
    assert asset.status_code == 200
    model_id = asset.json()["id"]
    listing = client.get(base, params={"sourceId": 1}).json()
    assert listing["canConfigure"] is True
    assert listing["canPublish"] is False
    assert listing["items"][0]["canEdit"] is True
    assert client.post(f"{base}/{model_id}/validate", json={"records": [{"hours": 2}]}).status_code == 200
    assert client.post(f"{base}/{model_id}/publish").status_code == 403


def test_business_cannot_edit_another_users_remote_model(env):
    asset = remote.save_asset(remote.RemoteConfig(**CONFIG), ADMIN)
    other = TenantContext("tenant-default", "Test", "test", "member", "user2")
    assert remote.list_assets(other, 1)[0]["canEdit"] is False
    with pytest.raises(HTTPException) as error:
        remote.save_asset(remote.RemoteConfig(**CONFIG), other, asset["id"])
    assert error.value.status_code == 403


def test_sse_transport_parses_matching_result(env):
    client = mcp_client.ModelMCPClient("http://model.test/mcp")
    client.client.close()
    # Class was saved before monkeypatch by httpx itself in TestClient's ancestry.
    from httpx._client import Client
    client.client = Client(transport=httpx.MockTransport(lambda request: httpx.Response(
        200, headers={"content-type": "text/event-stream"},
        text='event: message\ndata: {"jsonrpc":"2.0",\ndata: "id":1,"result":{"tools":[]}}\n\n')))
    try:
        assert client.request("tools/list") == {"tools": []}
    finally:
        client.client.close()


def test_real_http_demo_server_roundtrip(env, monkeypatch):
    import socket
    import threading
    import time
    import uvicorn
    from httpx._client import Client
    from scripts.mcp_model_demo import app
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    origin = f"http://127.0.0.1:{sock.getsockname()[1]}"
    monkeypatch.setattr(settings, "mcp_model_allowed_origins", [origin])
    monkeypatch.setattr(mcp_client.httpx, "Client", Client)
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", ws="none"))
    worker = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    worker.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started and time.monotonic() < deadline:
            time.sleep(.01)
        assert server.started
        asset = remote.save_asset(remote.RemoteConfig(**{**CONFIG, "endpoint": origin + "/mcp"}), ADMIN)
        remote.invoke(asset["id"], ADMIN, [{"hours": 8}], validate=True)
        remote.transition(asset["id"], ADMIN, "published")
        assert remote.invoke(asset["id"], ADMIN, [{"hours": 9}])["predictions"] == [90]
    finally:
        server.should_exit = True
        worker.join(timeout=5)
        sock.close()
        assert not worker.is_alive()


def test_explicit_agent_prediction_keeps_source_boundary(env, monkeypatch):
    from app.agent.capabilities.modeling import _predict
    monkeypatch.setattr(remote, "context_for_user", lambda *args: ADMIN)
    asset = remote.save_asset(remote.RemoteConfig(**CONFIG), ADMIN)
    remote.invoke(asset["id"], ADMIN, [{"hours": 1}], validate=True)
    remote.transition(asset["id"], ADMIN, "published")
    arguments = {"taskId": asset["id"], "records": [{"hours": 5}]}
    assert _predict(SimpleNamespace(source_id=1, user_id="user1"), arguments).payload["predictions"] == [50]
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as error:
        _predict(SimpleNamespace(source_id=2, user_id="user1"), arguments)
    assert error.value.status_code == 404
