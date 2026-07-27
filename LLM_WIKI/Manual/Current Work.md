# Current Work - редизайн панели HMH-II (релиз 1.26.0)

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
