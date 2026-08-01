/* Shared semantic scenario icon catalog mirrored from the Android tablet. */

const GROUPS = [
  ["Дом", "home", [
    ["home", "Я дома", "пришёл присутствие"], ["away", "Никого нет", "ушёл отсутствие"],
    ["work", "Работа", "офис"], ["school", "Школа", "учёба дети"],
    ["car", "В дороге", "машина поездка"], ["flight", "Путешествие", "отпуск самолёт"],
    ["garage", "Гараж", "ворота"], ["garden", "Сад", "двор растения"],
    ["terrace", "Терраса", "улица веранда"],
  ]],
  ["Режимы", "sun", [
    ["sleep", "Сон", "ночь спать weather-night"], ["wake_up", "Пробуждение", "утро рассвет weather-sunset-up morning"],
    ["evening", "Вечер", "закат сумерки"], ["night", "Ночной режим", "темно moon"],
    ["alarm", "Будильник", "проснуться время"], ["schedule", "По расписанию", "часы"],
    ["timer", "Таймер", "отсчёт задержка"], ["event", "Событие", "календарь"],
    ["cleaning", "Уборка", "пылесос чистота"], ["laundry", "Стирка", "бельё"],
    ["dinner", "Ужин", "еда вечер"], ["breakfast", "Завтрак", "еда утро"],
    ["party", "Вечеринка", "гости праздник"], ["birthday", "День рождения", "торт праздник"],
  ]],
  ["Комфорт", "thermometer", [
    ["climate", "Климат", "температура"], ["cooling", "Охлаждение", "кондиционер холод"],
    ["heating", "Обогрев", "тепло отопление"], ["air", "Свежий воздух", "вентиляция"],
    ["humidity", "Влажность", "увлажнитель вода"], ["spa", "Релакс", "отдых"],
    ["meditation", "Тишина", "спокойно"], ["bath", "Ванна", "купание"],
    ["shower", "Душ", "ванная"], ["pool", "Бассейн", "плавание"],
    ["coffee", "Кофе", "кофемашина"], ["reading", "Чтение", "книга"],
  ]],
  ["Свет", "lightbulb", [
    ["light", "Освещение", "лампа свет"], ["bright", "Яркий свет", "день лампы"],
    ["dark", "Приглушить", "темно ночь"], ["idea", "Вдохновение", "идея"],
    ["flash", "Импульс", "вспышка"], ["curtains", "Шторы", "занавес"],
    ["blinds", "Жалюзи", "окно"],
  ]],
  ["Медиа", "media", [
    ["movie", "Кино", "фильм проектор"], ["tv", "Телевизор", "экран видео"],
    ["music", "Музыка", "плейлист аудио"], ["headphones", "Наушники", "аудио тихо"],
    ["speaker", "Акустика", "колонка звук"], ["volume", "Громкость", "звук"],
    ["gaming", "Игры", "консоль приставка"], ["microphone", "Голос", "караоке"],
    ["cast", "Трансляция", "экран поток"],
  ]],
  ["Безопасность", "shield", [
    ["camera", "Камеры", "наблюдение видео"], ["security", "Охрана", "сигнализация"],
    ["lock", "Закрыть дом", "замок дверь"], ["unlock", "Открыть дом", "замок дверь"],
    ["key", "Доступ", "ключ гость"], ["front_door", "Входная дверь", "вход"],
    ["sliding_door", "Раздвижная дверь", "терраса"], ["sensors", "Датчики", "движение открытие"],
    ["fire", "Пожар", "дым огонь"], ["extinguisher", "Пожарная защита", "огнетушитель"],
    ["water_leak", "Протечка", "вода авария"], ["health", "Здоровье", "помощь"],
    ["notification", "Уведомить", "тревога сообщение"],
  ]],
  ["Люди", "home", [
    ["family", "Семья", "дом люди"], ["guests", "Гости", "вечеринка"],
    ["children", "Дети", "ребёнок детская"], ["elderly", "Старшие", "родители забота"],
    ["pets", "Питомцы", "кот собака"], ["workout", "Тренировка", "спорт фитнес"],
    ["run", "Пробежка", "спорт улица"], ["favorite", "Любимое", "сердце"],
  ]],
  ["Энергия", "energy", [
    ["power", "Питание", "выключить включить"], ["energy", "Энергия", "электричество мощность"],
    ["electricity", "Электрика", "розетка провод"], ["meter", "Счётчик", "потребление"],
    ["saving", "Экономия", "деньги бюджет"], ["eco", "Эко-режим", "зелёный"],
    ["energy_saving", "Энергосбережение", "экономия"], ["solar", "Солнечная энергия", "панели солнце"],
    ["router", "Сеть", "интернет wifi"], ["wifi", "Wi‑Fi", "сеть интернет"],
    ["storm", "Гроза", "погода молния"], ["cloud", "Облачно", "погода"],
    ["standby", "Ожидание", "питание пауза"],
  ]],
];

