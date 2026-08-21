# Current Work

## 2026-08-21: ширина главной HACS как у остальных страниц

- Изменён только HACS frontend. Главная теперь использует общий предел
  ширины 1600 px, центрирование и горизонтальные поля 34 px, как страница
  «Освещение».
- Планшетная компоновка внутри главной, её вертикальные размеры, раскрытый и
  свёрнутый rail не менялись.
- На viewport 1920 px границы `main` у главной и «Освещения» совпадают:
  160-1760 px. Горизонтального overflow нет. На 1280x800 визуально проверены
  light/dark и оба состояния rail.
- Профильные тесты: 118 passed, 82 subtests. Контракт главной отдельно:
  31 passed, 50 subtests.
- Версия остаётся `1.52.133`. Release, push и deploy не выполнялись.

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
