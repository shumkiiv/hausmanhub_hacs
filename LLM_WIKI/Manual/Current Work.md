# Current Work

## 2026-08-22: понятная индикация выполнения команд HACS

- Во время выполнения команды HACS показывает фиксированную синюю плашку
  «Команда отправляется» и название выбранного действия.
- Инициирующая кнопка получает локальный spinner, синюю рамку и `aria-busy`.
  Для range, checkbox и select сохраняются рамка и глобальная плашка без
  небезопасной вставки дочернего элемента в поле ввода.
- Индикация появляется сразу после click или change, не исчезает во время
  долгого запроса и снимается после результата. Повторное нажатие блокирует
  существующий общий busy-механизм. Успех и ошибка продолжают отображаться
  штатной плашкой результата.
- Профиль frontend: 105 passed, 50 subtests; JS syntax и `git diff --check`
  зелёные. Feature commit `0a27687`, версия остаётся `1.52.148`.
- Release, push и deploy не выполнялись. Android, backend, API, contracts и
  storage не менялись.

## 2026-08-22: понятная иконка кондиционеров и контрастные предупреждения HACS

- Изменён только HACS frontend на базе `1.52.148`.
- Снежинка в категории «Кондиционеры» заменена на иконку настенного блока
  кондиционера с решёткой и потоками воздуха. Снежинка остаётся обозначением
  режима охлаждения, но больше не представляет само устройство.
- Общий Hero библиотечных страниц больше не использует оранжевые рамки и
  оранжевый текст предупреждений. Hero остаётся нейтральным, текст читается
  основным цветом, а проблема обозначается красной точкой и контрастным
  значением в соответствующем показателе.
- Browser QA: 1504x900 light/dark и 900x1100 light. Профиль frontend:
  142 passed, 50 subtests; JS syntax и `git diff --check` зелёные.
- Feature commit `0952098`. Версия остаётся `1.52.148`; release, push и
  deploy не выполнялись. Android, backend, API, contracts и storage не
  менялись.

## 2026-08-22: чистые карточки освещения HACS

- Изменён только HACS frontend раздела «Освещение» на базе `1.52.148`.
- Карточки комнат, боковые списки и физические устройства используют чистые
  светлые поверхности, тонкие синие границы и мягкие тени. Активные действия
  и фильтры используют синий цвет.
- Оранжевый оставлен только как небольшой смысловой индикатор включённого
  света: иконка, точка состояния и короткая верхняя метка. Оранжевых рамок,
  подписей и серо-оранжевых подложек больше нет.
- В светлой, тёмной и узкой компоновке проверены карточки комнат, правая
  колонка, фильтры и нижние карточки устройств. Browser QA: 1504x1146
  light/dark и 900x1200 light.
- Профиль frontend: 141 passed, 50 subtests; JS syntax и `git diff --check`
  зелёные. Feature commit `c896ef8`. Версия остаётся `1.52.148`; release,
  push и deploy не выполнялись. Android, backend, API, contracts и storage
  не менялись.

## 2026-08-22: русская активность и кликабельные боковые карточки HACS

- В «Последней активности» больше не показываются технические английские
  причины. `scenario_failed` отображается как «Сценарий завершился с
  ошибкой», `restarted_by_new_trigger` как «Перезапущен новым событием».
- Неизвестный внутренний код получает нейтральную подпись «Статус события
  обновлён», поэтому будущие системные идентификаторы не протекут в UI.
- Вся карточка «Дом сейчас» открывает раздел «Комнаты», а вся карточка
  «Последняя активность» открывает «Сценарии». Обе доступны мышью, Enter и
  пробелом, имеют hover и focus состояния.
- Проверено `git diff --check`, синтаксис JS и профиль frontend: 141 passed,
  50 subtests. Версия остаётся `1.52.148`; release, push и deploy не
  выполнялись. Android, backend, API, contracts и storage не менялись.

## 2026-08-22: раскрытые панели HACS не перекрывают соседние

- Исправлен только HACS frontend на базе `1.52.148`. «Показания энергии» и
  «Освещение» по-прежнему раскрываются вместе и остаются в одной линии.
- На container 1050-1219 px подробные строки перестраиваются под доступную
  ширину: энергия использует две колонки метрик и вторую строку значений,
  освещение переносит состояние под название. Заголовки безопасно
  переносятся, списки прокручиваются внутри карточек.
- На viewport 1100 px прежнее горизонтальное наложение около 31 px устранено.
  После исправления внутреннее содержимое не пересекает соседние карточки,
  gap до ближайших событий равен 8 px, центральная и правая колонки имеют
  общую нижнюю границу.
- Browser QA: 1504x1146 light/dark, 1280x900 light, 1100x900 light/dark.
  Горизонтального overflow и runtime errors нет. Профиль UI: 102 passed,
  50 subtests.
- Версия остаётся `1.52.148`; release, push и deploy не выполнялись. Android,
  backend, API, contracts и storage не менялись.

