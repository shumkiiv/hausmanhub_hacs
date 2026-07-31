const CONTROL_CHANNEL_GUIDANCE = {
  direct_wifi: {
    title: "Напрямую через Home Assistant",
    description: "Лучший вариант, если у устройства есть собственная управляемая сущность climate или humidifier. HausmanHub сможет отправить команду и проверить новое состояние.",
  },
  yandex_remote: {
    title: "Через пульт Яндекса",
    description: "Выбирайте только для устройства, которым Home Assistant управляет через интеграцию Яндекса. Подтверждение зависит от состояния, которое возвращает эта сущность.",
  },
  universal_ir: {
    title: "Через универсальный ИК-пульт",
    description: "Для техники без прямого управления. ИК-пульт отправляет сигнал, но не получает ответ от самого устройства — физическую реакцию нужно подтвердить вручную после добавления ИК-кода.",
  },
};

function channelIdentity(choice) {
  const candidate = choice && choice.candidate ? choice.candidate : {};
  return [candidate.name, candidate.device_name, candidate.manufacturer, candidate.model]
    .filter(Boolean).join(" ").toLocaleLowerCase("ru-RU");
}

function matchingDashboardDevice(owner, choice) {
  const candidate = choice && choice.candidate ? choice.candidate : {};
  const devices = owner._homeDashboard && Array.isArray(owner._homeDashboard.devices)
    ? owner._homeDashboard.devices : [];
  if (!candidate.device_group_id) return null;
  return devices.find((device) => (
    device.physicalId === candidate.device_group_id || device.id === candidate.device_group_id
  )) || null;
}

export function resolveControlChannelTest(owner, choice, channel = null) {
  const selectedChannel = channel || (choice && choice.device && choice.device.channel);
  if (!selectedChannel) return { ready: false, reason: "Сначала выберите способ управления." };
  if (selectedChannel === "universal_ir") {
    return {
      ready: false,
      manual: true,
      reason: "ИК-канал проверяется после добавления кода: HausmanHub отправит сигнал, а вы подтвердите физическую реакцию устройства.",
    };
  }
  const device = matchingDashboardDevice(owner, choice);
  if (!device) {
    return {
      ready: false,
      reason: "Устройство пока не сопоставлено с управляемой сущностью Home Assistant. Обновите список или проверьте привязку устройства к комнате.",
    };
  }
  const targets = typeof owner._catalogTargets === "function" ? owner._catalogTargets(device) : [];
  const preferred = choice.type === "humidifier" ? ["set_humidity"] : ["set_temperature"];
  for (const actionId of preferred) {
    for (const target of targets) {
      const action = (target.actions || []).find((item) => item.action_id === actionId);
      if (!action) continue;
      const value = typeof owner._deviceActionInitialValue === "function"
        ? owner._deviceActionInitialValue(device, target, action) : null;
      if (value === null || value === undefined || value === "") continue;
      const step = actionId === "set_humidity" ? 1 : 0.5;
      const maximum = actionId === "set_humidity" ? 100 : 35;
      const numericValue = Number(value);
      const probeValue = numericValue + step <= maximum
        ? numericValue + step : numericValue - step;
      return {
        ready: true,
        actionId,
        actionTitle: action.title || "Текущая настройка",
        device,
        targetId: target.target_id,
        value,
        probeValue,
      };
    }
  }
  return {
    ready: false,
    reason: "У сущности нет безопасной тестовой команды. HausmanHub не будет включать или выключать устройство только ради проверки.",
  };
}

export function recommendControlChannel(owner, choice) {
  const available = new Set(((owner._firstRun.options || {}).control_channels || []));
  if (/(?:yandex|яндекс|yndx)/.test(channelIdentity(choice)) && available.has("yandex_remote")) {
    return { channel: "yandex_remote", reason: "Устройство определено как управляемое через интеграцию Яндекса." };
  }
  if (resolveControlChannelTest(owner, choice, "direct_wifi").ready && available.has("direct_wifi")) {
    return {
      channel: "direct_wifi",
      reason: "Найдена управляемая сущность Home Assistant и безопасная команда без изменения режима.",
    };
  }
  const remotes = Array.isArray((owner._firstRun.options || {}).ir_remotes)
    ? owner._firstRun.options.ir_remotes : [];
  if (remotes.length && available.has("universal_ir")) {
    return { channel: "universal_ir", reason: "Прямой канал не найден, но доступен универсальный ИК-пульт." };
  }
  return {
    channel: available.has("direct_wifi") ? "direct_wifi" : Array.from(available)[0] || null,
    reason: "Выберите канал по способу, которым это устройство уже управляется в Home Assistant.",
  };
}

