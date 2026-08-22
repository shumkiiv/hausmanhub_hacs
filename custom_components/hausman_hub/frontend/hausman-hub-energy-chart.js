const chartNumber = (value, digits = 1) => Number.isFinite(Number(value))
  ? new Intl.NumberFormat("ru-RU", { maximumFractionDigits: digits }).format(Number(value))
  : "—";

function tryParseColor(value) {
  const hex = value.match(/^#([0-9a-f]{3}|[0-9a-f]{6})$/i);
  if (hex) {
    let body = hex[1];
    if (body.length === 3) body = body.split("").map((char) => char + char).join("");
    return { r: parseInt(body.slice(0, 2), 16), g: parseInt(body.slice(2, 4), 16), b: parseInt(body.slice(4, 6), 16) };
  }
  const rgb = value.match(/^rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)/i);
  return rgb ? { r: Number(rgb[1]), g: Number(rgb[2]), b: Number(rgb[3]) } : null;
}

function parseThemeColor(raw, fallback) {
  return tryParseColor(String(raw || "").trim())
    || tryParseColor(String(fallback || "").trim())
    || { r: 79, g: 140, b: 255 };
}

const colorWithAlpha = (color, alpha) => `rgba(${color.r}, ${color.g}, ${color.b}, ${alpha})`;

export function energyChartTheme(panel) {
  let textDim = "";
  let accent = "";
  try {
    if (panel && typeof window !== "undefined" && typeof window.getComputedStyle === "function") {
      const styles = window.getComputedStyle(panel);
      textDim = styles.getPropertyValue("--hmh-text-dim");
      accent = styles.getPropertyValue("--hmh-accent");
    }
  } catch (error) {
    textDim = "";
    accent = "";
  }
  const dim = parseThemeColor(textDim, "#8497B1");
  const line = parseThemeColor(accent, "#4F8CFF");
  return {
    grid: colorWithAlpha(dim, 0.18), axis: colorWithAlpha(dim, 0.86),
    label: colorWithAlpha(dim, 0.92), average: colorWithAlpha(dim, 0.7),
    line: colorWithAlpha(line, 1), fillTop: colorWithAlpha(line, 0.3), fillBottom: colorWithAlpha(line, 0.02),
  };
}

export function redrawEnergyChartsForTheme(panel) {
  const root = panel && panel.shadowRoot;
  if (!root || typeof root.querySelectorAll !== "function") return;
  root.querySelectorAll("canvas.energy-history-canvas").forEach((canvas) => {
    if (typeof canvas._hmhEnergyRedraw === "function") canvas._hmhEnergyRedraw();
  });
}

function niceStep(value) {
  const safe = Math.max(Math.abs(Number(value)) || 0, 0.000001);
  const exponent = 10 ** Math.floor(Math.log10(safe));
  const fraction = safe / exponent;
  const nice = fraction <= 1 ? 1 : (fraction <= 2 ? 2 : (fraction <= 2.5 ? 2.5 : (fraction <= 5 ? 5 : 10)));
  return nice * exponent;
}

export function energyChartScale(values) {
  const finite = (Array.isArray(values) ? values : []).map(Number).filter(Number.isFinite);
  const rawMin = finite.length ? Math.min(...finite) : 0;
  const rawMax = finite.length ? Math.max(...finite) : 1;
  const initialMin = Math.min(0, rawMin);
  const initialMax = Math.max(0, rawMax);
  const step = niceStep(Math.max(initialMax - initialMin, 1) / 3);
  const min = initialMin < 0 ? Math.floor(initialMin / step) * step : 0;
  let max = Math.ceil(initialMax / step) * step;
  if (max <= min) max = min + step * 3;
  while ((max - min) / step < 3) max += step;
  const count = Math.round((max - min) / step);
  return { min, max, step, ticks: Array.from({ length: count + 1 }, (_, index) => min + step * index) };
}

function chartDate(value) {
  const date = value ? new Date(value) : null;
  return date && !Number.isNaN(date.getTime()) ? date : null;
}

const dateOptions = (options, timeZone) => timeZone ? { ...options, timeZone } : options;
const shortDate = (date, timeZone) => date.toLocaleDateString("ru-RU", dateOptions({ day: "numeric", month: "short" }, timeZone));

