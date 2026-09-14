// Explicit visual test entry. Never imported by the product app or production build.
import React from "react";
import { createRoot } from "react-dom/client";
import { HashRouter, Link, Route, Routes } from "react-router-dom";
import AlertsPage from "../../src/pages/business/AlertsPage";
import AlertDetailPage from "../../src/pages/business/AlertDetailPage";
import logo from "../../src/assets/forgesight-logo.png";
import "../../src/styles/index.css";

const entities = ["总装检测", "冲压成型", "涂装一线", "精密加工", "成品库"];
const metrics = ["工序良率", "产量", "非计划停机时长", "库存数量"];
const records = Array.from({ length: 38 }, (_, i) => ({
  id: `fixture-${i + 1}`,
  ruleId: "fixture-rule",
  ruleVersion: 3,
  sourceId: i % 5 === 0 ? 2 : 1,
  title:
    i === 5
      ? "精密加工与最终总装质量复检工序 · 连续多个统计周期低于企业已发布质量红线"
      : `${entities[i % entities.length]} · ${metrics[i % metrics.length]}异常`,
  entityId: `PR0${(i % 5) + 1}`,
  entityName: entities[i % entities.length],
  entityType: "process",
  metricName: metrics[i % metrics.length],
  severity: ["high", "critical", "medium", "low"][i % 4],
  status: ["firing", "firing", "recovering", "resolved"][i % 4],
  currentValue:
    i === 0
      ? 0.84
      : i === 2
        ? null
        : i === 3
          ? 0
          : i % 4 === 0
            ? 0.88
            : i % 4 === 1
              ? 1560000
              : 120,
  baselineValue: i % 4 === 0 ? 0.96 : i === 2 ? null : 150,
  sampleSize: 120 + i,
  recurrenceCount: i % 3,
  firstSeenAt: "2026-08-24T08:00:00",
  lastSeenAt:
    i === 0
      ? "2026-08-29T12:00:00"
      : `2026-08-${String(28 - (i % 4)).padStart(2, "0")}T${String(10 + (i % 8)).padStart(2, "0")}:00:00`,
  resolvedAt: i % 4 === 3 ? "2026-08-28T14:00:00" : null,
}));
const evidence = [0.96, 0.95, 0.94, 0.91, 0.88, 0.84, null, 0.87, 0.91, 0.84].map((value, i) => ({
  id: `evidence-${i}`,
  logicalPeriod: `2026-08-${20 + i}`,
  currentValue: value,
  baselineValue: i === 6 ? null : 0.96,
  sampleSize: i === 6 ? 0 : 120,
  qualityStatus: i === 6 ? "no_data" : "ok",
  evaluationState: i === 6 ? "no_data" : i < 4 ? "normal" : "firing",
  dataCutoffAt: `2026-08-${20 + i}T23:59:00`,
  createdAt: `2026-08-${20 + i}T12:00:00`,
}));
const fixtureParams = new URLSearchParams(window.location.search);
if (fixtureParams.get("theme") === "dark")
  document.documentElement.setAttribute("data-theme", "dark");
const failedDelivery = fixtureParams.get("deliveryError") === "1";
const empty = fixtureParams.get("empty") === "1";
window.fetch = async (input) => {
  const url = new URL(String(input), window.location.origin);
  const id = url.pathname.match(/alerts\/(fixture-\d+)$/)?.[1];
  let body;
  if (url.pathname === "/api/datasources")
    body = [
      { id: 1, name: "验收用制造数据（合成）" },
      { id: 2, name: "验收用库存数据（合成）" },
    ];
  else if (url.pathname.endsWith("/evidence")) body = { items: evidence };
  else if (url.pathname.endsWith("/deliveries")) {
    if (failedDelivery)
      return new Response(JSON.stringify({ detail: "合成测试：通知接口不可用" }), { status: 503 });
    body = {
      items: [
        {
          id: "delivery-1",
          channel: "feishu",
          eventType: "monitoring.alert.firing",
          status: "sent",
          attemptCount: 1,
          sentAt: "2026-08-28T14:00:00",
        },
        {
          id: "delivery-2",
          channel: "email",
          eventType: "monitoring.alert.firing",
          status: "retry_wait",
          attemptCount: 2,
          error: "合成测试：连接超时，等待重试",
        },
      ],
    };
  } else if (id)
    body = {
      ...records.find((record) => record.id === id),
      rule: {
        name: "工序良率低于红线",
        grain: "day",
        queryPlan: {
          kind: "ratio",
          table: "mes_process_output",
          timeField: "stat_date",
          entityField: "process_id",
        },
        condition: {
          operator: "lt",
          openThreshold: 0.9,
          closeThreshold: 0.93,
          pendingPeriods: 2,
          recoveryPeriods: 2,
        },
        qualityGate: { minimumSamples: 80 },
        scheduleSeconds: 86400,
      },
    };
  else if (url.pathname === "/api/v1/monitoring/alerts")
    body = {
      items: empty
        ? []
        : records.filter(
            (item) =>
              !url.searchParams.get("sourceId") ||
              String(item.sourceId) === url.searchParams.get("sourceId")
          ),
    };
  else throw new Error(`Visual fixture blocked unexpected request: ${url.pathname}`);
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
};
function FixtureShell() {
  return (
    <div className="fixture-app">
      <div className="fixture-banner">交互验收 · 合成测试数据 · 不连接业务 API / 不写入数据库</div>
      <div className="app-shell fixture-layout">
        <aside className="fixture-sidebar">
          <img src={logo} alt="" />
          <strong>ForgeSight</strong>
          <p>业务工作台 · 测试环境</p>
          <Link to="/workspace/alerts">异常列表</Link>
          <Link to="/workspace/alerts/fixture-1">证据详情</Link>
        </aside>
        <main className="fixture-main">
          <header className="fixture-topbar">
            <h1>智能异常监测</h1>
            <span>合成数据验收空间</span>
          </header>
          <div className="fixture-scroll">
            <Routes>
              <Route path="/workspace/alerts/:alertId" element={<AlertDetailPage />} />
              <Route
                path="/workspace/analysis"
                element={
                  <div className="fixture-analysis">
                    <h2>新分析草稿已准备</h2>
                    <p>合成验收页不会调用模型。</p>
                  </div>
                }
              />
              <Route path="*" element={<AlertsPage />} />
            </Routes>
          </div>
        </main>
      </div>
    </div>
  );
}
createRoot(document.getElementById("root")).render(
  <HashRouter>
    <FixtureShell />
  </HashRouter>
);

import "./fixture.css";
