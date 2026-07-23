/* HausmanHub admin panel: climate overview and everyday actions. */
const PANEL_API = "hausman_hub/v1/admin/panel";
const REFRESH_MS = 30000;

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function requestId(prefix) {
  const random = Math.random().toString(36).slice(2, 10);
  return `${prefix}-${Date.now()}-${random}`.slice(0, 64);
}

class HausmanHubPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._data = null;
    this._error = false;
    this._busy = false;
    this._notice = "";
    this._timer = null;
    this._onVisible = () => {
      if (!document.hidden) this._load();
    };
  }

  set hass(value) {
    const first = this._hass === null;
    this._hass = value;
    if (first) this._load();
  }

  connectedCallback() {
    this._timer = setInterval(() => this._load(), REFRESH_MS);
    document.addEventListener("visibilitychange", this._onVisible);
    this._render();
  }

  disconnectedCallback() {
    if (this._timer) clearInterval(this._timer);
    this._timer = null;
    document.removeEventListener("visibilitychange", this._onVisible);
  }

  async _load() {
    if (!this._hass) return;
    try {
      this._data = await this._hass.callApi("GET", PANEL_API);
      this._error = false;
    } catch (error) {
      this._error = true;
    }
    this._render();
  }

  async _post(path, payload, confirmText) {
    if (this._busy) return;
    if (confirmText && !window.confirm(confirmText)) return;
    this._busy = true;
    this._notice = "";
    this._render();
    try {
      const receipt = await this._hass.callApi("POST", path, payload);
      this._notice = this._receiptText(receipt);
      this._error = false;
      await this._load();
    } catch (error) {
      this._notice = "Действие не выполнено. Проверьте состояние климата.";
      this._render();
    } finally {
      this._busy = false;
      this._render();
    }
  }

  _receiptText(receipt) {
    const statuses = {
      confirmed: "Применено и подтверждено наблюдением.",
      pending: "Команды отправлены, подтверждение ещё проверяется.",
      partial: "Применено частично.",
      unavailable: "Состояние климатического контура недоступно.",
      up_to_date: "Состояние уже соответствует сохранённому.",
      denied: "Действие отклонено защитой.",
      failed: "Действие не выполнено.",
    };
    const status = receipt && typeof receipt.status === "string" ? receipt.status : "";
    return statuses[status] || `Статус операции: ${status || "неизвестен"}.`;
  }

  _names(section, code) {
    const names = this._data && this._data.snapshot && this._data.snapshot.display_names;
    const group = names && names[section];
    return (group && group[code]) || code;
  }

  _render() {
    if (!this.shadowRoot) return;
    const root = this.shadowRoot;
    root.innerHTML = "";
    const style = el("style");
    style.textContent = `
      :host { display: block; padding: 16px; max-width: 1100px; margin: 0 auto;
        font-family: var(--primary-font-family, sans-serif);
        color: var(--primary-text-color, #212121); }
      h1 { font-size: 22px; margin: 0 0 4px; }
      h2 { font-size: 17px; margin: 24px 0 8px; }
      .muted { color: var(--secondary-text-color, #727272); font-size: 13px; }
      .banner { padding: 10px 14px; border-radius: 8px; margin: 12px 0;
        background: var(--error-color, #db4437); color: #fff; }
      .notice { padding: 10px 14px; border-radius: 8px; margin: 12px 0;
        background: var(--success-color, #43a047); color: #fff; }
      .cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
        gap: 12px; }
      .card { background: var(--card-background-color, #fff); border-radius: 12px;
        padding: 14px; box-shadow: var(--ha-card-box-shadow, 0 1px 3px rgba(0,0,0,0.2)); }
      .card h3 { margin: 0 0 6px; font-size: 15px; }
      .row { display: flex; justify-content: space-between; font-size: 14px;
        padding: 2px 0; gap: 8px; }
      .value { font-weight: 500; }
      button { font: inherit; border: none; border-radius: 8px; padding: 8px 14px;
        margin: 6px 8px 0 0; cursor: pointer;
        background: var(--primary-color, #03a9f4); color: #fff; }
      button.secondary { background: var(--secondary-background-color, #e5e5e5);
        color: var(--primary-text-color, #212121); }
      button:disabled { opacity: 0.5; cursor: default; }
      input[type="number"] { font: inherit; width: 72px; padding: 6px;
        border-radius: 8px; border: 1px solid var(--divider-color, #ccc);
        margin-right: 8px; }
      .reasons { font-size: 13px; margin: 6px 0 0; }
      .chip { display: inline-block; border-radius: 10px; padding: 2px 10px;
        font-size: 12px; background: var(--secondary-background-color, #e5e5e5);
        margin: 2px 4px 2px 0; }
    `;
    root.appendChild(style);

    const container = el("div");
    root.appendChild(container);
    container.appendChild(el("h1", null, "HausmanHub"));

    if (this._error) {
      container.appendChild(
        el("div", "banner", "Данные HausmanHub недоступны. Проверьте интеграцию и повторите.")
      );
      return;
    }
    if (!this._data) {
      container.appendChild(el("div", "muted", "Загрузка…"));
      return;
    }

    const { snapshot, readiness } = this._data;
    if (this._notice) container.appendChild(el("div", "notice", this._notice));
    this._renderReadiness(container, readiness);
    if (snapshot) {
      this._renderRooms(container, snapshot);
      this._renderContours(container, snapshot);
    }
  }

  _renderReadiness(container, readiness) {
    container.appendChild(el("h2", null, "Состояние"));
    const card = el("div", "card");
    const labels = {
      ready: "Готов к управлению",
      not_ready: "Не готов",
      unavailable: "Недоступен",
      disabled: "Управление климатом выключено",
    };
    card.appendChild(el("div", "row", ""));
    const statusRow = el("div", "row");
    statusRow.appendChild(el("span", null, "Статус"));
    statusRow.appendChild(el("span", "value", labels[readiness.status] || readiness.status));
    card.appendChild(statusRow);
    const modeRow = el("div", "row");
    modeRow.appendChild(el("span", null, "Режим"));
    modeRow.appendChild(el("span", "value", readiness.bridge_mode));
    card.appendChild(modeRow);
    if (Array.isArray(readiness.reasons) && readiness.reasons.length) {
      const reasons = el("div", "reasons");
      readiness.reasons.forEach((reason) => {
        reasons.appendChild(el("span", "chip", this._names("blocked_reasons", reason)));
      });
      card.appendChild(reasons);
    }
    container.appendChild(card);
  }

  _renderRooms(container, snapshot) {
    container.appendChild(el("h2", null, "Комнаты"));
    const grid = el("div", "cards");
    (snapshot.rooms || []).forEach((room) => {
      const card = el("div", "card");
      card.appendChild(el("h3", null, room.name));
      this._row(card, "Температура", this._temp(room.temperature));
      this._row(card, "Влажность", this._humidity(room.humidity));
      this._row(card, "Цель", this._temp(room.target_temperature));
      this._row(card, "Режим", this._names("room_modes", room.mode));
      this._row(card, "Данные", this._names("data_statuses", room.actual && room.actual.data_status));
      (room.devices || []).forEach((device) => {
        this._row(
          card,
          device.name,
          this._names("device_states", device.state)
        );
      });
      grid.appendChild(card);
    });
    container.appendChild(grid);
  }

  _renderContours(container, snapshot) {
    const contours = (snapshot.contours || []).filter((item) => item.kind === "climate");
    if (!contours.length) return;
    container.appendChild(el("h2", null, "Климатический контур"));
    contours.forEach((contour) => {
      const card = el("div", "card");
      card.appendChild(el("h3", null, contour.name));
      this._row(card, "Статус", this._names("contour_statuses", contour.status));
      this._row(card, "Режим", this._names("contour_modes", contour.mode));
      if (contour.schedule && contour.schedule.enabled) {
        const next = contour.schedule.next_profile
          ? `${this._names("profiles", contour.schedule.next_profile)} · ${contour.schedule.next_change_at || ""}`
          : "Расписание включено";
        this._row(card, "Расписание", next);
      }
      if (Array.isArray(contour.reasons) && contour.reasons.length) {
        const reasons = el("div", "reasons");
        contour.reasons.forEach((reason) => {
          reasons.appendChild(el("span", "chip", this._names("contour_reasons", reason)));
        });
        card.appendChild(reasons);
      }

      const execution = contour.execution || {};
      const apply = execution.settings_apply || {};
      const applyButton = el("button", null, "Применить сохранённые настройки");
      applyButton.disabled = this._busy || apply.available !== true;
      applyButton.addEventListener("click", () => {
        this._post(
          `${PANEL_API}/apply`,
          {
            request_id: requestId("panel-apply"),
            contour_id: contour.id,
            confirm: true,
          },
          "Применить сохранённые настройки климата для всех комнат контура?"
        );
      });
      card.appendChild(applyButton);

      const temporary = execution.temporary_temperature || {};
      (contour.rooms || []).forEach((room) => {
        const block = el("div");
        block.appendChild(el("div", "muted", room.name || room.id));
        const input = el("input");
        input.type = "number";
        input.min = temporary.minimum;
        input.max = temporary.maximum;
        input.step = temporary.step;
        const current = room.temporary_temperature && room.temporary_temperature.active
          ? room.temporary_temperature.temperature
          : room.targets && room.targets.temperature;
        input.value = current;
        block.appendChild(input);
        const setButton = el("button", "secondary", "Временная температура");
        setButton.disabled = this._busy || !room.temporary_temperature || room.temporary_temperature.available !== true;
        setButton.addEventListener("click", () => {
          this._post(
            `${PANEL_API}/temporary-temperature`,
            {
              request_id: requestId("panel-temp"),
              contour_id: contour.id,
              room_id: room.id,
              action: "set",
              target_temperature: Number(input.value),
              confirm: true,
            },
            `Установить временную температуру ${input.value} °C в комнате «${room.name || room.id}» до следующей границы расписания?`
          );
        });
        block.appendChild(setButton);
        if (room.temporary_temperature && room.temporary_temperature.active) {
          const clearButton = el("button", "secondary", "Вернуться к расписанию");
          clearButton.disabled = this._busy;
          clearButton.addEventListener("click", () => {
            this._post(
              `${PANEL_API}/temporary-temperature`,
              {
                request_id: requestId("panel-temp"),
                contour_id: contour.id,
                room_id: room.id,
                action: "clear",
                target_temperature: null,
                confirm: true,
              },
              `Вернуть комнату «${room.name || room.id}» к расписанию?`
            );
          });
          block.appendChild(clearButton);
          block.appendChild(
            el("div", "muted", `Действует временная температура ${room.temporary_temperature.temperature} °C`)
          );
        }
        card.appendChild(block);
      });
      container.appendChild(card);
    });
  }

  _row(card, label, value) {
    const row = el("div", "row");
    row.appendChild(el("span", null, label));
    row.appendChild(el("span", "value", value));
    card.appendChild(row);
  }

  _temp(value) {
    return typeof value === "number" ? `${value.toFixed(1)} °C` : "—";
  }

  _humidity(value) {
    return typeof value === "number" ? `${Math.round(value)} %` : "—";
  }
}

customElements.define("hausman-hub-panel", HausmanHubPanel);
