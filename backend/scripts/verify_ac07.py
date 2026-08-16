"""AC-07 完整流程验收：重置登记状态 → 白名单拦截 → LLM 推荐 → 确认 → 放行。"""

import json
import sys
import urllib.request

from sqlalchemy import text

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8")

from app.db.platform import platform_engine  # noqa: E402

BASE = "http://127.0.0.1:8000"


def get(path):
    return json.load(urllib.request.urlopen(BASE + path))


def post(path, payload=None):
    data = json.dumps(payload).encode() if payload is not None else b""
    req = urllib.request.Request(BASE + path, data=data, method="POST", headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req))


with platform_engine.begin() as conn:
    conn.execute(text("UPDATE data_table SET registered=0 WHERE name='eqp_sensor_reading'"))
    flag = conn.execute(text("SELECT registered FROM data_table WHERE name='eqp_sensor_reading'")).scalar()
print("0. 重置为待确认，registered =", flag)

print("1. 未登记表:", [t["name"] for t in get("/api/resources/tables/unregistered")])

blocked = post("/api/gateway/query", {"sql": "SELECT * FROM eqp_sensor_reading"})
print("2. 未确认前查询:", "已拦截 ✓" if not blocked["ok"] else "未拦截 ✗", "|", blocked["error"])

suggestion = post("/api/resources/tables/eqp_sensor_reading/suggest?sourceId=1")
print("3. LLM 推荐:", suggestion["cn"], "|", suggestion["topic"], "| 置信度", suggestion["confidence"])

confirmed = post("/api/resources/tables/eqp_sensor_reading/confirm", {"sourceId": 1, "suggestion": suggestion})
print("4. 确认登记:", confirmed["cn"], "registered =", confirmed["registered"])

allowed = post("/api/gateway/query", {"sql": "SELECT equipment_id, AVG(temperature) FROM eqp_sensor_reading GROUP BY equipment_id"})
print("5. 确认后查询:", "放行 ✓" if allowed["ok"] else "被拦 ✗", allowed["rows"])
