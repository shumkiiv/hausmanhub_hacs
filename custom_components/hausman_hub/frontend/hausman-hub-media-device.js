const TV_IDENTITY = /(?:\btv\b|телевиз|television|smart[ _-]?tv|pus\d|oled|qled)/i;

function isMediaDevice(device) {
  return String(device && device.domain || "") === "media_player"
    || String(device && device.category || "") === "media";
}

function isTelevision(device) {
  return TV_IDENTITY.test([
    device && device.name,
    device && device.model,
    device && device.entityId,
  ].filter(Boolean).join(" "));
}

function mediaState(device) {
  const state = String(device && device.state || "").toLowerCase();
  if (device && device.unavailable) return { title: "Нет связи", detail: "Устройство временно недоступно" };
  if (state === "playing") {
    const source = device.attributes && (
      device.attributes.media_title || device.attributes.app_name || device.attributes.source
    );
    return { title: source || "Воспроизведение", detail: "Сейчас воспроизводится" };
  }
  if (state === "paused") return { title: "Пауза", detail: "Воспроизведение приостановлено" };
  if (["off", "standby"].includes(state)) return { title: "Выключен", detail: "Готов к включению" };
  return {
    title: device && device.stateLabel || "Готов",
    detail: state === "idle" ? "Сейчас ничего не воспроизводится" : "Устройство на связи",
  };
}

function canonicalMediaTarget(owner, device) {
  const targets = owner._catalogTargets(device).filter((target) => (
    String(target && target.entity_id || "").startsWith("media_player.")
  ));
  return targets.find((target) => target.entity_id === device.entityId) || targets[0] || null;
}

function actionButton(owner, target, actionId, label, className, el) {
  const action = target && (target.actions || []).find((item) => item.action_id === actionId);
  const button = el("button", className, label);
  button.type = "button";
  button.disabled = owner._busy || !action;
  button.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation && event.stopPropagation();
    if (action) owner._executeDeviceAction(target.target_id, action.action_id, null);
  });
  return button;
}

function appendProductVisual(container, device, iconName, { el, svgIcon }) {
  const imageUrl = String(device && device.imageUrl || "");
  if (/^https?:\/\//i.test(imageUrl)) {
    const image = el("img", "media-device-product-image");
    image.src = imageUrl;
    image.alt = "";
    container.appendChild(image);
    return;
  }
  const fallback = el("span", "media-device-product-fallback");
  fallback.appendChild(svgIcon(iconName));
  container.appendChild(fallback);
}

export function renderMediaDeviceCard(owner, device, deps) {
  if (!isMediaDevice(device)) return null;
  const { el, svgIcon } = deps;
  const television = isTelevision(device);
  const state = mediaState(device);
  const target = canonicalMediaTarget(owner, device);
  const card = el("details", `inventory-device-card media-device-card${device.unavailable ? " is-unavailable" : ""}`);
  const openKey = `device:${device.id || device.physicalId || device.entityId}`;
  card.open = owner._openHomeCards.has(openKey);
  card.addEventListener("toggle", () => {
    if (card.open) owner._openHomeCards.add(openKey);
    else owner._openHomeCards.delete(openKey);
  });

  const summary = el("summary", "inventory-device-summary media-device-summary");
  const icon = el("span", "inventory-device-icon media-device-icon");
  icon.appendChild(svgIcon("media"));
  summary.appendChild(icon);
  const copy = el("span", "inventory-device-copy");
  copy.appendChild(el("strong", null, television ? "Телевизор" : (device.name || "Медиоустройство")));
  const identity = [device.roomName, television ? device.name : device.model]
    .filter(Boolean).filter((value, index, values) => values.indexOf(value) === index);
  copy.appendChild(el("small", null, identity.join(" · ") || "Медиа дома"));
  summary.appendChild(copy);
  const summaryState = el("span", `media-device-summary-state${device.unavailable ? " is-unavailable" : ""}`);
  summaryState.appendChild(el("strong", null, state.title));
  summaryState.appendChild(el("small", null, state.detail));
  summary.appendChild(summaryState);
  card.appendChild(summary);

  const body = el("div", "inventory-device-body media-device-body");
  const visual = el("div", "media-device-visual");
  appendProductVisual(visual, device, "media", deps);
  visual.appendChild(el("span", "media-device-kind", television ? "TV" : "МЕДИА"));
  body.appendChild(visual);

  const control = el("div", "media-device-control");
  const heading = el("div", "media-device-heading");
  heading.appendChild(el("span", "media-device-eyebrow", television ? "ТЕЛЕВИЗОР" : "МЕДИАУСТРОЙСТВО"));
  heading.appendChild(el("strong", "media-device-primary", state.title));
  heading.appendChild(el("span", "media-device-secondary", state.detail));
  control.appendChild(heading);

  if (!target) {
    control.appendChild(el("p", "media-device-unavailable-note", device.unavailable
      ? "Управление вернётся автоматически после восстановления связи."
      : "Home Assistant пока не передал управление этим устройством."));
  } else {
    const transport = el("div", "media-device-transport");
    transport.appendChild(actionButton(owner, target, "media_play", "Играть", "media-transport-button", el));
    transport.appendChild(actionButton(owner, target, "media_pause", "Пауза", "media-transport-button", el));
    control.appendChild(transport);

    const power = el("div", "media-device-power-row");
    power.appendChild(el("span", null, "Питание"));
    const isOn = !["off", "standby", "unavailable", "unknown"].includes(String(device.state || "").toLowerCase());
    power.appendChild(actionButton(
      owner,
      target,
      isOn ? "turn_off" : "turn_on",
      isOn ? "Выключить" : "Включить",
      `media-power-button${isOn ? " is-on" : ""}`,
      el,
    ));
    control.appendChild(power);
  }
  body.appendChild(control);
  card.appendChild(body);
  return card;
}