export function createControlChannelAssistant(owner, choice, controlChannel, recommendation, room, deps) {
  const { CONTROL_CHANNEL_LABELS, el } = deps;
  const assistant = el("div", "device-channel-assistant");
  const refresh = () => {
    assistant.innerHTML = "";
    const selected = choice.device.channel;
    const guidance = CONTROL_CHANNEL_GUIDANCE[selected];
    const recommendationRow = el("div", "channel-recommendation");
    const recommendationCopy = el("div");
    recommendationCopy.appendChild(el("strong", null,
      `Рекомендуем: ${CONTROL_CHANNEL_LABELS[recommendation.channel] || "выбрать канал"}`));
    recommendationCopy.appendChild(el("small", null, recommendation.reason));
    recommendationRow.appendChild(recommendationCopy);
    if (recommendation.channel && selected !== recommendation.channel) {
      const useRecommended = el("button", "secondary channel-recommendation-action", "Выбрать");
      useRecommended.type = "button";
      useRecommended.disabled = owner._busy;
      useRecommended.addEventListener("click", () => {
        controlChannel.value = recommendation.channel;
        choice.device.channel = recommendation.channel;
        choice.device.channelTest = null;
        refresh();
        owner._firstRunInvalidate(room.id);
      });
      recommendationRow.appendChild(useRecommended);
    }
    assistant.appendChild(recommendationRow);
    const guide = el("details", "channel-guide");
    guide.appendChild(el("summary", null, "Как выбрать способ управления"));
    const guideList = el("div", "channel-guide-list");
    (owner._firstRun.options.control_channels || []).forEach((channel) => {
      const item = el("div", `channel-guide-item${channel === recommendation.channel ? " is-recommended" : ""}`);
      item.appendChild(el("strong", null, CONTROL_CHANNEL_LABELS[channel] || channel));
      item.appendChild(el("span", null, (CONTROL_CHANNEL_GUIDANCE[channel] || {}).description || "Канал управления устройством."));
      guideList.appendChild(item);
    });
    guide.appendChild(guideList);
    assistant.appendChild(guide);
    if (!selected) {
      assistant.appendChild(el("p", "channel-current-help", "Выберите вариант выше — здесь появятся пояснение и проверка связи."));
      return;
    }
    const current = el("div", "channel-current-help");
    current.appendChild(el("strong", null, guidance ? guidance.title : (CONTROL_CHANNEL_LABELS[selected] || selected)));
    current.appendChild(el("span", null, guidance ? guidance.description : "Проверьте канал перед сохранением комнаты."));
    assistant.appendChild(current);
    const plan = resolveControlChannelTest(owner, choice);
    const testRow = el("div", "channel-test-row");
    const testCopy = el("div", "channel-test-copy");
    const result = choice.device.channelTest;
    const resultTitle = result && result.title
      ? result.title : (plan.ready ? "Проверка без изменения режима" : "Проверка пока недоступна");
    testCopy.appendChild(el("strong", result && result.status ? `channel-test-${result.status}` : null, resultTitle));
    testCopy.appendChild(el("small", null, result && result.detail ? result.detail : (
      plan.ready
        ? `На короткое время изменим «${plan.actionTitle}» с ${plan.value} на ${plan.probeValue}, проверим ответ и вернём ${plan.value}. Режим устройства не изменится.`
        : plan.reason
    )));
    testRow.appendChild(testCopy);
    if (plan.ready) {
      const testButton = el("button", "secondary channel-test-action",
        result && result.status === "testing" ? "Проверяем…" : "Проверить канал");
      testButton.type = "button";
      testButton.disabled = owner._busy || (result && result.status === "testing");
      testButton.addEventListener("click", async () => {
        choice.device.channelTest = {
          status: "testing",
          title: "Команда отправляется",
          detail: "Ожидаем чтение состояния устройства…",
        };
        refresh();
        choice.device.channelTest = await owner._testFirstRunControlChannel(choice);
        refresh();
      });
      testRow.appendChild(testButton);
    }
    assistant.appendChild(testRow);
  };
  return { node: assistant, refresh };
}
