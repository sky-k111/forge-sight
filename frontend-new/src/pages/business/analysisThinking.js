// 分析页“Agent 思考”延迟常量与辅助函数，供组件与测试共享。
export const AGENT_THINKING_DELAY_MS = 1500;

export function waitForAgentThinking() {
  return new Promise((resolve) => window.setTimeout(resolve, AGENT_THINKING_DELAY_MS));
}