## 2026-08-22: HACS 1.52.147, плотность главной и карточек устройств

- В релиз вошли три frontend-правки. Главная заканчивает меню, центральную
  ленту и правую активность на одной линии. Энергия и освещение раскрываются
  вместе и заполняют доступную высоту без пустого участка. Hero увеличен до
  280 px, ближайшие события прокручиваются внутри компактной панели, рамка
  активной комнаты не обрезается. Карточки устройств уменьшены до 150 px.
- Feature commits `305c83c`, `ddd6d6e`, `5a2387f`; release commit/tag
  `aa4ef59`, `v1.52.147`. GitHub Release опубликован без assets. Local gate:
  1609 passed, 4 skipped. Actions `32576732490` завершён успешно.
- Full backup `aab91cba` хранится в `hassio.local` и
  `hassio.KeeneticSSD`, размер каждой защищённой копии 921815040 байт.
  Выполнены два config checks и один restart.
- После deploy installed/latest `v1.52.147`, config entry loaded. Dashboard
  и 11 ближайших событий отвечают HTTP 200. Journal остался на sequence 998,
  climate guard сохранил monitor revision 1 и не вооружён. Все 88 frontend
  assets совпадают с release, ошибок Hausman в system log нет.
- Android, backend, API, contracts и storage не менялись. Физические команды
  не отправлялись. Откат: backup `aab91cba`.

## 2026-08-22: единая высота колонок главной HACS

- Изменён только HACS frontend на базе `1.52.144`. Левая навигация,
  центральная лента и правая колонка имеют общую нижнюю границу как в
  компактном, так и в развёрнутом состоянии.
- Панель ближайших событий занимает оставшуюся высоту центральной колонки.
  Правая колонка растягивает «Последнюю активность» после фиксированного
  блока «Дом сейчас» и больше не выходит ниже меню.
- «Показания энергии» и «Освещение» используют один сохранённый режим.
  Нажатие «Развернуть» или «Свернуть» на любой карточке синхронно меняет обе.
  Обе карточки равны по высоте, а списки заполняют доступное место без
  пустого участка снизу.
- Desktop-активность сразу показывает название, описание и время события.
  На узких экранах сохранён компактный вид.
- Browser QA 1504x1146: compact menu/overview/right bottom 1134 px;
  expanded bottom 1337,5 px, обе utility-card 372 px. Светлая и тёмная темы
  без горизонтального overflow. Профиль UI: 102 passed, 50 subtests.
- Версия остаётся `1.52.144`; release, push и deploy не выполнялись. Android,
  backend, API, contracts и storage не менялись.

## 2026-08-22: HACS 1.52.142, серверная классификация сценариев

- Scenario list закреплён контрактом `hausman-hub-scenario-list` v1 из
  contracts `0.49.0` (`5478341`). Backend возвращает `activationKind`,
  `roomId`, `protected`, `nextRun`, `lastResult`, `temporaryException`.
- Защищённые system-сценарии нельзя удалить. Legacy storage без новых полей
  загружается и мигрирует безопасно. Android и HACS UI смогут убрать
  локальные эвристики отдельными клиентскими релизами.
- Feature commit `04064d2`, release commit/tag `3a1fb54`, `v1.52.142`.
  Финальный staged gate: 1580 tests, 4 skipped. Actions `32562805751`
  успешен.
- Production backup `7a4b14cd`: по 921886720 байт в `hassio.local` и
  `hassio.KeeneticSSD`. Выполнены два успешных config checks и один restart.
  Installed/latest `v1.52.142`, config entry loaded.
- Production response из 39 сценариев и operation journal проходят JSON
  Schema. В списке 24 защищённых system, 9 manual и 6 automatic. Девять
  shadow-сценариев сохранили `commandMode=shadow`.
- Все 88 frontend JS/CSS файлов совпадают с release по SHA-256. Визуальный
  baseline не менялся. Node-RED остаётся физическим владельцем до завершения
  soak.

## 2026-08-22: HACS 1.52.141, shadow-наблюдение перед Node-RED cutover

- Релизная цепочка: `v1.52.139` добавил fail-safe shadow runtime,
  `v1.52.140` научил безопасно подтверждать `turn_off` при уже выключенном
  источнике без HA-вызова, `v1.52.141` заменил HTTP 500 для stale target на
  структурированный HTTP 400. Contracts pin: `0.48.1` (`2f0f919`).
- UI baseline `1.52.138` сохранён: release-diff frontend содержит только
  cache-bust, 27 production assets совпадают по SHA-256.
- В production включены девять shadow-сценариев: восемь веток ванной и away
  из 24 действий. Старые live-дубли ванной, away и системных сумеречных штор
  выключены. Node-RED продолжает выполнять физические ветки, его flows.json
  не изменён и совпадает с backup по SHA-256
  `7e2d3830ea4712531e0898a78c6c9bc53c42f80ff0b415a1831f03018a36a316`.
