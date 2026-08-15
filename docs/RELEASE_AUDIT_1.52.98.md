# Release audit HausmanHub 1.52.98

Дата: 2026-08-15.

## Причина и изменение

- Runtime capability `voice_greeting` теперь публикует `POST` как test method,
  а также точные request и receipt contract identities.
- Consumer pin обновлён до contracts `0.32.3` (`67acee1`). Этот выпуск
  contracts добавляет fixture coverage всех десяти Android production screens.
- Изменение additive: прежние клиенты могут игнорировать новые optional поля
  capability. API v1 и существующая доменная логика не ломаются.
- Backend source commit: `438eadf`; release commit: `c00215e`.

## Проверки выпуска

- Профильный pytest: 44 теста и 222 subtests пройдены.
- Полный release-gate: 1411 тестов пройдено, 4 пропущено, 929 subtests
  пройдено.
- Synthetic fixtures, Android compatibility, staged version, product naming,
  HACS package и обе repository boundary проверки пройдены.
- Финальный аудит подтвердил 26 механических frontend/test файлов: в них
  изменилась только cache version `1.52.97 -> 1.52.98`.
- `git diff --check` пройден. GitHub Actions `31878143953` завершён успешно.
- Annotated tag и GitHub Release `v1.52.98` опубликованы из `c00215e`.

## Production

- Первый config check принят до установки.
- Создан файловый rollback archive 1.52.97 размером 38 203 421 байт.
- Полный backup `214c8013`, 912 271 360 байт, включает Home Assistant 2026.8.1,
  базу, три папки и десять add-ons. Он сохранён локально и на KeeneticSSD.
- Выполнен явный `update.install` версии `v1.52.98` без второго встроенного
  backup. После установки второй config check принят, затем выполнен один
  restart Home Assistant.
- Installed/latest, manifest и admin panel равны `1.52.98`. В frontend 104
  cache refs версии 1.52.98 и ни одной ссылки на 1.52.97.
- Все девять сущностей платформы `hausman_hub` и HACS update entity доступны.
  Runtime fresh, authority `hausman_hub`, active operations и blocked reasons
  равны нулю. Dashboard отвечает и содержит 13 комнат, 86 устройств и три
  сценария.
- `voice_greeting` доступен, рекламирует `POST`, request contract
  `hausman-hub-voice-greeting-test-request` v1 и receipt contract
  `hausman-hub-voice-command-receipt` v1.
- System log после restart не содержит записей HausmanHub. Физические команды,
  климатические цели и сценарии при deploy не запускались.

## Откат

При необходимости восстановить полный backup `214c8013`. Для быстрого
файлового отката доступен архив
`/config/hausmanhub-backups/2026-08-15-hacs-1.52.98/hausman_hub-1.52.97.tar.gz`,
после возврата компонента нужен config check и один restart.
