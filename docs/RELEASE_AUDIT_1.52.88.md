# Release audit HausmanHub 1.52.88

Дата: 2026-08-13.

## Область выпуска

- Dashboard API v1 публикует числовые возможности устройства как bounded
  `range` control с минимумом, максимумом, шагом, единицей измерения,
  публичным `targetId` и действием `set_value`.
- Backend разрешает только объявленный `number.set_value`, повторно проверяет
  границы и шаг до вызова Home Assistant, затем сверяет фактическое состояние.
- Технические английские подписи и известные состояния текущих устройств
  заменены русскими пользовательскими названиями.
- Contract pin обновлён до `hausmanhub-contracts 0.29.0` (`f3565fc`).
- Интерфейс HACS не менялся. Версии frontend-ресурсов обновлены только для
  согласованного release cache busting.

## Проверка до публикации

- Contract validation: 41 schema, 55 fixtures, 35 OpenAPI paths.
- Vendored dashboard schema и fixture совпадают с контрактом 0.29.0.
- Профильные backend-тесты: 53 пройдено.
- Полный backend-набор после финального adapter-исправления: 1380 тестов
  пройдено, 4 пропущено.
- `tools/check_local_release.py`: fixtures, Android compatibility, staged
  version, HACS package, naming и проверки безопасности зелёные.
- Физические команды устройствам не отправлялись.

## После публикации

Результаты release gate, GitHub Actions и production deployment будут
зафиксированы после выполнения соответствующих проверок.