- Контрольный away shadow-run не отправил физических команд. В journal
  записаны `command_mode=shadow`, confirmed false; запуск остановился partial
  на `power_source_unavailable`, что является ожидаемым fail-safe сигналом
  для периода сравнения.
- Full backup `fe727f76`: по 914862080 байт в `hassio.local` и
  `hassio.KeeneticSSD`, Home Assistant, база, 10 add-ons,
  `ssl/share/media`, agent errors отсутствуют.
- Финальный local gate: 1574 tests, 4 skipped. Actions `32561158937`
  успешен. Installed/latest `v1.52.141`, config entry loaded, API и panel
  HTTP 200, operation journal проходит JSON Schema, unavailable Hausman
  entities нет. Ошибок Hausman в system log нет; сторонние MQTT/Xiaomi
  ошибки и известное climate WARNING не вызваны релизом.
- Следующий шаг: собирать journal и сравнивать его с Node-RED 7-14 суток.
  Физическое владение ветками не переключать до успешного soak и проверки
  fault/recovery.

## 2026-08-22: HACS 1.52.138 опубликован и развёрнут

- В release вошли режимы «Показаний энергии» и «Освещения», полноразмерная
  «Активность», ближайшие события с действием «Пропустить» и исправление
  стартовой прокрутки Hero.
- Feature commit `2921547`, context commit `57b30c4`, release commit/tag
  `1596a74`, `v1.52.138`. GitHub Release без assets:
  https://github.com/shumkiiv/hausmanhub_hacs/releases/tag/v1.52.138.
- Local release-gate: 1563 tests, 4 skipped. GitHub Actions `32558198896`
  завершён успешно.
- Preflight: installed/latest `v1.52.137`, update idle, config check HTTP 200.
  Backup `22410676` завершён в local и KeeneticSSD, размер каждой копии
  919613440 байт. Включены Home Assistant, база, 10 add-ons и `ssl`, ошибок
  агентов нет.
- Явная `v1.52.138` установлена без второго backup. Повторный config check
  успешен, Home Assistant перезапущен один раз.
- После restart installed/latest `v1.52.138`, config entry loaded, 12
  релевантных сущностей доступны. Admin panel, Dashboard, upcoming events и
  operation journal отвечают HTTP 200; journal schema valid. Все 29
  изменённых frontend assets совпадают с release по SHA-256.
- В system log нет ошибок и traceback. Остаётся одно известное WARNING о
  расхождении физического уличного датчика и погодного сервиса. Физические
  команды не отправлялись.
- Android, backend, API, contracts и storage не менялись. Откат: backup
  `22410676`.

## 2026-08-22: режимы карточек и ближайшие события на главной HACS

- Карточки «Показания энергии» и «Освещение» по умолчанию остаются
  компактными. Кнопка «Развернуть» независимо увеличивает каждую карточку и
  выводит подробности из уже загруженного Dashboard snapshot. Выбор хранится
  только в localStorage браузера и не меняет backend или настройки Home
  Assistant.
- Развёрнутая энергия показывает общие мощность, ток, напряжение при
  разрешённом `showVoltage`, накопление кВт·ч и до шести выбранных источников.
  Развёрнутый свет показывает число активных и недоступных физических
  устройств, затем до шести устройств с комнатой и состоянием.
- Панель «Активность» получила размер левого меню 238x1360 px на viewport
  1440x1400, до 12 событий и внутреннюю прокрутку. Правая колонка и main не
  создают горизонтального overflow.
- Под основными карточками отображаются ближайшие события. Для cancellable
  запусков доступно действие «Пропустить» через существующий upcoming cancel
  API. Новых API и storage не добавлено.
- Исправлена стартовая прокрутка страницы: навигация комнат Hero центрирует
  кнопку только внутри горизонтальной ленты и больше не сдвигает весь экран
  вниз.
- Feature commit `2921547` на ветке
  `codex/hacs-overview-utility-modes-2026-08-22`, база HACS `1.52.137`.
  Профиль UI: 102 passed, 50 subtests. Full pytest: 1566 passed, 4 skipped,
  985 subtests. Browser QA после чистой загрузки: runtime errors отсутствуют.
- Release, push и deploy не выполнялись. Android, backend, API, contracts и
  storage не менялись.

## 2026-08-22: HACS 1.52.137, полный contracts 0.47.0

- Release-line gate нашёл три несовпадения vendored contracts после
  `1.52.136`: Dashboard `renameable`, water-meter schema и multi-source
  energy fixture. Все три синхронизированы, hash-тест добавлен.
- External validator подтверждает 52 schemas, 97 fixtures, 39 OpenAPI paths
  и HACS source matches. Full release-gate: 1562 tests, 4 skipped; Actions
  `32555953795` успешен.
- Feature commit `e51d58f`, release commit/tag `538bcc4`, `v1.52.137`.
- Production: backup `6b14607f`, 918568960 байт, два config checks, один
  restart. Installed/latest `v1.52.137`, 12 сущностей доступны, journal
  schema valid, 27 frontend assets совпадают, system log чист.
