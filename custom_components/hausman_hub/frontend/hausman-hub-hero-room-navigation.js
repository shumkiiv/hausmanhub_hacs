import { roomIconName, roomSvgIcon } from "./hausman-hub-room-icons.js?v=1.52.220";

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
  setAttr(home, "data-harness-key", "hero-room:home");
  setAttr(home, "data-harness-intent", "ui-only");
  home.appendChild(svgIcon("home-filled"));
  home.appendChild(el("span", null, "Дом"));
  strip.appendChild(home);
  const roomButtons = new Map();
  rooms.forEach((room) => {
    const button = el("button");
    button.type = "button";
    setAttr(button, "data-harness-key", `hero-room:${room.id}`);
    setAttr(button, "data-harness-intent", "ui-only");
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
  setAttr(previous, "data-harness-key", "hero-room:previous");
  setAttr(previous, "data-harness-intent", "ui-only");
  setAttr(next, "data-harness-key", "hero-room:next");
  setAttr(next, "data-harness-intent", "ui-only");
  element.appendChild(previous);
  element.appendChild(strip);
  element.appendChild(next);

  const slides = [null, ...rooms];
  let selectHeroRoom = null;
  const centerStripButton = (button, animate) => {
    if (!button || typeof strip.scrollTo !== "function"
      || typeof strip.getBoundingClientRect !== "function"
      || typeof button.getBoundingClientRect !== "function") return;
    const stripRect = strip.getBoundingClientRect();
    const buttonRect = button.getBoundingClientRect();
    const current = Number(strip.scrollLeft) || 0;
    const left = current + buttonRect.left - stripRect.left
      - (stripRect.width - buttonRect.width) / 2;
    strip.scrollTo({ left: Math.max(0, left), behavior: animate ? "smooth" : "auto" });
  };
  const move = (direction) => {
    if (!selectHeroRoom) return;
    const currentId = panel._overviewHeroRoomId || null;
    const index = Math.max(0, slides.findIndex((room) => (room?.id || null) === currentId));
    selectHeroRoom(slides[(index + direction + slides.length) % slides.length]);
  };
  previous.addEventListener("click", () => move(-1));
  next.addEventListener("click", () => move(1));
  const carouselDots = [];

  return {
    element,
    home,
    roomButtons,
    slides,
    move,
    attachCarouselChrome(hero) {
      const edgePrevious = el("button", "overview-canon-hero-edge is-previous");
      edgePrevious.type = "button";
      edgePrevious.appendChild(svgIcon("chevron-left"));
      setAttr(edgePrevious, "aria-label", "Предыдущий слайд");
      setAttr(edgePrevious, "data-harness-key", "hero-room:previous-slide");
      setAttr(edgePrevious, "data-harness-intent", "ui-only");
      edgePrevious.addEventListener("click", () => move(-1));
      hero.appendChild(edgePrevious);
      const edgeNext = el("button", "overview-canon-hero-edge is-next");
      edgeNext.type = "button";
      edgeNext.appendChild(svgIcon("chevron-right"));
      setAttr(edgeNext, "aria-label", "Следующий слайд");
      setAttr(edgeNext, "data-harness-key", "hero-room:next-slide");
      setAttr(edgeNext, "data-harness-intent", "ui-only");
      edgeNext.addEventListener("click", () => move(1));
      hero.appendChild(edgeNext);
      const dots = el("div", "overview-canon-hero-dots");
      slides.forEach((room) => {
        const dot = el("button", "overview-canon-hero-dot");
        dot.type = "button";
        setAttr(dot, "aria-label", room ? `Показать комнату ${room.name}` : "Показать весь дом");
        setAttr(dot, "data-harness-key", `hero-room:dot:${room?.id || "home"}`);
        setAttr(dot, "data-harness-intent", "ui-only");
        dot.addEventListener("click", () => selectHeroRoom?.(room));
        dots.appendChild(dot);
        carouselDots.push(dot);
      });
      hero.appendChild(dots);
      setAttr(hero, "tabindex", "0");
      hero.addEventListener("keydown", (event) => {
        if (event.key === "ArrowLeft") {
          if (typeof event.preventDefault === "function") event.preventDefault();
          move(-1);
        } else if (event.key === "ArrowRight") {
          if (typeof event.preventDefault === "function") event.preventDefault();
          move(1);
        }
      });
    },
    setActive(room, animate) {
      home.classList.toggle("is-active", !room);
      if (!room) setAttr(home, "aria-current", "page"); else home.removeAttribute("aria-current");
      roomButtons.forEach((button, roomId) => {
        const active = roomId === room?.id;
        button.classList.toggle("is-active", active);
        if (active) setAttr(button, "aria-current", "page"); else button.removeAttribute("aria-current");
      });
      carouselDots.forEach((dot, index) => {
        dot.classList.toggle("is-active", (slides[index]?.id || null) === (room?.id || null));
      });
      const activeButton = room ? roomButtons.get(room.id) : home;
      centerStripButton(activeButton, animate);
    },
    bind(select) {
      selectHeroRoom = select;
      home.addEventListener("click", () => select(null));
      rooms.forEach((room) => roomButtons.get(room.id)?.addEventListener("click", () => select(room)));
    },
  };
}
