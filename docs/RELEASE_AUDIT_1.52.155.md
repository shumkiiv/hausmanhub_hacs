# Release audit HACS 1.52.155

Дата: 2026-08-23. Направление 24, проверяемый паритет HACS и Android.

## Что выпущено

- Добавлен исполняемый component registry для header, card, detail, control,
  notice, picker и empty state.
- Паритет закреплён на общей dashboard fixture, semantic tokens, одинаковых
  пользовательских состояниях и русских receipts.
- Browser-specific преимущества сохранены: keyboard и focus, Esc, возврат
  focus, aria-live, URL navigation и responsive reflow.
- Gate фиксирует ширины 900, 1280, 1440 и 1920 px, light/dark и zoom
  125/150%.
- Выверенная визуальная компоновка HACS не менялась. Во frontend выполнен
  только cache-bust версии.

## Проверки релиза

- Полный локальный gate: 1641 test, 4 skipped; дополнительные subtests
  завершены без ошибок.
- Targeted parity, modal, UI state, tokens и source-of-truth gate: 39 tests и
  32 subtests.
- `git diff --check`, HACS package, Android compatibility, product naming,
  README/version sync и published-file safety зелёные.
- Commit/tag `7f98228`, GitHub Actions `32599293350` завершился успешно.
- GitHub Release:
  `https://github.com/shumkiiv/hausmanhub_hacs/releases/tag/v1.52.155`.

## Production deploy

- Перед установкой создан backup `b10cfc4f`. Home Assistant и база включены,
  копии по 930068480 байт подтверждены в `hassio.local` и
  `hassio.KeeneticSSD`, ошибок backup agents нет.
- Первый пробный вызов `backup.generate` как service и config check с
  `return_response` вернули HTTP 4xx и ничего не изменили. Затем использованы
  штатные `backup/generate` websocket и обычный config check.
- Выполнена точная установка `v1.52.155` через `update.install` с
  `backup=false`. До установки и после единственного restart config check
  завершился без ошибок.
- После restart installed/latest равны `v1.52.155`, config entry `HausmanHub`
  имеет состояние `loaded`.
- Все 88 frontend JS/CSS assets в production побайтно совпадают с release.
  В system log нет ERROR/CRITICAL записей Hausman.

## Откат

При проблеме восстановить backup `b10cfc4f` из локального хранилища или
KeeneticSSD. Он возвращает Home Assistant и базу к состоянию до установки
HACS 1.52.155.