- Runtime сценариев и UI не менялись. Физические команды не отправлялись.

## 2026-08-22: HACS 1.52.136, безопасное исполнение сценариев

- Сценарии получили ограниченную очередь, явный queue-full, выдержку
  состояния, debounce, cooldown и управляемую обработку recovery-событий.
- Условия запуска читаются единым snapshot. Неизвестные данные и устаревшие
  доказательства для критичных lock/valve-команд блокируют выполнение.
  Идемпотентность, partial-исходы, nested depth и проверка циклов действуют
  единообразно.
- Operation journal хранит редактированный trace решений, действий и исходов.
  Сырые entity ID, пользовательские имена и исключения наружу не выходят.
- HACS закреплён на contracts `0.47.0`, commit `57a1b04`. Feature commit
  `e4bca0a`, release commit/tag `02206a7`, `v1.52.136`. Release:
  https://github.com/shumkiiv/hausmanhub_hacs/releases/tag/v1.52.136.
- Профиль safety: 158 tests и 3 subtests. Полный release-gate: 1562 tests,
  4 skipped. GitHub Actions `32554721280` завершён успешно.
- Перед deploy создан full backup `f86830d3`, 917975040 байт, с Home
  Assistant, базой, 10 add-ons и `ssl`; failed-списки пусты. После явной
  установки, успешных config checks и одного restart installed/latest равны
  `v1.52.136`, config entry loaded, 12 сущностей доступны, journal schema
  valid, 27 frontend assets совпадают с release.
- Визуальная компоновка 1.52.135 сохранена: frontend release diff содержит
  только cache/version bump. Физические команды при проверке не
  отправлялись. Откат: backup `f86830d3`.

## 2026-08-22: HACS 1.52.135, планшетные правки и единый масштаб

- Главная приведена к масштабу остальных страниц: типографика, Hero,
  карточки метрик, интервалы и правая колонка больше не увеличиваются на
  широком viewport. Раздел «Освещение» получил актуальные планшетные
  физические каналы, образ потолочного светильника и парные диапазоны
  яркости и цветовой температуры.
- Feature commits `e6e2cf4`, `0a65de4`; release commit/tag `12c4609`,
  `v1.52.135`. Release без assets:
  https://github.com/shumkiiv/hausmanhub_hacs/releases/tag/v1.52.135.
- Staged `python3 tools/check_local_release.py`: 1548 tests, 4 skipped;
  package, fixtures, Android compatibility, README sync, version gate и
  repository safety успешны. GitHub Actions `32552485736` завершён успешно.
- Перед deploy создан full backup `362c8bac` с Home Assistant, базой,
  10 add-ons и папкой `ssl`. Копии по 916940800 байт подтверждены в
  `hassio.local` и `hassio.KeeneticSSD`.
- HACS установлен явной `v1.52.135`, `check_config` успешен, Home Assistant
  перезапущен один раз. После restart installed/latest равны `v1.52.135`,
  30 из 30 изменённых frontend assets совпадают с release, 10 сущностей
  доступны, system log не содержит ошибок или traceback Hausman Hub.
- На production viewport 2560x1306 главная и «Освещение» имеют одинаковую
  ширину `main` 1600 px, cache version `1.52.135`, горизонтального overflow
  нет. JS exceptions отсутствуют. Пять network 403 относятся к известному
  scope браузерной сессии для `capabilities/events`; read token получает
  capabilities с HTTP 200.
- Android, backend, API, contracts и storage не менялись. Откат доступен
  через backup `362c8bac`.

## 2026-08-22: масштаб главной HACS соответствует остальным страницам

- Причина визуального увеличения была не в ширине страницы, а в отдельных
  `vw`-размерах главной. На viewport 1600 они увеличивали планшетную
  композицию до 125%: заголовок достигал 38.24 px, Hero 281 px, строка
  метрик 245 px.
- Главная переведена на базовый масштаб без роста от ширины окна. Заголовок
  теперь 29 px, Hero 225 px, строка метрик 196 px.
- Каркас совпадает с обычными страницами: rail 238 px, свёрнутый rail 88 px,
  промежуток 28 px, поля main 28/34/56 px, max-width 1600 px.
- Проверены dark 1280x800 и 1600x1000, light 1600x1000, раскрытый и
  свёрнутый rail. Горизонтального overflow, наложений и обрезки нет.
- Full pytest: 1550 passed, 4 skipped, 984 subtests. Версия остаётся
  `1.52.134`; release, push и deploy не выполнялись. Android и backend не
  менялись.

## 2026-08-21: последние планшетные правки Android в HACS frontend

- Из Android 1.0.241 перенесено вертикальное центрирование значений в
  компактной карточке «Дом сейчас»: иконка сверху, значение в центральной
  зоне, подпись снизу.
