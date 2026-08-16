export function dupGroups(devices) {
  const groups = new Map();
  devices.forEach((device) => {
    const key = device.canonicalId || device.id;
    groups.set(key, (groups.get(key) || 0) + 1);
  });
  return groups;
}

export function dupSize(groups, device) {
  return groups.get(device.canonicalId || device.id) || 1;
}

export function dupAttention(device, groupSize = 1) {
  return groupSize > 1 || device.possibleDuplicate === true || !device.roomId || device.status !== "available";
}

export function dupFilter(device, filter, groupSize = 1) {
  if (filter === "all") return true;
  if (filter === "attention") return dupAttention(device, groupSize);
  if (filter === "unassigned") return !device.roomId;
  if (filter === "unavailable") return device.status === "unavailable";
  if (filter === "virtual") return device.kind === "virtual";
  if (filter === "entity_only") return device.kind === "entity_only";
  if (filter === "duplicates") return groupSize > 1;
  return true;
}

export function dupCompare(left, right) {
  const leftKey = left.canonicalId || left.id;
  const rightKey = right.canonicalId || right.id;
  if (leftKey !== rightKey) return String(left.name || "").localeCompare(String(right.name || ""), "ru");
  if (Boolean(left.canonical) !== Boolean(right.canonical)) return left.canonical ? -1 : 1;
  return String(left.id || "").localeCompare(String(right.id || ""));
}

export function dupView(device, groupSize, position) {
  if (groupSize < 2) return null;
  return device.canonical
    ? { className: "is-canonical", title: "Рекомендуется оставить", detail: `Основная запись · 1 из ${groupSize}` }
    : { className: "is-copy", title: "Копия записи", detail: `Сравните с основной · ${position} из ${groupSize}` };
}

export function dupGuide(detail, el, device, groupSize) {
  if (groupSize < 2) return;
  const guidance = el("section", `device-maintenance-duplicate-guide ${device.canonical ? "is-canonical" : "is-copy"}`);
  guidance.appendChild(el("strong", null, device.canonical
    ? "Рекомендуется оставить эту запись"
    : "Сравните с основной записью перед удалением"));
  guidance.appendChild(el("p", "muted", device.canonical
    ? `Hausman Hub выбрал её основной среди ${groupSize} похожих записей по доступности, состоянию и полноте сущностей.`
    : "Удаляйте копию только если ниже указано «Не используется настройками Hausman Hub», а все нужные сущности есть у основной записи."));
  detail.appendChild(guidance);
}
