import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { listSources } from "../../api/datasources";
import { listMonitoringAlerts } from "../../api/monitoring";
import { Button, ErrorNotice, PageFrame } from "../../components/ui";
import Icon from "../../components/ui/Icon";
import MonitoringStatus from "../../features/monitoring/MonitoringStatus";
import {
  ALERT_SEVERITIES,
  ALERT_STATES,
  filterAlerts,
  formatNumber,
  formatTime,
  latestTimestamp,
  summarizeAlerts,
} from "../../features/monitoring/alertPresentation";
import "./monitoring.css";

const RECORD_LIMIT = 300;
const PAGE_SIZES = [15, 30, 50];
const STATUS_TABS = [
  { value: "", label: "全部记录" },
  ...Object.entries(ALERT_STATES).map(([value, definition]) => ({
    value,
    label: definition.label,
  })),
];

export default function AlertsPage() {
  const [params, setParams] = useSearchParams();
  const requestedSource = params.get("sourceId") || "";
  const sourceId = /^[1-9]\d*$/.test(requestedSource) ? requestedSource : "";
  const status = Object.hasOwn(ALERT_STATES, params.get("status")) ? params.get("status") : "";
  const severity = Object.hasOwn(ALERT_SEVERITIES, params.get("severity"))
    ? params.get("severity")
    : "";
  const metric = params.get("metric") || "";
  const search = params.get("q") || "";
  const sort = params.get("sort") === "severity" ? "severity" : "latest";
  const requestedPage = Number(params.get("page")) || 1;
  const requestedPageSize = Number(params.get("size"));
  const pageSize = PAGE_SIZES.includes(requestedPageSize) ? requestedPageSize : 15;
  const [sources, setSources] = useState([]);
  const [sourceError, setSourceError] = useState(null);
  const [items, setItems] = useState(null);
  const [error, setError] = useState(null);
  const [refresh, setRefresh] = useState(0);
  const [loadedAt, setLoadedAt] = useState(null);

  useEffect(() => {
    let cancelled = false;
    listSources()
      .then((data) => {
        if (!cancelled) setSources(data);
      })
      .catch((failure) => {
        if (!cancelled) setSourceError(failure);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    setItems(null);
    // Fetch an unfiltered snapshot so status totals stay meaningful while filtering.
    listMonitoringAlerts({ sourceId, limit: RECORD_LIMIT })
      .then((data) => {
        if (!cancelled) {
          setItems(data);
          setLoadedAt(new Date().toISOString());
        }
      })
      .catch((failure) => {
        if (!cancelled) setError(failure);
      });
    return () => {
      cancelled = true;
    };
  }, [sourceId, refresh]);

  const counts = useMemo(() => (items ? summarizeAlerts(items) : null), [items]);
  const filtered = useMemo(
    () => filterAlerts(items || [], { status, severity, metric, search, sort }),
    [items, status, severity, metric, search, sort]
  );
  const metrics = useMemo(
    () => [...new Set((items || []).map((item) => item.metricName).filter(Boolean))].sort(),
    [items]
  );
  const sourceOptions = useMemo(() => {
    const options = new Map(sources.map((source) => [String(source.id), source.name]));
    (items || []).forEach((item) => {
      if (item.sourceId && !options.has(String(item.sourceId)))
        options.set(String(item.sourceId), `数据源 #${item.sourceId}`);
    });
    if (sourceId && !options.has(sourceId)) options.set(sourceId, `数据源 #${sourceId}`);
    return [...options];
  }, [sources, items, sourceId]);
  const pageCount = Math.max(1, Math.ceil(filtered.length / pageSize));
  const page = Math.min(pageCount, Math.max(1, Math.floor(requestedPage)));
  const rows = filtered.slice((page - 1) * pageSize, page * pageSize);
  const hasFilters = Boolean(status || severity || metric || search);
  const lastSeenAt = latestTimestamp(items || [], "lastSeenAt");
  const detailQuery = params.toString() ? `?${params.toString()}` : "";

  function updateFilters(changes) {
    setParams(
      (current) => {
        const next = new URLSearchParams(current);
        next.delete("page");
        Object.entries(changes).forEach(([key, value]) =>
          value ? next.set(key, String(value)) : next.delete(key)
        );
        return next;
      },
      { replace: true }
    );
  }
  function clearFilters() {
    updateFilters({ status: "", severity: "", metric: "", q: "" });
  }

  return (
    <PageFrame className="monitoring-page">
      <header className="monitoring-toolbar" aria-label="异常监测工具栏">
        <div className="monitoring-toolbar__main">
          <h2>异常监测</h2>
          <span className="monitoring-toolbar__divider" aria-hidden="true" />
          <label className="monitoring-source">
            <Icon name="database" size={14} />
            <select
              aria-label="监测数据源"
              value={sourceId}
              onChange={(event) => updateFilters({ sourceId: event.target.value, metric: "" })}
            >
              <option value="">全部授权数据源</option>
              {sourceOptions.map(([id, name]) => (
                <option key={id} value={id}>
                  {name}
                </option>
              ))}
            </select>
          </label>
        </div>
        <Button
          variant="ghost"
          disabled={!items && !error}
          onClick={() => setRefresh((value) => value + 1)}
        >
          <Icon name="refresh" size={14} />
          刷新记录
        </Button>
        <div className="monitoring-provenance">
          <span>
            <Icon name="shield" size={12} />
            企业规则 · 只读查看
          </span>
          <span>最近最多 {RECORD_LIMIT} 条记录</span>
          <span>
            {error
              ? "异常记录获取失败"
              : items
                ? `最近出现 ${formatTime(lastSeenAt)}`
                : "正在获取异常记录"}
          </span>
          {sourceError && (
            <span className="monitoring-provenance__warning" title={sourceError.message}>
              数据源名称暂不可用
            </span>
          )}
        </div>
      </header>

      <section className="monitoring-summary" aria-label="范围内异常汇总">
        <Summary
          label="告警中"
          value={counts?.firing}
          tone="red"
          description="已触发的异常"
          testId="monitoring-count-firing"
        />
        <Summary
          label="恢复观察"
          value={counts?.recovering}
          tone="amber"
          description="等待恢复条件满足"
        />
        <Summary
          label="已恢复"
          value={counts?.resolved}
          tone="green"
          description="已满足规则恢复条件"
        />
        <Summary label="涉及对象" value={counts?.affected} description="告警与恢复观察中的对象" />
      </section>

      <section className="monitoring-panel" aria-label="异常记录工作区">
        <div className="monitoring-panel__toolbar">
          <div className="monitoring-tabs" role="group" aria-label="告警状态">
            {STATUS_TABS.map((tab) => (
              <button
                key={tab.value}
                type="button"
                aria-pressed={status === tab.value}
                onClick={() => updateFilters({ status: tab.value })}
              >
                {tab.label}
                <span>{counts ? (tab.value ? counts[tab.value] : items.length) : "—"}</span>
              </button>
            ))}
          </div>
          <label className="monitoring-search">
            <Icon name="search" size={14} />
            <input
              type="search"
              aria-label="搜索异常"
              placeholder="搜索异常、指标或对象"
              value={search}
              onChange={(event) => updateFilters({ q: event.target.value })}
            />
          </label>
        </div>
        <div className="monitoring-filters">
          <Icon name="filter" size={14} />
          <select
            aria-label="严重程度"
            value={severity}
            onChange={(event) => updateFilters({ severity: event.target.value })}
          >
            <option value="">全部级别</option>
            {Object.entries(ALERT_SEVERITIES).map(([value, definition]) => (
              <option key={value} value={value}>
                {definition.label}
              </option>
            ))}
          </select>
          <select
            aria-label="监控指标"
            value={metric}
            onChange={(event) => updateFilters({ metric: event.target.value })}
          >
            <option value="">全部指标</option>
            {metric && !metrics.includes(metric) && <option value={metric}>{metric}</option>}
            {metrics.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
          {hasFilters && (
            <button type="button" className="monitoring-text-action" onClick={clearFilters}>
              <Icon name="close" size={12} />
              清除筛选
            </button>
          )}
          <label className="monitoring-sort">
            排序
            <select
              aria-label="异常排序"
              value={sort}
              onChange={(event) => updateFilters({ sort: event.target.value })}
            >
              <option value="latest">最近出现</option>
              <option value="severity">严重程度优先</option>
            </select>
          </label>
        </div>

        {error ? (
          <ErrorNotice
            title="异常记录加载失败"
            detail={error.message}
            onRetry={() => setRefresh((value) => value + 1)}
            className="monitoring-inline-error"
          />
        ) : !items ? (
          <div className="monitoring-loading" role="status">
            <Icon name="clock" size={18} />
            <span>正在加载异常记录…</span>
          </div>
        ) : !filtered.length ? (
          <div className="monitoring-empty" role="status">
            <span className="monitoring-empty__icon">
              <Icon name={items.length || hasFilters ? "search" : "scan"} size={24} />
            </span>
            <h3>{items.length || hasFilters ? "没有匹配的异常记录" : "当前范围暂无异常记录"}</h3>
            <p>
              {items.length || hasFilters
                ? "试试其他状态、级别或关键词；清除筛选可查看范围内全部已加载记录。"
                : "这里只展示已生成的异常记录；没有记录不代表监测任务的运行状态，也不构成业务正常的结论。"}
            </p>
            {(items.length > 0 || hasFilters) && (
              <Button size="sm" onClick={clearFilters}>
                重置筛选
              </Button>
            )}
          </div>
        ) : (
          <div
            className="monitoring-table-scroll"
            role="region"
            aria-label="异常表格，可横向滚动"
            tabIndex={0}
          >
            <table className="monitoring-table" aria-label="异常记录">
              <thead>
                <tr>
                  <th>异常 / 监控指标</th>
                  <th>监测对象</th>
                  <th className="is-numeric">当前值 / 基线</th>
                  <th className="monitoring-table__samples is-numeric">样本量</th>
                  <th>级别</th>
                  <th>状态</th>
                  <th>最近出现</th>
                  <th>
                    <span className="sr-only">证据入口</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {rows.map((item) => (
                  <tr key={item.id}>
                    <td className="monitoring-table__title">
                      <Link
                        to={`/workspace/alerts/${encodeURIComponent(item.id)}${detailQuery}`}
                        title={item.title}
                      >
                        {item.title}
                      </Link>
                      <span>{item.metricName || "指标名称未提供"}</span>
                    </td>
                    <td className="monitoring-table__entity">
                      <strong title={item.entityName || item.entityId}>
                        {item.entityName || item.entityId || "—"}
                      </strong>
                      <small>
                        {item.entityId || "—"}
                        {!sourceId
                          ? ` · ${sources.find((source) => source.id === item.sourceId)?.name || `数据源 #${item.sourceId ?? "—"}`}`
                          : ""}
                      </small>
                    </td>
                    <td className="is-numeric">
                      <strong className="monitoring-table__value">
                        {formatNumber(item.currentValue)}
                      </strong>
                      <small>基线 {formatNumber(item.baselineValue)}</small>
                    </td>
                    <td className="monitoring-table__samples is-numeric">
                      {formatNumber(item.sampleSize)}
                    </td>
                    <td>
                      <MonitoringStatus kind="severity" value={item.severity} />
                    </td>
                    <td>
                      <MonitoringStatus value={item.status} />
                    </td>
                    <td className="monitoring-table__time">{formatTime(item.lastSeenAt)}</td>
                    <td>
                      <Link
                        className="monitoring-evidence-link"
                        aria-label={`查看证据：${item.title}`}
                        to={`/workspace/alerts/${encodeURIComponent(item.id)}${detailQuery}`}
                      >
                        <Icon name="chevron" size={15} />
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <footer className="monitoring-list-footer">
          <span aria-live="polite">
            {items
              ? `${filtered.length} 条匹配 · 范围内已加载 ${items.length} 条`
              : "汇总仅针对已加载记录"}
            {items?.length === RECORD_LIMIT ? "（已达加载上限）" : ""}
          </span>
          <div className="monitoring-pagination">
            <label>
              每页
              <select
                aria-label="每页异常条数"
                value={pageSize}
                onChange={(event) => updateFilters({ size: event.target.value })}
              >
                {PAGE_SIZES.map((size) => (
                  <option key={size} value={size}>
                    {size}
                  </option>
                ))}
              </select>
            </label>
            <button
              type="button"
              aria-label="上一页异常"
              disabled={!items || page === 1}
              onClick={() => updateFilters({ page: page - 1 })}
            >
              <Icon name="chevron" size={14} className="rotate-180" />
            </button>
            <span>
              {page} / {pageCount}
            </span>
            <button
              type="button"
              aria-label="下一页异常"
              disabled={!items || page >= pageCount}
              onClick={() => updateFilters({ page: page + 1 })}
            >
              <Icon name="chevron" size={14} />
            </button>
          </div>
        </footer>
      </section>
      <div className="monitoring-footnote">
        <span>
          <Icon name="help" size={12} />
          当前值与基线保留规则原值，不跨指标比较；触发条件和数据截至时间请进入证据详情。
        </span>
        <span>{items && `记录获取于 ${formatTime(loadedAt)}`}</span>
      </div>
    </PageFrame>
  );
}

function Summary({ label, value, tone = "neutral", description, testId }) {
  return (
    <div className={`monitoring-summary__item monitoring-summary__item--${tone}`}>
      <span className="monitoring-summary__label">{label}</span>
      <strong data-testid={testId}>
        {value ?? "—"}
        <small>{label === "涉及对象" ? "个" : "条"}</small>
      </strong>
      <p>{description}</p>
    </div>
  );
}