- Из Android 1.0.242 перенесена компоновка комнаты освещения по физическим
  устройствам. Нумерованные каналы выключателя управляются отдельно, люстра
  получает штатный образ потолочного светильника, яркость и температура
  света показаны двумя компактными контролами рядом.
- Учтено последнее изменение исходников Android 1.0.243: суффиксы каналов
  `_1`, `_2` и `_3` распознаются как независимые линии.
- HACS использует только уже опубликованные данные Dashboard, catalog actions
  и opaque target IDs. Backend, API, contracts, storage и Android не
  менялись.
- Визуально проверены dark 1600x1000 для главной и модального окна кабинета.
  `git diff --check` и `node --check` зелёные. Full `python3 -m pytest -q`:
  1550 passed, 4 skipped, 984 subtests.
- Версия остаётся `1.52.134`. Release, push и deploy не выполнялись.

## 2026-08-21: HACS 1.52.134, ширина главной как у остальных страниц

- Главная использует общий с обычными страницами предел ширины 1600 px,
  центрирование и горизонтальные поля 34 px. Планшетная компоновка внутри
  главной, её вертикальные размеры и состояния rail не менялись.
- На живом Home Assistant при viewport 2560 px `main` главной и страницы
  «Освещение» имеет одинаковую вычисленную ширину 1600 px. Главная больше не
  растягивается до правого края экрана. Runtime errors и горизонтального
  overflow нет.
- Feature commit `a1e06e8`, release commit/tag `287f1e4`. Release:
  https://github.com/shumkiiv/hausmanhub_hacs/releases/tag/v1.52.134.
- Staged release-gate: 1547 tests, 4 skipped. GitHub Actions
  `32514457615` завершён успешно.
- Перед deploy создан защищённый full backup `2ac3c237` в local и
  KeeneticSSD. После явной установки, успешного `check_config` и одного
  restart installed/latest равны `v1.52.134`, все 28 изменённых
  frontend-файлов совпадают с release, 10 сущностей интеграции доступны,
  ошибок Hausman Hub в журнале нет.
- Изменение этой задачи относится только к HACS frontend. Android, API и
  contracts не менялись. Release собран поверх актуального `origin/main` и
  включает ранее принятый backend commit `4c79d8d`; backend этой задачей не
  редактировался.

## 2026-08-21: HACS 1.52.133, главная и киоск как на планшете

- Изменён только HACS frontend. Android использовался как read-only
  визуальный эталон; backend, API, contracts и storage не затрагивались.
- Главная повторяет планшетную геометрию на 1280x800: раскрытый rail 176 px,
  свёрнутый 72 px, правая колонка 194 px, шапка 88 px, Hero 225 px,
  метрики 196 px, сценарии 119 px и нижние карточки 124 px. На 1600x1000
  размеры масштабируются тем же коэффициентом.
- Hero снова содержит счётчики дома и навигацию комнат. Центральная часть
  собрана из погоды, цели климата, комфорта, избранных сценариев, источников
  энергии и света. Правая колонка компактная при раскрытом rail и подробная
  при свёрнутом, как на планшете.
- Киоск повторяет планшетные пропорции и порядок: Hero, три карточки метрик,
  избранные сценарии, справа погода, «Дом сейчас» и домофон. Нижний safe
  inset сохранён. Домофон активен только при реальной настройке быстрого
  доступа.
- Белые кнопки левого меню сохранены по предыдущему решению владельца.
  Значения берутся из текущего HACS Dashboard, демонстрационные цифры и
  недоступные системные индикаторы не добавлялись.
- Проверено в браузере: light/dark, 1280x800 и 1600x1000, раскрытый и
  свёрнутый rail, главная и киоск. Во всех состояниях viewport и content
  совпадают, overflow и runtime errors отсутствуют. `git diff --check` и
  `node --check` зелёные. Full `python3 -m pytest -q`: 1548 passed,
  4 skipped, 984 subtests.
- Feature commits `e69d482`, `c24b339`; release commit/tag `b69d84c`.
  GitHub Release без assets:
  https://github.com/shumkiiv/hausmanhub_hacs/releases/tag/v1.52.133.
- Полный pytest до выпуска: 1548 passed, 4 skipped, 984 subtests. Staged
  `python3 tools/check_local_release.py`: 1546 passed, 4 skipped. GitHub
  Actions `32507160930` завершён успешно.
- Перед deploy создан защищённый full backup `a2c07631` одновременно в
  local и KeeneticSSD. HACS установлен явной `v1.52.133`, `check_config`
  успешен, Core перезапущен один раз.
- После restart installed/latest равны `v1.52.133`, panel отвечает 200 и
  содержит cache version `1.52.133`. Все 31 изменённый frontend asset на
  production побайтово совпадает с release. Доступны 10 сущностей
  интеграции, unavailable среди них нет, ошибок интеграции в журнале нет.
  Два WARNING являются штатным сообщением Home Assistant о custom
  integration. Откат доступен через backup `a2c07631`.

## 2026-08-21: Dashboard HACS по новому референсу

