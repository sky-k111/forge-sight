import { httpPost } from "./_http";

// 阶段 4：为后续 Agent 流水线获取按数据源隔离的最小语义上下文包。
export function retrieveSemanticContext({
  question,
  sourceId,
  maxMetrics = 5,
  maxTables = 8,
  maxFieldsPerTable = 12,
}) {
  return httpPost("/api/retrieval/context", {
    question,
    sourceId,
    maxMetrics,
    maxTables,
    maxFieldsPerTable,
  });
}
