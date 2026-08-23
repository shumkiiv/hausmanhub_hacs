# Release audit HACS 1.52.157

Дата: 2026-08-23.

## Результат

Направление 27 product readiness закрыто исполняемым browser, visual и
accessibility gate. Принятый интерфейс HACS не перекомпоновывался. Для
отдельного проверяемого релиза изменён только cache-bust версии frontend.

## Версия и публикация

- Version и tag: `1.52.157`, `v1.52.157`.
- Feature commit: `9926419`.
- GitHub Release:
  `https://github.com/shumkiiv/hausmanhub_hacs/releases/tag/v1.52.157`.
- Release не содержит assets.

## Проверки

- Полный HACS gate: 1641 test, 4 skipped.
- Critical runtime: 119 test, branch coverage 75%.
- Playwright browser gate: 13 test.
- Visual baselines: 6 экранов, light/dark, wide/tablet/narrow.
- Accessibility: critical violations 0, serious baseline фиксирован точно и
  не может расшириться незаметно.
- GitHub Actions `32607274228`: success.

## Production deploy

- Config check до установки: успешно.
- Backup `3db97187`, имя `Before HausmanHub 1.52.157`.
- Две копии backup: `hassio.local` и `hassio.KeeneticSSD`, по 905011200 байт.
- Ошибок backup agent нет.
- Установлена версия `v1.52.157`, latest также `v1.52.157`.
- Выполнен один restart Home Assistant.
- Config check после restart: успешно.
- Config entry `HausmanHub`: `loaded`.
- Проверено 88 frontend assets, несовпадений нет.
- Ошибок Hausman уровня ERROR или CRITICAL нет.

## Rollback

Восстановить backup `3db97187`, затем выполнить config check и один restart.
Если нужен только runtime rollback, установить `v1.52.156` через HACS и
повторить ту же проверку.
