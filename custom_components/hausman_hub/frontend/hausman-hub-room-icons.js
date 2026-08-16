const SVG_NAMESPACE = "http://www.w3.org/2000/svg";

const ROOM_ICON_PATHS = {
  rooms: "M3 21V3h10v4h8v14h-2v-2h-4v2h-2v-8H5v8zm2-10h6V5H5zm10 6h4v-2h-4zm0-4h4v-2h-4z",
  living: "M21 10c-1.1 0-2 .9-2 2v3H5v-3c0-1.1-.9-2-2-2s-2 .9-2 2v5c0 1.1.9 2 2 2v2h2v-2h14v2h2v-2c1.1 0 2-.9 2-2v-5c0-1.1-.9-2-2-2m-4 3v2H7v-2c0-1.1-.9-2-2-2V7c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2v4c-1.1 0-2 .9-2 2",
  kitchen: "M8 2v2h1v3H8v2h1v3H8v10h8V2zm6 18h-4v-6h4zm0-8h-4V4h4z",
  child: "M14.5 10.5h-5v-1h5M12 2c-1.1 0-2 .9-2 2 0 .18.03.35.07.5C7.21 5.3 5 7.92 5 11v4H3v4h2v3h14v-3h2v-4h-2v-4c0-3.08-2.21-5.7-5.07-6.5.04-.15.07-.32.07-.5 0-1.1-.9-2-2-2m-4 9c.55 0 1 .45 1 1s-.45 1-1 1-1-.45-1-1 .45-1 1-1m8 0c.55 0 1 .45 1 1s-.45 1-1 1-1-.45-1-1 .45-1 1-1m1 9H7v-3.26c1.39.79 3.09 1.26 5 1.26s3.61-.47 5-1.26zm0-6c0 1.1-2.24 2-5 2s-5-.9-5-2v-3c0-2.76 2.24-5 5-5s5 2.24 5 5z",
  toilet: "M5.5 22v-7.5H4V9h3v5.5H5.5V22M16 22v-6h-2V9h6v7h-2v6h-2M5.5 8c-1.1 0-2-.9-2-2s.9-2 2-2 2 .9 2 2-.9 2-2 2M17 8c-1.1 0-2-.9-2-2s.9-2 2-2 2 .9 2 2-.9 2-2 2",
  bathroom: "M21 10H7V7c0-1.1.9-2 2-2s2 .9 2 2v1h2V7c0-2.21-1.79-4-4-4S5 4.79 5 7v3H3c-.55 0-1 .45-1 1v5c0 2.21 1.79 4 4 4v1c0 .55.45 1 1 1s1-.45 1-1v-1h8v1c0 .55.45 1 1 1s1-.45 1-1v-1c2.21 0 4-1.79 4-4v-5c0-.55-.45-1-1-1m-2 6c0 1.1-.9 2-2 2H6c-1.1 0-2-.9-2-2v-4h15z",
  bedroom: "M7 10c1.66 0 3-1.34 3-3S8.66 4 7 4 4 5.34 4 7s1.34 3 3 3m12-6h-8v7H3V4H1v15h2v-3h18v3h2v-9c0-3.31-2.69-6-6-6",
  hallway: "M19 19V5c0-1.1-.9-2-2-2H7c-1.1 0-2 .9-2 2v14H3v2h18v-2zm-2 0H7V5h10zm-3-7h2v2h-2z",
  terrace: "M21 14h-1V7h1V5H3v2h1v7H3v2h8v2H8v2h8v-2h-3v-2h8zm-3 0h-5V7h5zM6 7h5v7H6z",
  spa: "M9 9H7v2h2zm4 0h-2v2h2zm4 0h-2v2h2m2-4H5a3 3 0 0 0-3 3v5a5 5 0 0 0 5 5h10a5 5 0 0 0 5-5v-5a3 3 0 0 0-3-3m1 8a3 3 0 0 1-3 3H7a3 3 0 0 1-3-3v-5a1 1 0 0 1 1-1h14a1 1 0 0 1 1 1zM7 2h2v3H7zm4 0h2v3h-2zm4 0h2v3h-2z",
  office: "M20 6h-4V4c0-1.11-.89-2-2-2h-4C8.89 2 8 2.89 8 4v2H4c-1.11 0-1.99.89-1.99 2L2 19c0 1.11.89 2 2 2h16c1.11 0 2-.89 2-2V8c0-1.11-.89-2-2-2m-6 0h-4V4h4zm6 13H4v-5h6v1h4v-1h6zm-8-5.5A1.5 1.5 0 1 1 12 10a1.5 1.5 0 0 1 0 3.5M4 12V8h16v4h-6v-1h-4v1z",
  storage: "M20 8h-3V4H3v17h18V9c0-.55-.45-1-1-1M5 6h10v3H5zm14 13H5v-8h14zm-9-6h4v2h-4z",
  category: "M12 2 2 7l10 5 9-4.5V17h2V7zm-7.47 5L12 3.26 19.47 7 12 10.74zM5 11.3v5L12 20l7-3.7v-5L12 15z",
};

