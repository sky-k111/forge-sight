import { useEffect, useMemo, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";

import { listSources } from "../../api/datasources";
import {
  getMonitoringAlert,
  getMonitoringDeliveries,
  getMonitoringEvidence,
} from "../../api/monitoring";
import { Button, ErrorNotice, PageFrame, RouteSkeleton } from "../../components/ui";
import Icon from "../../components/ui/Icon";
import MonitoringEvidenceChart from "../../features/monitoring/MonitoringEvidenceChart";
import MonitoringStatus from "../../features/monitoring/MonitoringStatus";
import {
  CHANNELS,
  EVALUATION_STATES,
  GRAINS,
  QUALITY_STATES,
  evidenceRows,
  formatNumber,
  formatTime,
  latestTimestamp,
  numeric,
  recoveryCondition,
  ruleCondition,
  valueScale,
} from "../../features/monitoring/alertPresentation";
import "./monitoring.css";

export default function AlertDetailPage() {
  const { alertId } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [refresh, setRefresh] = useState(0);
  const [view, setView] = useState("trend");
  const listUrl = `/workspace/alerts${location.search}`;

  useEffect(() => {
    let cancelled = false;
    setData(null);
    setError(null);
    const snapshot = {
      alert: null,
      evidence: [],
      evidenceLoading: true,
      evidenceError: null,
      deliveries: [],
      deliveriesLoading: true,
      deliveriesError: null,
      sources: [],
    };
    const publish = () => {
      if (!cancelled && snapshot.alert) setData({ ...snapshot });
    };
    getMonitoringAlert(alertId)
      .then((alert) => {
        snapshot.alert = alert;
        publish();
      })
      .catch((reason) => {
        if (!cancelled) setError(reason);
      });
    // Optional resources resolve independently so a slow notification service
    // cannot hold the primary alert or its observation evidence behind a skeleton.
    getMonitoringEvidence(alertId)
      .then((items) => {
        snapshot.evidence = items;
      })
      .catch((reason) => {
        snapshot.evidenceError = reason;
      })
      .finally(() => {
        snapshot.evidenceLoading = false;
        publish();
      });
    getMonitoringDeliveries(alertId)
      .then((items) => {
        snapshot.deliveries = items;
      })
      .catch((reason) => {
        snapshot.deliveriesError = reason;
      })
      .finally(() => {
        snapshot.deliveriesLoading = false;
        publish();
      });
    listSources()
      .then((items) => {
        snapshot.sources = items;
        publish();
      })
      .catch(() => {
        // The source ID remains visible when its optional display name is unavailable.
      });
    return () => {
      cancelled = true;
    };
  }, [alertId, refresh]);

  const observations = useMemo(() => evidenceRows(data?.evidence || []), [data?.evidence]);
  const retry = () => setRefresh((value) => value + 1);
  if (error)
    return (
      <PageFrame className="monitoring-page">
        <BackLink to={listUrl} />
        <ErrorNotice title="异常详情加载失败" detail={error.message} onRetry={retry} />
      </PageFrame>
    );
  if (!data) return <RouteSkeleton />;
  const {
    alert,
    evidenceLoading,
    evidenceError,
    deliveries,
    deliveriesLoading,
    deliveriesError,
    sources,
  } = data;
  const sourceName =
    sources.find((source) => source.id === alert.sourceId)?.name ||
    `数据源 #${alert.sourceId ?? "—"}`;
  const cutoff = latestTimestamp(observations, "dataCutoffAt");
  const current = numeric(alert.currentValue);
  const baseline = numeric(alert.baselineValue);
  const difference = current !== null && baseline !== null ? current - baseline : null;
  const ruleVersionMismatch =
    alert.ruleVersion != null &&
    alert.rule?.version != null &&
    String(alert.ruleVersion) !== String(alert.rule.version);
  const historicalRule = ruleVersionMismatch ? null : alert.rule;
  const qualityGate = alert.rule?.qualityGate || {};
  const timeline = observations.slice(-6);
  function startAnalysis() {
    window.sessionStorage.setItem("forgesight:active-source-id", String(alert.sourceId));
    window.sessionStorage.removeItem("forgesight:analysis-session-id");
    window.sessionStorage.setItem("forgesight:analysis-new-session", "1");
    window.sessionStorage.setItem(
      "forgesight:analysis-draft",
      `请分析“${alert.metricName || alert.title}”的异常。监测对象：${alert.entityName || alert.entityId}（${alert.entityId || "未提供对象编号"}）。异常记录 ${alert.id}，首次出现 ${formatTime(alert.firstSeenAt, true)}，最近出现 ${formatTime(alert.lastSeenAt, true)}。请先核对数据范围、企业指标口径与样本，再展示趋势并比较历史基线；区分已经验证的事实和待验证的原因，不把监测信号直接当作根因结论。`
    );
    navigate("/workspace/analysis");
  }

  return (
    <PageFrame className="monitoring-page monitoring-detail">
      <div className="monitoring-detail__navigation">
        <BackLink to={listUrl} />
        <span>
          异常证据 / <code>{alert.id}</code>
        </span>
        <Button variant="ghost" onClick={retry}>
          <Icon name="refresh" size={14} />
          刷新证据
        </Button>
      </div>
      <header className="monitoring-detail__header">
        <div>
          <div className="monitoring-detail__states">
            <MonitoringStatus value={alert.status} />
            <MonitoringStatus kind="severity" value={alert.severity} />
          </div>
          <h2>{alert.title}</h2>
          <p>
            <span>{alert.metricName || "指标名称未提供"}</span>
            <span>
              {alert.entityName || alert.entityId || "对象未提供"}
              {alert.entityId && ` · ${alert.entityId}`}
            </span>
            <span>{sourceName}</span>
          </p>
        </div>
        <Button variant="primary" disabled={!alert.sourceId} onClick={startAnalysis}>
          <Icon name="analysis" size={14} />
          带入智能分析
          <Icon name="arrow" size={13} />
        </Button>
      </header>
      <section className="monitoring-detail__values" aria-label="异常指标摘要">
        <DetailValue
          label={`${alert.metricName || "监控指标"} · 最近记录值`}
          value={formatNumber(current)}
          note={valueScale(historicalRule)}
          emphasized
        />
        <DetailValue
          label="历史基线"
          value={formatNumber(baseline)}
          note={
            difference === null
              ? "未提供可比较的基线"
              : `较基线 ${difference > 0 ? "+" : ""}${formatNumber(difference)}（原值差）`
          }
        />
        <DetailValue
          label="样本量"
          value={formatNumber(alert.sampleSize)}
          note="最近关联观测的样本"
        />
        <DetailValue
          label="再次触发"
          value={formatNumber(alert.recurrenceCount)}
          note="恢复后重新触发的次数"
        />
      </section>
      {ruleVersionMismatch && (
        <p className="monitoring-version-warning" role="status">
          <Icon name="alert" size={14} />
          {`异常触发版本 v${alert.ruleVersion}，与当前规则 v${alert.rule.version} 不同。右侧仅展示当前规则供参考；历史规则快照未提供，不将当前阈值叠加到历史趋势。`}
        </p>
      )}
      <div className="monitoring-detail__layout">
        <div className="monitoring-detail__primary">
          <section
            className="monitoring-panel monitoring-evidence-panel"
            aria-label="指标趋势与证据"
          >
            <div className="monitoring-section-heading">
              <div>
                <h3>{alert.metricName || "监控指标"} · 趋势与证据</h3>
                <p>
                  统计粒度：{GRAINS[historicalRule?.grain] || historicalRule?.grain || "未提供"} ·{" "}
                  {valueScale(historicalRule)}
                </p>
              </div>
              <span title="仅展示与这条异常关联的观测，不代表完整指标历史。固定阈值来自规则，动态界限未提供时不会推算。">
                <Icon name="help" size={14} className="text-faint" />
              </span>
            </div>
            <div className="monitoring-evidence-tabs" role="group" aria-label="证据展示方式">
              <button
                type="button"
                aria-pressed={view === "trend"}
                onClick={() => setView("trend")}
              >
                <Icon name="insight" size={14} />
                指标趋势
              </button>
              <button type="button" aria-pressed={view === "data"} onClick={() => setView("data")}>
                <Icon name="database" size={14} />
                观测明细<span>{observations.length}</span>
              </button>
              <span>数据截至 {formatTime(cutoff, true)}</span>
            </div>
            {evidenceLoading ? (
              <div className="monitoring-chart-empty" role="status">
                正在加载观测证据…
              </div>
            ) : evidenceError ? (
              <ErrorNotice
                title="观测证据加载失败"
                detail={evidenceError.message}
                onRetry={retry}
                className="monitoring-inline-error"
              />
            ) : !observations.length ? (
              <div className="monitoring-chart-empty">
                暂无关联观测证据；当前状态不能替代历史数据。
              </div>
            ) : view === "trend" ? (
              <MonitoringEvidenceChart items={observations} rule={historicalRule} />
            ) : (
              <ObservationTable items={observations} />
            )}
          </section>
          <section className="monitoring-panel monitoring-notifications" aria-label="通知投递记录">
            <div className="monitoring-section-heading">
              <div>
                <h3>通知记录</h3>
                <p>发送状态与异常状态分开记录，发送失败不会改变检测结果。</p>
              </div>
              <Icon name="bell" size={15} className="text-faint" />
            </div>
            {deliveriesLoading ? (
              <p className="monitoring-muted-empty" role="status">
                正在加载通知记录…
              </p>
            ) : deliveriesError ? (
              <ErrorNotice
                title="通知记录加载失败"
                detail={deliveriesError.message}
                onRetry={retry}
                className="monitoring-inline-error"
              />
            ) : !deliveries.length ? (
              <p className="monitoring-muted-empty">暂无通知投递记录</p>
            ) : (
              <ul className="monitoring-delivery-list">
                {deliveries.map((item) => (
                  <li key={item.id}>
                    <div>
                      <strong>{CHANNELS[item.channel] || item.channel || "未知渠道"}</strong>
                      <small>
                        {deliveryEvent(item.eventType)} · 尝试 {formatNumber(item.attemptCount)} 次
                        {item.sentAt && ` · ${formatTime(item.sentAt)}`}
                      </small>
                      {item.error && <p>{item.error}</p>}
                    </div>
                    <MonitoringStatus kind="delivery" value={item.status} />
                  </li>
                ))}
              </ul>
            )}
          </section>
          <p className="monitoring-detail__boundary">
            <Icon name="shield" size={14} />
            异常记录是规则触发的信号，不是根因诊断。带入智能分析仅准备问题，不会自动发起查询。
          </p>
        </div>
        <aside className="monitoring-detail__context" aria-label="异常口径与时间">
          <section className="monitoring-panel monitoring-rule-panel">
            <div className="monitoring-section-heading">
              <h3>规则口径</h3>
              <span>v{alert.rule?.version ?? alert.ruleVersion ?? "—"}</span>
            </div>
            <dl className="monitoring-definition-list">
              <Definition label="规则名称" value={alert.rule?.name} />
              <Definition label="监控指标" value={alert.metricName || alert.rule?.metricName} />
              <Definition label="统计粒度" value={GRAINS[alert.rule?.grain] || alert.rule?.grain} />
            </dl>
            <div className="monitoring-condition">
              <span>
                <Icon name="alert" size={13} />
                触发条件
              </span>
              <p>{ruleCondition(alert.rule)}</p>
            </div>
            <div className="monitoring-condition monitoring-condition--recovery">
              <span>
                <Icon name="check" size={13} />
                恢复条件
              </span>
              <p>{recoveryCondition(alert.rule)}</p>
            </div>
            <dl className="monitoring-definition-list">
              <Definition
                label="最低样本量"
                value={qualityGate.minimumSamples ?? qualityGate.minimumSampleSize ?? "未设定"}
              />
              <Definition
                label="执行间隔"
                value={
                  alert.rule?.scheduleSeconds
                    ? `${formatNumber(alert.rule.scheduleSeconds)} 秒`
                    : null
                }
              />
            </dl>
            <details className="monitoring-binding">
              <summary>
                数据绑定
                <Icon name="chevron" size={12} />
              </summary>
              <dl className="monitoring-definition-list">
                <Definition label="数据源" value={sourceName} />
                <Definition label="表" value={alert.rule?.queryPlan?.table} />
                <Definition label="时间字段" value={alert.rule?.queryPlan?.timeField} />
                <Definition label="对象字段" value={alert.rule?.queryPlan?.entityField} />
              </dl>
            </details>
          </section>
          <section className="monitoring-panel">
            <div className="monitoring-section-heading">
              <h3>时间与观测</h3>
              <Icon name="clock" size={14} className="text-faint" />
            </div>
            <dl className="monitoring-definition-list">
              <Definition label="首次出现" value={formatTime(alert.firstSeenAt, true)} />
              <Definition label="最近出现" value={formatTime(alert.lastSeenAt, true)} />
              <Definition label="恢复时间" value={formatTime(alert.resolvedAt, true)} />
              <Definition label="数据截至" value={formatTime(cutoff, true)} />
            </dl>
            {timeline.length > 0 && (
              <div className="monitoring-timeline">
                <h4>最近关联观测</h4>
                <ol>
                  {timeline.map((item) => (
                    <li key={item.id}>
                      <span
                        className={`monitoring-timeline__dot ${item.evaluationState === "firing" ? "is-firing" : ""}`}
                      />
                      <div>
                        <strong>
                          {EVALUATION_STATES[item.evaluationState] ||
                            item.evaluationState ||
                            "状态未提供"}
                        </strong>
                        <p>
                          {item.logicalPeriod} · {formatNumber(item.currentValue)}
                        </p>
                        <small>{formatTime(item.createdAt)}</small>
                      </div>
                    </li>
                  ))}
                </ol>
              </div>
            )}
          </section>
        </aside>
      </div>
    </PageFrame>
  );
}

function BackLink({ to }) {
  return (
    <Link to={to} className="monitoring-back">
      <Icon name="chevron" size={13} className="rotate-180" />
      返回异常列表
    </Link>
  );
}
function DetailValue({ label, value, note, emphasized = false }) {
  return (
    <div className={emphasized ? "is-emphasized" : ""}>
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{note}</small>
    </div>
  );
}
function Definition({ label, value }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{value ?? "未提供"}</dd>
    </div>
  );
}
function deliveryEvent(value) {
  return (
    {
      "monitoring.alert.firing": "告警通知",
      "monitoring.alert.resolved": "恢复通知",
      "monitoring.alert.severity_changed": "级别变更",
    }[value] ||
    value ||
    "通知"
  );
}
function ObservationTable({ items }) {
  return (
    <div
      className="monitoring-observation-scroll"
      role="region"
      aria-label="观测明细，可滚动"
      tabIndex={0}
    >
      <table className="monitoring-table monitoring-observation-table" aria-label="异常观测明细">
        <thead>
          <tr>
            <th>统计周期</th>
            <th className="is-numeric">观测值</th>
            <th className="is-numeric">历史基线</th>
            <th className="is-numeric">样本量</th>
            <th>数据质量</th>
            <th>观测状态</th>
            <th>数据截至</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.id}>
              <td>{item.logicalPeriod}</td>
              <td className="is-numeric">{formatNumber(item.currentValue)}</td>
              <td className="is-numeric">{formatNumber(item.baselineValue)}</td>
              <td className="is-numeric">{formatNumber(item.sampleSize)}</td>
              <td>{QUALITY_STATES[item.qualityStatus] || item.qualityStatus || "未提供"}</td>
              <td>{EVALUATION_STATES[item.evaluationState] || item.evaluationState || "未提供"}</td>
              <td>{formatTime(item.dataCutoffAt)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
