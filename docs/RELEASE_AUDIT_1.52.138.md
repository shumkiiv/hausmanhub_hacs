# Hausman Hub HACS 1.52.138: release audit

Дата: 2026-08-22

## Результат

- Карточки «Показания энергии» и «Освещение» получили независимые компактный
  и развёрнутый режимы. Подробный вид использует реальные данные Dashboard
  snapshot, а выбор хранится только в localStorage браузера.
- Панель «Активность» выровнена с левым меню по ширине и высоте, показывает
  до 12 событий и прокручивает записи внутри карточки.
- Ниже основных карточек подключены ближайшие события. Cancellable-запуск
  можно пропустить через существующий upcoming cancel API.
- Навигация комнат Hero больше не прокручивает всю страницу вниз при первой
  загрузке.
- Feature commit `2921547`, release commit/tag `1596a74`, `v1.52.138`.
- GitHub Release без assets:
  https://github.com/shumkiiv/hausmanhub_hacs/releases/tag/v1.52.138.

## Проверки выпуска

- Профиль UI до release bump: 102 tests, 50 subtests.
- Full pytest до release bump: 1566 tests, 4 skipped, 985 subtests.
- Staged `python3 tools/check_local_release.py`: 1563 tests, 4 skipped.
- HACS package, fixtures, Android compatibility, README sync, version gate и
  repository safety успешны.
- GitHub Actions `32558198896` завершён со статусом success.

## Production deploy

- До установки update entity сообщал installed/latest `v1.52.137`,
  `in_progress=false`. `homeassistant.check_config` завершился HTTP 200.
- Создан full backup `22410676`, по 919613440 байт в `hassio.local` и
  `hassio.KeeneticSSD`. В backup входят Home Assistant, база, 10 add-ons и
  `ssl`; failed-списки агентов пусты.
- Явная `v1.52.138` установлена через `update.install` без второго backup.
- Повторный `homeassistant.check_config` завершился HTTP 200. Home Assistant
  перезапущен ровно один раз и вернулся штатно.

## Проверка после restart

- Update entity: installed/latest `v1.52.138`, `in_progress=false`.
- Config entry Hausman Hub имеет состояние loaded.
- Доступны 12 релевантных сущностей, unavailable и unknown нет.
- Admin panel, Dashboard и upcoming events отвечают HTTP 200. В upcoming
  snapshot доступны 5 ближайших событий.
- Operation journal отвечает HTTP 200 и проходит JSON Schema.
- Панель отвечает HTTP 200 и содержит cache version `1.52.138`. SHA-256 всех
  29 изменённых frontend assets release-коммита совпадает с production.
- System log не содержит ошибок и traceback. Остаётся одно известное WARNING
  о расхождении физического уличного датчика и погодного сервиса.
- Физические команды не отправлялись.

## Откат

При проблеме восстановить full backup `22410676`. Он содержит состояние до
установки `v1.52.138`.

Android, backend, API, contracts и storage этой задачей не менялись.
