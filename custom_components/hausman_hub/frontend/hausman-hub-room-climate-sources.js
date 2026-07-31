export function renderFirstRunClimateSources(owner, room, fields, choices, deps) {
  const { ZIGBEE2MQTT_IMAGE_PATTERN, el, setAttr, svgIcon } = deps;
  const section = el("section", "climate-source-stage climate-source-summary");
  const heading = el("div", "climate-source-stage-heading");
  const headingCopy = el("div");
  headingCopy.appendChild(el("span", "setup-stage-number", "1"));
  headingCopy.appendChild(el("strong", null, "Главные показания комнаты"));
  heading.appendChild(headingCopy);
  heading.appendChild(el(
    "p",
    null,
    "Контур сравнивает цели именно с этими двумя показаниями. Для каждой роли выберите один датчик, которому доверяете."
  ));
  section.appendChild(heading);
  const grid = el("div", "climate-source-role-grid");
  const sourceFields = [];
  const specs = [
    {
      helper: "По этому показанию включаются охлаждение и обогрев.",
      missing: "Выберите датчик температуры комнаты",
      title: "Температура комнаты",
      type: "temperature_sensor",
    },
    {
      helper: "По этому показанию включается и отключается увлажнение.",
      missing: "Выберите датчик влажности комнаты",
      title: "Влажность комнаты",
      type: "humidity_sensor",
    },
  ];
  specs.forEach((spec) => {
    const role = el("article", "climate-source-role");
    const roleHeader = el("div", "climate-source-role-header");
    const copy = el("div");
    copy.appendChild(el("strong", null, spec.title));
    copy.appendChild(el("small", null, spec.helper));
    roleHeader.appendChild(copy);
    const stateLabel = el("span", "climate-source-role-state");
    roleHeader.appendChild(stateLabel);
    role.appendChild(roleHeader);
    const options = el("div", "climate-source-options");
    const typeChoices = choices.filter((choice) => choice.type === spec.type && !choice.pseudo);
    if (!typeChoices.length) {
      options.appendChild(el(
        "div",
        "climate-source-empty",
        `В этой комнате не найден подходящий датчик. ${spec.missing}, затем обновите список.`
      ));
    }
    typeChoices.forEach((choice) => {
      const selectable = owner._firstRunCandidateSelectable(choice.candidate, room);
      if (!selectable && choice.device.selected) choice.device.selected = false;
      const label = el("label", `climate-source-option${selectable ? "" : " is-disabled"}`);
      const radio = el("input");
      radio.type = "radio";
      radio.name = `climate-source-${room.id}-${spec.type}`;
      radio.value = choice.candidate.candidate_id;
      radio.checked = choice.device.selected;
      radio.disabled = !selectable || owner._busy;
      label.appendChild(radio);
      const thumb = el("span", "climate-source-thumb");
      const fallback = el("span", "climate-source-thumb-fallback");
      fallback.appendChild(svgIcon("device"));
      if (choice.candidate.image_url && ZIGBEE2MQTT_IMAGE_PATTERN.test(choice.candidate.image_url)) {
        const image = el("img");
        image.src = choice.candidate.image_url;
        image.alt = "";
        setAttr(image, "loading", "lazy");
        setAttr(image, "decoding", "async");
        setAttr(image, "referrerpolicy", "no-referrer");
        fallback.hidden = true;
        image.addEventListener("error", () => {
          image.hidden = true;
          fallback.hidden = false;
        });
        thumb.appendChild(image);
      }
      thumb.appendChild(fallback);
      label.appendChild(thumb);
      const identity = el("span", "climate-source-identity");
      identity.appendChild(el("strong", null, choice.candidate.device_name || choice.candidate.name));
      if (choice.candidate.device_name && choice.candidate.device_name !== choice.candidate.name) {
        identity.appendChild(el("small", null, choice.candidate.name));
      }
      if (choice.candidate.status === "unavailable") {
        identity.appendChild(el("small", "climate-source-warning", "Сейчас недоступен"));
      }
      label.appendChild(identity);
      const selectedMark = el("span", "climate-source-selected", "Главный");
      selectedMark.hidden = !choice.device.selected;
      label.appendChild(selectedMark);
      options.appendChild(label);
      const field = {
        checkbox: radio,
        choice,
        controlChannel: null,
        channelRow: null,
        key: choice.key,
        label,
        selectedMark,
        type: choice.type,
        unavailableWarning: null,
      };
      fields.devices.push(field);
      sourceFields.push(field);
      radio.addEventListener("change", () => {
        if (!selectable || !radio.checked) return;
        choices.forEach((peer) => {
          if (peer !== choice && peer.type === spec.type) peer.device.selected = false;
        });
        choice.device.selected = true;
        refresh();
        owner._firstRunInvalidate(room.id);
      });
    });
    role.appendChild(options);
    fields.climateSources[spec.type] = { role, stateLabel };
    grid.appendChild(role);
  });
  const refresh = () => {
    specs.forEach((spec) => {
      const selected = choices.filter((choice) => choice.type === spec.type && choice.device.selected);
      const source = fields.climateSources[spec.type];
      source.role.className = `climate-source-role${selected.length === 1 ? " is-ready" : " is-missing"}`;
      source.stateLabel.textContent = selected.length === 1
        ? "Выбран" : (selected.length ? "Оставьте один" : "Обязательно");
    });
    sourceFields.forEach((field) => {
      const selected = field.choice.device.selected === true;
      field.checkbox.checked = selected;
      field.label.className = `climate-source-option${field.checkbox.disabled ? " is-disabled" : ""}${selected ? " is-selected" : ""}`;
      field.selectedMark.hidden = !selected;
    });
  };
  fields.refreshClimateSources = refresh;
  refresh();
  section.appendChild(grid);
  return section;
}
