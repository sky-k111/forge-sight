const SCHEMA_GRAPH_WIDTH = 920;
const SCHEMA_GRAPH_HEIGHT = 245;
const SCHEMA_NODE_WIDTH = 160;
const SCHEMA_NODE_HEIGHT = 60;

function round(value) {
  return Math.round(value * 100) / 100;
}

function toPercent(value, total) {
  return `${round(value / total * 100).toFixed(2)}%`;
}

export function buildSchemaLinkPath(from, to, nodeWidth = SCHEMA_NODE_WIDTH, nodeHeight = SCHEMA_NODE_HEIGHT) {
  const startX = from.x + nodeWidth;
  const startY = from.y + nodeHeight / 2;
  const endX = to.x;
  const endY = to.y + nodeHeight / 2;
  const offset = round((endX - startX) / 2);

  return `M${round(startX)} ${round(startY)} C${round(startX + offset)} ${round(startY)} ${round(endX - offset)} ${round(endY)} ${round(endX)} ${round(endY)}`;
}

export function getSchemaNodeStyle(node, nodeWidth = SCHEMA_NODE_WIDTH, nodeHeight = SCHEMA_NODE_HEIGHT) {
  return {
    left: toPercent(node.x, SCHEMA_GRAPH_WIDTH),
    top: toPercent(node.y, SCHEMA_GRAPH_HEIGHT),
    width: toPercent(nodeWidth, SCHEMA_GRAPH_WIDTH),
    height: toPercent(nodeHeight, SCHEMA_GRAPH_HEIGHT),
  };
}

export {
  SCHEMA_GRAPH_HEIGHT,
  SCHEMA_GRAPH_WIDTH,
  SCHEMA_NODE_HEIGHT,
  SCHEMA_NODE_WIDTH,
};
