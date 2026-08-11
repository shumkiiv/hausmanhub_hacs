import { roomIconName, roomSvgIcon } from "./hausman-hub-room-icons.js?v=1.52.66";

/** Tablet-style cyclic Hero navigation without a visible native scrollbar. */
export function createHeroRoomNavigation(panel, rooms, deps) {
  const { el, setAttr, svgIcon } = deps;
  const element = el("div", "overview-canon-room-navigation");
  const previous = el("button", "overview-canon-room-arrow is-previous");
  previous.type = "button";
  previous.appendChild(svgIcon("chevron-left"));
  setAttr(previous, "aria-label", "Предыдущая комната");
  const strip = el("div", "overview-canon-room-strip");
  const home = el("button");
  home.type = "button";
  home.appendChild(svgIcon("home"));
  home.appendChild(el("span", null, "Дом"));
  strip.appendChild(home);
  const roomButtons = new Map();
  rooms.forEach((room) => {
    const button = el("button");
    button.type = "button";
    button.appendChild(roomSvgIcon(roomIconName(room)));
    button.appendChild(el("span", null, room.name));
    setAttr(button, "aria-label", `Показать комнату ${room.name}`);
    roomButtons.set(room.id, button);
    strip.appendChild(button);
  });
  const next = el("button", "overview-canon-room-arrow is-next");
  next.type = "button";
  next.appendChild(svgIcon("chevron-right"));
  setAttr(next, "aria-label", "Следующая комната");
  element.appendChild(previous);
  element.appendChild(strip);
  element.appendChild(next);

  return {
    element,
    home,
    roomButtons,
    setActive(room, animate) {
      home.classList.toggle("is-active", !room);
      if (!room) setAttr(home, "aria-current", "page"); else home.removeAttribute("aria-current");
      roomButtons.forEach((button, roomId) => {
        const active = roomId === room?.id;
        button.classList.toggle("is-active", active);
        if (active) setAttr(button, "aria-current", "page"); else button.removeAttribute("aria-current");
      });
      const activeButton = room ? roomButtons.get(room.id) : home;
      activeButton?.scrollIntoView?.({ behavior: animate ? "smooth" : "auto", block: "nearest", inline: "center" });
    },
    bind(selectHeroRoom) {
      home.addEventListener("click", () => selectHeroRoom(null));
      rooms.forEach((room) => roomButtons.get(room.id)?.addEventListener("click", () => selectHeroRoom(room)));
      const slides = [null, ...rooms];
      const move = (direction) => {
        const currentId = panel._overviewHeroRoomId || null;
        const index = Math.max(0, slides.findIndex((room) => (room?.id || null) === currentId));
        selectHeroRoom(slides[(index + direction + slides.length) % slides.length]);
      };
      previous.addEventListener("click", () => move(-1));
      next.addEventListener("click", () => move(1));
    },
  };
}
