export function renderFirstRunDeviceGroups(owner, choiceList, room, fields, allChoices, searchable, deps) {
  const {
    ACTIVE_DEVICE_TYPES, CONTROL_CHANNEL_LABELS, SENSOR_DEVICE_TYPES, ZIGBEE2MQTT_IMAGE_PATTERN,
    el, normalizedText, selectField, setAttr, svgIcon,
  } = deps;
  const groups = el("div", "entity-groups");
  const grouped = new Map();
  choiceList.forEach((choice) => {
    const id = choice.candidate.device_group_id || `candidate:${choice.candidate.candidate_id}`;
    if (!grouped.has(id)) grouped.set(id, []);
    grouped.get(id).push(choice);
  });
  Array.from(grouped.entries()).forEach(([groupId, groupChoices]) => {
    const first = groupChoices[0].candidate;
    const group = el("div", "entity-group device-card");
    setAttr(group, "data-device-group-id", groupId);
    const header = el("div", "device-card-header");
    const thumb = el("div", "device-thumb");
    const fallback = el("span", "device-thumb-fallback");
    fallback.appendChild(svgIcon("device"));
    setAttr(fallback, "aria-hidden", "true");
    if (first.image_url && ZIGBEE2MQTT_IMAGE_PATTERN.test(first.image_url)) {
      const image = el("img");
      image.src = first.image_url;
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
    header.appendChild(thumb);
    const identity = el("div");
    identity.appendChild(el("strong", "device-card-title", first.device_name || first.name));
    const details = [first.manufacturer, first.model].filter(Boolean);
    if (details.length) identity.appendChild(el("small", "device-card-meta", details.join(" · ")));
    const deviceTypeNames = ((owner._firstRun.options || {}).display_names || {}).device_types || {};
    const chipTypes = [];
    groupChoices.forEach((choice) => {
      if (!choice.pseudo && !chipTypes.includes(choice.type)) chipTypes.push(choice.type);
    });
    if (chipTypes.length) {
      const chips = el("div", "device-card-chips");
      chipTypes.forEach((type) => chips.appendChild(el("span", "chip", deviceTypeNames[type] || type)));
      identity.appendChild(chips);
    }
    header.appendChild(identity);
    group.appendChild(header);
    const options = el("div", "device-card-options");
    groupChoices.sort((left, right) => left.order - right.order).forEach((choice) => {
      const selectable = !choice.pseudo && owner._firstRunCandidateSelectable(choice.candidate, room);
      if (!selectable && choice.device.selected) {
        choice.device.selected = false;
        choice.device.channel = null;
      }
      const isClimateSource = SENSOR_DEVICE_TYPES.has(choice.type);
      const checkbox = el("input");
      checkbox.type = isClimateSource ? "radio" : "checkbox";
      if (isClimateSource) checkbox.name = `climate-source-${room.id}-${choice.type}`;
      checkbox.checked = choice.device.selected;
      checkbox.value = choice.candidate.candidate_id;
      checkbox.disabled = !selectable || owner._busy;
      const label = el("label", selectable ? "device-option" : "device-option is-disabled");
      label.appendChild(checkbox);
      const labelText = el("span", "entity-label");
      const deviceName = choice.pseudo
        ? "Тип не определён"
        : ((owner._firstRun.options.display_names || {}).device_types || {})[choice.type] || choice.type;
      labelText.appendChild(el("strong", null, deviceName));
      labelText.appendChild(el("small", null, choice.candidate.name));
      const sourceBadge = isClimateSource
        ? el("small", "climate-source-badge", choice.type === "temperature_sensor"
          ? "Главный источник температуры" : "Главный источник влажности")
        : null;
      if (sourceBadge) {
        sourceBadge.hidden = !choice.device.selected;
        labelText.appendChild(sourceBadge);
      }
      const status = el("small", choice.candidate.status === "available" ? "status-badge is-ready" : "status-badge is-attention");
      status.textContent = choice.candidate.status === "unavailable"
        ? "Сейчас недоступно" : owner._firstRunCandidateStatusName(choice.candidate);
      labelText.appendChild(status);
      const reason = el("small", "status-badge is-attention");
      reason.textContent = owner._firstRunCandidateReasonName(choice.candidate);
      labelText.appendChild(reason);
      if (choice.candidate.room_id && choice.candidate.room_id !== room.id) {
        labelText.appendChild(el("small", "status-badge is-attention", `Сейчас: ${owner._firstRunCandidateRoomName(choice.candidate)}`));
      }
      if (!selectable) labelText.appendChild(el("small", "muted", owner._firstRunCandidateHint(choice.candidate, room)));
      const unavailableWarning = choice.candidate.status === "unavailable"
        ? el("small", "device-unavailable-warning", `Устройство «${choice.candidate.name}» недоступно, оно будет применено, когда появится в сети.`)
        : null;
      if (unavailableWarning) {
        unavailableWarning.hidden = !choice.device.selected;
        labelText.appendChild(unavailableWarning);
      }
      label.appendChild(labelText);
      options.appendChild(label);
      let controlChannel = null;
      let channelRow = null;
      if (ACTIVE_DEVICE_TYPES.has(choice.type) && selectable) {
        controlChannel = selectField(
          [{ label: "Не выбран", value: "" }].concat((owner._firstRun.options.control_channels || []).map((channel) => ({
            label: CONTROL_CHANNEL_LABELS[channel] || channel,
            value: channel,
          }))),
          choice.device.channel,
          () => {
            choice.device.channel = controlChannel.value || null;
            owner._firstRunInvalidate(room.id);
          }
        );
        channelRow = el("label", "form-field", "Канал управления");
        channelRow.appendChild(controlChannel);
        channelRow.hidden = !choice.device.selected;
        options.appendChild(channelRow);
      }
      checkbox.addEventListener("change", () => {
        if (!selectable) return;
        choice.device.selected = checkbox.checked;
        if (checkbox.checked) {
          allChoices.forEach((peer) => {
            const sameClimateSource = isClimateSource && peer.type === choice.type;
            const sameEntity = peer.candidate.candidate_id === choice.candidate.candidate_id;
            if (peer !== choice && (sameClimateSource || sameEntity)) {
              peer.device.selected = false;
              const peerField = fields.devices.find((item) => item.key === peer.key);
              if (peerField) {
                peerField.checkbox.checked = false;
                if (peerField.channelRow) peerField.channelRow.hidden = true;
                if (peerField.unavailableWarning) peerField.unavailableWarning.hidden = true;
              }
            }
          });
        }
        if (channelRow) channelRow.hidden = !checkbox.checked;
        if (unavailableWarning) unavailableWarning.hidden = !checkbox.checked;
        if (fields.refreshClimateSources) fields.refreshClimateSources();
        owner._firstRunInvalidate(room.id);
      });
      fields.devices.push({
        checkbox, choice, controlChannel, channelRow, key: choice.key, label, sourceBadge,
        type: choice.type, unavailableWarning,
      });
      if (fields.refreshClimateSources) fields.refreshClimateSources();
    });
    group.appendChild(options);
    groups.appendChild(group);
    searchable.push({
      group,
      text: normalizedText([first.name, first.device_name, first.manufacturer, first.model].filter(Boolean).join(" ")),
    });
  });
  return groups;
}
