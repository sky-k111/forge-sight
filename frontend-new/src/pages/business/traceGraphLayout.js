function round(value) {
  return Math.round(value * 100) / 100;
}

export function buildTracePath(points) {
  if (!points?.length) return "";
  const [[startX, startY], ...rest] = points;
  let path = `M${startX} ${startY}`;
  let currentX = startX;
  let currentY = startY;

  rest.forEach(([nextX, nextY]) => {
    if (currentX === nextX) {
      path += `V${nextY}`;
    } else {
      const pivotY = round((currentY + nextY) / 2);
      path += `V${pivotY}C${currentX} ${pivotY} ${nextX} ${pivotY} ${nextX} ${nextY}`;
    }
    currentX = nextX;
    currentY = nextY;
  });

  return path;
}

export function getTraceFitTransform({
  positions,
  viewportWidth,
  viewportHeight,
  nodeRadius = 16,
  labelWidth = 38,
  labelHeight = 28,
  padding = 18,
}) {
  if (!positions?.length || !viewportWidth || !viewportHeight) {
    return { scale: 1, x: 0, y: 0 };
  }

  const xs = positions.map(([x]) => x);
  const ys = positions.map(([, y]) => y);
  const minX = Math.min(...xs) - nodeRadius - labelWidth - padding;
  const maxX = Math.max(...xs) + nodeRadius + labelWidth + padding;
  const minY = Math.min(...ys) - nodeRadius - padding;
  const maxY = Math.max(...ys) + nodeRadius + labelHeight + padding;
  const contentWidth = maxX - minX;
  const contentHeight = maxY - minY;
  const scale = round(Math.min(viewportWidth / contentWidth, viewportHeight / contentHeight));
  const x = round((viewportWidth - contentWidth * scale) / 2 - minX * scale);
  const y = round((viewportHeight - contentHeight * scale) / 2 - minY * scale);

  return { scale, x, y };
}
