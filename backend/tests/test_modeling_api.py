"""建模 API 契约测试：路由、参数映射、错误码（409/404）。"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

TASK_PAYLOAD = {
    "title": "高不良风险识别模型",
    "source": "agent",
    "sourceId": 1,
    "ownerId": "zhangsan",
    "goal": "识别高不良风险的生产记录",
    "taskType": "classification",
    "datasetName": "工序产量事实表",
    "datasetSql": "SELECT * FROM mes_process_output LIMIT 10000",
    "targetField": "high_defect_risk",
    "targetLabel": "高风险标记",
    "features": ["工序", "产线", "停机时长", "计划数量"],
    "candidates": ["logistic", "tree", "forest"],
    "runId": None,
}


def _create_task():
    resp = client.post("/api/v1/modeling/tasks", json=TASK_PAYLOAD)
    assert resp.status_code == 201
    return resp.json()


def test_algorithms_catalog(modeling_session):
    resp = client.get("/api/v1/modeling/algorithms")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 6
    assert {item["id"] for item in items} == {"linear", "tree", "forest", "logistic", "kmeans", "isolation"}
    assert all({"family", "accent", "scene"} <= set(item) for item in items)


def test_create_list_get_task(modeling_session):
    task = _create_task()
    assert task["status"] == "draft"
    assert task["id"].startswith("mt")

    listed = client.get("/api/v1/modeling/tasks?ownerId=zhangsan").json()["items"]
    assert len(listed) == 1

    fetched = client.get(f"/api/v1/modeling/tasks/{task['id']}").json()
    assert fetched["id"] == task["id"]


def test_train_and_evaluation_endpoint(modeling_session):
    task = _create_task()
    resp = client.post(
        f"/api/v1/modeling/tasks/{task['id']}/train",
        json={"strategy": "auto", "candidateIds": ["logistic", "tree"], "userId": "zhangsan"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["task"]["status"] == "trained"
    assert len(body["results"]["models"]) == 2

    evaluation = client.get(f"/api/v1/modeling/tasks/{task['id']}/evaluation").json()
    assert evaluation["recommendedModel"] in {"logistic", "tree"}


def test_predict_endpoint(modeling_session):
    task = _create_task()
    client.post(
        f"/api/v1/modeling/tasks/{task['id']}/train",
        json={"strategy": "auto", "candidateIds": ["logistic"], "userId": "zhangsan"},
    )
    model = client.get("/api/v1/model-governance/models").json()["items"][0]
    client.post(
        f"/api/v1/model-governance/models/{model['id']}/review",
        json={"userId": "admin", "decision": "pass", "comment": "指标达标"},
    )
    client.post(f"/api/v1/model-governance/models/{model['id']}/publish", json={"userId": "admin"})
    resp = client.post(
        f"/api/v1/modeling/tasks/{task['id']}/predict",
        json={
            "userId": "zhangsan",
            "records": [
                {"工序": "SMT贴片", "产线": "L01", "停机时长": 480, "计划数量": 3200},
                {"工序": "AOI检测", "产线": "L04", "停机时长": 30, "计划数量": 2100},
            ],
        },
    )
    assert resp.status_code == 200
    predictions = resp.json()["predictions"]
    assert len(predictions) == 2
    assert all("probability" in item for item in predictions)


def test_governance_endpoints(modeling_session):
    task = _create_task()
    client.post(
        f"/api/v1/modeling/tasks/{task['id']}/train",
        json={"strategy": "auto", "candidateIds": ["tree"], "userId": "zhangsan"},
    )
    model = client.get("/api/v1/model-governance/models").json()["items"][0]
    assert model["status"] == "pending"

    # 复核通过后显式发布
    reviewed = client.post(
        f"/api/v1/model-governance/models/{model['id']}/review",
        json={"userId": "admin", "decision": "pass", "comment": "指标达标"},
    ).json()
    assert reviewed["status"] == "reviewing"
    published_model = client.post(
        f"/api/v1/model-governance/models/{model['id']}/publish", json={"userId": "admin"}
    ).json()
    assert published_model["status"] == "published"
    # 按状态过滤
    published = client.get("/api/v1/model-governance/models?status=published").json()["items"]
    assert len(published) == 1
    # 下线
    retired = client.post(
        f"/api/v1/model-governance/models/{model['id']}/retire", json={"userId": "admin"}
    ).json()
    assert retired["status"] == "retired"


def test_automl_diagnostics_versions_reuse_and_threshold_governance(modeling_session):
    task = _create_task()
    diagnostics = client.post(f"/api/v1/modeling/tasks/{task['id']}/diagnostics", json={})
    assert diagnostics.status_code == 200
    assert diagnostics.json()["status"] in {"ready", "warning"}

    trained = client.post(
        f"/api/v1/modeling/tasks/{task['id']}/train",
        json={
            "strategy": "auto",
            "candidateIds": ["logistic"],
            "optimizationLevel": "balanced",
            "userId": "zhangsan",
        },
    )
    assert trained.status_code == 200
    assert trained.json()["results"]["models"][0]["tuning"]["optimizationLevel"] == "balanced"

    versions = client.get(f"/api/v1/modeling/tasks/{task['id']}/versions")
    assert versions.status_code == 200
    assert len(versions.json()["models"]) == 1

    model = client.get(f"/api/v1/model-governance/models?taskId={task['id']}").json()["items"][0]
    client.post(
        f"/api/v1/model-governance/models/{model['id']}/review",
        json={"userId": "admin", "decision": "pass", "comment": "受控搜索通过"},
    )
    client.post(f"/api/v1/model-governance/models/{model['id']}/publish", json={"userId": "admin"})

    reusable = client.get(
        "/api/v1/modeling/reusable-models",
        params={"question": "识别高不良风险生产记录", "sourceId": 1, "taskType": "classification"},
    )
    assert reusable.status_code == 200
    assert reusable.json()["items"][0]["modelId"] == model["id"]

    variant = client.post(
        f"/api/v1/model-governance/models/{model['id']}/threshold-variant",
        json={"userId": "admin", "threshold": 0.35, "reason": "降低漏报"},
    )
    assert variant.status_code == 201
    assert variant.json()["decisionThreshold"] == 0.35
    client.post(f"/api/v1/model-governance/models/{variant.json()['id']}/publish", json={"userId": "admin"})

    restored = client.post(f"/api/v1/model-governance/models/{model['id']}/rollback", json={"userId": "admin"})
    assert restored.status_code == 200
    assert restored.json()["status"] == "published"


def test_error_mapping(modeling_session):
    # 任务不存在 → 404
    assert client.get("/api/v1/modeling/tasks/mt-none").status_code == 404
    # 未训练推理 → 409
    task = _create_task()
    resp = client.post(
        f"/api/v1/modeling/tasks/{task['id']}/predict",
        json={"userId": "zhangsan", "records": [{"工序": "SMT贴片"}]},
    )
    assert resp.status_code == 409
    assert "尚未完成训练" in resp.json()["detail"]
    # 未训练取评估 → 409
    assert client.get(f"/api/v1/modeling/tasks/{task['id']}/evaluation").status_code == 409
    # 非法治理迁移 → 409（pending 直接发布）
    client.post(
        f"/api/v1/modeling/tasks/{task['id']}/train",
        json={"strategy": "auto", "candidateIds": ["tree"], "userId": "zhangsan"},
    )
    model = client.get("/api/v1/model-governance/models").json()["items"][0]
    resp = client.post(
        f"/api/v1/model-governance/models/{model['id']}/publish", json={"userId": "admin"}
    )
    assert resp.status_code == 409
    assert "不允许模型发布" in resp.json()["detail"]
    # 模型不存在 → 404
    assert client.post(
        "/api/v1/model-governance/models/mdl-none/review",
        json={"userId": "admin", "decision": "pass"},
    ).status_code == 404