- Изменён только интерфейс HACS. Backend, API, Android, storage и production
  не затрагивались.
- Сетка повторяет переданный референс: крупный Hero с точками карусели,
  погода и «Дом сейчас» справа, единый ряд «Климат / Освещение /
  Безопасность», четыре избранных сценария и компактная правая колонка.
- Дубли attention удалены. Один красный блок расположен под энергией;
  большой пустой блок ближайших событий убран, события доступны из кнопки в
  заголовке.
- Энергия показывает переданное значение механическими барабанами. Для
  целого значения отображаются только целые барабаны, для дробного остаётся
  красный десятичный барабан. «Подробнее» и «Настройки» сделаны светлыми.
- Hero дома выводит «Дом» и подпись «Комфорт и безопасность вашего дома».
  Визуальная навигация комнат скрыта, точки и клавиатурное управление
  сохранены.
- Проверено: light/dark 1600x1000, адаптивные 900x1000 и 640x1000;
  `python3 -m pytest -q` - 1548 passed, 4 skipped, 984 subtests.
- Версия остаётся `1.52.132`. Release, push и deploy не выполнялись.

## 2026-08-21: HACS 1.52.132, Hero вкладки «Дом»

- Причина технического заголовка: Hero без нормализации использовал
  `dashboard.summary.homeName`, а production Home Assistant отдавал в нём
  стандартное `Home Assistant`.
- Frontend HACS теперь заменяет только `Home Assistant` и `HomeAssistant` на
  «Дом». Любое заданное пользователем название не меняется. Backend, API,
  Android и пользовательские настройки не трогались.
- Release commit/tag `d17ccb0`, GitHub Release:
  https://github.com/shumkiiv/hausmanhub_hacs/releases/tag/v1.52.132.
  В тот же выпуск вошёл P0 Dashboard: спокойный Hero, единый блок внимания и
  объединённая карточка климата.
- Full staged `python3 tools/check_local_release.py`: 1546 passed, 4 skipped.
  Перед deploy создан full backup `c04771be`; HACS установлен явной версией,
  Core перезапущен один раз. Проверка: installed/latest `v1.52.132`, панель
  200 с cache version `1.52.132`, 10 сущностей доступны, ошибок интеграции
  нет. Нефатальное WARNING о расхождении источников температуры на 3.5 C
  относится к живым данным и не блокирует команды.

## 2026-07-28: релиз 1.27.0 - обучение IR-кодов в мастере

## 2026-07-28: релиз 1.27.0 - обучение IR-кодов в мастере
- Release commit `f22df48` на `origin/main` (30 файлов, +4021/-57); tag
  `v1.27.0`; GitHub Actions run `30352650893` success; публичный релиз:
  https://github.com/shumkiiv/hausmanhub_hacs/releases/tag/v1.27.0.
- Деплой: HACS update entity, явная версия v1.27.0, рестарт HA, проверено
  на живом: `integration_version: 1.27.0`.
- Состав: шаг «Источник IR-кодов» после сохранения контура + возобновляемая
  кнопка на карточке контура; приоритет SmartIR -> Broadlink .storage ->
  ручное обучение с явным replace; коды в версионированном Store с привязкой
  к device_id; канонические ключи ac.off/ac.cool.<t>/ac.heat.<t>/
  humidifier.on|off; типизированный remote.send_command через строгий
  исполнитель; ir_command_not_learned без молчаливой подмены; endpoint
  /api/hausman_hub/v1/admin/ir-codes/bindings; 422/409 защиты; контракт v1
  без изменений; сервис за протоколами, HA-адаптеры снаружи.
- Oracle отклонил первую реализацию; одна программа исправлений в 3 прохода
  (сессия ses_057e6b30affe0BV6WgVQntepEC) закрыла все блокеры. Второго
  раунда ревью нет по политике. Гейт: 887 passed / 4 skipped,
  check_local_release.py зелёный, бюджет панели 230 KiB сохранён.
- Основной checkout hausmanhub_hasc устарел (первый WIP 1.27.0 + DESIGN.md
  rev 4, позади origin/main): перед дальнейшей работой сбросить или
  перебазировать, не строить на нём.

## 2026-07-28: релиз 1.26.2 - заметное предупреждение об устройствах без комнаты
- Release commit `083b4c7` на `origin/main`; tag `v1.26.2`; GitHub Actions
  run `30341142526` success; публичный релиз:
  https://github.com/shumkiiv/hausmanhub_hacs/releases/tag/v1.26.2.
- Worktree `hausmanhub_hasc-1262` (branch `ui-1.26.2-roomless-warning`).
- `.wizard-warning` amber-баннер: шаг списка комнат - счётчик roomless,
  шаг комнаты - до 5 имён с «и ещё N» и оба пути (привязка только в
  HausmanHub через секцию «Устройства без комнаты» или назначение зоны в
  HA + «Обновить список устройств»). Баннеры скрыты, когда roomless нет.
