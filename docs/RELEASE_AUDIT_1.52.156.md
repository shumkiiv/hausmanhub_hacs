# Release audit HACS 1.52.156

Дата: 2026-08-23. Направление 25, backend safety gate.

## Результат

- CI измеряет branch coverage water safety, scenario executor, scenario
  service и climate deviation guard.
- 119 safety tests закреплены как отдельный release gate, минимальный порог
  branch coverage равен 75%.
- Реестр связывает pure domain, state-machine, restart, fault, contract,
  storage и настоящий Home Assistant harness с критериями продуктового плана.
- Автоматический retry тестов не используется. Flaky или упавший тест
  блокирует release.
- Интерфейс HACS не менялся, выполнен только cache-bust.

## Проверки и release

- Полный локальный gate: 1641 test, 4 skipped, без ошибок.
- Safety gate: 119 tests, суммарное branch coverage 75%; critical modules
  находятся в диапазоне 70-83%.
- Commit/tag `6398765`, GitHub Actions `32600039797` завершился успешно,
  включая отдельный coverage step.
- Release: `https://github.com/shumkiiv/hausmanhub_hacs/releases/tag/v1.52.156`.

## Production deploy

- Backup `4ad0c1ff` включает Home Assistant и базу. Копии по 930426880 байт
  подтверждены в `hassio.local` и `hassio.KeeneticSSD`, agent errors нет.
- Config check зелёный до установки и после одного restart.
- Production installed/latest равны `v1.52.156`, config entry `HausmanHub`
  загружен.
- Все 88 frontend assets совпадают с release, ERROR/CRITICAL Hausman в
  system log отсутствуют.

Откат: восстановить backup `4ad0c1ff` из local или KeeneticSSD.
