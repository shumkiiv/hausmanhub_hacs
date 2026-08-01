export const HOME_SIGNAL_BINDINGS = [
  {
    key: "outdoor_temperature_entity_id", kind: "outdoor_temperature",
    title: "Наружная температура",
    helper: "Источник температуры именно на улице, а не в одной из комнат.",
    purpose: "Нужен для погодной блокировки отопления и оценки нагрузки на климат.",
    recommendation: "Выберите исправный уличный датчик. Если его нет — погодный сервис. Комнатные термодатчики, увлажнители и термоголовки не подходят.",
  },
  {
    key: "presence_entity_id", kind: "presence", title: "Общее присутствие дома",
    helper: "Общий режим дома, не датчик отдельной комнаты.",
    purpose: "Переключает общую политику между режимами «кто-то дома» и «никого нет».",
    recommendation: "Выберите режим «дома / не дома», профиль или телефон. Комнатные датчики настраиваются отдельно.",
  },
  {
    key: "central_heating_entity_id", kind: "central_heating",
    title: "Центральное отопление",
    helper: "Прямой сигнал работы или температура батареи / трубы отопления.",
    purpose: "Позволяет контуру понимать, работает ли котёл, насос или общая подача тепла.",
    recommendation: "Лучший вариант — прямой сигнал «работает / не работает». Если его нет, выберите датчик температуры батареи или трубы; обычные комнатные термометры и клавиши выключателей не подходят.",
  },
];

export function weatherSourceDisplayName(entityId) {
  const source = String(entityId || "").split(".", 2)[1] || "";
  const normalized = source.replace(/^(?:weather|forecast)_/, "");
  const known = {
    home_assistant: "Home Assistant",
    omsk: "Омск",
    yandex: "Яндекс",
    yandex_weather: "Яндекс Погода",
    openweathermap: "OpenWeather",
    met_no: "MET Norway",
    home: "Дом",
  };
  if (known[normalized]) return known[normalized];
  return normalized.split("_").filter(Boolean).map((part) => (
    part.charAt(0).toLocaleUpperCase("ru-RU") + part.slice(1)
  )).join(" ") || "Home Assistant";
}

const AWAY_MODE_PATTERN = /away|not[ _-]?home|никого.{0,8}(?:нет|дома)|не.{0,4}дома/i;
const CENTRAL_HEATING_IDENTITY_PATTERN =
  /central.{0,12}heat|heating|boiler|heat.{0,12}(pump|supply)|circulat.{0,12}pump|radiator|heating.{0,12}(pipe|flow|return)|(pipe|flow|return).{0,12}heat|централ.{0,12}отоп|отоплен|кот[её]л|теплоснаб|(насос|подач).{0,12}(отоп|тепл)|(отоп|тепл).{0,12}(насос|подач)|батаре|радиатор|труб.{0,12}(отоп|подач|обрат)|(отоп|подач|обрат).{0,12}труб|теплонос/;
const CENTRAL_HEATING_ACCESSORY_PATTERN =
  /(^|[^a-z0-9])trv([^a-z0-9]|$)|thermostatic.{0,12}radiator|radiator.{0,12}valve|термоголов|увлажн|humidifier/;
export const AWAY_MODE_TYPE = "Режим «Дома / Не дома»";
export const AWAY_MODE_EXPLANATION =
  "Логический режим дома: включено — никого нет, выключено — дома. Это не физический датчик присутствия.";

export function isAwayModeCandidate(candidate) {
  return AWAY_MODE_PATTERN.test([
    candidate?.name, candidate?.entity_id, candidate?.device_name,
  ].filter(Boolean).join(" "));
}

export function isCentralHeatingCandidate(candidate, identity) {
  return ["binary_sensor", "switch", "input_boolean", "sensor"].includes(candidate.domain)
    && (candidate.domain !== "binary_sensor"
      || ["heat", "running", "power"].includes(candidate.device_class))
    && (candidate.domain !== "sensor" || candidate.device_class === "temperature")
    && CENTRAL_HEATING_IDENTITY_PATTERN.test(identity)
    && !CENTRAL_HEATING_ACCESSORY_PATTERN.test(identity);
}