- Тесты в `tests/test_hausmanhub_panel_wizard.py`: имена, усечение до 5,
  счётчик, отсутствие баннера. Гейт: 829 passed / 4 skipped,
  `check_local_release.py` passed.
- Деплой: HACS update entity, явная `v1.26.2`, рестарт HA; read-only
  проверка: `integration_version: 1.26.2`, панель отдаёт новую сборку
  (212368 байт, строки предупреждения на месте).
- Тот же паттерн баннера отрисован в Figma HMH--HA (обе темы), партия 2
  закрыла все экраны раздела 4 DESIGN.md в dark+light.

## 2026-07-27: релиз 1.26.1 - светлая тема + фикс селекта канала
- Release commit `0d215c4` на `origin/main`; tag `v1.26.1`; GitHub Actions
  run `30288293165` passed; публичный релиз:
  https://github.com/shumkiiv/hausmanhub_hacs/releases/tag/v1.26.1.
- Светлая тема: токены `:host(.theme-light)`, переключатель авто/светлая/
  тёмная (авто следует `hass.themes.darkMode`), выбор только в сессии
  панели (localStorage запрещён контрактным тестом).
- `--warning-color` светлой темы `#9A5F0B` (замеренный контраст 4.62-5.23:1)
  после Oracle-замечания: `#B06F14` давал 4.09:1 < WCAG 4.5:1. DESIGN.md
  ревизия 3 обновлён (только в основном checkout, untracked).
- Фикс обрезки селекта «Канал управления»: `.device-card-options
  label.form-field` переведён в одноколоночный grid, селект на всю ширину
  карточки устройства.
- Расследование «кондиционер Broadlink отсутствует в мастере»: не баг.
  SmartIR-сущность есть в payload climate-drafts (candidate_0001,
  can_add true, available), но у неё нет area в HA (room_id "",
  suggested_room_id null, reason unassigned_room; template-проверка:
  area_id/area_name/device_id все None). В first-run мастере она видна в
  свернутой секции «Устройства без комнаты», в редакторе контура
  roomless-устройства скрыты с предупреждением (by design). Решение для
  пользователя: назначить зону сущности climate.komanchi_living_smartir.
- Гейты: 827 passed, 4 skipped, 732 subtests; check_local_release.py
  зелёный после бампа версии (manifest + 3 ссылки на версию в тестах).
- Собрано в worktree `hausmanhub_hasc-1261` (ветка ui-1.26.1-theme);
  IR-learning WIP 1.27.0 остаётся незакоммиченным в основном checkout.

## 2026-07-27: релиз 1.26.0 - редизайн панели + selectable-недоступные
- Release commit `3ab7584` на `origin/main`; tag `v1.26.0`; GitHub Actions
  run `30282626657` passed; публичный релиз:
  https://github.com/shumkiiv/hausmanhub_hacs/releases/tag/v1.26.0.
- Панель перерисована по утверждённой ревизии 2 DESIGN.md (тёмная палитра
  HMH-II, inline SVG-иконки). Ревизия 2 имеет приоритет над историческими
  разделами 1-2 (neumorphism, токены `--hh-*`): это важно при ревью,
  Oracle один раз ошибочно применил токены из отклонённой ревизии 1.
- Дефолты комфорта: день 25.0°/53%, ночь 25.5°/50%, границы 24.5-27°,
  шаг влажности 1%.
- Недоступные устройства выбираемы (продуктовое решение 4): бейдж
  «Сейчас недоступно», warning после выбора, `save_allowed` true; backend
  `can_accept` покрывает available+unavailable.
- Oracle-ревью: REJECT, 2 major; одна итерация - схема
  `climate-draft-validation.schema.json` `message` теперь anyOf
  (статический enum для error-кодов + pattern для динамического
  `device_unavailable` с именем устройства). Второе finding отклонено
  как основанное на ревизии 1.
- Гейты: 825 passed, 4 skipped, 732 subtests; check_local_release.py
  зелёный после бампа версии; визуальная проверка headless Chrome
  1224/420/360 px (wizard, шаг комнаты, настроенный обзор).
- Релиз собран в worktree `hausmanhub_hasc-1260` с чистого main;
  IR-learning WIP 1.27.0 остаётся незакоммиченным в основном checkout.

## 2026-07-27: first-run wizard device visibility + Oracle fix iteration (1.25.3)
- В шаге комнаты первичной настройки недоступные устройства своей области
  остаются видимыми, но получают disabled checkbox, статус, причину и подсказку
  обновить каталог Home Assistant.
- Добавлены: группа похожей области для активных климатических устройств,
  переключатель полного каталога с группами по областям и последней группой
  «Без комнаты», обновление каталога с сохранением прежнего выбора и канала,
  а также disabled-псевдострока «Тип не определён» для кандидатов с пустым
  `suggested_types`.