const MDI = {
  home: "home-heart", away: "home-export-outline", work: "briefcase", school: "school", car: "car", flight: "airplane", garage: "garage", garden: "flower", terrace: "balcony",
  sleep: "sleep", wake_up: "weather-sunset-up", evening: "weather-sunset-down", night: "weather-night", alarm: "alarm", schedule: "calendar-clock", timer: "timer-outline", event: "calendar-star", cleaning: "robot-vacuum", laundry: "washing-machine", dinner: "food", breakfast: "food-croissant", party: "party-popper", birthday: "cake-variant",
  climate: "thermometer", cooling: "snowflake", heating: "fire", air: "air-filter", humidity: "water-percent", spa: "spa", meditation: "meditation", bath: "bathtub", shower: "shower", pool: "pool", coffee: "coffee", reading: "book-open-page-variant",
  light: "lightbulb", bright: "brightness-7", dark: "brightness-4", idea: "lightbulb-on-outline", flash: "flash", curtains: "curtains", blinds: "blinds",
  movie: "movie-open", tv: "television", music: "music", headphones: "headphones", speaker: "speaker", volume: "volume-high", gaming: "controller", microphone: "microphone", cast: "cast",
  camera: "cctv", security: "shield-home", lock: "lock", unlock: "lock-open", key: "key", front_door: "door", sliding_door: "door-sliding", sensors: "motion-sensor", fire: "fire-alert", extinguisher: "fire-extinguisher", water_leak: "water-alert", health: "heart-pulse", notification: "bell-alert",
  family: "account-group", guests: "account-multiple-plus", children: "human-child", elderly: "human-cane", pets: "paw", workout: "dumbbell", run: "run", favorite: "heart",
  power: "power", energy: "lightning-bolt", electricity: "power-plug", meter: "counter", saving: "cash", eco: "leaf", energy_saving: "leaf-circle", solar: "solar-power", router: "router-wireless", wifi: "wifi", storm: "weather-lightning", cloud: "weather-cloudy", standby: "power-sleep",
};

export const SCENARIO_ICON_GROUPS = GROUPS.map(([title, glyph, entries]) => ({
  title,
  glyph,
  items: entries.map(([key, label, aliases]) => ({ key, label, aliases, glyph, mdi: MDI[key] || glyph, category: title })),
}));

export const SCENARIO_ICONS = SCENARIO_ICON_GROUPS.flatMap((group) => group.items);

function normalized(value) {
  return String(value || "").toLocaleLowerCase("ru").replace(/^mdi:/, "").replaceAll("-", "_");
}

export function scenarioIconMeta(icon, title = "") {
  const source = `${normalized(icon)} ${normalized(title)}`;
  if (/home_export|away|уходим|никого/.test(source)) return SCENARIO_ICONS.find((item) => item.key === "away");
  return SCENARIO_ICONS.find((item) => source.includes(item.key)
    || String(item.aliases || "").split(" ").some((alias) => alias && source.includes(normalized(alias))))
    || SCENARIO_ICONS.find((item) => item.key === "energy");
}
