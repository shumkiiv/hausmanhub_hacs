/* Fast natural-language scenario composer. AI output remains an inert draft. */

function composerState(panel) {
  panel._scenarioAiComposer = panel._scenarioAiComposer || {
    open: false, text: "", mentions: [], busy: false, error: "", questions: [], listening: false,
  };
  return panel._scenarioAiComposer;
}

function publicDevices(panel) {
  const source = panel._scenarios.catalog && Array.isArray(panel._scenarios.catalog.devices)
    ? panel._scenarios.catalog.devices : [];
  const seen = new Set();
  return source.filter((device) => {
    if (!device || !device.target_id || seen.has(device.target_id)) return false;
    seen.add(device.target_id);
    return true;
  }).sort((left, right) => String(left.physical_name || left.name || "").localeCompare(String(right.physical_name || right.name || ""), "ru"));
}

function deviceLabel(device) {
  const name = device.physical_name || device.name || "Устройство";
  return device.room_name ? `${name} · ${device.room_name}` : name;
}

function mentionToken(device, existing) {
  const base = `@${device.physical_name || device.name || "Устройство"}`.slice(0, 121);
  if (!existing.some((item) => item.token === base && item.targetId !== device.target_id)) return base;
  return `@${device.physical_name || device.name || "Устройство"} (${device.room_name || device.target_id})`.slice(0, 121);
}

function nextMentionId(existing) {
  const used = new Set(existing.map((item) => item.id));
  for (let index = 1; index <= 999; index += 1) {
    if (!used.has(`mention_${index}`)) return `mention_${index}`;
  }
  return "mention_999";
}

function activeAtQuery(text, caret) {
  const prefix = text.slice(0, caret);
  const at = prefix.lastIndexOf("@");
  if (at < 0 || /\s/.test(prefix.slice(at + 1)) && prefix.slice(at + 1).length > 40) return null;
  const value = prefix.slice(at + 1);
  if (value.includes("\n") || value.length > 80) return null;
  return { at, value: value.toLocaleLowerCase("ru") };
}

export function openScenarioAiComposer(panel, refresh) {
  const state = composerState(panel);
  state.open = true;
  state.error = "";
  state.questions = [];
  refresh();
}