export function signalCandidateDisplayName(candidate, peers, normalize) {
  if (!candidate) return "Источник не выбран";
  if (candidate.domain === "person") {
    return `${candidate.name || candidate.entity_id} · профиль пользователя`;
  }
  const name = candidate.device_name || candidate.name || candidate.entity_id;
  if (candidate.domain !== "weather") return name;
  const duplicate = peers.filter((peer) => (
    peer.domain === "weather"
      && normalize(peer.name || peer.entity_id)
        === normalize(candidate.name || candidate.entity_id)
  )).length > 1;
  const generic = /^(?:forecast|weather|прогноз|погода)$/i.test(
    String(candidate.name || "").trim()
  );
  if (!duplicate && !generic) return name;
  const localized = /^(?:forecast|weather)$/i.test(String(name).trim())
    ? "Погода" : name;
  return `${localized} · ${weatherSourceDisplayName(candidate.entity_id)}`;
}

export function createHeatingTemperatureFields(config, deps) {
  const { el, numberField } = deps;
  const root = el("div", "heating-temperature-thresholds");
  root.appendChild(el("h3", "threshold-heading", "Температура батареи или трубы"));
  root.appendChild(el(
    "div", "muted threshold-intro",
    "Только для датчика батареи или трубы. Между порогами сохраняется предыдущее состояние."
  ));
  const on = numberField(config.onValue ?? 35, -40, 120, 0.5, config.onChange);
  const onRow = el("label", "form-field", "Считать отопление включённым от, °C");
  onRow.appendChild(on);
  const off = numberField(config.offValue ?? 30, -40, 120, 0.5, config.onChange);
  const offRow = el("label", "form-field", "Считать отопление выключенным ниже, °C");
  offRow.appendChild(off);
  const grid = el("div", "home-threshold-grid");
  grid.appendChild(onRow);
  grid.appendChild(offRow);
  root.appendChild(grid);
  return {
    root,
    values: () => ({ on: Number(on.value), off: Number(off.value) }),
    valid: () => on.value !== "" && off.value !== ""
      && Number.isFinite(Number(on.value)) && Number.isFinite(Number(off.value))
      && Number(on.value) >= -40 && Number(on.value) <= 120
      && Number(off.value) >= -40 && Number(off.value) <= 120
      && Number(off.value) < Number(on.value),
  };
}