- Oracle-ревью (gpt-5.6-sol) нашло 4 блокера; одна итерация закрыла все:
  - backend отдаёт стабильный `candidate_key` (`ckey_<sha256(source_id)[:12]>`)
    в `climate_device_candidates` и `climate_setup_options`; обе v1 JSON-схемы
    и обе фикстуры обновлены. UI объединяет выбор по `candidate_key` и при
    отправке черновика берёт текущий позиционный `candidate_id`, поэтому
    перенумерация при refresh больше не переносит выбор на чужое устройство;
  - новые кандидаты стартуют с `selected: false` (явный выбор пользователем);
  - матчинг похожей комнаты сначала пробует полное нормализованное имя, затем
    укороченный корень от 4 символов («Ванная», «Зал» работают);
  - успешный refresh сбрасывает устаревшие report/validRooms/draft/validation;
  - `_unbound_suggested_kinds` при неинформативных `hvac_modes`
    (`("off", "auto")`) теперь пробует TRV-маркеры имени; `("heat", "cool")`
    остаётся кондиционером.
- Тесты: wizard-файл 21 тест (было 14); refresh-тест моделирует реальную
  перенумерацию id. Новые backend-тесты: TRV-fallback и стабильность ключа.
  Бюджет panel.js поднят 200 -> 210 КиБ (рост от переработки мастера).
- Полный suite: 861 passed, 4 skipped, 728 subtests passed, 11 failures -
  только предсуществующая незакоммиченная IR-learning ветка. Коммит и релиз
  не делались; сначала разобрать IR-learning WIP.

## Result
- Release 1.25.3 (first-run wizard device-catalog rework) is RELEASED on
  2026-07-27.
- Release commit `f3cb4e7` on `origin/main`; tag `v1.25.3`; GitHub Actions
  run `30251991310` passed; public release:
  https://github.com/shumkiiv/hausmanhub_hacs/releases/tag/v1.25.3.
- Full local gate in a clean worktree from the release commit: 825 passed,
  4 skipped, 732 subtests; `tools/check_local_release.py` passed (829 tests
  OK plus fixture, naming, and repo-safety checks).
- For the release the unfinished 1.26.0 `code_source` step was removed from
  the panel (broken `state.choices` read); wizard flow is home → validation
  again. 1.26.0 IR-learning WIP files stay uncommitted in the working tree.
- Release 1.25.2 (wizard device-selection fix) is RELEASED and DEPLOYED on
  2026-07-26.
- Release commit `3eb8ffe` on `origin/main`; tag `v1.25.2`; GitHub Actions
  run `30219220629` passed; public Latest release:
  https://github.com/shumkiiv/hausmanhub_hacs/releases/tag/v1.25.2.
- Full local gate in clean worktree: 812 tests passed, 4 skipped, plus
  `tools/check_local_release.py`.

## Root cause (live bug "не выбирается устройство")
- `climate_ha_state_view.py` `entity_catalog()` read `supported_features`
  with the strict guard `type(x) is int`. Real HA stores it as a
  `ClimateEntityFeature` IntFlag, so the guard zeroed it for every climate
  entity: command_types collapsed to `(climate.set_hvac_mode,)` and every
  air-conditioner candidate failed validation with "device is missing
  required capabilities: power, target_temperature".
- The guard existed since 09aea13 (native discovery, 1.21.0). Tests and
  REST/JSON dumps always carry plain ints, which hid the bug locally.
- Fix: `isinstance` check plus `int()` normalization; regression test
  `test_catalog_accepts_intflag_supported_features`.
- Proven end-to-end before release: clean tag 1.25.1 fed IntFlag features
  reproduced the exact live error; fed plain ints it returned `ready`.

## Diagnostics shipped in 1.25.1
- Commit `4d15037`, tag `v1.25.1`: `detail` field in `unsupported_device_set`
  issues (stage import/setup plus original error text), which pinpointed the
  failure stage on live without server logs.

## Deploy verification (live HA)
- HACS update entity installed `v1.25.2` explicitly; HA restarted;
  `installed_version: v1.25.2`.
- Draft validation for гостиная: `status: ready`, `save_allowed: true`,
  `issues: []`; snapshot_revision `239926551809926` matches the local
  clean-tag reconstruction exactly.
- Four of five AC candidates validate `ready`; candidate_0030 (Electrolux
  air purifier) is honestly blocked on missing `target_temperature` -
  correct behaviour, it is not an air conditioner.

## Next
- 1.26.0 wizard IR-learning vertical ("2 lite"): SmartIR code DB scan,
  Broadlink `.storage` codes, `remote.learn_command` last. WIP files stay
  uncommitted in the working tree.
- Known WIP-scope issues to fix there: the `code_source` wizard step was
  removed from the released panel (it read nonexistent `state.choices` and
  always rendered an empty IR-device list); a fixed re-implementation must
  consume the real draft/choices shape. Failing WIP tests include
  `test_raw_remote_endpoint_stays_blocked_for_any_channel`,
  `test_ir_code_storage`, local-summary boundary, and read-only skeleton
  (11 failures total in the dirty tree).
