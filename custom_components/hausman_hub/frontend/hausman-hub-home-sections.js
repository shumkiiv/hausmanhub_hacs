/* Tablet-style sections use the panel's physical-device presentation helpers. */

export function renderHomeSection(panel, sectionId, container, deps) {
  const { el, svgIcon, sections, subtitles } = deps;
  container.innerHTML = "";
  if (sectionId === "energy") {
    panel._renderEnergySection(container);
    return;
  }
  const meta = sections.find((section) => section.id === sectionId) || sections[0];
  const heading = el("div", "home-section-heading");
  const headingIcon = el("span", "home-section-icon");
  headingIcon.appendChild(svgIcon(meta.icon));
  heading.appendChild(headingIcon);
  const headingCopy = el("div");
  headingCopy.appendChild(el("h2", null, meta.label));
  headingCopy.appendChild(el("p", "section-intro", subtitles[sectionId]));
  heading.appendChild(headingCopy);
  container.appendChild(heading);
  if (!panel._homeDashboard) {
    container.appendChild(el(
      "div",
      "card empty-state",
      "Снимок дома пока недоступен. Обновите страницу после запуска HausmanHub."
    ));
    return;
  }
  if (sectionId === "rooms") {
    panel._renderRoomInventory(container);
    return;
  }
  const devices = panel._homeDevices(sectionId);
  if (sectionId === "security") panel._renderAlarmSummary(container);
  if (!devices.length) {
    container.appendChild(el(
      "div",
      "card empty-state",
      `${meta.label}: подходящие физические устройства не найдены.`
    ));
    return;
  }
  const byRoom = new Map();
  devices.forEach((device) => {
    const room = device.roomName || "Без комнаты";
    if (!byRoom.has(room)) byRoom.set(room, []);
    byRoom.get(room).push(device);
  });
  [...byRoom.entries()]
    .sort(([left], [right]) => left.localeCompare(right, "ru"))
    .forEach(([room, roomDevices]) => {
      const section = el("section", "inventory-room");
      const title = el("div", "inventory-room-heading");
      title.appendChild(el("h3", null, room));
      title.appendChild(el(
        "span",
        "status-badge",
        `${roomDevices.length} ${panel._deviceCountWord(roomDevices.length)}`
      ));
      section.appendChild(title);
      const grid = el("div", "inventory-device-grid");
      roomDevices.forEach((device) => grid.appendChild(panel._deviceInventoryCard(device)));
      section.appendChild(grid);
      container.appendChild(section);
    });
}
