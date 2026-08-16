const PHASE_LABELS = {
  not_configured: "Контур ещё не настроен",
  shadow: "Идёт безопасная проверка без команд",
  ready_for_canary: "Пилот готов к запуску",
  canary: "Работает пилотная комната",
  managed: "Автоматическое управление активно",
};

const REASON_LABELS = {
  contour_not_configured: "Сначала настройте климатический контур",
  contour_not_automatic: "Для пилота нужен автоматический режим контура",
  canary_room_not_selected: "Выберите одну пилотную комнату",
  multiple_canary_rooms: "Пилотной может быть только одна комната",
  managed_scope_already_present: "Найдены устройства с прежним полным управлением",
  mixed_device_scopes: "В одной комнате смешаны разные уровни управления",
  shadow_evidence_missing: "Проверка ещё не собрала наблюдения",
  shadow_evidence_not_ready: "Для выбранной комнаты пока недостаточно подтверждённых наблюдений",
};

export function renderRolloutReadiness(snapshot, setup, rollout, deps) {
  const { el } = deps;
  const ready = rollout.enable_allowed === true;
  const card = el("section", `rollout-readiness${ready ? " is-ready" : ""}`);
  const head = el("div", "rollout-readiness-head");
  const copy = el("div");
  copy.appendChild(el("strong", null, PHASE_LABELS[rollout.phase] || "Подготовка пилотной комнаты"));
  copy.appendChild(el(
    "span", "muted",
    ready
      ? "Проверка завершена: команды получит только выбранная пилотная комната."
      : "Hausman Hub пока только наблюдает и не отправляет команды устройствам."
  ));
  head.appendChild(copy);
  head.appendChild(el("span", `status-badge${ready ? " is-ready" : ""}`, ready ? "Готово" : "Без команд"));
  card.appendChild(head);

  const rooms = snapshot && Array.isArray(snapshot.rooms)
    ? snapshot.rooms
    : setup && Array.isArray(setup.rooms) ? setup.rooms : [];
  const canaryRoom = rooms.find((room) => room.id === rollout.canary_room_id);
  const facts = el("div", "rollout-readiness-facts");
  [
    [rollout.shadow_sample_count ?? 0, "Наблюдений"],
    [rollout.shadow_ready_room_count ?? 0, "Комнат проверено"],
    [canaryRoom?.name || rollout.canary_room_id || "Не выбрана", "Пилотная комната"],
  ].forEach(([value, label]) => {
    const fact = el("span");
    fact.appendChild(el("strong", null, value));
    fact.appendChild(el("small", "muted", label));
    facts.appendChild(fact);
  });
  card.appendChild(facts);

  const reasons = Array.isArray(rollout.reasons) ? rollout.reasons : [];
  if (reasons.length) {
    const list = el("ul", "rollout-readiness-reasons");
    reasons.forEach((reason) => list.appendChild(el(
      "li", null, REASON_LABELS[reason] || "Завершите проверку перед запуском"
    )));
    card.appendChild(list);
  }
  return card;
}