function pointTimeLabel(value, period, endpoint, timeZone) {
  const date = chartDate(value);
  if (!date) return { primary: "", secondary: "" };
  if (period === "day") return {
    primary: date.toLocaleTimeString("ru-RU", dateOptions({ hour: "2-digit", minute: "2-digit" }, timeZone)),
    secondary: endpoint ? shortDate(date, timeZone) : "",
  };
  if (period === "week") return {
    primary: date.toLocaleDateString("ru-RU", dateOptions({ weekday: "short" }, timeZone)),
    secondary: date.toLocaleTimeString("ru-RU", dateOptions({ hour: "2-digit", minute: "2-digit" }, timeZone)),
  };
  if (period === "month") return { primary: shortDate(date, timeZone), secondary: "" };
  return {
    primary: date.toLocaleDateString("ru-RU", dateOptions({ month: "short" }, timeZone)),
    secondary: endpoint ? date.toLocaleDateString("ru-RU", dateOptions({ year: "numeric" }, timeZone)) : "",
  };
}

export function energyChartTimeTicks(points, period, width, timeZone) {
  const items = Array.isArray(points) ? points : [];
  if (!items.length) return [];
  const count = Math.min(items.length, width >= 760 ? 5 : (width >= 520 ? 4 : 3));
  const indexes = Array.from({ length: count }, (_, index) => Math.round(index * (items.length - 1) / Math.max(count - 1, 1)));
  return [...new Set(indexes)].map((index, position, all) => ({
    index, ...pointTimeLabel(items[index].start, period, position === 0 || position === all.length - 1, timeZone),
  }));
}

export function energyChartWindowLabel(points, timeZone) {
  const items = Array.isArray(points) ? points : [];
  const first = items.length ? chartDate(items[0].start) : null;
  const last = items.length ? chartDate(items[items.length - 1].start) : null;
  if (!first || !last) return "Время дома";
  const time = (date) => date.toLocaleTimeString("ru-RU", dateOptions({ hour: "2-digit", minute: "2-digit" }, timeZone));
  return `${shortDate(first, timeZone)}, ${time(first)} - ${shortDate(last, timeZone)}, ${time(last)} · время дома`;
}

