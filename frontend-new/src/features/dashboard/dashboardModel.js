function nextPosition(widgets, width = 3, height = 3) {
  const bottom = Math.max(0, ...widgets.map((item) => item.layout.y + item.layout.h));
  return { x: 0, y: bottom, w: width, h: height, minW: 2, minH: 2 };
}

export const SYSTEM_WIDGET_KINDS = ["context", "recent", "catalog"];

const SIZE_PRESETS = {
  kpi: { compact: [2, 2], standard: [3, 2], emphasis: [6, 3] },
  trend: { compact: [6, 5], standard: [8, 7], emphasis: [12, 8] },
  rank: { compact: [4, 5], standard: [6, 6], emphasis: [8, 7] },
  context: { compact: [3, 5], standard: [3, 7], emphasis: [6, 7] },
  recent: { compact: [5, 5], standard: [8, 6], emphasis: [12, 7] },
  catalog: { compact: [3, 5], standard: [4, 6], emphasis: [6, 7] },
};

export function updateWidgetLayouts(config, layout) {
  const byId = new Map(layout.map((item) => [item.i, item]));
  return {
    ...config,
    widgets: config.widgets.map((widget) => {
      const next = byId.get(widget.id);
      if (!next) return widget;
      return {
        ...widget,
        layout: {
          ...widget.layout,
          x: next.x,
          y: next.y,
          w: next.w,
          h: next.h,
        },
      };
    }),
  };
}

export function moveWidget(config, widgetId, delta, resize = false) {
  return {
    ...config,
    widgets: config.widgets.map((widget) => {
      if (widget.id !== widgetId) return widget;
      const layout = { ...widget.layout };
      if (resize) {
        layout.w = Math.max(layout.minW, Math.min(12 - layout.x, layout.w + delta.x));
        layout.h = Math.max(layout.minH, Math.min(20, layout.h + delta.y));
      } else {
        layout.x = Math.max(0, Math.min(12 - layout.w, layout.x + delta.x));
        layout.y = Math.max(0, layout.y + delta.y);
      }
      return { ...widget, layout };
    }),
  };
}

export function addWidget(config, definition) {
  if (SYSTEM_WIDGET_KINDS.includes(definition.kind)
    && config.widgets.some((item) => item.kind === definition.kind)) {
    return config;
  }
  const base = `${definition.kind}-${definition.metricId || "system"}`;
  let id = base;
  let suffix = 2;
  while (config.widgets.some((item) => item.id === id)) id = `${base}-${suffix++}`;
  const dimensions = {
    trend: [8, 7],
    rank: [4, 6],
    context: [3, 7],
    recent: [5, 6],
    catalog: [3, 6],
  }[definition.kind] || [3, 3];
  return {
    ...config,
    widgets: [
      ...config.widgets,
      { ...definition, id, layout: nextPosition(config.widgets, ...dimensions) },
    ],
  };
}

export function removeWidget(config, widgetId) {
  return { ...config, widgets: config.widgets.filter((item) => item.id !== widgetId) };
}

export function updateWidgetAppearance(config, widgetId, patch) {
  return {
    ...config,
    widgets: config.widgets.map((widget) => (
      widget.id === widgetId
        ? { ...widget, appearance: { ...widget.appearance, ...patch } }
        : widget
    )),
  };
}

export function applyWidgetSizePreset(config, widgetId, preset) {
  return {
    ...config,
    widgets: config.widgets.map((widget) => {
      if (widget.id !== widgetId) return widget;
      const [w, h] = SIZE_PRESETS[widget.kind]?.[preset] || [widget.layout.w, widget.layout.h];
      return {
        ...widget,
        layout: {
          ...widget.layout,
          x: Math.min(widget.layout.x, 12 - w),
          w,
          h,
          minW: Math.min(widget.layout.minW, w),
          minH: Math.min(widget.layout.minH, h),
        },
      };
    }),
  };
}