export function renderScenarioAiComposer(panel, container, deps, onDraft, refresh) {
  const state = composerState(panel);
  if (!state.open) return;
  const { el, setAttr } = deps;
  const overlay = el("div", "scenario-ai-overlay");
  const dialog = el("section", "scenario-ai-dialog");
  setAttr(dialog, "role", "dialog");
  setAttr(dialog, "aria-modal", "true");
  setAttr(dialog, "aria-labelledby", "scenario-ai-title");

  const header = el("header", "scenario-ai-header");
  const copy = el("div");
  const title = el("h2", null, "Создать с Hausman AI");
  setAttr(title, "id", "scenario-ai-title");
  copy.appendChild(title);
  copy.appendChild(el("p", null, "Опишите результат обычными словами. Черновик откроется в редакторе и останется выключенным до сохранения."));
  header.appendChild(copy);
  const close = el("button", "secondary scenario-ai-close", "×");
  close.type = "button";
  setAttr(close, "aria-label", "Закрыть создание с Hausman AI");
  close.addEventListener("click", () => { state.open = false; refresh(); });
  header.appendChild(close);
  dialog.appendChild(header);

  const textarea = el("textarea", "scenario-ai-input");
  textarea.value = state.text;
  textarea.maxLength = 2000;
  textarea.rows = 6;
  textarea.placeholder = "Например: когда @Датчик движения заметит движение после 22:00, включи свет в коридоре на 40%";
  setAttr(textarea, "aria-label", "Опишите сценарий");
  dialog.appendChild(textarea);

  const toolbar = el("div", "scenario-ai-toolbar");
  const mentionButton = el("button", "secondary", "@ Устройство");
  mentionButton.type = "button";
  mentionButton.addEventListener("click", () => {
    const start = textarea.selectionStart == null ? textarea.value.length : textarea.selectionStart;
    textarea.setRangeText("@", start, textarea.selectionEnd == null ? start : textarea.selectionEnd, "end");
    state.text = textarea.value;
    textarea.focus();
    renderSuggestions();
  });
  toolbar.appendChild(mentionButton);

  const Recognition = globalThis.SpeechRecognition || globalThis.webkitSpeechRecognition;
  const voice = el("button", "secondary", state.listening ? "Остановить запись" : "🎙 Голосом");
  voice.type = "button";
  voice.disabled = !Recognition || state.busy;
  if (!Recognition) voice.title = "Распознавание речи недоступно в этом браузере";
  voice.addEventListener("click", () => {
    if (!Recognition || state.listening) return;
    const recognition = new Recognition();
    recognition.lang = "ru-RU";
    recognition.interimResults = false;
    recognition.maxAlternatives = 1;
    recognition.onstart = () => { state.listening = true; voice.textContent = "Слушаю…"; };
    recognition.onresult = (event) => {
      const spoken = event.results && event.results[0] && event.results[0][0] && event.results[0][0].transcript;
      if (spoken) {
        textarea.value = `${textarea.value}${textarea.value.trim() ? " " : ""}${spoken}`.slice(0, 2000);
        state.text = textarea.value;
      }
    };
    recognition.onerror = () => { state.error = "Не удалось распознать речь. Можно продолжить ввод с клавиатуры."; };
    recognition.onend = () => {
      state.listening = false;
      voice.textContent = "🎙 Голосом";
      refresh();
    };
    recognition.start();
  });
  toolbar.appendChild(voice);
  toolbar.appendChild(el("span", "scenario-ai-counter", `${state.text.length}/2000`));
  dialog.appendChild(toolbar);

  const chips = el("div", "scenario-ai-mentions");
  const renderChips = () => {
    chips.innerHTML = "";
    state.mentions.filter((item) => textarea.value.includes(item.token)).forEach((item) => {
      chips.appendChild(el("span", "scenario-ai-mention", item.token));
    });
  };
  dialog.appendChild(chips);

  const suggestions = el("div", "scenario-ai-suggestions");
  const renderSuggestions = () => {
    state.text = textarea.value;
    state.mentions = state.mentions.filter((item) => state.text.includes(item.token));
    renderChips();
    toolbar.lastChild.textContent = `${state.text.length}/2000`;
    suggestions.innerHTML = "";
    const query = activeAtQuery(textarea.value, textarea.selectionStart || 0);
    if (!query) return;
    const matches = publicDevices(panel).filter((device) => deviceLabel(device).toLocaleLowerCase("ru").includes(query.value)).slice(0, 8);
    matches.forEach((device) => {
      const button = el("button", "scenario-ai-suggestion");
      button.type = "button";
      button.appendChild(el("strong", null, device.physical_name || device.name || "Устройство"));
      button.appendChild(el("small", null, [device.room_name, device.capability_name].filter(Boolean).join(" · ")));
      button.addEventListener("click", () => {
        const token = mentionToken(device, state.mentions);
        const end = textarea.selectionStart || textarea.value.length;
        textarea.setRangeText(`${token} `, query.at, end, "end");
        state.text = textarea.value;
        const prior = state.mentions.find((item) => item.targetId === device.target_id && item.token === token);
        if (!prior) state.mentions.push({
          id: nextMentionId(state.mentions),
          token,
          label: device.physical_name || device.name || "Устройство",
          targetId: device.target_id,
        });
        renderSuggestions();
        textarea.focus();
      });
      suggestions.appendChild(button);
    });
  };
  textarea.addEventListener("input", renderSuggestions);
  textarea.addEventListener("keyup", renderSuggestions);
  dialog.appendChild(suggestions);
  renderChips();

  if (state.questions.length) {
    const clarification = el("div", "scenario-ai-clarification");
    clarification.appendChild(el("strong", null, "Hausman просит уточнить:"));
    const list = el("ul");
    state.questions.forEach((question) => list.appendChild(el("li", null, question)));
    clarification.appendChild(list);
    dialog.appendChild(clarification);
  }
  if (state.error) dialog.appendChild(el("div", "scenario-ai-error", state.error));
  dialog.appendChild(el("p", "scenario-ai-privacy", "Голос распознаёт служба браузера. В нейросеть передаются описание и безопасный каталог названий и возможностей. Токен Home Assistant, адреса и технические entity_id не передаются."));

  const actions = el("footer", "scenario-ai-actions");
  const cancel = el("button", "secondary", "Отмена");
  cancel.type = "button";
  cancel.addEventListener("click", () => { state.open = false; refresh(); });
  const generate = el("button", null, state.busy ? "Создаю черновик…" : "Создать черновик");
  generate.type = "button";
  generate.disabled = state.busy || state.text.trim().length < 3;
  generate.addEventListener("click", async () => {
    if (state.busy) return;
    state.text = textarea.value.trim();
    state.mentions = state.mentions.filter((item) => state.text.includes(item.token));
    state.busy = true; state.error = ""; state.questions = [];
    refresh();
    try {
      const result = await panel._hass.callApi("POST", deps.aiDraftApi, {
        contract: { name: "hausman-hub-scenario-ai-draft-request", version: 1 },
        text: state.text,
        locale: "ru-RU",
        mentions: state.mentions,
      });
      if (result && result.status === "ready" && result.draft && result.saved === false && result.commandSent === false) {
        state.open = false;
        onDraft(result.draft);
      } else if (result && result.status === "needs_clarification") {
        state.questions = Array.isArray(result.clarifyingQuestions) ? result.clarifyingQuestions.slice(0, 3) : [];
      } else {
        state.error = "Hausman не смог подготовить безопасный черновик. Уточните описание.";
      }
    } catch (error) {
      const body = error && typeof error.body === "object" ? error.body : {};
      state.error = body.message || "Нейросеть не ответила. Проверьте её настройки и повторите.";
    } finally {
      state.busy = false;
      refresh();
    }
  });
  actions.appendChild(cancel);
  actions.appendChild(generate);
  dialog.appendChild(actions);
  overlay.appendChild(dialog);
  overlay.addEventListener("click", (event) => { if (event.target === overlay) { state.open = false; refresh(); } });
  container.appendChild(overlay);
  Promise.resolve().then(() => textarea.focus());
}