function fullPointLabel(value, timeZone) {
  const date = chartDate(value);
  if (!date) return "Время не указано";
  return date.toLocaleString("ru-RU", dateOptions({ day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" }, timeZone));
}

function axisValueLabel(value, metric, scale) {
  if (metric === "power" && Math.max(Math.abs(scale.min), Math.abs(scale.max)) >= 1000) {
    return `${chartNumber(value / 1000, 2)} кВт`;
  }
  return `${chartNumber(value, metric === "energy" ? 2 : 0)} ${metric === "energy" ? "кВт·ч" : "Вт"}`;
}

function chartTimeZone(panel) {
  const zone = panel && panel._hass && panel._hass.config && panel._hass.config.time_zone;
  if (!zone) return undefined;
  try {
    new Intl.DateTimeFormat("ru-RU", { timeZone: zone }).format(new Date());
    return zone;
  } catch (error) {
    return undefined;
  }
}

const pointExtremum = (points, value) => points.find((point) => point.value === value) || points[0];

export function renderEnergyHistoryChart(panel, source, deps, retry) {
  const { el, setAttr } = deps;
  const wrap = el("div", "energy-history");
  const metric = panel._energyHistoryMetric || "power";
  const store = metric === "energy" ? panel._energyConsumptionHistory : panel._energyHistory;
  const history = store && store[source.id];
  const period = panel._energyHistoryPeriod || "day";
  const timeZone = chartTimeZone(panel);
  const unit = metric === "energy" ? "кВт·ч" : "Вт";
  const points = Array.isArray(history) ? history.map((point, index) => ({
    start: point.start, value: Number(point.mean), order: index, timestamp: chartDate(point.start)?.getTime(),
  })).filter((point) => Number.isFinite(point.value)) : [];
  if (points.every((point) => Number.isFinite(point.timestamp))) {
    points.sort((left, right) => left.timestamp - right.timestamp || left.order - right.order);
  }
  const values = points.map((point) => point.value);
  if (!values.length) {
    const empty = el("div", "energy-history-empty");
    empty.appendChild(el("strong", null, panel._energyHistoryError ? "Не удалось получить историю" : `История ${metric === "energy" ? "расхода" : "мощности"} пока недоступна`));
    empty.appendChild(el("span", null, panel._energyHistoryError ? "Проверьте Recorder Home Assistant и повторите загрузку." : "Текущие показания продолжают обновляться."));
    const button = el("button", "secondary", "Обновить");
    button.type = "button";
    button.disabled = panel._energyHistoryLoading;
    button.addEventListener("click", retry);
    empty.appendChild(button);
    wrap.appendChild(empty);
    return wrap;
  }

  const max = Math.max(...values);
  const min = Math.min(...values);
  const average = values.reduce((sum, value) => sum + value, 0) / values.length;
  const total = values.reduce((sum, value) => sum + value, 0);
  const latest = values[values.length - 1];
  const latestLabel = period === "month" || period === "year" ? "Последний день" : "Последний час";
  const stats = el("div", "energy-chart-metrics");
  const metricItems = metric === "energy"
    ? [["За период", total, 3, energyChartWindowLabel(points, timeZone)], ["Среднее", average, 3, "На один интервал"], ["Пиковый интервал", max, 3, fullPointLabel(pointExtremum(points, max).start, timeZone)], ["Интервалов", values.length, 0, "Получено из истории"]]
    : [[latestLabel, latest, 1, fullPointLabel(points[points.length - 1].start, timeZone)], ["Среднее", average, 1, "За выбранный период"], ["Пик", max, 1, fullPointLabel(pointExtremum(points, max).start, timeZone)], ["Минимум", min, 1, fullPointLabel(pointExtremum(points, min).start, timeZone)]];
  metricItems.forEach(([label, value, digits, caption]) => {
    const item = el("span", "energy-chart-metric");
    item.appendChild(el("small", null, label));
    item.appendChild(el("strong", null, label === "Интервалов" ? String(value) : `${chartNumber(value, digits)} ${unit}`));
    item.appendChild(el("span", null, caption));
    stats.appendChild(item);
  });
  wrap.appendChild(stats);

  const bucketSize = Math.max(1, Math.ceil(values.length / 36));
  const visible = [];
  for (let index = 0; index < points.length; index += bucketSize) {
    const bucket = points.slice(index, index + bucketSize);
    const bucketTotal = bucket.reduce((sum, point) => sum + point.value, 0);
    visible.push({ start: bucket[Math.floor((bucket.length - 1) / 2)].start, value: metric === "energy" ? bucketTotal : bucketTotal / bucket.length });
  }
  const visibleValues = visible.map((point) => point.value);
  const chartAverage = visibleValues.reduce((sum, value) => sum + value, 0) / visibleValues.length;
  const scale = energyChartScale(visibleValues);
  const plot = el("div", "energy-chart-plot");
  const meta = el("div", "energy-chart-meta");
  const legend = el("span", "energy-chart-legend");
  legend.appendChild(el("i"));
  legend.appendChild(el("span", null, metric === "energy" ? "Расход за интервал" : "Средняя мощность за час"));
  meta.appendChild(legend);
  meta.appendChild(el("span", "energy-chart-window", energyChartWindowLabel(visible, timeZone)));
  plot.appendChild(meta);
  const stage = el("div", "energy-chart-stage");
  const chart = el("canvas", "energy-history-canvas");
  chart.width = 960;
  chart.height = 210;
  chart.tabIndex = 0;
  setAttr(chart, "role", "img");
  setAttr(chart, "aria-label", `График ${metric === "energy" ? "расхода энергии" : "мощности"} ${source.name}. ${energyChartWindowLabel(visible, timeZone)}`);
  const tooltip = el("span", "energy-chart-tooltip");
  tooltip.hidden = true;
  tooltip.appendChild(el("strong"));
  tooltip.appendChild(el("span"));
  const updateTooltip = () => {
    const index = chart._hmhEnergyHoverIndex;
    const geometry = chart._hmhEnergyGeometry;
    if (!Number.isInteger(index) || !geometry || !visible[index]) { tooltip.hidden = true; return; }
    const point = geometry.points[index];
    tooltip.hidden = false;
    tooltip.querySelector("strong").textContent = fullPointLabel(visible[index].start, timeZone);
    tooltip.querySelector("span").textContent = `${chartNumber(visible[index].value, metric === "energy" ? 3 : 1)} ${unit}`;
    tooltip.style.left = `${point.x}px`;
    tooltip.style.top = `${point.y}px`;
    tooltip.classList.toggle("is-left", point.x < 112);
    tooltip.classList.toggle("is-right", point.x > geometry.width - 112);
    tooltip.classList.toggle("is-below", point.y < 62);
  };
  const redraw = () => {
    chart._hmhEnergyGeometry = drawChart(chart, visible, scale, chartAverage, metric, period, timeZone, energyChartTheme(panel));
    updateTooltip();
  };
  chart._hmhEnergyRedraw = redraw;
  const selectPoint = (index) => {
    chart._hmhEnergyHoverIndex = Math.max(0, Math.min(visible.length - 1, index));
    redraw();
  };
  const pointFromEvent = (event) => {
    const rect = typeof chart.getBoundingClientRect === "function" ? chart.getBoundingClientRect() : null;
    const geometry = chart._hmhEnergyGeometry;
    if (!rect || !rect.width || !geometry) return;
    const x = (Number(event.clientX) - rect.left) * geometry.width / rect.width;
    const ratio = (x - geometry.pad.left) / Math.max(geometry.width - geometry.pad.left - geometry.pad.right, 1);
    selectPoint(Math.round(Math.max(0, Math.min(1, ratio)) * (visible.length - 1)));
  };
  chart.addEventListener("pointermove", pointFromEvent);
  chart.addEventListener("pointerdown", pointFromEvent);
  chart.addEventListener("pointerleave", () => { chart._hmhEnergyHoverIndex = null; redraw(); });
  chart.addEventListener("focus", () => selectPoint(visible.length - 1));
  chart.addEventListener("blur", () => { chart._hmhEnergyHoverIndex = null; redraw(); });
  chart.addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End", "Escape"].includes(event.key)) return;
    event.preventDefault();
    if (event.key === "Escape") { chart._hmhEnergyHoverIndex = null; redraw(); return; }
    if (event.key === "Home") { selectPoint(0); return; }
    if (event.key === "End") { selectPoint(visible.length - 1); return; }
    const current = Number.isInteger(chart._hmhEnergyHoverIndex) ? chart._hmhEnergyHoverIndex : visible.length - 1;
    selectPoint(current + (event.key === "ArrowLeft" ? -1 : 1));
  });
  redraw();
  stage.appendChild(chart);
  stage.appendChild(tooltip);
  plot.appendChild(stage);
  wrap.appendChild(plot);
  if (typeof requestAnimationFrame === "function") requestAnimationFrame(redraw);
  if (typeof ResizeObserver === "function") {
    const observer = new ResizeObserver(() => {
      if (chart.isConnected === false) { observer.disconnect(); return; }
      redraw();
    });
    observer.observe(chart);
    chart._hmhEnergyResizeObserver = observer;
  }
  return wrap;
}

function canvasSize(chart, context) {
  const rect = typeof chart.getBoundingClientRect === "function" ? chart.getBoundingClientRect() : null;
  const width = Math.max(320, Math.round(rect && rect.width || 960));
  const height = Math.max(170, Math.round(rect && rect.height || 210));
  const ratio = Math.min(2, Math.max(1, typeof window !== "undefined" ? Number(window.devicePixelRatio) || 1 : 1));
  const pixelWidth = Math.round(width * ratio);
  const pixelHeight = Math.round(height * ratio);
  if (chart.width !== pixelWidth) chart.width = pixelWidth;
  if (chart.height !== pixelHeight) chart.height = pixelHeight;
  if (typeof context.setTransform === "function") context.setTransform(ratio, 0, 0, ratio, 0, 0);
  return { width, height };
}

function drawChart(chart, visible, scale, average, metric, period, timeZone, theme) {
  if (typeof chart.getContext !== "function") return null;
  const context = chart.getContext("2d");
  if (!context) return null;
  const colors = theme || energyChartTheme(null);
  const { width, height } = canvasSize(chart, context);
  const pad = { left: width < 560 ? 58 : 72, right: width < 560 ? 10 : 18, top: 16, bottom: 45 };
  const plotWidth = width - pad.left - pad.right;
  const plotHeight = height - pad.top - pad.bottom;
  const span = Math.max(scale.max - scale.min, 0.000001);
  context.clearRect(0, 0, width, height);
  context.font = "12px sans-serif";
  context.textAlign = "right";
  context.textBaseline = "middle";
  scale.ticks.forEach((tick) => {
    const y = pad.top + (1 - ((tick - scale.min) / span)) * plotHeight;
    context.beginPath();
    context.moveTo(pad.left, y);
    context.lineTo(width - pad.right, y);
    context.strokeStyle = colors.grid;
    context.lineWidth = 1;
    context.stroke();
    context.fillStyle = colors.axis;
    context.fillText(axisValueLabel(tick, metric, scale), pad.left - 9, y);
  });
  const points = visible.map((point, index) => ({
    x: pad.left + (index / Math.max(visible.length - 1, 1)) * plotWidth,
    y: pad.top + (1 - ((point.value - scale.min) / span)) * plotHeight,
  }));
  const averageY = pad.top + (1 - ((average - scale.min) / span)) * plotHeight;
  if (averageY >= pad.top && averageY <= height - pad.bottom) {
    context.save();
    context.setLineDash([5, 5]);
    context.beginPath();
    context.moveTo(pad.left, averageY);
    context.lineTo(width - pad.right, averageY);
    context.strokeStyle = colors.average;
    context.lineWidth = 1;
    context.stroke();
    context.restore();
  }
  const fill = context.createLinearGradient(0, pad.top, 0, height - pad.bottom);
  fill.addColorStop(0, colors.fillTop);
  fill.addColorStop(1, colors.fillBottom);
  context.beginPath();
  points.forEach((point, index) => index ? context.lineTo(point.x, point.y) : context.moveTo(point.x, point.y));
  context.lineTo(points[points.length - 1].x, height - pad.bottom);
  context.lineTo(points[0].x, height - pad.bottom);
  context.closePath();
  context.fillStyle = fill;
  context.fill();
  context.beginPath();
  points.forEach((point, index) => index ? context.lineTo(point.x, point.y) : context.moveTo(point.x, point.y));
  context.strokeStyle = colors.line;
  context.lineWidth = 3;
  context.lineJoin = "round";
  context.lineCap = "round";
  context.stroke();
  const latest = points[points.length - 1];
  context.beginPath();
  context.arc(latest.x, latest.y, 4.5, 0, Math.PI * 2);
  context.fillStyle = colors.line;
  context.fill();
  if (Number.isInteger(chart._hmhEnergyHoverIndex) && points[chart._hmhEnergyHoverIndex]) {
    const active = points[chart._hmhEnergyHoverIndex];
    context.beginPath();
    context.moveTo(active.x, pad.top);
    context.lineTo(active.x, height - pad.bottom);
    context.strokeStyle = colors.average;
    context.lineWidth = 1;
    context.stroke();
    context.beginPath();
    context.arc(active.x, active.y, 6, 0, Math.PI * 2);
    context.fillStyle = colors.line;
    context.fill();
    context.lineWidth = 3;
    context.strokeStyle = "rgba(255,255,255,.92)";
    context.stroke();
  }
  energyChartTimeTicks(visible, period, plotWidth, timeZone).forEach((tick, position, ticks) => {
    const point = points[tick.index];
    context.fillStyle = colors.label;
    context.font = "12px sans-serif";
    context.textAlign = position === 0 ? "left" : (position === ticks.length - 1 ? "right" : "center");
    context.textBaseline = "bottom";
    context.fillText(tick.primary, point.x, tick.secondary ? height - 18 : height - 7);
    if (tick.secondary) {
      context.fillStyle = colors.axis;
      context.font = "10px sans-serif";
      context.fillText(tick.secondary, point.x, height - 4);
    }
  });
  return { width, height, pad, points };
}