/**
 * Canonical room purposes used by the tablet. Home Assistant stores the MDI
 * icon on the area, so every Hausman Hub surface reads the same semantic value.
 */
export const ROOM_TYPE_OPTIONS = [
  { id: "rooms", label: "Обычная комната", mdiIcon: "mdi:door-open" },
  { id: "living", label: "Гостиная", mdiIcon: "mdi:sofa" },
  { id: "kitchen", label: "Кухня", mdiIcon: "mdi:fridge-outline" },
  { id: "bedroom", label: "Спальня", mdiIcon: "mdi:bed" },
  { id: "child", label: "Детская", mdiIcon: "mdi:human-child" },
  { id: "bathroom", label: "Ванная или душевая", mdiIcon: "mdi:bathtub" },
  { id: "toilet", label: "Туалет или санузел", mdiIcon: "mdi:human-male-female" },
  { id: "hallway", label: "Прихожая или коридор", mdiIcon: "mdi:door" },
  { id: "office", label: "Кабинет", mdiIcon: "mdi:briefcase" },
  { id: "terrace", label: "Балкон или терраса", mdiIcon: "mdi:balcony" },
  { id: "spa", label: "Спа или сауна", mdiIcon: "mdi:hot-tub" },
  { id: "storage", label: "Кладовая", mdiIcon: "mdi:archive" },
  { id: "category", label: "Другое", mdiIcon: "mdi:shape" },
];

const ROOM_TYPE_BY_MDI_ICON = new Map(ROOM_TYPE_OPTIONS.map((item) => [item.mdiIcon, item.id]));

export function canonicalRoomMdiIcon(type) {
  return ROOM_TYPE_OPTIONS.find((item) => item.id === type)?.mdiIcon || "mdi:door-open";
}

const HERO_ASSET_ROOT = "/api/hausman_hub/panel/assets";
const HERO_ASSETS = {
  living: { prefix: "hero_room_living_", suffix: ".webp" },
  kitchen: { prefix: "hero_room_kitchen_", suffix: ".webp" },
  bedroom: { prefix: "hero_room_bedroom_", suffix: ".webp" },
  bathroom: { prefix: "hero_room_bathroom_", suffix: "_v2.jpg" },
  toilet: { prefix: "hero_room_toilet_", suffix: "_v2.jpg" },
  hallway: { prefix: "hero_room_hallway_", suffix: "_v2.jpg" },
  office: { prefix: "hero_room_office_", suffix: ".webp" },
  child: { prefix: "hero_room_kids_", suffix: "_v2.jpg" },
  terrace: { prefix: "hero_room_winter_garden_", suffix: ".webp" },
  spa: { prefix: "hero_room_spa_", suffix: ".webp" },
  other: { prefix: "hero_room_other_", suffix: ".webp" },
};

// The tablet uses these higher-resolution approved masters for Home/Living and
// Kitchen. HACS deliberately resolves the exact same files, not a parallel set.
const PREMIUM_HERO_ASSETS = {
  living: {
    morning: "hero_living_room_morning.png",
    day: "hero_living_room_day.png",
    evening: "hero_living_room_golden_hour.png",
    night: "hero_living_room_night.png",
    rain: "hero_living_room_rain.png",
    snow: "hero_premium_living_snow_v2.png",
  },
  kitchen: {
    morning: "hero_kitchen_morning.png",
    day: "hero_room_kitchen_day.webp",
    evening: "hero_kitchen_evening.png",
    night: "hero_premium_kitchen_night_v2.png",
    rain: "hero_premium_kitchen_rain_v3.png",
    snow: "hero_premium_kitchen_snow_v2.png",
  },
};