export function createPriorityChoicePicker(owner, config, deps) {
  const { el, setAttr } = deps;
  const {
    title, helper, purpose, recommendation, candidates, current, signalKind, onChange,
    pickerId = "",
  } = config;
  const fieldset = el("fieldset", "signal-picker priority-signal-picker");
  fieldset.appendChild(el("legend", null, title));
  if (helper) fieldset.appendChild(el("div", "muted signal-picker-help", helper));
  if (purpose || recommendation) {
    const guide = el("div", "signal-picker-guide");
    [["Зачем это нужно", purpose], ["Как работает приоритет", recommendation]].forEach(([label, copy]) => {
      if (!copy) return;
      const item = el("div");
      item.appendChild(el("strong", null, label));
      item.appendChild(el("span", null, copy));
      guide.appendChild(item);
    });
    fieldset.appendChild(guide);
  }
  const currentValues = Array.isArray(current) ? current : current ? [current] : [];
  const visible = owner._signalCandidatesForPicker(
    candidates, currentValues[0] || "", signalKind
  );
  currentValues.slice(1).forEach((entityId) => {
    if (!visible.some((candidate) => candidate.entity_id === entityId)) {
      visible.push(...owner._candidateWithCurrent([], entityId));
    }
  });
  let selectedValues = [...new Set(currentValues)].filter((entityId) => (
    visible.some((candidate) => candidate.entity_id === entityId)
  )).slice(0, 8);
  const selected = el("div", "priority-source-list");
  const selectedHeading = el("div", "priority-source-heading");
  selectedHeading.appendChild(el("strong", null, "Порядок использования"));
  selectedHeading.appendChild(el(
    "span", "muted", "Первый доступный источник становится активным"
  ));
  fieldset.appendChild(selectedHeading);
  fieldset.appendChild(el(
    "div", "muted priority-source-kinds",
    "Можно совместно использовать Уличные датчики и Погодные сервисы."
  ));
  fieldset.appendChild(selected);
  const chooser = el("details", "signal-chooser priority-source-chooser");
  chooser.open = Boolean(pickerId && owner._openSignalPickers.has(pickerId));
  const summary = el("summary", "signal-chooser-summary");
  summary.appendChild(el("strong", null, "Добавить резервный источник"));
  const count = el("span", "muted");
  summary.appendChild(count);
  chooser.appendChild(summary);
  if (pickerId) chooser.addEventListener("toggle", () => {
    owner._openSignalPickers[chooser.open ? "add" : "delete"](pickerId);
  });
  const available = el("div", "priority-source-available");
  chooser.appendChild(available);
  fieldset.appendChild(chooser);

  const candidateById = new Map(
    visible.map((candidate) => [candidate.entity_id, candidate])
  );
  const changed = () => {
    onChange([...selectedValues]);
    render();
  };
  const render = () => {
    selected.innerHTML = "";
    if (!selectedValues.length) {
      selected.appendChild(el(
        "div", "priority-source-empty",
        "Источник не выбран — погодная блокировка отопления работать не будет."
      ));
    }
    selectedValues.forEach((entityId, index) => {
      const candidate = candidateById.get(entityId);
      const row = el(
        "div", `priority-source-row${index === 0 ? " is-primary" : ""}`
      );
      row.appendChild(el("span", "priority-source-number", String(index + 1)));
      const copy = el("span", "priority-source-copy");
      copy.appendChild(el(
        "strong", null, owner._signalCandidateDisplayName(candidate, visible)
      ));
      copy.appendChild(el(
        "small", null,
        index === 0
          ? "Основной источник"
          : `Резерв ${index} · включится, если источники выше недоступны`
      ));
      copy.appendChild(el(
        "small", null,
        `${owner._signalCandidateType(candidate, signalKind)} · ${owner._signalCandidateExplanation(candidate, signalKind)}`
      ));
      row.appendChild(copy);
      const actions = el("span", "priority-source-actions");
      const up = el("button", "secondary icon-button", "↑");
      setAttr(up, "aria-label", "Повысить приоритет");
      up.disabled = index === 0;
      up.addEventListener("click", () => {
        [selectedValues[index - 1], selectedValues[index]] = [
          selectedValues[index], selectedValues[index - 1],
        ];
        changed();
      });
      const down = el("button", "secondary icon-button", "↓");
      setAttr(down, "aria-label", "Понизить приоритет");
      down.disabled = index === selectedValues.length - 1;
      down.addEventListener("click", () => {
        [selectedValues[index], selectedValues[index + 1]] = [
          selectedValues[index + 1], selectedValues[index],
        ];
        changed();
      });
      const remove = el("button", "secondary priority-source-remove", "Убрать");
      remove.addEventListener("click", () => {
        selectedValues = selectedValues.filter((value) => value !== entityId);
        changed();
      });
      actions.appendChild(up);
      actions.appendChild(down);
      actions.appendChild(remove);
      row.appendChild(actions);
      selected.appendChild(row);
    });
    available.innerHTML = "";
    const remaining = visible.filter(
      ({ entity_id }) => !selectedValues.includes(entity_id)
    );
    count.textContent = `${remaining.length} доступно`;
    remaining.forEach((candidate) => {
      const row = el("div", "priority-source-candidate");
      const copy = el("span", "priority-source-copy");
      copy.appendChild(el(
        "strong", null, owner._signalCandidateDisplayName(candidate, visible)
      ));
      copy.appendChild(el(
        "small", null,
        `${owner._signalCandidateType(candidate, signalKind)} · ${owner._signalCandidateExplanation(candidate, signalKind)}`
      ));
      const add = el(
        "button", "secondary",
        selectedValues.length ? "Добавить в резерв" : "Сделать основным"
      );
      add.value = candidate.entity_id;
      add.disabled = selectedValues.length >= 8;
      add.addEventListener("click", () => {
        selectedValues.push(candidate.entity_id);
        changed();
      });
      row.appendChild(copy);
      row.appendChild(add);
      available.appendChild(row);
    });
    if (!remaining.length) {
      available.appendChild(el(
        "div", "muted priority-source-empty",
        "Все подходящие источники уже добавлены."
      ));
    }
  };
  render();
  return { root: fieldset, value: () => [...selectedValues] };
}
