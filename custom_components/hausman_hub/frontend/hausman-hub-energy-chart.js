const chartNumber = (value, digits = 1) => Number.isFinite(Number(value))
  ? new Intl.NumberFormat("ru-RU", { maximumFractionDigits: digits }).format(Number(value))
  : "—";

export function renderEnergyHistoryChart(panel, source, deps, retry) {
  const { el, setAttr } = deps;
  const wrap = el("div", "energy-history");
  const metric = panel._energyHistoryMetric || "power";
  const store = metric === "energy" ? panel._energyConsumptionHistory : panel._energyHistory;
  const history = store && store[source.id];
  const period = panel._energyHistoryPeriod || "day";
  const powerLabels = {
    day: ["за последние 24 часа", "Почасовая средняя мощность · последние 24 часа"],
    week: ["за последние 7 дней", "Почасовая средняя мощность · последние 7 дней"],
    month: ["за последний месяц", "Средняя мощность по дням · последний месяц"],
    year: ["за последний год", "Средняя мощность по дням · последний год"],
  };
  const energyLabels = {
    day: ["за последние 24 часа", "Расход энергии по часам · последние 24 часа"],
    week: ["за последние 7 дней", "Расход энергии по часам · последние 7 дней"],
    month: ["за последний месяц", "Расход энергии по дням · последний месяц"],
    year: ["за последний год", "Расход энергии по дням · последний год"],
  };
  const labels = metric === "energy" ? energyLabels : powerLabels;
  const unit = metric === "energy" ? "кВт·ч" : "Вт";
  const points = Array.isArray(history) ? history.map((point) => ({
    start: point.start,
    value: Number(point.mean),
  })).filter((point) => Number.isFinite(point.value)) : [];
  const values = points.map((point) => point.value);
  if (!values.length) {
    const empty = el("div", "energy-history-empty");
    empty.appendChild(el("strong", null, panel._energyHistoryError ? "Не удалось получить историю" : `История ${metric === "energy" ? "расхода" : "мощности"} пока недоступна`));
    empty.appendChild(el("span", null, panel._energyHistoryError
      ? "Проверьте Recorder Home Assistant и повторите загрузку."
      : "Текущие показания продолжают обновляться."));
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
  const stats = el("div", "energy-chart-metrics");
  const metricItems = metric === "energy"
    ? [["За период", total, 3], ["Среднее", average, 3], ["Пиковый интервал", max, 3], ["Интервалов", values.length, 0]]
    : [["Сейчас", latest, 1], ["Среднее", average, 1], ["Пик", max, 1], ["Минимум", min, 1]];
  metricItems.forEach(([label, value, digits]) => {
    const item = el("span", "energy-chart-metric");
    item.appendChild(el("small", null, label));
    item.appendChild(el("strong", null, label === "Интервалов" ? String(value) : `${chartNumber(value, digits)} ${unit}`));
    stats.appendChild(item);
  });
  wrap.appendChild(stats);

  const bucketSize = Math.max(1, Math.ceil(values.length / 36));
  const visible = [];
  for (let index = 0; index < points.length; index += bucketSize) {
    const bucket = points.slice(index, index + bucketSize);
    const bucketTotal = bucket.reduce((sum, point) => sum + point.value, 0);
    visible.push({
      start: bucket[0].start,
      value: metric === "energy" ? bucketTotal : bucketTotal / bucket.length,
    });
  }
  const visibleValues = visible.map((point) => point.value);
  const chartMax = Math.max(...visibleValues, 1);
  const chartMin = Math.min(0, ...visibleValues);
  const span = Math.max(chartMax - chartMin, 1);
  const plot = el("div", "energy-chart-plot");
  const chart = el("canvas", "energy-history-canvas");
  chart.width = 960;
  chart.height = 224;
  setAttr(chart, "role", "img");
  setAttr(chart, "aria-label", `График ${metric === "energy" ? "расхода энергии" : "мощности"} ${source.name} ${labels[period][0]}`);
  setAttr(chart, "title", `${chartNumber(values[values.length - 1], metric === "energy" ? 3 : 1)} ${unit} · ${labels[period][1]}`);
  drawChart(chart, visible, chartMin, chartMax, span, metric, unit, period);
  plot.appendChild(chart);
  plot.appendChild(el("div", "energy-chart-caption", labels[period][1]));
  wrap.appendChild(plot);
  return wrap;
}

function drawChart(chart, visible, chartMin, chartMax, span, metric, unit, period) {
  if (typeof chart.getContext !== "function") return;
  const context = chart.getContext("2d");
  if (!context) return;
  const width = chart.width;
  const height = chart.height;
  const pad = { left: 88, right: 24, top: 38, bottom: 36 };
  context.strokeStyle = "rgba(132, 151, 177, .18)";
  context.lineWidth = 1;
  for (let row = 0; row < 4; row += 1) {
    const y = pad.top + ((height - pad.top - pad.bottom) / 3) * row;
    context.beginPath();
    context.moveTo(pad.left, y);
    context.lineTo(width - pad.right, y);
    context.stroke();
    context.fillStyle = "rgba(132, 151, 177, .82)";
    context.font = "22px sans-serif";
    context.textAlign = "right";
    context.textBaseline = "middle";
    const axisValue = chartMax - ((chartMax - chartMin) / 3) * row;
    context.fillText(`${chartNumber(axisValue, metric === "energy" ? 2 : 0)} ${unit}`, pad.left - 12, y);
  }
  const chartPoints = visible.map((point, index) => ({
    x: pad.left + (index / Math.max(visible.length - 1, 1)) * (width - pad.left - pad.right),
    y: pad.top + (1 - ((point.value - chartMin) / span)) * (height - pad.top - pad.bottom),
  }));
  const fill = context.createLinearGradient(0, pad.top, 0, height - pad.bottom);
  fill.addColorStop(0, "rgba(79, 140, 255, .28)");
  fill.addColorStop(1, "rgba(79, 140, 255, 0)");
  context.beginPath();
  chartPoints.forEach((point, index) => index ? context.lineTo(point.x, point.y) : context.moveTo(point.x, point.y));
  context.lineTo(chartPoints[chartPoints.length - 1].x, height - pad.bottom);
  context.lineTo(chartPoints[0].x, height - pad.bottom);
  context.closePath();
  context.fillStyle = fill;
  context.fill();
  context.beginPath();
  chartPoints.forEach((point, index) => index ? context.lineTo(point.x, point.y) : context.moveTo(point.x, point.y));
  context.strokeStyle = "#4F8CFF";
  context.lineWidth = 5;
  context.lineJoin = "round";
  context.lineCap = "round";
  context.stroke();
  const lastPoint = chartPoints[chartPoints.length - 1];
  context.beginPath();
  context.arc(lastPoint.x, lastPoint.y, 7, 0, Math.PI * 2);
  context.fillStyle = "#4F8CFF";
  context.fill();

  const labelIndexes = [...new Set([0, Math.floor((visible.length - 1) / 2), visible.length - 1])];
  labelIndexes.forEach((index) => {
    context.fillStyle = "rgba(132, 151, 177, .88)";
    context.font = "22px sans-serif";
    context.textBaseline = "bottom";
    context.textAlign = index === 0 ? "left" : (index === visible.length - 1 ? "right" : "center");
    context.fillText(chartTimeLabel(visible[index].start, period), chartPoints[index].x, height - 4);
  });
}

function chartTimeLabel(value, period) {
  const date = value ? new Date(value) : null;
  if (!date || Number.isNaN(date.getTime())) return "";
  if (period === "day" || period === "week") return date.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
  if (period === "month") return date.toLocaleDateString("ru-RU", { day: "numeric", month: "short" });
  return date.toLocaleDateString("ru-RU", { month: "short", year: "2-digit" });
}