function roomIdentity(room) {
  return `${room?.id || ""} ${room?.name || ""} ${room?.icon || ""}`.toLocaleLowerCase("ru-RU");
}

function containsAny(identity, values) {
  return values.some((value) => identity.includes(value));
}

/** Translate HA room identity into the same semantic category as the tablet. */
export function roomIconName(room) {
  const identity = roomIdentity(room);
  const explicit = ROOM_TYPE_BY_MDI_ICON.get(String(room?.icon || "").trim().toLocaleLowerCase("ru-RU"));
  if (explicit) return explicit;
  if (containsAny(identity, ["kids", "child", "detsk", "дет", "human-male-child", "human-female-child", "toy", "teddy", "playroom"])) return "child";
  if (containsAny(identity, ["living", "gostin", "гост", "sofa", "lounge"])) return "living";
  if (containsAny(identity, ["kitchen", "kukhn", "кух", "countertop", "fridge", "chef"])) return "kitchen";
  if (containsAny(identity, ["toilet", "tualet", "туал", "mdi:wc", "сануз"])) return "toilet";
  if (containsAny(identity, ["bath", "vann", "ван", "shower", "dush", "душ"])) return "bathroom";
  if (containsAny(identity, ["bedroom", "spal", "спал", "mdi:bed"])) return "bedroom";
  if (containsAny(identity, ["tambour", "tambur", "hall", "corridor", "koridor", "entry", "entrance", "тамбур", "прих", "корид", "door"])) return "hallway";
  if (containsAny(identity, ["terrace", "террас", "balcony", "балкон", "deck"])) return "terrace";
  if (containsAny(identity, ["spa", "спа", "hot-tub", "hottub", "сауна", "хамам"])) return "spa";
  if (containsAny(identity, ["office", "study", "work", "kabinet", "кабин", "desk"])) return "office";
  if (containsAny(identity, ["storage", "pantry", "kladov", "кладов", "inventory", "archive"])) return "storage";
  if (containsAny(identity, ["other", "misc", "проч", "category"])) return "category";
  return "rooms";
}

function heroPeriod(localIso) {
  const matchedHour = String(localIso || "").match(/T(\d{2}):/);
  const hour = matchedHour ? Number(matchedHour[1]) : new Date().getHours();
  if (hour >= 6 && hour <= 10) return "morning";
  if (hour >= 11 && hour <= 16) return "day";
  if (hour >= 17 && hour <= 21) return "evening";
  return "night";
}

function heroWeather(condition) {
  const value = String(condition || "").toLocaleLowerCase("en-US");
  if (["snow", "sleet", "hail"].some((token) => value.includes(token))) return "snow";
  if (["rain", "pour", "lightning", "drizzle"].some((token) => value.includes(token))) return "rain";
  return null;
}

/** Resolve the same room/time/weather matrix used by the Android Hero. */
export function roomHeroImage(room, summary = {}, localIso = "") {
  const icon = room ? roomIconName(room) : "living";
  const category = HERO_ASSETS[icon] ? icon : "other";
  const state = heroWeather(summary.weatherCondition) || heroPeriod(localIso);
  const premium = PREMIUM_HERO_ASSETS[category]?.[state];
  if (premium) return `${HERO_ASSET_ROOT}/${premium}`;
  const asset = HERO_ASSETS[category];
  return `${HERO_ASSET_ROOT}/${asset.prefix}${state}${asset.suffix}`;
}

export function roomSvgIcon(name) {
  const svg = document.createElementNS(SVG_NAMESPACE, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  svg.setAttribute("class", "icon");
  const path = document.createElementNS(SVG_NAMESPACE, "path");
  path.setAttribute("d", ROOM_ICON_PATHS[name] || ROOM_ICON_PATHS.rooms);
  path.setAttribute("fill", "currentColor");
  svg.appendChild(path);
  return svg;
}
